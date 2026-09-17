#!/usr/bin/env python3
"""ecosystem_status.py — Omo-Koda2 Ecosystem Intelligence Aggregator.

One endpoint that returns live state across the entire Ares trading ecosystem:
- Ares Council verdicts + calibration (:8001)
- Signal Fusion top picks (:8003)
- Pool health (:8004)
- Wallet intel stats (wallet_intel.db)
- Token intelligence bundle (given a token address)

Deploy on VPS: place in /opt/ares/Omo-Koda2/ and run as a service.

Usage:
  python3 ecosystem_status.py [--port 8005]

Endpoints:
  GET /api/ecosystem/status        → full live state snapshot
  GET /api/ecosystem/token/{addr}  → intelligence bundle for one token
  GET /api/ecosystem/health        → quick health check
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib import request as urllib_request, error as urlerror

PORT = int(os.environ.get("ECOSYSTEM_PORT", "8005"))
VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://127.0.0.1:8001")
FUSION_URL  = os.environ.get("FUSION_URL",  "http://127.0.0.1:8003")
POOL_URL    = os.environ.get("POOL_URL",    "http://127.0.0.1:8004")
VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")
WALLET_DB   = Path(os.environ.get("WALLET_INTEL_DB", "/opt/ares/wallet_intel/wallet_intel.db"))

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 30.0
_LOCK = threading.Lock()


def _http_get(url: str, headers: dict | None = None, timeout: int = 6) -> Any:
    try:
        req = urllib_request.Request(url, headers=headers or {})
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def _cached(key: str, fn):
    with _LOCK:
        now = time.time()
        if key in _CACHE and now - _CACHE[key][0] < _CACHE_TTL:
            return _CACHE[key][1]
        result = fn()
        _CACHE[key] = (now, result)
        return result


def _vantage_headers() -> dict:
    return {"X-Agent-Key": VANTAGE_KEY} if VANTAGE_KEY else {}


def fetch_council() -> dict:
    return _cached("council", lambda: {
        "overview":    _http_get(f"{VANTAGE_URL}/api/council/overview",    _vantage_headers()),
        "verdicts":    _http_get(f"{VANTAGE_URL}/api/council/verdicts?limit=5", _vantage_headers()),
        "calibration": _http_get(f"{VANTAGE_URL}/api/council/calibration", _vantage_headers()),
    })


def fetch_picks() -> Any:
    return _cached("picks", lambda: _http_get(f"{FUSION_URL}/api/picks?limit=5"))


def fetch_pool_health() -> Any:
    return _cached("pool_health", lambda: _http_get(f"{POOL_URL}/api/poolhealth"))


def fetch_wallet_stats() -> dict:
    def _query():
        if not WALLET_DB.exists():
            return {"error": "wallet_intel.db not found"}
        try:
            conn = sqlite3.connect(str(WALLET_DB))
            conn.row_factory = sqlite3.Row
            stats = {}
            for col, label in [
                ("COUNT(*)", "total_wallets"),
                ("COUNT(DISTINCT token_address)", "distinct_tokens_traded"),
            ]:
                try:
                    row = conn.execute(f"SELECT {col} AS v FROM wallet_intel LIMIT 1").fetchone()
                    if row:
                        stats[label] = row["v"]
                except Exception:
                    pass

            # Top 5 most-bought tokens in last 24h
            try:
                rows = conn.execute("""
                    SELECT token_address, COUNT(*) AS buy_count
                    FROM wallet_intel
                    WHERE action='buy'
                      AND ts >= datetime('now', '-1 day')
                    GROUP BY token_address
                    ORDER BY buy_count DESC
                    LIMIT 5
                """).fetchall()
                stats["top_tokens_24h"] = [dict(r) for r in rows]
            except Exception:
                pass

            conn.close()
            return stats
        except Exception as e:
            return {"error": str(e)}
    return _cached("wallet_stats", _query)


def fetch_token_bundle(token_address: str) -> dict:
    council = fetch_council()
    picks_data = fetch_picks()
    pool_health = fetch_pool_health()

    # Find this token in picks
    token_pick = None
    if isinstance(picks_data, list):
        token_pick = next((p for p in picks_data if p.get("token_address") == token_address), None)

    # Find this token in verdicts
    token_verdict = None
    verdicts = council.get("verdicts", [])
    if isinstance(verdicts, list):
        token_verdict = next((v for v in verdicts if v.get("symbol") == token_address
                              or v.get("token_address") == token_address), None)

    # Wallet intel for this token
    wallet_data: dict = {}
    if WALLET_DB.exists():
        try:
            conn = sqlite3.connect(str(WALLET_DB))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT address, role, buy_volume_usd, created_at
                FROM wallet_intel
                WHERE token_address = ?
                  AND action = 'buy'
                ORDER BY created_at DESC
                LIMIT 20
            """, (token_address,)).fetchall()
            wallet_data = {
                "recent_buyers": [dict(r) for r in rows],
                "buyer_count": len(rows),
            }
            conn.close()
        except Exception as e:
            wallet_data = {"error": str(e)}

    return {
        "token_address": token_address,
        "pick": token_pick,
        "verdict": token_verdict,
        "wallet_activity": wallet_data,
        "pool_health_context": pool_health,
        "scored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def get_status() -> dict:
    return {
        "council":       fetch_council(),
        "top_picks":     fetch_picks(),
        "pool_health":   fetch_pool_health(),
        "wallet_stats":  fetch_wallet_stats(),
        "as_of":         time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def _respond(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/ecosystem/status":
            self._respond(get_status())
        elif path == "/api/ecosystem/health":
            self._respond({"ok": True, "ts": time.time()})
        elif path.startswith("/api/ecosystem/token/"):
            addr = path.removeprefix("/api/ecosystem/token/").strip("/")
            if not addr:
                self._respond({"error": "token address required"}, 400)
            else:
                self._respond(fetch_token_bundle(addr))
        else:
            self._respond({"error": "not found"}, 404)


if __name__ == "__main__":
    print(f"Ecosystem aggregator on :{PORT}")
    print(f"  Vantage: {VANTAGE_URL}")
    print(f"  Signal Fusion: {FUSION_URL}")
    print(f"  Pool Health: {POOL_URL}")
    print(f"  Wallet DB: {WALLET_DB}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
