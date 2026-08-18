#!/usr/bin/env python3
"""farm_gmgn_keys.py — grow a GMGN API key pool.

GMGN keys are bound to Ed25519 keypairs (NOT emails — no mail.tm flow here).
Per key:
  1. Generate an ed25519 keypair (same crypto gmgn-cli config uses)
  2. Build the pre-filled creation link: https://gmgn.ai/ai/generateapi?pbk=<pubkey>
  3. User clicks the link in a browser (Cloudflare + GMGN login required),
     GMGN issues an API key bound to that public key
  4. User pastes the key back; we store key + private key in the pool

Pool file: ~/.hermes/gmgn_pool.json  (local, Fold 4)
   {"pending": [{"name","pubkey","privkey_pem","link","created_at"}],
    "keys":    [{"name","api_key","privkey_pem","added_at"}]}

Deploy keys to the VPS scanner pool: /opt/ares/.gmgn_keys.json
   [{"api_key": "...", "privkey_pem": "..."}]

NOTE: the ban GMGN issues is per-IP. Key rotation spreads the per-key
quota (RATE_LIMIT_EXCEEDED); the IP ban (RATE_LIMIT_BANNED) needs proxy
rotation — gmgn-cli honors HTTPS_PROXY, and the scanner's gmgn_* helpers
accept a proxy per call. See wallet/scanner.py.

Usage:
    python3 farm_gmgn_keys.py --gen 3        # generate 3 keypairs + links
    python3 farm_gmgn_keys.py --list         # show pending links + keys
    python3 farm_gmgn_keys.py --add <KEY>    # add a collected key (uses last pending pubkey)
    python3 farm_gmgn_keys.py --deploy       # write /opt/ares/.gmgn_keys.json on VPS via ssh
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

POOL = os.path.expanduser("~/.hermes/gmgn_pool.json")
VPS_KEYS = "/opt/ares/.gmgn_keys.json"
VPS_HOST = "root@2.25.70.156"


def load_pool():
    if os.path.exists(POOL):
        with open(POOL) as f:
            return json.load(f)
    return {"pending": [], "keys": []}


def save_pool(pool):
    os.makedirs(os.path.dirname(POOL), exist_ok=True)
    with open(POOL, "w") as f:
        json.dump(pool, f, indent=2)
    os.chmod(POOL, 0o600)


def gen_keypair():
    """ed25519 keypair, PEM, same as gmgn-cli config's crypto.generateKeyPairSync."""
    # try python cryptography first, fall back to openssl CLI
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        priv = Ed25519PrivateKey.generate()
        priv_pem = priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        pub_pem = priv.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        return priv_pem, pub_pem
    except ImportError:
        import subprocess
        out = subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-outform", "PEM"],
                             capture_output=True, text=True)
        if out.returncode != 0:
            sys.exit(f"no cryptography lib and openssl failed: {out.stderr}")
        priv_pem = out.stdout
        out2 = subprocess.run(["openssl", "pkey", "-pubout", "-inform", "PEM"],
                              input=priv_pem, capture_output=True, text=True)
        pub_pem = out2.stdout
        return priv_pem, pub_pem


def build_link(pub_pem):
    import urllib.parse
    return "https://gmgn.ai/ai/generateapi?pbk=" + urllib.parse.quote(pub_pem.strip())


def cmd_gen(n):
    pool = load_pool()
    for i in range(n):
        priv_pem, pub_pem = gen_keypair()
        name = f"gmgn-{time.strftime('%Y%m%d')}-{len(pool['pending']) + len(pool['keys']) + 1}"
        link = build_link(pub_pem)
        pool["pending"].append({
            "name": name, "pubkey": pub_pem.strip(), "privkey_pem": priv_pem,
            "link": link, "created_at": datetime.now(timezone.utc).isoformat(),
        })
    save_pool(pool)
    print(f"generated {n} keypair(s) — {len(pool['pending'])} pending, {len(pool['keys'])} active")
    for p in pool["pending"][-n:]:
        print(f"\n  {p['name']}\n  {p['link']}")


def cmd_list():
    pool = load_pool()
    print(f"pending: {len(pool['pending'])}   active keys: {len(pool['keys'])}")
    for i, p in enumerate(pool["pending"], 1):
        print(f"  [{i}] {p['name']}  {p['link'][:80]}...")
    for k in pool["keys"]:
        print(f"  KEY {k['name']}  {k['api_key'][:12]}...")


def cmd_add(api_key):
    pool = load_pool()
    if not pool["pending"]:
        sys.exit("no pending keypair — run --gen first")
    p = pool["pending"].pop(0)  # FIFO: first generated, first activated
    pool["keys"].append({
        "name": p["name"], "api_key": api_key.strip(),
        "privkey_pem": p["privkey_pem"], "added_at": datetime.now(timezone.utc).isoformat(),
    })
    save_pool(pool)
    print(f"added key {api_key[:12]}... to {p['name']} — {len(pool['keys'])} active")


def cmd_deploy():
    pool = load_pool()
    if not pool["keys"]:
        sys.exit("no active keys to deploy")
    import subprocess
    keys = [{"api_key": k["api_key"], "privkey_pem": k["privkey_pem"]} for k in pool["keys"]]
    tmp = os.path.expanduser("~/.hermes/.gmgn_keys.tmp.json")
    with open(tmp, "w") as f:
        json.dump(keys, f, indent=2)
    os.chmod(tmp, 0o600)
    r = subprocess.run(["scp", "-q", tmp, f"{VPS_HOST}:{VPS_KEYS}"], capture_output=True, text=True)
    os.remove(tmp)
    if r.returncode != 0:
        sys.exit(f"scp failed: {r.stderr}")
    print(f"deployed {len(keys)} key(s) to {VPS_HOST}:{VPS_KEYS}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", type=int, help="generate N keypairs + links")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--add", metavar="KEY", help="add a collected API key")
    ap.add_argument("--deploy", action="store_true")
    a = ap.parse_args()
    if a.gen:
        cmd_gen(a.gen)
    elif a.list:
        cmd_list()
    elif a.add:
        cmd_add(a.add)
    elif a.deploy:
        cmd_deploy()
    else:
        ap.print_help()
