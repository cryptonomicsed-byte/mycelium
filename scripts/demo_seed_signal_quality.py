"""Seed the substrate with real signal_post traces demonstrating the
signal-quality domain (mycelium/miners/signal_quality.py) -- a rich,
checkable trading call; a bare low-effort one; and duplicate-content spam
across three agents. Mine after seeding to see the real scoring/dedupe
findings:

    python3 scripts/demo_seed_signal_quality.py
    python3 -m mycelium.cli mine --miner signal_quality
    python3 -m mycelium.cli findings

Run: python3 scripts/demo_seed_signal_quality.py [--wipe]
"""
from __future__ import annotations

import argparse
import os
import sys

# Termux deployment path first (matches scripts/demo_seed.py's existing
# convention), falling back to the real repo root computed from this file's
# location -- so this actually runs on a non-Termux dev machine too, not
# just the deploy target (confirmed live: demo_seed.py's Termux-only path
# doesn't resolve here either, a pre-existing limitation this script
# doesn't repeat).
sys.path.insert(0, "/data/data/com.termux/files/home/mycelium")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mycelium import core  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true", help="re-init DB before seeding")
    args = ap.parse_args()
    if args.wipe:
        core.init_db()

    core.emit(
        "signal-quality-demo-agent", "demo-session-1", "decision", action="signal_post",
        payload={
            "title": "BTC breakout setup",
            "content": (
                "BTC breaking out above resistance, target 75000, confidence 80%, "
                "because volume is surging and on-chain data confirms accumulation"
            ),
            "symbol": "BTC",
            "tags": ["ta", "breakout"],
        },
    )
    core.emit(
        "signal-quality-demo-agent-2", "demo-session-2", "observation", action="signal_post",
        payload={"content": "buy now trust me"},
    )
    for i in range(3):
        core.emit(
            f"signal-quality-demo-spam-{i}", f"demo-spam-session-{i}", "decision", action="signal_post",
            payload={"content": "pump incoming"},
        )

    print("Seeded 5 signal_post traces (1 rich, 1 bare, 3 duplicate spam).")
    print("Run: python3 -m mycelium.cli mine --miner signal_quality")


if __name__ == "__main__":
    main()
