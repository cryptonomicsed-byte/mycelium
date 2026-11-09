"""P2-03 — the resolver: mark every mirrored signal_record at +1h/+6h/+24h.

Vantage mirrors every ingested signal_record into the Mycelium substrate as a
trace (agent `vantage_ledger`, action `signal_record`, P1-08). This module
closes the loop: once a record's entry has aged past 1h/6h/24h, it fetches the
current price, computes the signed return against the entry, and emits a
`signal_outcome` trace (agent `vantage_ledger`, action `signal_outcome`). P2-04
then mines source hit-rates from those outcomes, in shadow.

Scoring rules (from the handoff):
- long/short are scored: `signed_ret_pct` is the return in the position's
  favour (+ for a profitable move, - for a loss); `hit` = signed > threshold.
- hold/exit/alert are logged but not scored (hit is null).
- a mark more than 15 minutes past its window's due time is `late`.
- a record with no entry price (or no current price) is logged with null
  return/hit rather than an invented number.

Idempotent: before emitting, the resolver reads the signal_outcome traces that
already exist and skips any (record, window) pair it has already marked, so a
crash-and-restart between fetch and emit never doubles an outcome.

Enabled by MYCELIUM_RESOLVER_ENABLED (default off, read each cycle).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import core


# The ledger stores what producers sent (BUY / SELL / buy / NEUTRAL ...);
# same table as Vantage's signal_record.LEGACY_DIRECTION.
_LEGACY_DIRECTION = {"buy": "long", "bullish": "long", "sell": "short", "bearish": "short",
                     "flat": "hold", "neutral": "hold", "wait": "hold"}


def canonical_direction(d) -> str:
    d = str(d or "").strip().lower()
    return _LEGACY_DIRECTION.get(d, d)

log = logging.getLogger("mycelium.resolver")

RESOLVER_AGENT = "vantage_ledger"
MIRROR_ACTION = "signal_record"
OUTCOME_ACTION = "signal_outcome"

# window seconds -> label (sorted so 1h marks before 6h before 24h)
WINDOWS: List[Tuple[int, str]] = [(3600, "1h"), (6 * 3600, "6h"), (24 * 3600, "24h")]

HIT_THRESHOLD_PCT = 1.0
LATE_S = 15 * 60
VANTAGE_BASE = os.environ.get("VANTAGE_BASE", "http://127.0.0.1:8001")


def enabled() -> bool:
    return os.environ.get("MYCELIUM_RESOLVER_ENABLED", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _get(url: str, timeout: float = 8.0) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mycelium-resolver/1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        log.debug("resolver fetch failed for %s: %s", url, e)
        return None


def fetch_price(chain: str, address: str, symbol: str) -> Tuple[Optional[float], str]:
    """Current price for a record. Solana by mint (DexScreener); majors and
    everything else by symbol through Vantage's public price endpoint."""
    if (chain or "").lower() == "solana" and address:
        data = _get(f"https://api.dexscreener.com/latest/dex/tokens/{address}")
        pairs = (data or {}).get("pairs") or []
        if pairs:
            best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
            px = best.get("priceUsd")
            if px:
                return float(px), "dexscreener"
    if symbol:
        data = _get(f"{VANTAGE_BASE}/api/trading/markets/{symbol}/price")
        px = (data or {}).get("price")
        if px:
            return float(px), "vantage"
    return None, "none"


def _parse(trace: Dict[str, Any]) -> Optional[dict]:
    p = trace.get("payload")
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except (TypeError, ValueError):
            return None
    return p if isinstance(p, dict) else None


def _marked_windows() -> set:
    """(record_id, window) pairs already emitted, so a re-run never doubles."""
    marked: set = set()
    for t in core.iter_rows(core.query_traces(agent=RESOLVER_AGENT, action=OUTCOME_ACTION, limit=100000)):
        p = _parse(t)
        if t.get("target") and isinstance(p, dict) and p.get("window"):
            marked.add((t["target"], p["window"]))
    return marked


def resolve_due(now: Optional[float] = None,
                fetch: Callable[[str, str, str], Tuple[Optional[float], str]] = fetch_price) -> Dict[str, Any]:
    """One marking pass. Returns {emitted, records, marked}. Never raises."""
    try:
        return _resolve_due(now=now, fetch=fetch)
    except Exception as e:  # noqa: BLE001
        log.warning("resolver pass failed (fail-open): %s", e)
        return {"emitted": 0, "records": -1, "marked": -1, "error": str(e)}


def _resolve_due(now: Optional[float] = None,
                 fetch: Callable[[str, str, str], Tuple[Optional[float], str]] = fetch_price) -> Dict[str, int]:
    now = float(now or time.time())
    records = core.iter_rows(core.query_traces(agent=RESOLVER_AGENT, action=MIRROR_ACTION, limit=100000))
    marked = _marked_windows()

    emitted = 0
    for t in records:
        p = _parse(t)
        if not p:
            continue
        rid = p.get("id") or t.get("target")
        ingested = p.get("ingested_at")
        if not rid or not isinstance(ingested, (int, float)) or ingested <= 0:
            continue
        direction = canonical_direction(p.get("direction"))
        scored = direction in ("long", "short")
        entry = p.get("entry_price_usd")
        chain = p.get("chain") or ""
        address = p.get("address") or ""
        symbol = p.get("symbol") or ""

        for secs, label in WINDOWS:
            due = ingested + secs
            if now < due or (rid, label) in marked:
                continue
            late = (now - due) > LATE_S
            price, src = fetch(chain, address, symbol)

            ret = signed = hit = None
            if price and isinstance(entry, (int, float)) and entry > 0:
                ret = (price - entry) / entry * 100.0
                signed = ret if direction == "long" else -ret
                if scored:
                    hit = bool(signed > HIT_THRESHOLD_PCT)

            core.emit(
                agent=RESOLVER_AGENT, session="resolver", kind="observation",
                action=OUTCOME_ACTION, target=rid, outcome="info",
                payload={
                    "window": label, "price": price, "price_src": src,
                    "ret_pct": round(ret, 4) if ret is not None else None,
                    "signed_ret_pct": round(signed, 4) if signed is not None else None,
                    "hit": hit, "scored": scored, "late": late,
                    "marked_at": int(now), "threshold_pct": HIT_THRESHOLD_PCT,
                    "direction": direction, "symbol": symbol, "chain": chain,
                    "entry_price_usd": entry,
                },
            )
            emitted += 1
            marked.add((rid, label))

    return {"emitted": emitted, "records": len(records), "marked": len(marked)}


def loop(interval_s: int = 60) -> None:
    """Daemon loop for the ares-mycelium-resolver service. The flag is read
    each cycle, so toggling it needs no restart; off means resolve_due is
    never entered."""
    while True:
        time.sleep(interval_s)
        if enabled():
            try:
                out = resolve_due()
                if out["emitted"]:
                    log.info("resolver marked %d records", out["emitted"])
            except Exception as e:  # noqa: BLE001
                log.warning("resolver cycle failed: %s", e)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Mycelium signal-ledger resolver (P2-03)")
    ap.add_argument("--once", action="store_true", help="one marking pass, then exit")
    ap.add_argument("--loop", action="store_true", help="run every 60 s until stopped")
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args(argv)
    # Ensure the shared traces table exists (the resolver may start before the
    # gateway has created it, e.g. on a fresh box). Idempotent.
    core.init_db()
    if args.loop:
        loop(args.interval)
    else:
        print(json.dumps(resolve_due()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
