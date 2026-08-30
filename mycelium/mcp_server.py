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
import os
import sys
from typing import Any, Dict, List, Optional

try:
    from . import core, miners
    from .apply import apply_finding
    from . import publish as publish_mod
    from . import a2a as a2a_mod
except ImportError:  # launched as a script (python3 mcp_server.py), not -m
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from mycelium import core, miners
    from mycelium.apply import apply_finding
    from mycelium import publish as publish_mod
    from mycelium import a2a as a2a_mod

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "mycelium", "version": "0.1.0"}


def _account_farm():
    """Lazy-import wallet/account_farm.py -- keeps camoufox/playwright (a
    real browser-automation dependency) out of the MCP server's startup
    path; only pulled in when an agent actually calls one of the
    account-farm tools below."""
    import sys as _sys

    wallet_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "wallet")
    if wallet_dir not in _sys.path:
        _sys.path.insert(0, wallet_dir)
    import account_farm as _af
    return _af


def dashboard_url() -> str:
    """Same MYCELIUM_ADDR env var the Go gateway reads (gateway/main.go),
    same default ("localhost:8811") -- one shared value, not two
    independently-defaulted ones that could silently drift apart."""
    return f"http://{os.environ.get('MYCELIUM_ADDR', 'localhost:8811')}/web/"


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
        "name": "mycelium.dismiss_finding",
        "description": "Dismiss an open finding without applying it. No-ops with an error if the finding is already applied or dismissed.",
        "inputSchema": {
            "type": "object",
            "properties": {"finding_id": {"type": "string"}},
            "required": ["finding_id"],
        },
    },
    {
        "name": "mycelium.dashboard_url",
        "description": "Return the URL of the agent-native dashboard served by the Go gateway (live traces, findings, provenance, wallets, miners, on-device mining).",
        "inputSchema": {"type": "object", "properties": {}},
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
    {
        "name": "mycelium.signup_account",
        "description": (
            "Sign up for a new account on a registered service using a disposable email, "
            "solving any CAPTCHA encountered (proven for Shumei-family slider puzzles; see "
            "wallet/account_farm.py's module docstring for what's honestly out of scope -- "
            "reCAPTCHA/hCaptcha image grids, Cloudflare Turnstile). Returns real credentials "
            "on success. Rate-limited per agent and globally per service (24h rolling); "
            "`reason` is required and permanently logged so any agent can explain later why "
            "it created a given account. Call mycelium.list_account_services first to see "
            "which services are registered -- this will NOT automate an arbitrary URL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "must be a registered SiteProfile name"},
                "agent": {"type": "string", "description": "calling agent's identity, for the audit log + rate limit"},
                "reason": {"type": "string", "description": "why this agent needs its own account here (>=15 chars, logged verbatim)"},
            },
            "required": ["service", "agent", "reason"],
        },
    },
    {
        "name": "mycelium.list_account_services",
        "description": "List services account-signup is registered for (the allowlist), with each one's credential type and ToS note.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mycelium.account_farm_audit",
        "description": "Read the account-signup audit log -- what got created, by which agent, and why.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "agent": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
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
    if name == "mycelium.dismiss_finding":
        return core.dismiss_finding(args.get("finding_id", "")) or {"error": "not found"}
    if name == "mycelium.dashboard_url":
        return {"url": dashboard_url()}
    if name == "mycelium.publish":
        return publish_mod.publish()
    if name == "mycelium.publish_findings":
        return a2a_mod.publish_findings(limit=args.get("limit", 3))
    if name == "mycelium.signup_account":
        af = _account_farm()
        return af.signup(
            args.get("service", ""), agent=args.get("agent", ""), reason=args.get("reason", ""),
        )
    if name == "mycelium.list_account_services":
        af = _account_farm()
        return {"services": af.list_services()}
    if name == "mycelium.account_farm_audit":
        af = _account_farm()
        return {"entries": af.recent_audit(
            service=args.get("service"), agent=args.get("agent"), limit=args.get("limit", 20),
        )}
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
