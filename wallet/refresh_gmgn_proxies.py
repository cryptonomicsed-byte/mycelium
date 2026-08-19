#!/usr/bin/env python3
"""refresh_gmgn_proxies.py — refresh /opt/ares/.gmgn_proxies.json with
GMGN-live free proxies only.

Pipeline:
  1. Pull fresh candidates from ruproxy (residential, fraudScore<=10) + hproxy
  2. TCP-probe them (parallel, short timeout)
  3. GMGN-test each TCP-live proxy via gmgn-cli (banned exits get dropped)
  4. Merge survivors into /opt/ares/.gmgn_proxies.json (keep old live ones)
  5. Prune cooldown state entries for proxies no longer in the pool

Run:  python3 refresh_gmgn_proxies.py [--max N]
Cron:  */15 * * * *  cd /opt/ares/wallet_intel && python3 refresh_gmgn_proxies.py --max 25 >> /opt/ares/logs/proxy_refresh.log 2>&1
"""
import json
import os
import random
import socket
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

PROXIES_FILE = "/opt/ares/.gmgn_proxies.json"
STATE_FILE = "/opt/ares/.gmgn_proxy_state.json"
ENV_FILE = os.path.expanduser("~/.config/gmgn/.env")
TEST_KEY = "gmgn_fc3700f8af1b6ad9e7bd38e79467544f"
MAX_NEW = 25


def get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_candidates():
    cands = []
    try:
        d = get_json("https://ruproxy.to/api/v2/proxies?residential=true&maxFraudScore=10&limit=100")
        for p in d:
            if p.get("status") == 1 and p.get("address") and p.get("port"):
                cands.append({"server": f"http://{p['address']}:{p['port']}",
                              "exit": f"{p.get('country','')} {p.get('isp','')}", "src": "ruproxy"})
    except Exception as e:
        print(f"ruproxy fail: {e}")
    try:
        d = get_json("https://hproxy.com/api/proxy-list?format=json&limit=500")
        items = d if isinstance(d, list) else d.get("data", [])
        for p in items:
            if p.get("is_datacenter", True):
                continue
            if p.get("ip") and p.get("port"):
                cands.append({"server": f"http://{p['ip']}:{p['port']}",
                              "exit": f"{p.get('country_code','')} {p.get('asn_org','')}", "src": "hproxy"})
    except Exception as e:
        print(f"hproxy fail: {e}")
    seen = set()
    out = []
    for c in cands:
        if c["server"] not in seen:
            seen.add(c["server"])
            out.append(c)
    return out


def tcp_alive(server, timeout=6):
    try:
        host, port = server.replace("http://", "").split(":")
        s = socket.create_connection((host, int(port)), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def gmgn_live(server, timeout=35):
    """True if gmgn-cli returns code 0 through this proxy."""
    env = dict(os.environ)
    env["HTTPS_PROXY"] = server
    env["HTTP_PROXY"] = server
    with open(ENV_FILE) as f:
        env_lines = f.read()
    env["GMGN_API_KEY"] = TEST_KEY
    # gmgn-cli reads .env file, so swap it temporarily
    bak = ENV_FILE + ".bak"
    try:
        with open(bak, "w") as f:
            f.write(env_lines)
        with open(ENV_FILE, "w") as f:
            f.write(f"GMGN_API_KEY={TEST_KEY}\nGMGN_PRIVATE_KEY=\n")
        cmd = ["/usr/local/bin/gmgn-cli", "market", "trending", "--chain", "sol",
               "--interval", "1h", "--limit", "1", "--raw"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        ok = out.returncode == 0 and '"code":0' in out.stdout
        return ok, out.stderr[:100] if not ok else ""
    except Exception as e:
        return False, str(e)[:100]
    finally:
        try:
            os.replace(bak, ENV_FILE)
        except Exception:
            pass


def main():
    max_new = MAX_NEW
    if "--max" in sys.argv:
        max_new = int(sys.argv[sys.argv.index("--max") + 1])

    cands = fetch_candidates()
    print(f"[refresh] {len(cands)} candidates fetched")

    alive = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futs = {ex.submit(tcp_alive, c["server"]): c for c in cands}
        for f in as_completed(futs, timeout=60):
            c = futs[f]
            try:
                if f.result():
                    alive.append(c)
            except Exception:
                pass
    print(f"[refresh] {len(alive)} TCP-alive")

    live = []
    for c in random.sample(alive, min(len(alive), max_new * 2)):
        ok, err = gmgn_live(c["server"])
        if ok:
            live.append(c)
        else:
            print(f"  drop {c['server']}: {err[:60]}")
    print(f"[refresh] {len(live)} GMGN-live")

    # merge with existing live entries
    old = []
    try:
        with open(PROXIES_FILE) as f:
            d = json.load(f)
        old = d.get("proxies", d) if isinstance(d, dict) else d
    except Exception:
        pass
    old_servers = {p.get("server") for p in old}
    merged = old + [c for c in live if c["server"] not in old_servers]
    # cap at 40
    merged = merged[:40]
    out = {"proxies": merged, "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "note": "GMGN-live free proxies, refreshed by cron"}
    with open(PROXIES_FILE, "w") as f:
        json.dump(out, f, indent=2)
    os.chmod(PROXIES_FILE, 0o600)
    print(f"[refresh] pool now {len(merged)} proxies -> {PROXIES_FILE}")

    # prune cooldown state for dead servers
    try:
        with open(STATE_FILE) as f:
            st = json.load(f)
        keep = {k: v for k, v in st.items() if k in {p.get("server") for p in merged}}
        with open(STATE_FILE, "w") as f:
            json.dump(keep, f, indent=2)
    except Exception:
        pass


if __name__ == "__main__":
    main()
