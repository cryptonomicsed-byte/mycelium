#!/usr/bin/env python3
"""Check mail.tm delivery: create account, request Teamo code via API (captcha
needed... can't) — instead check if the earlier probe inboxes got mail.
Simpler: query mail.tm for any recent messages on a fresh account after the
UI flow, using the same inbox the probe used."""
import json, os, sys, time, urllib.request, urllib.error, re, random

MAIL_API = "https://api.mail.tm"
PASS = "HermesT3mp!"

def http_json(url, data=None, method=None, headers=None, timeout=20):
    h = {"Content-Type": "application/json"}
    if headers: h.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode(errors="replace")
            try: return r.status, json.loads(raw)
            except Exception: return r.status, {"raw": raw[:300]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try: return e.code, json.loads(raw)
        except Exception: return e.code, {"raw": raw[:300]}
    except Exception as e:
        return 0, {"error": str(e)}

# 1. create account
domains = http_json(f"{MAIL_API}/domains")[1]
doms = [d.get("domain") for d in domains.get("hydra:member", [])] if isinstance(domains, dict) else []
print("domains:", doms)
addr = f"deliv-{random.randint(100000,9999999)}@{random.choice(doms)}"
st, res = http_json(f"{MAIL_API}/accounts", {"address": addr, "password": PASS}, method="POST")
print("create:", st, addr)
st, res = http_json(f"{MAIL_API}/token", {"address": addr, "password": PASS}, method="POST")
tok = res.get("token")
print("token:", bool(tok))
# 2. wait 60s and poll — but no code will arrive without signup; instead just
# verify the inbox API works and shows 0 messages cleanly
for i in range(3):
    time.sleep(10)
    st, res = http_json(f"{MAIL_API}/messages", headers={"Authorization": f"Bearer {tok}"})
    msgs = res.get("hydra:member", res) if isinstance(res, dict) else res
    print(f"poll {i}: status={st} msgs={len(msgs) if isinstance(msgs, list) else msgs}")
    if isinstance(msgs, list) and msgs:
        for m in msgs[:2]:
            if isinstance(m, dict):
                print("  subject:", m.get("subject"))
