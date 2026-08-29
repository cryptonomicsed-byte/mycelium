"""ares-signal-fusion picks sidecar — serves /api/picks + /health from ares_picks.db.

The mycelium gateway (Fold 4) proxies /api/picks here (MYCELIUM_PICKS_BASE,
default http://2.25.70.156:8003). stdlib only, binds 0.0.0.0:PICKS_PORT
(default 8003). Reads the picks DB path from SIGNAL_FUSION_CONFIG config.json.

Also serves the real Mycelium dashboard (web/dashboard/dist, a static
vanilla-TS build with no runtime framework) at / when DASHBOARD_DIST_DIR
exists -- same-origin as /api/picks so the dashboard's api.ts (GATEWAY_BASE
is empty, fetches relative to its own origin) works without a separate
reverse proxy. The dashboard also calls /api/council/overview and
/api/webtransport/cert-hash, which this sidecar does not implement -- those
panels will show their own fetch-failed state, the Picks view (the one
that matters here) works standalone.
"""

import http.server
import json
import mimetypes
import os
import sys
import urllib.parse

DEFAULT_CONFIG = "/opt/ares/ares-signal-fusion/config.json"
DEFAULT_DASHBOARD_DIST = "/opt/ares/ares-signal-fusion/web-dashboard-dist"


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
        if self.path.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        return self._static()

    def _static(self):
        dist_dir = os.environ.get("DASHBOARD_DIST_DIR", DEFAULT_DASHBOARD_DIST)
        req_path = self.path.split("?", 1)[0]
        if req_path == "/":
            req_path = "/index.html"
        # index.html's own asset tags are all hardcoded absolute to
        # /web/dashboard/... (its own comment: written to be served behind
        # the Fold 4 gateway's path-rewrite, not from a bare root). Rather
        # than rewrite the committed HTML, strip that exact prefix so real
        # asset requests resolve to real files under dist_dir.
        PREFIX = "/web/dashboard/"
        if req_path.startswith(PREFIX):
            req_path = "/" + req_path[len(PREFIX):]
        # No path traversal above dist_dir.
        rel = req_path.lstrip("/")
        full = os.path.normpath(os.path.join(dist_dir, rel))
        if not full.startswith(os.path.normpath(dist_dir)):
            return self._json(403, {"error": "forbidden"})
        is_asset_request = "." in os.path.basename(req_path)
        if not os.path.isfile(full):
            if is_asset_request:
                # A real asset (.js/.css/.png/...) that's genuinely missing
                # must 404, never fall back to index.html -- serving HTML
                # with a 200 for a JS/CSS request is a silent white-screen
                # bug (browser gets markup where it expected a script).
                return self._json(404, {"error": "not found", "path": req_path})
            # SPA-style fallback so client-side routes (e.g. /picks) still
            # load the app shell instead of a bare 404.
            full = os.path.join(dist_dir, "index.html")
            if not os.path.isfile(full):
                return self._json(404, {"error": "not found"})
        ctype, _ = mimetypes.guess_type(full)
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
