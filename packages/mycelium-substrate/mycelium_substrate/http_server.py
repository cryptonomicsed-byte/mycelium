"""[P2-01] Kept from mycelium-tools; `core` here resolves (via the
mycelium_substrate shim) to this repo's real `mycelium.core`.

Minimal stdlib-only HTTP server exposing the trace ingest/query contract
that other tools (mycelium-collector, mycelium-cycle) depend on.

This is NOT a port of the full Go gateway (cryptonomicsed-byte/mycelium's
gateway/main.go) -- that's a much larger, separate product surface: a live
dashboard, findings UI, WebTransport streaming, council/picks proxies. That
stays exactly where it is, still runnable on any device with Go installed,
still the richer option for anyone who wants the dashboard. This exists so
a pure `pip install mycelium-substrate` gives every OTHER tool in this repo
something to talk to over HTTP without requiring Go at all -- just the two
endpoints the rest of the system actually calls:

  POST /api/trace    -- emit one trace (mycelium-collector's trace())
  GET  /api/traces   -- query traces (mycelium-collector's score_edges())
  GET  /api/status   -- trivial health/count check

Same MYCELIUM_ADDR env var mcp_server.py's dashboard_url() already reads,
so both agree on one address without redefining it here.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import core


class _Handler(BaseHTTPRequestHandler):
    server_version = "mycelium-substrate-http/0.1"

    def log_message(self, fmt, *args):  # quieter default access log
        pass

    def _send_json(self, status: int, obj) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 (stdlib naming)
        parsed = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            if parsed.path == "/api/status":
                rows = core.iter_rows(core.query_traces(limit=1))
                self._send_json(200, {"status": "ok", "db": core.DB_PATH})
                return
            if parsed.path == "/api/traces":
                if "limit" in qs:
                    qs["limit"] = int(qs["limit"])
                rows = core.iter_rows(core.query_traces(**{
                    k: v for k, v in qs.items()
                    if k in ("agent", "kind", "action", "outcome", "session", "limit")
                }))
                self._send_json(200, {"count": len(rows), "traces": rows})
                return
            self._send_json(404, {"error": "not found"})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw or b"{}")
        except Exception as e:
            self._send_json(400, {"error": f"invalid JSON body: {e}"})
            return
        try:
            if parsed.path == "/api/trace":
                row = core.emit(**{k: v for k, v in body.items() if k in (
                    "agent", "session", "kind", "action", "target", "outcome",
                    "duration_ms", "payload",
                )})
                self._send_json(201, {"status": "ok", "id": row["id"]})
                return
            self._send_json(404, {"error": "not found"})
        except TypeError as e:
            self._send_json(400, {"error": str(e)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})


def serve() -> None:
    addr = os.environ.get("MYCELIUM_ADDR", "localhost:8811")
    host, _, port_s = addr.rpartition(":")
    host = host or "0.0.0.0"
    port = int(port_s or "8811")
    core.init_db()
    httpd = ThreadingHTTPServer((host, port), _Handler)
    print(f"mycelium-substrate HTTP server listening on http://{host}:{port} (db={core.DB_PATH})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    serve()
