"""mycelium-collector CLI.

Usage:
  mycelium-collector --once        single collection cycle
  mycelium-collector --daemon N    loop every N seconds (default 300)
  mycelium-collector --status      registry snapshot
"""
import argparse
import json
import time

from . import config
from .collector import run_cycle, status
from .log import log


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mycelium-collector", description=__doc__)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="run a single collection cycle")
    group.add_argument("--daemon", type=int, nargs="?", const=300, metavar="SECONDS",
                        help="loop every SECONDS (default 300)")
    group.add_argument("--status", action="store_true", help="print registry snapshot")
    args = ap.parse_args(argv)

    if args.status:
        print(json.dumps(status(), indent=2, default=str))
        return 0

    if args.daemon is not None:
        log(f"[daemon] starting interval={args.daemon}s db={config.DB_PATH} substrate={config.MYCELIUM_URL}")
        while True:
            try:
                run_cycle()
            except Exception as e:
                log(f"[daemon] cycle error: {e}")
            time.sleep(args.daemon)
        return 0  # unreachable, kept for clarity

    # default: --once
    print(json.dumps(run_cycle(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
