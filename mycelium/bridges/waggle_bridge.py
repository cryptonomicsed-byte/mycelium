"""Bridge between Waggle (~/Agentic — a real-time, decay-based scent field)
and Mycelium (this repo — a durable SQLite trace log + offline pattern
miners). They are two different, independent stigmergic substrates, not one
absorbed into the other:

  Waggle:    live agents sniff/claim/deposit signals *right now*; state lives
             in-memory + a journal, decays via half-life, no long-term mining.
  Mycelium:  every trace is durable and gets mined *after the fact* for
             recurring workflows, anomalies, cross-agent correlation, and
             opportunities that no single live signal reveals.

This bridge is a plain polling script (stdlib only, per both projects'
zero-dependency doctrine) — no changes to either core:

  sync_from_waggle():   pulls Waggle's high-signal kinds (gold, dead-end,
                         warn, handoff) via GET /v1/recall/window and inserts
                         each as a Mycelium trace (kind="waggle_signal") via
                         the Go gateway's POST /api/trace — one more real
                         trace source for the miners to run recurring_workflow/
                         anomaly/cross_agent/opportunity mining over.

  sync_from_mycelium():  pulls Mycelium's open findings via GET /api/findings
                         and deposits each as a Waggle signal (kind="gold")
                         via POST /v1/signals — so a live agent doing
                         sniff-before-act on a resource can discover an
                         offline-mined finding without querying Mycelium
                         directly.

Cursors (last-synced timestamp per direction) persist in a small JSON state
file so re-running only picks up what's new.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, Optional

DEFAULT_WAGGLE_URL = os.environ.get("WAGGLE_URL", "http://127.0.0.1:7777")
DEFAULT_MYCELIUM_GATEWAY = os.environ.get("MYCELIUM_GATEWAY_URL", "http://127.0.0.1:8811")
STATE_PATH = os.environ.get(
    "WAGGLE_BRIDGE_STATE",
    os.path.expanduser("~/.mycelium_waggle_bridge_state.json"),
)

# The Waggle signal kinds worth mirroring into Mycelium's durable trace log —
# "explored" and "heartbeat" are too high-volume/low-value to be worth mining.
BRIDGED_WAGGLE_KINDS = {"gold", "dead-end", "warn", "handoff"}


def _now_rfc3339() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _http_json(url: str, method: str = "GET", body: Optional[dict] = None, timeout: float = 10.0) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "null")


def _load_state() -> Dict[str, str]:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: Dict[str, str]) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)


def sync_from_waggle(waggle_url: str = DEFAULT_WAGGLE_URL,
                      mycelium_gateway: str = DEFAULT_MYCELIUM_GATEWAY) -> int:
    """Mirror new high-signal Waggle deposits into Mycelium as traces.
    Returns the number of traces written."""
    state = _load_state()
    since = state.get("waggle_since") or "1970-01-01T00:00:00Z"
    until = _now_rfc3339()
    url = f"{waggle_url}/v1/recall/window?since={since}&until={until}"
    try:
        events = _http_json(url) or []
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        raise RuntimeError(f"could not reach Waggle at {waggle_url}: {e}") from e

    written = 0
    for ev in events:
        sig = ev.get("signal") or ev
        kind = sig.get("kind", "")
        if kind not in BRIDGED_WAGGLE_KINDS:
            continue
        trace_body = {
            "agent": sig.get("agent", "waggle"),
            "session": "waggle-bridge",
            "kind": "waggle_signal",
            "action": kind,
            "target": sig.get("resource", ""),
            "outcome": kind,
            "payload": {
                "note": sig.get("note", ""),
                "intensity": sig.get("intensity"),
                "half_life_s": sig.get("half_life_s"),
                "subtype": sig.get("subtype"),
                "meta": sig.get("meta"),
                "waggle_signal_id": sig.get("id"),
            },
        }
        _http_json(f"{mycelium_gateway}/api/trace", method="POST", body=trace_body)
        written += 1

    state["waggle_since"] = until
    _save_state(state)
    return written


def sync_from_mycelium(mycelium_gateway: str = DEFAULT_MYCELIUM_GATEWAY,
                        waggle_url: str = DEFAULT_WAGGLE_URL) -> int:
    """Mirror new open Mycelium findings into Waggle as gold signals.
    Returns the number of signals deposited."""
    state = _load_state()
    since = state.get("mycelium_since", "")
    q = f"state=open&since={since}" if since else "state=open"
    try:
        resp = _http_json(f"{mycelium_gateway}/api/findings?{q}")
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        raise RuntimeError(f"could not reach Mycelium gateway at {mycelium_gateway}: {e}") from e

    findings = resp.get("findings", resp) if isinstance(resp, dict) else resp
    written = 0
    latest_ts = since
    for f in findings or []:
        resource = f.get("target") or f"finding://{f['id']}"
        confidence = float(f.get("confidence", 0.5))
        sig_body = {
            "signal": {
                "agent": "mycelium-bridge",
                "resource": resource,
                "kind": "gold",
                "note": f"[mycelium:{f.get('miner', '?')}] {f.get('title', '')} -> {f.get('suggestion', '')}"[:500],
                "intensity": max(1.0, min(10.0, confidence * 10)),
                "half_life_s": 86400,  # a day — offline-mined findings are durable, not momentary
                "decay": "power",
                "meta": {"mycelium_finding_id": f.get("id", ""), "miner": f.get("miner", "")},
            }
        }
        _http_json(f"{waggle_url}/v1/signals", method="POST", body=sig_body)
        written += 1
        ts = f.get("created_ts", "")
        if ts > latest_ts:
            latest_ts = ts

    if latest_ts:
        state["mycelium_since"] = latest_ts
        _save_state(state)
    return written


def sync_both(waggle_url: str = DEFAULT_WAGGLE_URL,
              mycelium_gateway: str = DEFAULT_MYCELIUM_GATEWAY) -> Dict[str, int]:
    return {
        "waggle_to_mycelium": sync_from_waggle(waggle_url, mycelium_gateway),
        "mycelium_to_waggle": sync_from_mycelium(mycelium_gateway, waggle_url),
    }


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Bridge Waggle signals <-> Mycelium traces/findings")
    p.add_argument("--waggle-url", default=DEFAULT_WAGGLE_URL)
    p.add_argument("--mycelium-gateway", default=DEFAULT_MYCELIUM_GATEWAY)
    p.add_argument("--loop", action="store_true", help="run continuously instead of once")
    p.add_argument("--interval", type=float, default=30.0, help="seconds between polls in --loop mode")
    args = p.parse_args()

    def once():
        counts = sync_both(args.waggle_url, args.mycelium_gateway)
        print(f"[{_now_rfc3339()}] waggle->mycelium: {counts['waggle_to_mycelium']} traces, "
              f"mycelium->waggle: {counts['mycelium_to_waggle']} signals")

    if args.loop:
        while True:
            try:
                once()
            except RuntimeError as e:
                print(f"[{_now_rfc3339()}] bridge error: {e}")
            time.sleep(args.interval)
    else:
        once()


if __name__ == "__main__":
    main()
