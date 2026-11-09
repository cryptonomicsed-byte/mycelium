"""mycelium-cycle -- was a Fold4-only cron script hardcoded to
/data/data/com.termux/files/home/mycelium/mycelium/cli.py. Now a proper
console script installed by pip, runnable from any systemd timer or cron
on any device that has mycelium-substrate installed.

Chains one full self-maintenance cycle:
  1. mine (sandboxed) + auto-apply high-confidence skill findings
  2. publish -- checkpoint the anchor log, push to Gitea if configured
  3. a2a -- publish open findings to the Vantage feed

Each step is independent and failure-isolated: a failure in step 2 or 3
does not undo step 1's results, and is reported in the summary rather than
raising, so a single flaky network call doesn't turn a mostly-successful
cycle into a hard cron failure. Exit code is non-zero only if step 1
(mine+apply, the core substrate-maintenance work) itself raises.

Watchdog semantics for cron/systemd: silent (prints nothing) unless there
is something to report (new findings, applied skills, sandbox errors, or
a step failure) -- same contract the original Fold4 cron script used.
"""
import argparse
import json
import sys

from mycelium_substrate import a2a as a2a_mod
from mycelium_substrate import publish as publish_mod
from mycelium_substrate.cli import cmd_cycle as _run_mine_and_apply


class _Args:
    """cmd_cycle in mycelium_substrate.cli takes an argparse.Namespace but
    doesn't read any attributes off it -- an empty stand-in object works."""


def run_full_cycle(a2a_limit: int = 3) -> dict:
    import io
    from contextlib import redirect_stdout

    summary = {"mine_and_apply": None, "publish": None, "a2a": None, "errors": []}

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            _run_mine_and_apply(_Args())
        summary["mine_and_apply"] = json.loads(buf.getvalue())
    except Exception as e:
        summary["errors"].append(f"mine_and_apply: {e}")
        raise  # the core step -- a real failure here should fail the cycle

    try:
        summary["publish"] = publish_mod.publish()
    except Exception as e:
        summary["errors"].append(f"publish: {e}")

    try:
        summary["a2a"] = a2a_mod.publish_findings(limit=a2a_limit)
    except Exception as e:
        summary["errors"].append(f"a2a: {e}")

    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mycelium-cycle", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a2a-limit", type=int, default=3, help="max findings to publish to Vantage per cycle")
    ap.add_argument("--quiet-if-noop", action="store_true", default=True,
                    help="print nothing if there's nothing to report (default; cron-friendly)")
    ap.add_argument("--verbose", action="store_true", help="always print the full summary")
    args = ap.parse_args(argv)

    try:
        summary = run_full_cycle(a2a_limit=args.a2a_limit)
    except Exception as e:
        print(f"mycelium-cycle FAILED: {e}", file=sys.stderr)
        return 1

    ma = summary.get("mine_and_apply") or {}
    noteworthy = (
        args.verbose
        or ma.get("new_findings", 0) > 0
        or ma.get("auto_applied")
        or ma.get("sandbox_errors", 0) > 0
        or summary.get("errors")
    )
    if noteworthy:
        print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
