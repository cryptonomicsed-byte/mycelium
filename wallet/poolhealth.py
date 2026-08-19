#!/usr/bin/env python3
"""poolhealth.py — tiny HTTP status endpoint for the key pools + proxy pool.

Serves GET /health and GET /api/poolhealth on :8004. Read-only, localhost
only by default (systemd unit binds 127.0.0.1). The gateway proxies
/api/poolhealth to this port so the dashboard never sees raw keys.

Data: GMGN keys/cooldowns, proxy pool size + cooldown state, Solscan keys,
TeamoRouter keys. No secret material — counts and states only.
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "/opt/ares"
WALLET = os.path.join(BASE, "wallet_intel")
DB = os.path.join(WALLET, "wallet_intel.db")

def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def _count_keys(paths):
    """Count entries across candidate pool files; tolerate both list and {keys:[]} shapes."""
    total = 0
    for p in paths:
        d = _load(p, None)
        if d is None:
            continue
        if isinstance(d, list):
            total += len(d)
        elif isinstance(d, dict):
            total += len(d.get("keys", d.get("proxies", [])) if "keys" in d or "proxies" in d else [])
    return total

def pool_status():
    now = time.time()
    # GMGN
    gmgn_keys = _load(os.path.join(BASE, ".gmgn_keys.json"), [])
    if isinstance(gmgn_keys, dict):
        gmgn_keys = gmgn_keys.get("keys", [])
    gmgn_state = _load(os.path.join(BASE, ".gmgn_pool_state.json"), {})
    cooling = sum(1 for k in gmgn_keys if gmgn_state.get(k.get("api_key", ""), 0) > now)
    # proxies
    prox = _load(os.path.join(BASE, ".gmgn_proxies.json"), {"proxies": []})
    prox_list = prox.get("proxies", prox) if isinstance(prox, dict) else prox
    pstate = _load(os.path.join(BASE, ".gmgn_proxy_state.json"), {})
    pcooldown = sum(1 for p in prox_list if pstate.get(p.get("server", ""), 0) > now)
    # IP ban from wallet_intel.db
    ip_ban = 0.0
    try:
        import sqlite3
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
        try:
            row = c.execute("SELECT v FROM state WHERE k='gmgn_ban_until'").fetchone()
            ip_ban = float(row[0]) if row and row[0] else 0.0
        finally:
            c.close()
    except Exception:
        pass
    # Solscan / Teamo
    solscan = _count_keys([os.path.join(BASE, ".solscan_keys.json"),
                           os.path.expanduser("~/.hermes/solscan_keys.json")])
    teamo = _count_keys([os.path.expanduser("~/.hermes/teamorouter_keys.json"),
                         os.path.join(BASE, ".teamo_keys.json")])
    return {
        "gmgn": {
            "keys": len(gmgn_keys) if isinstance(gmgn_keys, list) else 0,
            "cooling": cooling,
            "ip_ban_until": ip_ban,
            "ip_banned": ip_ban > now,
        },
        "proxies": {
            "total": len(prox_list) if isinstance(prox_list, list) else 0,
            "cooldown": pcooldown,
            "live_estimate": max(0, (len(prox_list) if isinstance(prox_list, list) else 0) - pcooldown),
        },
        "solscan": {"keys": solscan},
        "teamorouter": {"keys": teamo},
        "ts": now,
    }

class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/health", "/api/poolhealth"):
            self._send(200, pool_status())
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8004), H).serve_forever()
