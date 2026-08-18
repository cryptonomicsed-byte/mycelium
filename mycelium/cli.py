"""Mycelium CLI — agent- and human-invocable entry point.

Usage:
  mycelium init                          create substrate DB
  mycelium trace --agent A --session S --kind tool_call --action patch [opts]
  mycelium list [--agent A] [--limit N]
  mycelium mine [--miner NAME|all]
  mycelium findings [--state open] [--miner NAME]
  mycelium apply FINDING_ID
"""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    from . import __version__, core, miners
    from .apply import apply_finding
    from . import sandbox
    from . import publish as publish_mod
    from . import a2a as a2a_mod
except ImportError:  # launched as a script, not -m
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from mycelium import __version__, core, miners
    from mycelium.apply import apply_finding
    from mycelium import sandbox
    from mycelium import publish as publish_mod
    from mycelium import a2a as a2a_mod

AUTO_APPLY_MIN_CONFIDENCE = 0.9


def _p(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_trace(args) -> None:
    row = core.emit(
        agent=args.agent,
        session=args.session,
        kind=args.kind,
        action=args.action,
        target=args.target,
        outcome=args.outcome,
        duration_ms=args.duration_ms,
        payload=json.loads(args.payload) if args.payload else None,
    )
    _p({"status": "ok", "id": row["id"]})


def cmd_list(args) -> None:
    rows = core.iter_rows(core.query_traces(agent=args.agent, limit=args.limit))
    _p({"count": len(rows), "traces": rows})


def cmd_mine(args) -> None:
    if args.miner == "all":
        found = miners.run_all()
    else:
        found = miners.run_miner(args.miner)
    saved = []
    for f in found:
        row = core.add_finding(**f)
        saved.append(row["id"])
    _p({"miner": args.miner, "findings_saved": len(saved), "ids": saved})


def cmd_findings(args) -> None:
    rows = core.iter_rows(
        core.query_findings(miner=args.miner, state=args.state, limit=args.limit)
    )
    _p({"count": len(rows), "findings": rows})


def cmd_cycle(args) -> None:
    """One self-maintenance cycle: trace -> mine (sandboxed) -> auto-apply.
    Designed for cron: idempotent (dedupe), bounded (rlimits), silent on no-op."""
    import time
    session = f"cycle-{int(time.time())}"
    core.emit(agent="mycelium-cron", session=session, kind="tool_call",
              action="cycle", outcome="success", duration_ms=1,
              payload={"miner": "all", "sandboxed": True})
    found = sandbox.run_all_sandboxed(miners.MINERS)
    errors = [f for f in found if "error" in f]
    saved_ids, dupes = [], 0
    for f in found:
        if "error" in f or "findings" in f:
            continue
        res = core.add_finding(miner=f["miner"], confidence=f["confidence"],
                               title=f["title"], evidence=f["evidence"],
                               suggestion=f["suggestion"], payload=f.get("payload"))
        (dupes := dupes + 1) if res.get("duplicate") else saved_ids.append(res["id"])
    applied = []
    for fid in saved_ids:
        f = core.row_to_dict(core.get_finding(fid))
        if f["suggestion"] == "skill" and f["confidence"] >= AUTO_APPLY_MIN_CONFIDENCE:
            info = apply_finding(fid)
            if info and "error" not in info:
                applied.append({"id": fid, "skill": info["slug"]})
    _p({"status": "ok", "session": session, "miners_run": len(miners.MINERS),
        "sandbox_errors": len(errors), "new_findings": len(saved_ids),
        "duplicates_skipped": dupes, "auto_applied": applied})


def cmd_publish(args) -> None:
    """Checkpoint the anchor log; optionally push to Gitea (env creds)."""
    _p(publish_mod.publish())


def cmd_a2a(args) -> None:
    """Publish open findings to the Vantage feed (A2A distribution)."""
    _p(a2a_mod.publish_findings(limit=args.limit))


def cmd_alerts(args) -> None:
    """Evaluate generated alert configs against the substrate."""
    import glob
    import json as _json

    alerts_dir = publish_mod.CHECKPOINT_DIR.replace("checkpoints", "generated-alerts")
    alerts_dir = os.environ.get("MYCELIUM_ALERTS_DIR",
                                os.path.expanduser("~/mycelium/generated-alerts"))
    results = []
    for path in sorted(glob.glob(os.path.join(alerts_dir, "*.json"))):
        with open(path) as fh:
            cfg = _json.load(fh)
        cond = cfg.get("condition", {})
        action = cond.get("action")
        rows = core.query_traces(action=action, limit=100000)
        total = len(rows)
        fails = sum(1 for r in rows if r["outcome"] == "failure")
        rate = fails / total if total else 0.0
        tripped = total >= cond.get("min_failures", 3) and rate >= cond.get("min_rate", 0.5)
        results.append({
            "alert": os.path.basename(path), "action": action,
            "failures": fails, "total": total, "rate": round(rate, 3),
            "tripped": tripped, "state": cfg.get("state"),
        })
    _p({"alerts": results, "tripped": sum(1 for r in results if r["tripped"])})


def cmd_apply(args) -> None:
    result = apply_finding(args.finding_id)
    if result is None:
        _p({"status": "error", "message": f"no finding {args.finding_id}"})
        sys.exit(1)
    if "error" in result:
        _p({"status": "error", **result})
        sys.exit(1)
    _p({"status": "applied", **result})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mycelium", description=__doc__)
    p.add_argument("--version", action="version", version=f"mycelium {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create substrate DB")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("trace", help="emit a trace event")
    s.add_argument("--agent", required=True)
    s.add_argument("--session", required=True)
    s.add_argument("--kind", required=True, choices=sorted(core.VALID_KINDS))
    s.add_argument("--action")
    s.add_argument("--target")
    s.add_argument("--outcome", default="info", choices=sorted(core.VALID_OUTCOMES))
    s.add_argument("--duration-ms", type=int)
    s.add_argument("--payload", help="JSON object string")
    s.set_defaults(fn=cmd_trace)

    s = sub.add_parser("list", help="list traces")
    s.add_argument("--agent")
    s.add_argument("--limit", type=int, default=100)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("mine", help="run pattern miners")
    s.add_argument("--miner", default="all", choices=["all"] + sorted(miners.MINERS))
    s.set_defaults(fn=cmd_mine)

    s = sub.add_parser("findings", help="list findings")
    s.add_argument("--state", choices=["open", "applied", "dismissed"])
    s.add_argument("--miner", choices=sorted(miners.MINERS))
    s.add_argument("--limit", type=int, default=100)
    s.set_defaults(fn=cmd_findings)

    s = sub.add_parser("apply", help="apply a finding (auto-generate skill)")
    s.add_argument("finding_id")
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("cycle", help="trace -> sandboxed mine -> auto-apply (cron)")
    s.set_defaults(fn=cmd_cycle)

    s = sub.add_parser("publish", help="checkpoint anchor log (+ Gitea push if creds)")
    s.set_defaults(fn=cmd_publish)

    s = sub.add_parser("a2a-publish", help="publish open findings to Vantage feed")
    s.add_argument("--limit", type=int, default=3)
    s.set_defaults(fn=cmd_a2a)

    s = sub.add_parser("alerts", help="evaluate generated alert configs")
    s.set_defaults(fn=cmd_alerts)
    return p


def cmd_init(args) -> None:
    core.init_db()
    _p({"status": "ok", "db": core.DB_PATH, "schema_version": core.SCHEMA_VERSION})


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
