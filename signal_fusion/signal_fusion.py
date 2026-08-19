#!/usr/bin/env python3
"""ares-signal-fusion — fuse every intelligence source into one ranked,
transparent list of the best tokens to trade.

    python3 -m signal_fusion.signal_fusion --once      # single run
    python3 -m signal_fusion.signal_fusion --daemon    # 15-min loop (systemd)

Per run: fetch + normalize all sources (sources.py) -> group by token ->
hard gates (gates.py, every rejection logged with its gate name) -> score
survivors (scoring.py) -> top-N into ares_picks.db (store.py) -> top-3
mirrored into the Vantage signal pool as source='signal_fusion' (append-
only; the council picks them up in its normal debate, zero council code
changes) -> mycelium traces bracketing the run + a finding when a pick
crosses the score threshold -> outcome marks recorded for older picks.

PAPER ONLY: this engine never executes anything. Its output is candidates
for the council. Non-negotiable.

SIGHUP hot-reloads config.json (weights/thresholds/half-lives) without a
restart.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from signal_fusion import gates as gates_mod  # noqa: E402
from signal_fusion import scoring, sources  # noqa: E402
from signal_fusion.store import PickStore  # noqa: E402

log = logging.getLogger("signal_fusion")
CONFIG_PATH = os.environ.get(
    "SIGNAL_FUSION_CONFIG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))

_config: Dict[str, Any] = {}


def load_config() -> Dict[str, Any]:
    global _config
    with open(CONFIG_PATH) as fh:
        _config = json.load(fh)
    log.info("config loaded from %s", CONFIG_PATH)
    return _config


def _on_sighup(signum, frame):  # noqa: ARG001
    log.info("SIGHUP: hot-reloading config")
    try:
        load_config()
    except (OSError, ValueError) as exc:
        log.error("config reload failed, keeping previous: %s", exc)


# ------------------------------------------------------- mycelium tracing


def emit_trace(cfg: Dict[str, Any], kind: str, action: str, target: str = "substrate",
               outcome: str = "success", payload: Optional[Dict[str, Any]] = None):
    """POST a trace to the mycelium gateway. Fire-and-forget: tracing must
    never break a fusion run."""
    gw = cfg.get("endpoints", {}).get("mycelium_gateway", "")
    if not gw:
        return
    body = json.dumps({
        "agent": "signal_fusion", "session": "fusion",
        "kind": kind, "action": action, "target": target, "outcome": outcome,
        "payload": payload or {},
    }).encode()
    try:
        req = urllib.request.Request(f"{gw}/api/trace", data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as exc:  # noqa: BLE001
        log.debug("trace emit failed (non-fatal): %s", exc)


def mirror_to_vantage(cfg: Dict[str, Any], picks: List[Dict[str, Any]]):
    """Top-N picks -> the Vantage intel signal pool as source='signal_fusion',
    strength=score/100, meta.source_pick_id for outcome attribution. The
    council consumes the pool via its normal read, so no council changes.
    Two paths: HTTP POST /api/intel/signals/ingest with the system intel
    tool key (local-only .vantage_tool_keys.json, never in git), or a
    direct append-only INSERT into the signal_pool table (same durable
    write the ingest does internally)."""
    ep = cfg.get("endpoints", {})
    base = ep.get("vantage_base", "")
    if not base:
        return
    tool_key = ""
    try:
        key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".vantage_tool_keys.json")
        with open(key_file) as f:
            tool_key = json.load(f).get("intel", "")
    except OSError:
        pass
    for p in picks[: cfg.get("mirror_top_n", 3)]:
        payload = {
            "symbol": p["symbol"], "source": "signal_fusion", "type": "trending",
            "conviction": max(0.0, min(1.0, p["score"] / 100.0)),
            "direction": "buy",
            "detail": f"fusion pick rank {p['rank']} (pick {p.get('pick_id')})",
            "mint": p["token_addr"], "ts": int(time.time()),
        }
        mirrored = False
        if tool_key:
            try:
                req = urllib.request.Request(
                    f"{base}/api/intel/signals/ingest",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json",
                             "X-Vantage-Tool": "intel",
                             "X-Vantage-Tool-Key": tool_key})
                urllib.request.urlopen(req, timeout=10).read()
                mirrored = True
            except Exception as exc:  # noqa: BLE001
                log.warning("intel ingest mirror failed for %s: %s", p["symbol"], exc)
        if not mirrored:
            try:
                db_path = ep.get("vantage_db", "")
                if db_path and os.path.exists(db_path):
                    conn = sqlite3.connect(db_path, timeout=30)
                    try:
                        conn.execute(
                            "INSERT INTO signal_pool (symbol, source, type, conviction, direction, detail, mint, ts) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (payload["symbol"], payload["source"], payload["type"],
                             payload["conviction"], payload["direction"],
                             payload["detail"], payload["mint"], payload["ts"]))
                        conn.commit()
                        mirrored = True
                    finally:
                        conn.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("direct signal_pool mirror failed for %s: %s", p["symbol"], exc)
        if mirrored:
            log.info("mirrored pick %s (%s, %.1f) into vantage pool", p["rank"], p["symbol"], p["score"])


def sabbath_active(cfg: Dict[str, Any]) -> bool:
    """Reads the Sabbath window from the Vantage config endpoint when
    configured; unreadable/unset -> not active (the council enforces its own
    Sabbath gate regardless, so this is a courtesy skip, not the last line)."""
    url = cfg.get("endpoints", {}).get("sabbath_config", "")
    if not url:
        return False
    try:
        data = json.loads(urllib.request.urlopen(url, timeout=5).read().decode())
        return bool(data.get("sabbath_active"))
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------- the run


def run_once(cfg: Dict[str, Any], store: PickStore, market_provider=None,
             signals: Optional[List[sources.Signal]] = None,
             now: Optional[float] = None) -> Dict[str, Any]:
    """One fusion pass. `signals`/`now` injectable for tests/backtests —
    production callers pass neither and get live fetch + wall clock."""
    now = now if now is not None else time.time()
    emit_trace(cfg, "workflow_start", "fusion_run", payload={"cadence_min": cfg.get("cadence_minutes")})
    t0 = time.time()

    if signals is None:
        signals = sources.fetch_all(cfg, market_provider=market_provider)
    snapshots = sources.market_snapshots(signals)

    by_token: Dict[str, List[sources.Signal]] = {}
    for s in signals:
        by_token.setdefault(s.token_addr, []).append(s)

    sab = sabbath_active(cfg)
    scored: List[Dict[str, Any]] = []
    veto_count = 0
    for addr, token_signals in by_token.items():
        symbol = next((s.symbol for s in token_signals if s.symbol and s.symbol != "?"), "?")
        snap = snapshots.get(addr, {})
        passed, vetoes = gates_mod.evaluate_gates(
            addr, snap, cfg, recent_pick_ts=store.last_pick_ts(addr),
            sabbath_active=sab, now=now)
        if not passed:
            veto_count += 1
            store.record_veto(addr, symbol, vetoes, ts=now)
            log.info("VETO %s (%s): %s", symbol, addr[:10],
                     "; ".join(f"{v['gate']}: {v['reason']}" for v in vetoes))
            continue
        result = scoring.composite_score(token_signals, snap, cfg, now=now)
        scored.append({
            "token_addr": addr, "symbol": symbol, "score": result["score"],
            "components": result["components"], "dominant": result["dominant"],
            "entry_price": (snap or {}).get("price_usd"),
        })

    scored.sort(key=lambda x: -x["score"])
    top = scored[: cfg.get("top_n_picks", 10)]
    for rank, p in enumerate(top, 1):
        p["rank"] = rank
        p["pick_id"] = store.record_pick(
            p["token_addr"], p["symbol"], p["score"], rank,
            p["components"], {"passed": True, "vetoes": []},
            p["entry_price"], ts=now)
        emit_trace(cfg, "observation", "fusion_pick", target=p["symbol"],
                   payload={"rank": rank, "score": p["score"], "dominant": p["dominant"],
                            "pick_id": p["pick_id"]})
        if p["score"] >= cfg.get("finding_score_threshold", 80):
            emit_trace(cfg, "observation", "fusion_high_score", target=p["symbol"], payload={
                "miner": "opportunity", "confidence": min(0.97, p["score"] / 100.0),
                "title": f"Signal fusion: {p['symbol']} scored {p['score']:.1f}",
                "evidence": f"dominant driver {p['dominant']}; see pick {p['pick_id']} components",
                "suggestion": "alert",
            })

    mirror_to_vantage(cfg, top)
    marks_recorded = record_due_marks(cfg, store, market_provider, now=now)

    duration = time.time() - t0
    emit_trace(cfg, "workflow_end", "fusion_run", outcome="success", payload={
        "picks": len(top), "vetoes": veto_count, "tokens_seen": len(by_token),
        "marks_recorded": marks_recorded, "duration_s": round(duration, 2)})
    log.info("run done: %d tokens, %d picks, %d vetoes, %d marks, %.1fs",
             len(by_token), len(top), veto_count, marks_recorded, duration)
    return {"picks": top, "vetoes": veto_count, "tokens_seen": len(by_token)}


def record_due_marks(cfg: Dict[str, Any], store: PickStore, market_provider=None,
                     now: Optional[float] = None) -> int:
    """+4h/+24h/+7d outcome marks for older picks — the real-data outcome
    tracking that IS the backtest in PAPER mode."""
    due = store.due_marks(now=now)
    if not due:
        return 0
    prices: Dict[str, Optional[float]] = {}
    if market_provider is not None:
        try:
            for snap in market_provider(sorted({d["token_addr"] for d in due})):
                addr = snap.get("token") or snap.get("token_addr") or ""
                if addr:
                    prices[addr] = snap.get("price_usd")
        except Exception as exc:  # noqa: BLE001
            log.warning("mark pricing unavailable: %s", exc)
    n = 0
    for d in due:
        store.record_mark(d["pick_id"], d["mark"], prices.get(d["token_addr"]),
                          d["entry_price"], ts=now)
        n += 1
    return n


def default_market_provider(cfg: Dict[str, Any]):
    """Market snapshots for the gates. PRIMARY: the Vantage signal pool
    (sources.vantage_market_provider — radar rows carry liquidity /
    volume_24h / price / age_hours, free of rate limits). ENRICHMENT:
    gmgn_pool.token_snapshot() (top10_share, bundler_rat_share) via the
    wallet_intel dir. If both fail the gates fail closed (no picks)."""

    def provider(token_addrs: List[str]) -> List[Dict[str, Any]]:
        out = sources.vantage_market_provider(token_addrs)
        by_addr = {s.get("token") or s.get("address"): s for s in out}
        # Fill tokens the Vantage pool doesn't cover with DexScreener's
        # public token API (no key, no Cloudflare wall). Pool snapshots win;
        # dex fills gaps.
        dex_cap = int(cfg.get("dex_enrichment_max", 30))
        missing = [a for a in token_addrs if a not in by_addr][:dex_cap]
        for snap in sources.dexscreen_market_provider(missing):
            key = snap.get("token")
            if key is None:
                continue
            existing = by_addr.get(key)
            if existing is None:
                by_addr[key] = snap
                out.append(snap)
            else:
                for k, v in snap.items():
                    if k not in existing:
                        existing[k] = v
        try:
            for p in (
                os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "wallet"),
                "/opt/ares/wallet_intel",
            ):
                if p not in sys.path:
                    sys.path.insert(0, p)
            import gmgn_pool  # type: ignore
            # GMGN calls are rate-limited: enrich pool-covered tokens (cheap,
            # gates need top10/bundler there) plus the strongest candidates up
            # to enrichment_max. Never the full candidate list.
            cap = int(cfg.get("enrichment_max", 20))
            pool_covered = set(by_addr)
            others = [a for a in token_addrs if a not in pool_covered]
            to_enrich = list(pool_covered) + others[:max(0, cap - len(pool_covered))]
            for addr in to_enrich:
                snap = by_addr.get(addr)
                if snap is None:
                    snap = {"token": addr}
                    by_addr[addr] = snap
                    out.append(snap)
                assert snap is not None
                try:
                    enr = gmgn_pool.token_snapshot(addr)
                    if enr:
                        for k, v in enr.items():
                            if k not in snap:
                                snap[k] = v
                except Exception as exc:  # noqa: BLE001
                    log.debug("enrich failed for %s: %s", addr[:10], exc)
        except ImportError:
            log.warning("gmgn_pool not importable — Vantage pool snapshots only")
        return out

    return provider


def main():
    parser = argparse.ArgumentParser(description="ares signal fusion engine")
    parser.add_argument("--once", action="store_true", help="single run, then exit")
    parser.add_argument("--daemon", action="store_true", help="run every cadence_minutes")
    parser.add_argument("--report", action="store_true", help="print the calibration report")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = load_config()
    signal.signal(signal.SIGHUP, _on_sighup)
    store = PickStore(os.path.expanduser(cfg["endpoints"]["picks_db"]))
    provider = default_market_provider(cfg)

    if args.report:
        print(json.dumps(store.calibration_report(), indent=2))
        return
    if args.daemon:
        log.info("daemon mode: every %d min", cfg.get("cadence_minutes", 15))
        while True:
            try:
                run_once(_config, store, market_provider=provider)
            except Exception:  # noqa: BLE001
                log.exception("run failed — retrying next cycle")
                emit_trace(_config, "error", "fusion_run", outcome="failure")
            time.sleep(_config.get("cadence_minutes", 15) * 60)
    else:
        result = run_once(cfg, store, market_provider=provider)
        print(json.dumps({"picks": [
            {k: p[k] for k in ("rank", "symbol", "score", "dominant", "pick_id")}
            for p in result["picks"]], "vetoes": result["vetoes"]}, indent=2))


if __name__ == "__main__":
    main()
