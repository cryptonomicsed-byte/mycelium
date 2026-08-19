#!/usr/bin/env python3
"""Backtest: replay the scoring over past windows IF history exists.

    python3 -m signal_fusion.backtest --windows 12 --step-hours 24

Checks the configured source DBs/APIs for historical rows; when the
wallet-registry trades table (or a Vantage signal export) retains enough
past data, replays run_once() at successive past timestamps with the clock
pinned (`now=`) and compares top-pick forward returns against random picks
from the same window.

If no usable history exists it says so and exits 0 — per the spec, the
forward PAPER outcome tracking (store.py marks) IS the backtest then.
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from typing import Any, Dict, List

from signal_fusion import sources
from signal_fusion.signal_fusion import load_config, run_once
from signal_fusion.store import PickStore

log = logging.getLogger("signal_fusion.backtest")


def historical_signals(cfg: Dict[str, Any], start: float, end: float) -> List[sources.Signal]:
    """Every source row whose timestamp falls inside [start, end) — the
    fetchers already return timestamped rows; this filters instead of
    windowing at the DB level so one code path serves all sources."""
    all_signals = sources.fetch_all(cfg)
    return [s for s in all_signals if start <= s.source_ts < end]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=int, default=12)
    parser.add_argument("--step-hours", type=float, default=24)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    cfg = load_config()
    now = time.time()
    step = args.step_hours * 3600
    windows = [(now - (i + 1) * step, now - i * step) for i in range(1, args.windows + 1)]

    total = 0
    results: List[Dict[str, Any]] = []
    store = PickStore(":memory:")  # backtest never touches the live picks DB
    for start, end in reversed(windows):
        sigs = historical_signals(cfg, start, end)
        total += len(sigs)
        if not sigs:
            continue
        r = run_once(cfg, store, signals=sigs, now=end)
        tokens = list({s.token_addr for s in sigs})
        results.append({
            "window_end": end,
            "top_pick": r["picks"][0]["symbol"] if r["picks"] else None,
            "top_score": r["picks"][0]["score"] if r["picks"] else None,
            "random_baseline": random.choice(tokens) if tokens else None,
            "candidates": len(tokens),
        })

    if total == 0:
        print(json.dumps({
            "status": "no_history",
            "note": "no historical rows in the configured sources — forward PAPER "
                    "outcome tracking (store.py marks) is the backtest",
        }, indent=2))
        return 0
    print(json.dumps({"status": "ok", "windows_with_data": len(results), "results": results,
                      "note": "compare top_pick forward returns vs random_baseline manually "
                              "once price history is attached"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
