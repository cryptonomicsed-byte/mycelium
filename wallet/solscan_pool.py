"""solscan_pool.py — round-robin Solscan API key pool.

Keys from /opt/ares/.solscan_keys.json (or ~/.hermes/solscan_keys.json).
Auth: header `token: <key>` against https://pro-api.solscan.io/v2.0/*.
Per-key cooldown on 429, escalating backoff, shared across callers.
Free-tier keys are validated on first use (401 = drop from the pool).

Usage:  from solscan_pool import token_holders, status
"""

import json
import os
import time
import urllib.error
import urllib.request

HOST = "https://pro-api.solscan.io/v2.0"
POOL_FILE = "/opt/ares/.solscan_keys.json"
FALLBACK = os.path.expanduser("~/.hermes/solscan_keys.json")

_cooldowns = {}      # key -> unix ts when usable again
_failures = {}       # key -> consecutive failures (escalation)


def _load():
    for path in (POOL_FILE, FALLBACK):
        try:
            with open(path) as f:
                return json.load(f)
        except OSError:
            continue
    return []


def keys():
    return [k.get("api_key") for k in _load() if k.get("api_key")]


def status():
    now = time.time()
    ks = keys()
    return {"keys": len(ks), "cooling": sum(1 for k in ks if _cooldowns.get(k, 0) > now)}


def _pick():
    now = time.time()
    for k in keys():
        if _cooldowns.get(k, 0) <= now:
            return k
    return None


def _mark_cooldown(key, seconds):
    _cooldowns[key] = time.time() + seconds


def solscan_get(path, params, key="", timeout=15):
    """GET with the rotating key. Returns (status, body_dict) or (0, {error})."""
    k = key or _pick()
    if not k:
        return 0, {"error": "no solscan key available"}
    q = "&".join(f"{p}={urllib.parse.quote(str(v))}" for p, v in params.items()) if params else ""
    url = f"{HOST}{path}?{q}" if q else f"{HOST}{path}"
    req = urllib.request.Request(url, headers={"accept": "application/json", "token": k})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode(errors="replace"))
        except Exception:
            body = {}
        if e.code == 401:
            # invalid/expired key — drop it from the pool file
            _failures[k] = _failures.get(k, 0) + 1
            if _failures[k] >= 2:
                _drop_key(k)
        elif e.code == 429:
            _mark_cooldown(k, 30 + 30 * _failures.get(k, 0))
            _failures[k] = _failures.get(k, 0) + 1
        return e.code, body
    except Exception as e:
        return 0, {"error": str(e)}


def _drop_key(key):
    """Remove a dead key from the pool file(s)."""
    for path in (POOL_FILE, FALLBACK):
        try:
            with open(path) as f:
                data = json.load(f)
            n = len(data)
            data = [d for d in data if d.get("api_key") != key]
            if len(data) != n:
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
                os.chmod(path, 0o600)
        except OSError:
            continue


def token_holders(mint, limit=20):
    """Top holders: [{wallet, pct}] or [] on failure. Defensive — the v2
    response shape may vary by plan; handle both list-of-holders and
    {data:{items:[]}} wrappers."""
    st, body = solscan_get("/token/holders",
                           {"address": mint, "page": 1, "page_size": limit})
    if st != 200 or not isinstance(body, dict):
        return []
    data = body.get("data", body)
    if isinstance(data, dict):
        items = data.get("items") or data.get("holders") or []
    elif isinstance(data, list):
        items = data
    else:
        return []
    out = []
    for h in items:
        if not isinstance(h, dict):
            continue
        w = h.get("owner") or h.get("address") or h.get("wallet") or ""
        if not w:
            continue
        pct = h.get("percentage") or h.get("amount_percentage") or h.get("pct")
        try:
            pct = float(pct) if pct is not None else 0.0
        except (TypeError, ValueError):
            pct = 0.0
        out.append({"wallet": w, "pct": pct})
    return out


def token_meta(mint):
    """Token metadata (symbol/name/decimals) or {} — best-effort."""
    st, body = solscan_get("/token/meta", {"address": mint})
    if st != 200 or not isinstance(body, dict):
        return {}
    d = body.get("data", body)
    return d if isinstance(d, dict) else {}


if __name__ == "__main__":
    import urllib.parse  # noqa: PLC0415
    print(status())
