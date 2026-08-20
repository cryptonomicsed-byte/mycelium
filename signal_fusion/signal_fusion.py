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
    """Top-N picks -> the Vantage signal pool as source='signal_fusion',
    strength=score/100, meta.source_pick_id for outcome attribution.
    Append-only rows; the council consumes them via its normal pool read."""
    ep = cfg.get("endpoints", {})
    base = ep.get("vantage_base", "")
    key = sources._read_key(ep.get("vantage_key_file", ""))
    if not base:
        return
    for p in picks[: cfg.get("mirror_top_n", 3)]:
        body = json.dumps({
            "token_addr": p["token_addr"], "symbol": p["symbol"],
            "direction": "buy", "strength": p["score"] / 100.0,
            "source": "signal_fusion",
            "meta": {"source_pick_id": p["pick_id"], "rank": p["rank"]},
        }).encode()
        try:
            req = urllib.request.Request(f"{base}/api/signals", data=body,
                                         headers={"Content-Type": "application/json"})
            if key:
                req.add_header("X-Agent-Key", key)
            urllib.request.urlopen(req, timeout=10).read()
            log.info("mirrored pick %s (%s, %.1f) into vantage pool", p["rank"], p["symbol"], p["score"])
        except Exception as exc:  # noqa: BLE001
            log.warning("vantage mirror failed for %s: %s", p["symbol"], exc)


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
    wallet_reputation = sources.fetch_wallet_reputation(cfg)
    wallet_clusters = sources.fetch_wallet_clusters(cfg)

    by_token: Dict[str, List[sources.Signal]] = {}
    for s in signals:
        by_token.setdefault(s.token_addr, []).append(s)

    sab = sabbath_active(cfg)
    scored: List[Dict[str, Any]] = []
    veto_count = 0
    for addr, token_signals in by_token.items():
        symbol = next((s.symbol for s in token_signals if s.symbol and s.symbol != "?"), "?")
        snap = snapshots.get(addr, {})
        ring_share, ring_members = scoring.wallet_cluster_share(token_signals, wallet_clusters)
        passed, vetoes = gates_mod.evaluate_gates(
            addr, snap, cfg, recent_pick_ts=store.last_pick_ts(addr),
            sabbath_active=sab, now=now,
            bundler_ring_share=ring_share, bundler_ring_members=ring_members)
        if not passed:
            veto_count += 1
            store.record_veto(addr, symbol, vetoes, ts=now)
            log.info("VETO %s (%s): %s", symbol, addr[:10],
                     "; ".join(f"{v['gate']}: {v['reason']}" for v in vetoes))
            continue
        result = scoring.composite_score(token_signals, snap, cfg, now=now,
                                         wallet_reputation=wallet_reputation,
                                         wallet_clusters=wallet_clusters)
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
    """Wire gmgn_pool (wallet/gmgn_pool.py, rotating keys + proxies) as the
    market-data source when it's importable — on the VPS it is; anywhere
    else fusion runs marketless and the gates fail closed."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "wallet"))
        import gmgn_pool  # type: ignore

        def provider(token_addrs: List[str]) -> List[Dict[str, Any]]:
            out = []
            for addr in token_addrs:
                try:
                    out.append(gmgn_pool.token_snapshot(addr))
                except Exception as exc:  # noqa: BLE001
                    log.debug("snapshot failed for %s: %s", addr[:10], exc)
            return out

        return provider
    except ImportError:
        log.warning("gmgn_pool not importable — running without market data (gates fail closed)")
        return None


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
