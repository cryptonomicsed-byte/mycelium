#!/usr/bin/env python3
"""farm_teamorouter.py — full TeamoRouter key harvest with auto Shumei solve.

Flow (all verified live 2026-08-18):
  1. Create mail.tm account
  2. camoufox: open sign-in, fill email, Continue -> Shumei slider pops
  3. auto-solve via shumei_solver (band-NCC gap detection + human drag)
  4. (email code is sent right after the captcha passes)
  5. poll mail.tm for the 6-digit code (same browser session stays open)
  6. enter code -> account auto-registers -> dashboard
  7. extract sk-teamo-... key from the page
  8. store in ~/.hermes/teamorouter_keys.json + vault

Usage:
  python3 farm_teamorouter.py --once [--max 3]     # harvest N accounts
  python3 farm_teamorouter.py --list                # show collected keys
"""
import argparse
import asyncio
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error

MAIL_API = "https://api.mail.tm"
PASS = "HermesT3mp!"
KEYS_FILE = os.path.expanduser("~/.hermes/teamorouter_keys.json")
VAULT_FILE = os.path.expanduser("~/.hermes/credential_vault.json")

KEY_RE = re.compile(r"sk-teamo-[A-Za-z0-9]{20,}")


def log(msg):
    print(f"[teamofarm {time.strftime('%H:%M:%S')}] {msg}", flush=True)


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


# ── persistence ───────────────────────────────────────────────────────
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


# ── mail sources ──────────────────────────────────────────────────────
# GuerrillaMail PRIMARY (multi-domain: guerrillamail.com / sharklasers.com /
# guerrillamailblock.com) — fresh domain buckets beat mail.tm's single
# emalupe.com (which TeamoRouter rate-limits). mail.tm is the fallback.
GM_API = "https://api.guerrillamail.com/ajax.php"
GM_AGENT = "collector/1.0"
GM_UA = "Mozilla/5.0 (Linux; Android 14) Chrome/126.0 Mobile Safari/537.36"


def gm_create():
    dom = random.choice(["guerrillamail.com", "sharklasers.com"])
    r = subprocess.run(["curl", "-s", f"{GM_API}?f=get_email_address&agent={GM_AGENT}&domain={dom}"],
                       capture_output=True, text=True, timeout=15)
    try:
        d = json.loads(r.stdout)
    except Exception:
        return None, None
    return d.get("email_addr"), d.get("sid_token")


def gm_check(sid, retries=14, wait=8):
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


def mail_domains():
    st, res = http_json(f"{MAIL_API}/domains")
    if st == 200 and isinstance(res, dict):
        return [d.get("domain") for d in res.get("hydra:member", []) if d.get("domain")]
    return []


def fresh_mail():
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
        addr2 = f"teamo-{random.randint(100000, 9999999)}@{dom}"
        st, res = http_json(f"{MAIL_API}/accounts", {"address": addr2, "password": PASS}, method="POST")
        if st in (200, 201):
            st2, res2 = http_json(f"{MAIL_API}/token", {"address": addr2, "password": PASS}, method="POST")
            tok = res2.get("token") if st2 == 200 else None
            if tok:
                return addr2, ("mailtm", tok)
    return None, None


def poll_code(mail_handle):
    """Dispatch to the right inbox poller based on source kind."""
    kind, handle = mail_handle
    if kind == "gm":
        return gm_check(handle)
    return _poll_mailtm(handle)


def _poll_mailtm(token, retries=12, wait=7):
    """Poll mail.tm for the 6-digit verification code."""
    for _ in range(retries):
        time.sleep(wait)
        st, res = http_json(f"{MAIL_API}/messages", headers={"Authorization": f"Bearer {token}"})
        if st != 200:
            continue
        msgs = res.get("hydra:member", res) if isinstance(res, dict) else res
        if not isinstance(msgs, list):
            continue
        if msgs:
            log(f"inbox has {len(msgs)} message(s)")
        for m in msgs:
            if not isinstance(m, dict):
                continue
            try:
                log(f"  msg: {str(m.get('subject'))[:80]}")
            except Exception:
                pass
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


# ── the full harvest, ONE browser session ────────────────────────────
async def harvest_one(email: str, mail_handle, use_proxy: bool = False) -> str:
    """Full flow in one camoufox session. Returns the sk-teamo key or ''.
    mail_handle: (source_kind, handle) tuple — ('gm', sid) or ('mailtm', token).
    use_proxy: retry path — route through the shared proxy pool to beat the
    per-IP code-send cooldown (free proxies are slow, so direct is preferred
    and the proxy is only used as the fallback attempt)."""
    from camoufox.async_api import AsyncCamoufox
    import shumei_solver

    key = ""
    kw: dict = {"headless": True}
    if use_proxy:
        try:
            import gmgn_cli_proxy as _g
            proxy, _ = _g._pick_proxy()
            if proxy:
                kw["proxy"] = {"server": proxy}
                kw["geoip"] = True
                log(f"proxy retry via {proxy[:40]}...")
            else:
                log("no usable proxy — staying direct")
        except Exception:
            log("proxy pick failed — staying direct")
    async with AsyncCamoufox(**kw) as fox:
        page = await fox.new_page()
        await page.goto("https://teamorouter.com/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(3)
        await page.locator("text=Get API key").first.click()
        await asyncio.sleep(2)

        # 1. fill email
        el = page.locator("input[placeholder='name@example.com']").first
        await el.click()
        await el.press_sequentially(email, delay=25)
        await asyncio.sleep(1)
        dlg = page.locator("[role=dialog]").last
        await dlg.locator("button:has-text('Continue')").first.click()
        log("clicked Continue")
        await asyncio.sleep(3)

        # 2. solve captcha if it pops (may take a moment to render)
        popup = page.locator(".shumei_captcha_popup_wrapper").first
        captcha_seen = False
        for _w in range(6):
            try:
                if await popup.is_visible(timeout=3000):
                    captcha_seen = True
                    break
            except Exception:
                pass
            await asyncio.sleep(2)
        if captcha_seen:
            log("captcha visible — solving")
            ok = await shumei_solver.solve(page, popup, max_attempts=4)
            log(f"captcha solve: {ok}")
            if not ok:
                log("captcha failed 4x — abort this account")
                return ""
        else:
            log("no captcha this run — proceeding")
            try:
                txt = await dlg.inner_text()
                log("DIALOG: " + txt[:300].replace("\n", " | "))
            except Exception:
                pass

        # 3. after solve (or directly), the code email is sent — poll inbox
        log("polling inbox for verification code...")
        code = poll_code(mail_handle)
        if not code:
            log("no code arrived")
            await page.screenshot(path="/tmp/teamo_no_code.png", full_page=True)
            return ""
        log(f"code: {code}")

        # 4. enter the code (same session)
        await asyncio.sleep(2)
        entered = False
        for sel in ["input[inputmode='numeric']", "input[placeholder*='code' i]",
                    "input[placeholder*='verification' i]", "input[placeholder*='6-digit' i]",
                    "[role=dialog] input"]:
            try:
                inp = page.locator(sel).first
                if await inp.is_visible(timeout=3000):
                    await inp.click()
                    await inp.press_sequentially(code, delay=60)
                    entered = True
                    log(f"code entered via {sel}")
                    await asyncio.sleep(1)
                    for btn in ["button:has-text('Verify')", "button:has-text('Confirm')",
                                "button:has-text('Sign in')", "button[type='submit']"]:
                        try:
                            b = page.locator(btn).first
                            if await b.is_visible(timeout=1200):
                                await b.click()
                                log(f"submitted via {btn}")
                                break
                        except Exception:
                            continue
                    await asyncio.sleep(4)
                    break
            except Exception:
                continue
        if not entered:
            log("no code input found")
            try:
                dlg2 = page.locator("[role=dialog]").last
                print((await dlg2.inner_text())[:400])
            except Exception:
                pass
            await page.screenshot(path="/tmp/teamo_no_code_input.png", full_page=True)
            return ""

        # 5. extract key: scan page, then try keys pages
        await asyncio.sleep(2)
        body_txt = await page.locator("body").inner_text()
        m = KEY_RE.search(body_txt)
        if m:
            key = m.group(0)
        if not key:
            for url in ["https://teamorouter.com/dashboard", "https://teamorouter.com/keys",
                        "https://teamorouter.com/api-keys", "https://teamorouter.com/settings",
                        "https://teamorouter.com/console"]:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(2)
                    body_txt = await page.locator("body").inner_text()
                    m = KEY_RE.search(body_txt)
                    if m:
                        key = m.group(0)
                        break
                except Exception:
                    continue
        await page.screenshot(path="/tmp/teamo_dashboard.png", full_page=True)
        log(f"key extracted: {'YES ' + key[:14] + '...' if key else 'NO'}")
        return key


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--max", type=int, default=1)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        keys = load_keys()
        print(f"{len(keys)} TeamoRouter keys:")
        for k in keys:
            print(f"  {k['email']}  {k['api_key'][:16]}...")
        return
    n = 0
    while n < a.max:
        addr, handle = fresh_mail()
        if not addr:
            log("mail sources exhausted — stop")
            break
        log(f"mail: {addr} (source {handle[0] if handle else '?'})")
        key = asyncio.run(harvest_one(addr, handle))
        if not key.startswith("sk-teamo"):
            # likely the per-IP code-send cooldown (发送过于频繁) — retry
            # once through the proxy pool for a fresh IP
            log("direct harvest failed — retrying via proxy")
            time.sleep(5)
            key = asyncio.run(harvest_one(addr, handle, use_proxy=True))
        if key.startswith("sk-teamo"):
            store_key(addr, key)
        else:
            log("harvest failed for this account")
        n += 1
        time.sleep(25)


if __name__ == "__main__":
    main()
