#!/usr/bin/env python3
"""mail_provider.py — disposable-email creation + verification-code polling.

Extracted from farm_teamorouter.py (2026-08-18, verified live), where this
logic already proved itself provider-agnostic — farm_solscan.py already
reuses gm_create/gm_check directly. This module makes that reuse explicit
instead of importing from another farm script.

Two providers, GuerrillaMail primary (multi-domain, avoids the single-domain
rate-limiting some services apply to mail.tm's one domain), mail.tm fallback.
Both return a 6-digit numeric verification code, the shape every service
this has been used against (TeamoRouter, Solscan) actually sends. A service
that emails a link instead of a code needs a different poller — not built
here since nothing in this codebase has needed it yet (documented honestly
in account_farm.py rather than guessed at).
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

MAIL_API = "https://api.mail.tm"
PASS = "HermesT3mp!"

GM_API = "https://api.guerrillamail.com/ajax.php"
GM_AGENT = "collector/1.0"
GM_UA = "Mozilla/5.0 (Linux; Android 14) Chrome/126.0 Mobile Safari/537.36"

MailHandle = Tuple[str, str]  # (source_kind, handle) -- ("gm", sid) | ("mailtm", token)


def http_json(url, data=None, method=None, headers=None, timeout=25):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, {"raw": raw[:500]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:500]}
    except Exception as e:
        return 0, {"error": str(e)}


def gm_create() -> Tuple[Optional[str], Optional[str]]:
    dom = random.choice(["guerrillamail.com", "sharklasers.com"])
    r = subprocess.run(["curl", "-s", f"{GM_API}?f=get_email_address&agent={GM_AGENT}&domain={dom}"],
                       capture_output=True, text=True, timeout=15)
    try:
        d = json.loads(r.stdout)
    except Exception:
        return None, None
    return d.get("email_addr"), d.get("sid_token")


def gm_check(sid: str, retries: int = 14, wait: int = 8) -> Optional[str]:
    """Poll GuerrillaMail for the verification code. GM needs browser UA
    (403 without), empty inbox returns empty stdout — handle both."""
    for _ in range(retries):
        time.sleep(wait)
        r = subprocess.run(["curl", "-s", "-A", GM_UA, f"{GM_API}?f=get_email_list&sid_token={sid}"],
                           capture_output=True, text=True, timeout=15)
        if not r.stdout.strip():
            continue
        try:
            d = json.loads(r.stdout)
        except Exception:
            continue
        msgs = d.get("list", []) if isinstance(d, dict) else []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            r2 = subprocess.run(["curl", "-s", "-A", GM_UA,
                                 f"{GM_API}?f=fetch_email&sid_token={sid}&email_id={m.get('mail_id')}"],
                                capture_output=True, text=True, timeout=15)
            if not r2.stdout.strip():
                continue
            try:
                full = json.loads(r2.stdout)
            except Exception:
                continue
            body = full.get("mail_text", "") or full.get("body", "") or ""
            cm = re.search(r"(?:verification code[:\s]*|is[:\s]*|code[:\s]*)(\d{6})\b", body, re.IGNORECASE)
            if not cm:
                cm = re.search(r"\b(\d{6})\b", body)
            if cm:
                return cm.group(1)
    return None


def mail_domains() -> list:
    st, res = http_json(f"{MAIL_API}/domains")
    if st == 200 and isinstance(res, dict):
        return [d.get("domain") for d in res.get("hydra:member", []) if d.get("domain")]
    return []


def fresh_mail() -> Tuple[Optional[str], Optional[MailHandle]]:
    """GuerrillaMail first (multi-domain), mail.tm fallback. Returns
    (email, (source_kind, handle)) where handle = GM sid or mail.tm token."""
    addr, sid = gm_create()
    if addr and sid:
        return addr, ("gm", sid)
    domains = mail_domains()
    if not domains:
        return None, None
    for _ in range(3):
        dom = random.choice(domains)
        addr2 = f"acct-{random.randint(100000, 9999999)}@{dom}"
        st, res = http_json(f"{MAIL_API}/accounts", {"address": addr2, "password": PASS}, method="POST")
        if st in (200, 201):
            st2, res2 = http_json(f"{MAIL_API}/token", {"address": addr2, "password": PASS}, method="POST")
            tok = res2.get("token") if st2 == 200 else None
            if tok:
                return addr2, ("mailtm", tok)
    return None, None


def _poll_mailtm(token: str, retries: int = 12, wait: int = 7) -> Optional[str]:
    """Poll mail.tm for the 6-digit verification code."""
    for _ in range(retries):
        time.sleep(wait)
        st, res = http_json(f"{MAIL_API}/messages", headers={"Authorization": f"Bearer {token}"})
        if st != 200:
            continue
        msgs = res.get("hydra:member", res) if isinstance(res, dict) else res
        if not isinstance(msgs, list):
            continue
        for m in msgs:
            if not isinstance(m, dict):
                continue
            st2, full = http_json(f"{MAIL_API}/messages/{m['id']}",
                                  headers={"Authorization": f"Bearer {token}"})
            if st2 != 200 or not isinstance(full, dict):
                continue
            body = ""
            for k in ("text", "intro", "html"):
                v = full.get(k)
                if isinstance(v, str):
                    body += v + " "
                elif isinstance(v, list):
                    body += " ".join(str(x) for x in v) + " "
            cm = re.search(r"(?:verification code[:\s]*|is[:\s]*|code[:\s]*)(\d{6})\b", body, re.IGNORECASE)
            if not cm:
                cm = re.search(r"\b(\d{6})\b", body)
            if cm:
                return cm.group(1)
    return None


def poll_code(mail_handle: MailHandle) -> Optional[str]:
    """Dispatch to the right inbox poller based on source kind."""
    kind, handle = mail_handle
    if kind == "gm":
        return gm_check(handle)
    return _poll_mailtm(handle)
