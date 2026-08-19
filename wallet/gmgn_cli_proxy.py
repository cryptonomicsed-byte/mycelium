#!/usr/bin/env python3
"""gmgn_cli_proxy.py — run gmgn-cli through the proxy pool when the VPS IP
is banned. Shared by scanner.py and collector.py.

Free-proxy reality (observed 2026-08-19): exits burn FAST — GMGN per-IP
bans a proxy exit after a handful of requests, then it recovers in ~10 min.
So this runner keeps a per-proxy cooldown state file and:
  - never picks a proxy in cooldown
  - on RATE_LIMIT_BANNED through a proxy: marks THAT proxy cooling (15 min),
    retries ONCE with a different proxy, and does NOT attribute the ban to
    the VPS IP (it wasn't the VPS that got banned)
  - falls back to direct (no proxy) when the VPS ban has expired
"""
import json
import os
import random
import sqlite3
import subprocess
import time

PROXIES_FILE = "/opt/ares/.gmgn_proxies.json"
STATE_FILE = "/opt/ares/.gmgn_proxy_state.json"
DB = "/opt/ares/wallet_intel/wallet_intel.db"
PROXY_COOLDOWN = 900  # 15 min after a GMGN ban on that exit


def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(st):
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(st, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def ip_banned() -> float:
    """Shared VPS-IP ban (set by scanner/collector via _mark_ban)."""
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
        try:
            row = c.execute("SELECT v FROM state WHERE k='gmgn_ban_until'").fetchone()
            return float(row[0]) if row and row[0] else 0.0
        finally:
            c.close()
    except Exception:
        return 0.0


def _candidates():
    try:
        with open(PROXIES_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("proxies", [])
        if not isinstance(data, list):
            return []
        return data
    except Exception:
        return []


def _pick_proxy():
    """Pick a proxy not in cooldown. Returns (url, proxy_entry) or (None, None)."""
    st = _load_state()
    now = time.time()
    live = [p for p in _candidates()
            if st.get(p.get("server") or str(p), 0) <= now]
    if not live:
        return None, None
    p = random.choice(live)
    return (p if isinstance(p, str) else p.get("server", "")), p


def _mark_proxy_cooldown(proxy_key):
    st = _load_state()
    st[proxy_key] = time.time() + PROXY_COOLDOWN
    _save_state(st)


def _run(cmd, env, timeout):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except Exception as e:
        return None


def run_cli(cmd, timeout=60):
    """Run gmgn-cli. Returns CompletedProcess-like (rc, stdout, stderr).

    Proxy-aware: if the VPS IP is banned, route through a pool proxy with
    per-proxy cooldown + one retry on a different proxy. If the VPS IP is
    NOT banned, run direct (saves the fragile free proxies)."""
    base_env = dict(os.environ)

    # 1) Direct path when the VPS IP is fine
    if ip_banned() <= time.time():
        out = _run(cmd, base_env, timeout)
        if out is None:
            return type("R", (), {"returncode": -1, "stdout": "", "stderr": "exec failed"})
        return out

    # 2) Proxied path — up to 2 attempts with different proxies
    for attempt in (1, 2):
        url, entry = _pick_proxy()
        if not url:
            break
        env = dict(base_env)
        env["HTTPS_PROXY"] = url
        env["HTTP_PROXY"] = url
        out = _run(cmd, env, timeout)
        if out is None:
            _mark_proxy_cooldown(url)
            continue
        err = (out.stderr or "") + (out.stdout or "")
        if "RATE_LIMIT_BANNED" in err or "banned" in err.lower():
            # The PROXY exit got banned — cooldown that proxy, not the VPS.
            _mark_proxy_cooldown(url)
            if attempt == 1:
                continue  # retry on a different proxy
            # both proxies banned: report as a soft failure (no VPS ban write)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": "proxies exhausted"})
        return out

    # 3) No usable proxy — report proxy state, do NOT mark VPS banned
    return type("R", (), {"returncode": 0, "stdout": "", "stderr": "no usable proxy"})


if __name__ == "__main__":
    print("ip_banned:", ip_banned() > 0)
    print("state:", _load_state())
