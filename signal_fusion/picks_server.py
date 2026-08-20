"""ares-signal-fusion picks sidecar — serves /api/picks + /health from ares_picks.db.

The mycelium gateway (Fold 4) proxies /api/picks here (MYCELIUM_PICKS_BASE,
default http://2.25.70.156:8003). stdlib only, binds 0.0.0.0:PICKS_PORT
(default 8003). Reads the picks DB path from SIGNAL_FUSION_CONFIG config.json.
"""

import http.server
import json
import os
import sys
import urllib.parse

DEFAULT_CONFIG = "/opt/ares/ares-signal-fusion/config.json"


def _load_config():
    path = os.environ.get("SIGNAL_FUSION_CONFIG", DEFAULT_CONFIG)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        if self.path.startswith("/health"):
            return self._json(200, {"ok": True, "service": "ares-signal-fusion-picks"})
        if self.path.startswith("/api/picks"):
            try:
                cfg = _load_config()
                db = cfg.get("endpoints", {}).get(
                    "picks_db", "/opt/ares/ares-signal-fusion/ares_picks.db"
                )
                store = PickStore(db)
                limit = 10
                if "?" in self.path:
                    params = urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "limit" in params:
                        limit = int(params["limit"][0])
                picks = store.top_picks(limit=limit)
                store.close()
                return self._json(200, {"picks": picks, "count": len(picks)})
            except Exception as e:  # noqa: BLE001 — sidecar surfaces errors as JSON
                return self._json(500, {"error": str(e)})
        return self._json(404, {"error": "not found"})

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        sys.stderr.write("[picks %s] %s\n" % (self.log_date_time_string(), format % args))


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    global PickStore  # noqa: PLW0603 — import after path fix
    from signal_fusion.store import PickStore  # noqa: PLC0415

    port = int(os.environ.get("PICKS_PORT", "8003"))
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"picks sidecar listening on :{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
