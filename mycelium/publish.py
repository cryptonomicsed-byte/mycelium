"""publish — external anchoring + A2A distribution of substrate state.

Three channels:
  1. Checkpoint store (local, always): timestamped copies of the anchor log
     + manifest (pubkey, counts, ts). Cheap, offline, append-only by design.
  2. Gitea push (remote, when creds exist): publishes the latest checkpoint
     to a repo via the Gitea API — an external anchor an attacker on this
     box cannot silently rewrite.
  3. Nostr wire (remote, when a relay + key are configured): publishes each
     new finding as a signed engram + Crucible claim, so another agent on
     another box can read what this substrate learned. See nostr_wire.py —
     a finding the checkpoint/Gitea channels anchor but nobody else can read
     is a trace nobody follows.

Env for Gitea: GITEA_URL (e.g. https://gitea.example.com), GITEA_TOKEN,
GITEA_REPO ("owner/repo"), GITEA_BRANCH (default "main").

Env for Nostr: NOSTR_SECKEY (nsec1... or hex), NOSTR_RELAY (wss://...).
Requires minipae on PYTHONPATH; skipped, not failed, when absent.
"""
from __future__ import annotations

import asyncio
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


NOSTR_STATE_PATH = os.environ.get(
    "MYCELIUM_NOSTR_STATE_PATH",
    os.path.join(CHECKPOINT_DIR, ".nostr_published.json"),
)


def _nostr_env() -> Dict[str, str]:
    """Resolve Nostr creds: env vars first, then the credential vault."""
    seckey = os.environ.get("NOSTR_SECKEY", "")
    relay = os.environ.get("NOSTR_RELAY", "")
    if seckey and relay:
        return {"seckey": seckey, "relay": relay}
    try:
        vault = os.path.expanduser("~/.hermes/credential_vault.json")
        with open(vault) as fh:
            n = json.load(fh).get("nostr", {})
        if n.get("seckey") and (relay or n.get("relay")):
            return {"seckey": n["seckey"], "relay": relay or n["relay"]}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _load_published(path: str) -> set:
    try:
        with open(path) as fh:
            return set(json.load(fh))
    except (OSError, json.JSONDecodeError):
        return set()


def _save_published(path: str, ids: set) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(sorted(ids), fh)


def nostr_publish() -> Dict[str, Any]:
    """Publish findings not yet on the wire, as engram + claim per finding.

    Idempotent across runs via a local set of already-published finding ids
    (NOSTR_STATE_PATH) — publish.py may run on a cron, and re-signing +
    re-sending the same finding every cycle would flood the relay.
    """
    env = _nostr_env()
    if not env:
        return {"status": "skipped", "reason": "NOSTR_SECKEY/NOSTR_RELAY not set (env or vault)"}

    try:
        from . import nostr_wire
        import minipae as m
    except ImportError as exc:
        return {"status": "skipped", "reason": f"minipae not on PYTHONPATH: {exc}"}

    seckey_raw = env["seckey"]
    seckey = m.nsec_decode(seckey_raw) if seckey_raw.startswith("nsec") else bytes.fromhex(seckey_raw)
    owner_pubkey = m.pubkey_from_secret(int.from_bytes(seckey, "big"))

    published = _load_published(NOSTR_STATE_PATH)
    findings = core.iter_rows(core.query_findings(state="open", limit=100))
    new = [f for f in findings if f["id"] not in published]
    if not new:
        return {"status": "ok", "published": 0}

    results: Dict[str, Any] = {}
    for f in new:
        try:
            results[f["id"]] = asyncio.run(
                nostr_wire.publish_finding(f, seckey, owner_pubkey, env["relay"])
            )
            published.add(f["id"])
        except Exception as exc:  # noqa: BLE001
            results[f["id"]] = {"status": "error", "reason": str(exc)}
    _save_published(NOSTR_STATE_PATH, published)
    return {"status": "ok", "published": len(new), "results": results}


def publish() -> Dict[str, Any]:
    """Full publish: local checkpoint + (optional) Gitea push + (optional) Nostr wire."""
    cp = publish_checkpoint()
    if cp is None:
        return {"status": "error", "reason": "anchor log not found (gateway never ran?)"}
    gitea = gitea_push(cp)
    nostr = nostr_publish()
    return {"status": "ok", "checkpoint": cp, "gitea": gitea, "nostr": nostr}
