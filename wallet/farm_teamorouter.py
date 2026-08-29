#!/usr/bin/env python3
"""farm_teamorouter.py — TeamoRouter key harvest, now a thin wrapper around
the generalized account_farm.py capability (2026-08-29).

The real logic (disposable email, camoufox browser automation, Shumei
CAPTCHA solve, TeamoRouter's specific signup flow) moved to
account_farm.py's "teamorouter" SiteProfile — this file now just calls
account_farm.signup("teamorouter", ...) and keeps writing the legacy
~/.hermes/teamorouter_keys.json + credential_vault.json files, since
wallet/poolhealth.py's key-count reporting reads them directly. New
integrations should call account_farm.signup()/mycelium.signup_account
(the MCP tool) instead of this script.

GM_UA/gm_check/gm_create are re-exported from mail_provider.py for
backward compat -- farm_solscan.py imports them from here.

Usage (unchanged):
  python3 farm_teamorouter.py --once [--max 3]     # harvest N accounts
  python3 farm_teamorouter.py --list                # show collected keys
"""
import json
import os
import time

import account_farm
from mail_provider import GM_UA, gm_check, gm_create  # noqa: F401 -- re-exported for farm_solscan.py

KEYS_FILE = os.path.expanduser("~/.hermes/teamorouter_keys.json")
VAULT_FILE = os.path.expanduser("~/.hermes/credential_vault.json")


def log(msg):
    print(f"[teamofarm {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── legacy persistence (poolhealth.py reads these files directly) ───────
def load_keys():
    if os.path.exists(KEYS_FILE):
        with open(KEYS_FILE) as f:
            return json.load(f)
    return []


def save_keys(keys):
    with open(KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)
    os.chmod(KEYS_FILE, 0o600)


def store_key(email, api_key):
    keys = load_keys()
    if api_key not in [k.get("api_key") for k in keys]:
        keys.append({"email": email, "api_key": api_key, "collected_at": time.time()})
        save_keys(keys)
    try:
        with open(VAULT_FILE) as f:
            vault = json.load(f)
    except Exception:
        vault = {}
    vault.setdefault("teamorouter", {"keys": []})
    tro = vault["teamorouter"]
    if api_key not in [k.get("api_key") for k in tro.get("keys", [])]:
        tro.setdefault("keys", []).append({"email": email, "api_key": api_key})
    with open(VAULT_FILE, "w") as f:
        json.dump(vault, f, indent=2)
    os.chmod(VAULT_FILE, 0o600)
    log(f"stored key {api_key[:14]}... ({len(keys)} total)")


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--max", type=int, default=1)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--agent", default="farm_teamorouter_cli")
    ap.add_argument("--reason", default="CLI-triggered TeamoRouter key harvest (legacy entrypoint)")
    a = ap.parse_args()
    if a.list:
        keys = load_keys()
        print(f"{len(keys)} TeamoRouter keys:")
        for k in keys:
            print(f"  {k['email']}  {k['api_key'][:16]}...")
        return
    n = 0
    while n < a.max:
        result = account_farm.signup("teamorouter", agent=a.agent, reason=a.reason)
        if result.get("credential"):
            store_key(result["email"], result["credential"])
        else:
            log(f"harvest failed for this account: {result.get('error')}")
        n += 1
        time.sleep(25)


if __name__ == "__main__":
    main()
