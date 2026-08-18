"""Mycelium MCP server — stdio transport, stdlib-only.

Implements the Model Context Protocol (JSON-RPC 2.0 over newline-delimited
stdio) so ANY agent (Hermes, Claude Code, Codex, ...) can discover and invoke
Mycelium as native tools:

  mycelium.trace          — emit a trace event into the substrate
  mycelium.list_traces    — query the substrate
  mycelium.mine           — run pattern miners, persist findings
  mycelium.list_findings  — read findings (with filters)
  mycelium.get_finding    — one finding by id
  mycelium.apply_finding  — apply a finding (auto-generate skill)

No human-only interface. The CLI exists only as a debugging mirror.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

try:
    from . import core, miners
    from .apply import apply_finding
    from . import publish as publish_mod
    from . import a2a as a2a_mod
except ImportError:  # launched as a script (python3 mcp_server.py), not -m
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from mycelium import core, miners
    from mycelium.apply import apply_finding
    from mycelium import publish as publish_mod
    from mycelium import a2a as a2a_mod

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "mycelium", "version": "0.1.0"}

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "mycelium.trace",
        "description": "Emit a trace event into the shared substrate (stigmergic memory).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "agent identity"},
                "session": {"type": "string", "description": "session/task id"},
                "kind": {"type": "string", "enum": sorted(core.VALID_KINDS)},
                "action": {"type": "string", "description": "tool/action name"},
                "target": {"type": "string", "description": "resource acted on"},
                "outcome": {"type": "string", "enum": sorted(core.VALID_OUTCOMES)},
                "duration_ms": {"type": "integer"},
                "payload": {"type": "object", "description": "free-form JSON"},
            },
            "required": ["agent", "session", "kind"],
        },
    },
    {
        "name": "mycelium.list_traces",
        "description": "Query the substrate trace log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string"},
                "kind": {"type": "string"},
                "action": {"type": "string"},
                "outcome": {"type": "string"},
                "limit": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "mycelium.mine",
        "description": "Run pattern miners over the substrate; persist findings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "miner": {"type": "string", "enum": ["all"] + sorted(miners.MINERS)},
            },
            "required": [],
        },
    },
    {
        "name": "mycelium.list_findings",
        "description": "Read discovered patterns/findings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "enum": ["open", "applied", "dismissed"]},
                "miner": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "mycelium.get_finding",
        "description": "Fetch one finding by id.",
        "inputSchema": {
            "type": "object",
            "properties": {"finding_id": {"type": "string"}},
            "required": ["finding_id"],
        },
    },
    {
        "name": "mycelium.apply_finding",
        "description": "Apply a finding — auto-generates a hot-swappable SKILL.md for skill findings.",
        "inputSchema": {
            "type": "object",
            "properties": {"finding_id": {"type": "string"}},
            "required": ["finding_id"],
        },
    },
    {
        "name": "mycelium.publish",
        "description": "Checkpoint the anchor log locally; push to Gitea if creds configured.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mycelium.publish_findings",
        "description": "Publish open findings to the Vantage feed (A2A distribution to other agents).",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 3}},
        },
    },
    {
        "name": "mycelium.check_alerts",
        "description": "Evaluate generated alert configs against the current substrate.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _call_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if name == "mycelium.trace":
        row = core.emit(**args)
        return {"status": "ok", "id": row["id"]}
    if name == "mycelium.list_traces":
        rows = core.iter_rows(core.query_traces(**{k: v for k, v in args.items() if k in ("agent", "kind", "action", "outcome", "session", "limit")}))
        return {"count": len(rows), "traces": rows}
    if name == "mycelium.mine":
        miner = args.get("miner", "all")
        found = miners.run_all() if miner == "all" else miners.run_miner(miner)
        ids = [core.add_finding(**f)["id"] for f in found]
        return {"miner": miner, "findings_saved": len(ids), "ids": ids}
    if name == "mycelium.list_findings":
        rows = core.iter_rows(core.query_findings(**{k: v for k, v in args.items() if k in ("state", "miner", "limit")}))
        return {"count": len(rows), "findings": rows}
    if name == "mycelium.get_finding":
        row = core.get_finding(args.get("finding_id", ""))
        return {"finding": core.row_to_dict(row) if row else None}
    if name == "mycelium.apply_finding":
        return apply_finding(args.get("finding_id", "")) or {"error": "not found"}
    if name == "mycelium.publish":
        return publish_mod.publish()
    if name == "mycelium.publish_findings":
        return a2a_mod.publish_findings(limit=args.get("limit", 3))
    if name == "mycelium.check_alerts":
        from mycelium import cli as _cli
        import io
        import json as _json
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            _cli.cmd_alerts(type("A", (), {})())
        finally:
            sys.stdout = old
        return _json.loads(buf.getvalue())
    raise ValueError(f"unknown tool {name}")


def handle(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = msg.get("method", "")
    rid = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }}
    if method == "notifications/initialized":
        return None  # no response to notifications
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        try:
            result = _call_tool(params.get("name", ""), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": json.dumps(result, default=str)}],
                "isError": False,
            }}
        except Exception as exc:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"error: {exc}"}],
                "isError": True,
            }}
    return {"jsonrpc": "2.0", "id": rid, "error": {
        "code": -32601, "message": f"method not found: {method}"}}


def serve() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    serve()
