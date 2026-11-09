"""picks-digest -- fetch and print the picks feed from a configured
service. Per the orchestration plan this was already mostly universal on
Fold4 (GET :8003/api/picks with PICKS_URL env config); this is a clean
reimplementation of that same shape since the original cron script itself
wasn't reachable from the build environment (BUILT FROM SPEC, same caveat
as substrate-tunnel -- diff against the real phone script before treating
this as a drop-in replacement there).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_PICKS_URL = os.environ.get("PICKS_URL", "http://127.0.0.1:8003/api/picks")
DEFAULT_TIMEOUT = int(os.environ.get("PICKS_TIMEOUT", "15"))


def fetch_picks(url: str, timeout: int) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "picks-digest/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def format_digest(data) -> str:
    picks = data.get("picks", data) if isinstance(data, dict) else data
    if not isinstance(picks, list):
        return json.dumps(data, indent=2, default=str)
    if not picks:
        return "picks-digest: no picks right now"
    lines = [f"picks-digest: {len(picks)} pick(s)"]
    for p in picks:
        if not isinstance(p, dict):
            lines.append(f"  {p}")
            continue
        symbol = p.get("symbol") or p.get("token") or p.get("ticker") or "?"
        conviction = p.get("conviction") or p.get("confidence") or p.get("score")
        note = p.get("note") or p.get("reason") or p.get("summary") or ""
        line = f"  {symbol}"
        if conviction is not None:
            line += f"  conviction={conviction}"
        if note:
            line += f"  — {note}"
        lines.append(line)
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="picks-digest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_PICKS_URL,
                    help=f"picks service URL (env: PICKS_URL, default {DEFAULT_PICKS_URL})")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--json", action="store_true", help="print raw JSON instead of the formatted digest")
    args = ap.parse_args(argv)

    try:
        data = fetch_picks(args.url, args.timeout)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"picks-digest: failed to fetch {args.url}: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"picks-digest: unexpected error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(format_digest(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
