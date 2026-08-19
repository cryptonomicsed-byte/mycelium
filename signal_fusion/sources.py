"""Source normalizers: every intelligence source -> ONE common schema.

    Signal = { token_addr, symbol, direction (+1/-1/0), strength 0..1,
               source, source_ts (unix), meta {...} }

Two layers, deliberately separated:
  - normalize_*(raw_rows, ...) -> [Signal]  — PURE functions from raw source
    rows to the common schema. These carry all the mapping logic and are the
    unit-tested surface; they never touch the network.
  - fetch_all(cfg) -> [Signal]              — thin adapters that pull raw rows
    from the live systems (Vantage HTTP API, wallet_registry SQLite, the
    mycelium gateway) and hand them to the normalizers. Every fetcher is
    defensive: an unreachable source yields [] plus a warning, never an
    exception — a fusion run must degrade to the sources that ARE up.

direction=-1 (sell/dump) signals are kept and scored: they suppress rank.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("signal_fusion.sources")


@dataclass
class Signal:
    token_addr: str
    symbol: str
    direction: int  # +1 buy/accumulate, -1 sell/dump, 0 neutral/context
    strength: float  # 0..1
    source: str
    source_ts: float  # unix seconds
    meta: Dict[str, Any] = field(default_factory=dict)


def _direction_of(raw: Any) -> int:
    s = str(raw or "").lower()
    if s in ("buy", "long", "accumulate", "bull", "+1", "1", "positive"):
        return 1
    if s in ("sell", "short", "dump", "bear", "-1", "negative"):
        return -1
    return 0


def _ts_of(raw: Any) -> float:
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    if isinstance(raw, str) and raw:
        try:
            import datetime as _dt

            return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return time.time()


# ------------------------------------------------------------- normalizers


def normalize_vantage_signals(rows: List[Dict[str, Any]]) -> List[Signal]:
    """Vantage signal-pool rows -> Signals. The pool's own `source` field
    (smart_money/kol/onchain/social/news) is preserved into meta.pool_source
    for the per-sub-source trust weighting in scoring.py."""
    out = []
    for r in rows:
        addr = r.get("token_addr") or r.get("token") or r.get("address") or ""
        if not addr:
            continue
        out.append(Signal(
            token_addr=addr,
            symbol=str(r.get("symbol") or "?")[:16],
            direction=_direction_of(r.get("direction") or r.get("side") or "buy"),
            strength=max(0.0, min(1.0, float(r.get("strength") or r.get("confidence") or 0.5))),
            source="vantage_signal",
            source_ts=_ts_of(r.get("ts") or r.get("created_at")),
            meta={"pool_source": str(r.get("source") or "_default"), "pool_id": r.get("id")},
        ))
    return out


def normalize_wallet_trades(rows: List[Dict[str, Any]]) -> List[Signal]:
    """collector.py smart-money/KOL trade rows -> Signals. Wallet quality
    tags ride in meta for scoring.py's per-wallet quality weighting;
    amount_usd rides along for size normalization."""
    out = []
    for r in rows:
        addr = r.get("token") or r.get("token_addr") or ""
        wallet = r.get("wallet") or ""
        if not addr or not wallet:
            continue
        action = str(r.get("action") or r.get("side") or "buy").lower()
        out.append(Signal(
            token_addr=addr,
            symbol=str(r.get("symbol") or "?")[:16],
            direction=-1 if "sell" in action else 1,
            strength=1.0,  # per-trade strength comes from wallet quality x size in scoring
            source="wallet_activity",
            source_ts=_ts_of(r.get("ts")),
            meta={
                "wallet": wallet,
                "tags": list(r.get("tags") or []),
                "amount_usd": float(r.get("amount_usd") or 0),
            },
        ))
    return out


def normalize_wallet_roles(rows: List[Dict[str, Any]]) -> List[Signal]:
    """scanner.py classification rows (token-scoped holder/trader scans) ->
    aggregated per-token role context. direction=0: roles are context that
    scoring folds into S_market penalties/bonuses, not a buy/sell push."""
    out = []
    for r in rows:
        addr = r.get("token") or r.get("token_addr") or ""
        if not addr:
            continue
        out.append(Signal(
            token_addr=addr,
            symbol=str(r.get("symbol") or "?")[:16],
            direction=0,
            strength=1.0,
            source="wallet_role",
            source_ts=_ts_of(r.get("ts") or r.get("last_seen")),
            meta={"wallet": r.get("wallet") or "", "roles": list(r.get("roles") or [])},
        ))
    return out


def normalize_council_verdicts(rows: List[Dict[str, Any]]) -> List[Signal]:
    """Gates-passed council verdicts -> Signals, conviction-weighted.
    Only verdicts that PASSED the gates count (a vetoed debate is not a
    signal); LIVE verdicts carry meta.live for the 1.5x trust multiplier."""
    out = []
    for r in rows:
        if r.get("gates_passed") is False:
            continue
        addr = r.get("token_addr") or r.get("token") or ""
        if not addr:
            continue
        conviction = float(r.get("conviction") or 0)
        out.append(Signal(
            token_addr=addr,
            symbol=str(r.get("symbol") or "?")[:16],
            direction=_direction_of(r.get("direction")),
            strength=max(0.0, min(1.0, abs(conviction))),
            source="council_verdict",
            source_ts=_ts_of(r.get("posted_at") or r.get("ts")),
            meta={"verdict_id": r.get("id"), "live": not bool(r.get("paper", 1))},
        ))
    return out


def normalize_mycelium_findings(rows: List[Dict[str, Any]]) -> List[Signal]:
    """Mycelium findings -> Signals. opportunity adds its confidence;
    anomaly is direction-aware negative; wallet_correlation marks whale
    clusters (meta.cluster_wallets) for the correlated-accumulation bonus."""
    out = []
    for r in rows:
        miner = str(r.get("miner") or "")
        payload = r.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                payload = {}
        addr = payload.get("token") or payload.get("token_addr") or ""
        if miner == "wallet_correlation":
            # cluster findings carry shared token lists, one signal per token
            for tok in payload.get("shared") or []:
                out.append(Signal(
                    token_addr=tok, symbol="?", direction=1, strength=float(r.get("confidence") or 0.5),
                    source="mycelium_wallet_correlation", source_ts=_ts_of(r.get("created_ts")),
                    meta={"finding_id": r.get("id"),
                          "cluster_wallets": [payload.get("wallet_a"), payload.get("wallet_b")]},
                ))
            continue
        if not addr:
            continue
        if miner == "opportunity":
            src, direction = "mycelium_opportunity", 1
        elif miner in ("anomaly", "wallet_anomaly"):
            src, direction = "mycelium_anomaly", -1
        else:
            continue
        out.append(Signal(
            token_addr=addr, symbol=str(payload.get("symbol") or "?")[:16],
            direction=direction, strength=max(0.0, min(1.0, float(r.get("confidence") or 0.5))),
            source=src, source_ts=_ts_of(r.get("created_ts")),
            meta={"finding_id": r.get("id"), "miner": miner},
        ))
    return out


def normalize_market_momentum(rows: List[Dict[str, Any]]) -> List[Signal]:
    """Market snapshots (GMGN/Birdeye) -> momentum context signals AND the
    per-token market snapshot the gates need. The full raw snapshot rides in
    meta so gates.py never needs a second fetch."""
    out = []
    for r in rows:
        addr = r.get("token") or r.get("token_addr") or r.get("address") or ""
        if not addr:
            continue
        vol_trend = float(r.get("volume_trend") or 0)  # signed: rising/falling 24h volume
        out.append(Signal(
            token_addr=addr,
            symbol=str(r.get("symbol") or "?")[:16],
            direction=1 if vol_trend > 0 else (-1 if vol_trend < 0 else 0),
            strength=min(1.0, abs(vol_trend)),
            source="market_momentum",
            source_ts=_ts_of(r.get("ts")),
            meta={"snapshot": r},
        ))
    return out


# ---------------------------------------------------------------- fetchers


def _http_json(url: str, key: str = "", timeout: int = 10) -> Any:
    req = urllib.request.Request(url)
    if key:
        req.add_header("X-Agent-Key", key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _read_key(path: str) -> str:
    try:
        return open(os.path.expanduser(path)).read().strip()
    except OSError:
        return ""


def fetch_all(cfg: Dict[str, Any], market_provider=None) -> List[Signal]:
    """Pull every live source, normalize, concatenate. Any source that's
    down contributes [] and a warning — never an exception."""
    ep = cfg.get("endpoints", {})
    base = ep.get("vantage_base", "")
    key = _read_key(ep.get("vantage_key_file", ""))
    signals: List[Signal] = []

    def guard(name, fn):
        try:
            got = fn()
            log.info("source %s: %d signals", name, len(got))
            signals.extend(got)
        except Exception as exc:  # noqa: BLE001 — degrade per-source, never die
            log.warning("source %s unavailable: %s", name, exc)

    guard("vantage_signal", lambda: normalize_vantage_signals(
        _http_json(f"{base}/api/signals?limit=500", key) or []))
    guard("council_verdict", lambda: normalize_council_verdicts(
        _http_json(f"{base}/api/council/verdicts?limit=50", key) or []))
    guard("wallet_activity", lambda: normalize_wallet_trades(
        _fetch_wallet_trades(ep.get("wallet_registry_db", ""))))
    guard("wallet_role", lambda: normalize_wallet_roles(
        _fetch_wallet_roles(ep.get("wallet_registry_db", ""))))
    guard("mycelium_findings", lambda: normalize_mycelium_findings(
        (_http_json(f"{ep.get('mycelium_gateway', '')}/api/findings?limit=200") or {}).get("findings", [])))
    if market_provider is not None:
        guard("market_momentum", lambda: normalize_market_momentum(
            market_provider([s.token_addr for s in signals])))
    return signals


def _fetch_wallet_trades(db_path: str, window_hours: int = 48) -> List[Dict[str, Any]]:
    """Recent trades straight from the wallet-intel registry DB (read-only).
    Column names follow collector.py's trades table; a missing DB or a
    different schema surfaces as the caller's per-source warning."""
    if not db_path or not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    since = time.time() - window_hours * 3600
    try:
        rows = conn.execute(
            "SELECT wallet, token, symbol, action, amount_usd, tags, ts FROM trades WHERE ts >= ?",
            (since,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("tags"), str):
            try:
                d["tags"] = json.loads(d["tags"])
            except (ValueError, TypeError):
                d["tags"] = [t for t in d["tags"].split(",") if t]
        out.append(d)
    return out


def _fetch_wallet_roles(db_path: str, window_hours: int = 168) -> List[Dict[str, Any]]:
    if not db_path or not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    since = time.time() - window_hours * 3600
    try:
        rows = conn.execute(
            "SELECT wallet, token, roles, last_seen AS ts FROM token_wallet_roles WHERE last_seen >= ?",
            (since,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("roles"), str):
            try:
                d["roles"] = json.loads(d["roles"])
            except (ValueError, TypeError):
                d["roles"] = [x for x in d["roles"].split(",") if x]
        out.append(d)
    return out


def market_snapshots(signals: List[Signal]) -> Dict[str, Dict[str, Any]]:
    """token_addr -> the raw market snapshot carried by its market_momentum
    signal (gates.py reads liquidity/holders/tax from here)."""
    snaps: Dict[str, Dict[str, Any]] = {}
    for s in signals:
        if s.source == "market_momentum" and isinstance(s.meta.get("snapshot"), dict):
            snaps[s.token_addr] = s.meta["snapshot"]
    return snaps
