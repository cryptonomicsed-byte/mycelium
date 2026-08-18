"""a2a — distribute findings to the agent network (Vantage feed).

Every applied finding becomes a feed post so OTHER agents on the platform
see what the substrate discovered — the A2A half of the loop. Uses the
Vantage REST API with X-Agent-Key auth (same pattern as every Vantage
module). No human in the loop: publish is a tool any agent can call.

Env: VANTAGE_URL (default https://omokoda.duckdns.org:8001 or local),
VANTAGE_KEY (or ~/.vantage_key file).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional
from urllib import request, error as urlerror

DEFAULT_URL = os.environ.get("VANTAGE_URL", "https://omokoda.duckdns.org")


def _key() -> str:
    k = os.environ.get("VANTAGE_KEY", "")
    if not k:
        path = os.path.expanduser("~/.vantage_key")
        if os.path.exists(path):
            with open(path) as fh:
                k = fh.read().strip()
    return k


def _post(path: str, payload: Dict[str, Any], retries: int = 2) -> Dict[str, Any]:
    key = _key()
    if not key:
        return {"status": "error", "reason": "no Vantage key (VANTAGE_KEY or ~/.vantage_key)"}
    req = request.Request(
        f"{DEFAULT_URL}{path}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "X-Agent-Key": key,
            "Content-Type": "application/json",
        },
    )
    # Retry on timeout/5xx (Vantage periodically degrades; the cron cycle
    # should tolerate a slow tick instead of burning the run).
    for attempt in range(retries + 1):
        try:
            with request.urlopen(req, timeout=20) as resp:
                return {"status": "ok", "http": resp.status, "body": resp.read().decode()[:500]}
        except urlerror.HTTPError as exc:
            if attempt < retries and exc.code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            return {"status": "error", "reason": f"HTTP {exc.code}", "body": exc.read().decode()[:300]}
        except Exception as exc:  # noqa: BLE001 — timeout/conn refused
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return {"status": "error", "reason": str(exc)}
    return {"status": "error", "reason": "unreachable"}


def _post_multipart(path: str, fields: Dict[str, str], filename: str, content: bytes) -> Dict[str, Any]:
    """Multipart/form-data POST (Vantage /api/agents/publish needs title+file)."""
    import uuid as _uuid

    key = _key()
    if not key:
        return {"status": "error", "reason": "no Vantage key (VANTAGE_KEY or ~/.vantage_key)"}
    boundary = f"----mycelium{_uuid.uuid4().hex}"
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: text/plain\r\n\r\n".encode() + content + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = request.Request(
        f"{DEFAULT_URL}{path}",
        data=body,
        method="POST",
        headers={
            "X-Agent-Key": key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with request.urlopen(req, timeout=25) as resp:
            return {"status": "ok", "http": resp.status, "body": resp.read().decode()[:400]}
    except urlerror.HTTPError as exc:
        return {"status": "error", "reason": f"HTTP {exc.code}", "body": exc.read().decode()[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)}


def publish_finding(finding: Dict[str, Any], channel: str = "feed") -> Dict[str, Any]:
    """Push one finding to the Vantage feed via the lightweight event endpoint.

    POST /api/agents/me/publish-event requires a 'channel' field (feed,
    activity, public, broadcast, system all accepted) and returns
    {ok, channel, event_type}. Lightweight JSON — no multipart needed.
    """
    return _post("/api/agents/me/publish-event", {
        "channel": channel,
        "event": "mycelium_finding",
        "title": finding.get("title", "Mycelium finding"),
        "payload": {
            "miner": finding.get("miner", "?"),
            "confidence": finding.get("confidence", 0.0),
            "suggestion": finding.get("suggestion", "?"),
            "evidence": finding.get("evidence", "")[:500],
        },
    })


def publish_findings(findings: Optional[list] = None, limit: int = 3) -> Dict[str, Any]:
    """Publish the most recent open findings to the feed."""
    from . import core

    rows = core.query_findings(state="open", limit=limit) if findings is None else findings
    results = []
    for r in rows:
        f = r if isinstance(r, dict) else core.row_to_dict(r)
        results.append({"finding": f.get("title"), **publish_finding(f)})
    return {"status": "ok", "published": len(results), "results": results}
