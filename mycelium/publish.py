"""publish — external anchoring + A2A distribution of substrate state.

Two channels:
  1. Checkpoint store (local, always): timestamped copies of the anchor log
     + manifest (pubkey, counts, ts). Cheap, offline, append-only by design.
  2. Gitea push (remote, when creds exist): publishes the latest checkpoint
     to a repo via the Gitea API — an external anchor an attacker on this
     box cannot silently rewrite.

Env for Gitea: GITEA_URL (e.g. https://gitea.example.com), GITEA_TOKEN,
GITEA_REPO ("owner/repo"), GITEA_BRANCH (default "main").
"""
from __future__ import annotations

import json
import os
import shutil
import time
from typing import Any, Dict, Optional
from urllib import request

from . import core

CHECKPOINT_DIR = os.environ.get(
    "MYCELIUM_CHECKPOINT_DIR", os.path.expanduser("~/mycelium/checkpoints")
)
ANCHOR_PATH = os.environ.get(
    "MYCELIUM_ANCHOR_PATH",
    os.path.expanduser("~/mycelium/gateway/chain_state.jsonl"),
)
PUBKEY_PATH = os.environ.get(
    "MYCELIUM_PUBKEY_PATH",
    os.path.expanduser("~/mycelium/gateway/provenance_key.json"),
)


def manifest(pubkey: str) -> Dict[str, Any]:
    return {
        "schema": "mycelium-checkpoint-v1",
        "ts": core._now(),
        "pubkey": pubkey,
        "traces": len(core.query_traces(limit=100000)),
        "findings": len(core.query_findings(limit=100000)),
    }


def publish_checkpoint() -> Optional[str]:
    """Copy the anchor log into a timestamped checkpoint dir. Returns path."""
    if not os.path.exists(ANCHOR_PATH):
        return None
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = os.path.join(CHECKPOINT_DIR, ts)
    os.makedirs(dest, exist_ok=True)
    shutil.copy2(ANCHOR_PATH, os.path.join(dest, "chain_state.jsonl"))
    pubkey = ""
    if os.path.exists(PUBKEY_PATH):
        try:
            with open(PUBKEY_PATH) as fh:
                pubkey = json.load(fh).get("pub", "")
        except (json.JSONDecodeError, OSError):
            pass
    with open(os.path.join(dest, "manifest.json"), "w") as fh:
        json.dump(manifest(pubkey), fh, indent=2)
    return dest


def _gitea_env() -> Dict[str, str]:
    """Resolve Gitea creds: env vars first, then the credential vault."""
    url = os.environ.get("GITEA_URL", "")
    token = os.environ.get("GITEA_TOKEN", "")
    repo = os.environ.get("GITEA_REPO", "")
    if url and token and repo:
        return {"url": url, "token": token, "repo": repo,
                "branch": os.environ.get("GITEA_BRANCH", "main")}
    try:
        vault = os.path.expanduser("~/.hermes/credential_vault.json")
        with open(vault) as fh:
            g = json.load(fh).get("gitea", {})
        if g.get("url") and g.get("token") and g.get("repo"):
            return {"url": g["url"], "token": g["token"], "repo": g["repo"],
                    "branch": g.get("branch", "main")}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def gitea_push(checkpoint_dir: str) -> Dict[str, Any]:
    """Push the checkpoint manifest+anchor to Gitea. Needs env/vault creds."""
    env = _gitea_env()
    if not env:
        return {"status": "skipped", "reason": "GITEA_URL/GITEA_TOKEN/GITEA_REPO not set (env or vault)"}
    url, token, repo, branch = env["url"], env["token"], env["repo"], env["branch"]
    ts = os.path.basename(checkpoint_dir)
    files = []
    for name in ("chain_state.jsonl", "manifest.json"):
        path = os.path.join(checkpoint_dir, name)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                content = fh.read()
            import base64
            files.append({
                "operation": "create",
                "path": f"mycelium/checkpoints/{ts}/{name}",
                "content": base64.b64encode(content).decode(),
            })
    payload = json.dumps({
        "branch": branch,
        "files": files,
        "message": f"mycelium checkpoint {ts}",
    }).encode()
    api = f"{url}/api/v1/repos/{repo}/contents"
    req = request.Request(
        api, data=payload, method="POST",
        headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read())
            return {"status": "ok", "commit": body.get("commit", {}).get("sha", "?")}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)}


def publish() -> Dict[str, Any]:
    """Full publish: local checkpoint + (optional) Gitea push."""
    cp = publish_checkpoint()
    if cp is None:
        return {"status": "error", "reason": "anchor log not found (gateway never ran?)"}
    gitea = gitea_push(cp)
    return {"status": "ok", "checkpoint": cp, "gitea": gitea}
