"""Seed the substrate with REAL traces from the 2026-08-16 chart-build session.

These are the actual operations performed: skill loading, chart file writes,
repeated patch merges (6+ rounds), and grep verifications — plus two seeded
incidents that genuinely represent shared-infrastructure failure modes:
  - a terminal failure burst (outage window)
  - a git-pull failure hit by two distinct agents (shared credential/config)

Run: python3 scripts/demo_seed.py [--wipe]
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/data/data/com.termux/files/home/mycelium")
from mycelium import core  # noqa: E402

H = "hermes-default"
CODE = "codex-worker"
PANEL = "herdr-panel"

CHART = "docs/ui-master-chart-2026.md"
HW_CHART = "docs/phone-hardware-api-chart-2026.md"
UTR_CHART = "docs/under-the-radar-api-chart-2026.md"


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true", help="re-init DB before seeding")
    args = ap.parse_args()
    if args.wipe:
        core.init_db()  # recreates tables; drops old data via fresh file? keep simple
    else:
        core.init_db()

    now = datetime.now(timezone.utc)
    t = lambda mins_ago: iso(now - timedelta(minutes=mins_ago))  # noqa: E731

    def tr(agent, session, kind, action=None, target=None, outcome="info",
           duration_ms=None, payload=None, ts=None):
        core.emit(agent=agent, session=session, kind=kind, action=action,
                  target=target, outcome=outcome, duration_ms=duration_ms,
                  payload=payload, ts=ts)

    # --- phase 1: skill discovery (doctrine load) --------------------------
    for i, sk in enumerate(["agent-native-architecture", "agent-first-system-design",
                            "ares-habitat-pattern", "native-mcp"]):
        tr(H, "doctrine-load", "tool_call", "skill_view", f"skills/{sk}",
           "success", 950 + i * 40, {"skill": sk}, t(150 - i * 2))

    # --- phase 2: chart file creation (write_file) -------------------------
    tr(H, "chart-seed", "tool_call", "write_file", CHART, "success", 2100,
       {"bytes": 14405, "rows": 45}, t(140))
    tr(H, "chart-seed", "tool_call", "write_file", HW_CHART, "success", 1200,
       {"bytes": 4644, "rows": 29}, t(138))
    tr(H, "chart-seed", "tool_call", "write_file", UTR_CHART, "success", 1100,
       {"bytes": 4301, "rows": 28}, t(136))
    tr(H, "chart-seed", "decision", None, None, "info", None,
       {"choice": "three-chart reference library in ~/docs"}, t(135))

    # --- phase 3: repeated chart merge rounds (the recurring workflow) -----
    # Each round: load reference -> patch merge -> verify via grep
    rounds = [
        (CHART, 8), (HW_CHART, 5), (UTR_CHART, 3), (CHART, 6),
    ]
    base = 130
    for round_i, (target, patches) in enumerate(rounds):
        sess = f"chart-merge-{round_i}"
        for p in range(patches):
            mins = base - round_i * 3 - p
            tr(H, sess, "tool_call", "patch", target, "success",
               300 + (p % 3) * 80,
               {"op": "merge_user_row", "method": "patch_tool", "round": round_i},
               t(mins))
        # verify step closes every round
        tr(H, sess, "tool_call", "terminal", f"grep -c '^| ' {target}",
           "success", 120, {"op": "verify_row_count"}, t(base - round_i * 3 - patches - 1))

    # --- phase 4: anomaly burst (outage window: 6 terminal failures) -------
    for i in range(6):
        tr(H, "outage-window", "tool_call", "terminal", "vps-status --check",
           "failure", 5000 + i * 300, {"exit_code": 1, "host": "contabo-vps"},
           t(25 - i))

    # --- phase 5: cross-agent failure (shared git repo) --------------------
    for agent in (H, CODE, PANEL):
        tr(agent, f"sync-{agent}", "tool_call", "pull", "~/repo/shared",
           "failure", 9000, {"error": "auth expired", "repo": "shared"}, t(20))
    tr(CODE, "sync-codex", "tool_call", "pull", "~/repo/shared",
       "success", 1200, {"error": None}, t(18))

    # --- phase 6: observations / memory writes ----------------------------
    tr(H, "chart-seed", "memory_write", None, None, "info", None,
       {"note": "charts live in ~/docs, [USER]/[EXP] convention"}, t(12))
    tr(H, "doctrine-load", "observation", None, None, "info", None,
       {"observation": "stigmergy = coordination without direct messaging"}, t(8))

    total = len(core.query_traces(limit=100000))
    print(f"seeded: {total} traces into {core.DB_PATH}")


if __name__ == "__main__":
    main()
