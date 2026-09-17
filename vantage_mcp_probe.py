#!/usr/bin/env python3
"""Probe the live Vantage MCP server: handshake + tools/list."""
import json
import re
import urllib.error
import urllib.request

ENV = "/opt/ares/Vantage/backend/.env"
BASE = "http://127.0.0.1:8001/mcp"

env = open(ENV).read()


def val(name):
    m = re.search(rf"^{name}=(.*)$", env, re.M)
    return m.group(1).strip().strip("\"'") if m else ""


NAMES = ["VANTAGE_API_KEY", "VANTAGE_MCP_KEY", "MCP_VANTAGE_API_KEY",
         "AGENT_API_KEY", "VANTAGE_KEY", "API_KEY"]
found = [n for n in NAMES if val(n)]
key = val(found[0]) if found else ""
print("env var used:", found[:1] or "(none found)", "| key present:", bool(key))

H = {"Content-Type": "application/json",
     "Accept": "application/json, text/event-stream"}
if key:
    H["X-Agent-Key"] = key


def post(body, sid=None):
    h = dict(H)
    if sid:
        h["Mcp-Session-Id"] = sid
    req = urllib.request.Request(
        BASE, data=json.dumps(body).encode(), headers=h, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.headers.get("Mcp-Session-Id"), r.read().decode()
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:400]}"
    except Exception as e:
        return None, f"ERR {type(e).__name__}: {e}"


def parse(out):
    """MCP streamable HTTP may return SSE framing."""
    for chunk in out.split("\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            chunk = chunk[6:]
        if chunk.startswith("{"):
            try:
                return json.loads(chunk)
            except Exception:
                continue
    return None


sid, out = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "hermes-probe", "version": "1.0"}}})
print("initialize -> session:", sid)
d = parse(out)
if d and "result" in d:
    si = d["result"].get("serverInfo", {})
    print("  server:", si.get("name"), si.get("version"))
    print("  capabilities:", list(d["result"].get("capabilities", {}).keys()))
else:
    print("  raw:", out[:500])

if sid:
    # required by the MCP spec before other calls
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    _, out2 = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid)
    d2 = parse(out2)
    if d2 and "result" in d2:
        tools = d2["result"].get("tools", [])
        print(f"\n=== MCP TOOLS: {len(tools)} ===")
        for t in tools:
            nm = t.get("name", "?")
            desc = (t.get("description") or "").replace("\n", " ")[:78]
            print(f"  {nm:<44} {desc}")
        hits = [t.get("name") for t in tools
                if re.search(r"guild|event|channel|message|receipt|trace|agent",
                             t.get("name", ""), re.I)]
        print(f"\n=== RELEVANT TO HERDR/GUILD ({len(hits)}) ===")
        for h in hits:
            print("  ", h)
    else:
        print("tools/list raw:", (out2 or "")[:700])
