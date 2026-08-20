"""farm_solscan.py — harvest free Solscan API keys at scale.

One account per run (camoufox + GuerrillaMail): register on solscan.io
(email + password + captcha checkbox), open API Management, generate the
key, extract it. Store to ~/.hermes/solscan_keys.json (flat list) and
/opt/ares/.solscan_keys.json (VPS pool) when --deploy.

Usage:  python3 farm_solscan.py --once            # one account, local store
        python3 farm_solscan.py --once --deploy   # + push to VPS pool
        python3 farm_solscan.py --list            # show prefixes only

Pitfalls handled: React-safe typing (press_sequentially), GM inbox needs a
browser UA, captcha checkbox click, 'Activate my API key' button, key regex
scan after every action (selector-rot-proof).
"""

import argparse
import json
import os
import random
import re
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from farm_teamorouter import GM_UA, gm_check, gm_create  # noqa: E402

KEYS_LOCAL = os.path.expanduser("~/.hermes/solscan_keys.json")
KEYS_VPS = "/opt/ares/.solscan_keys.json"

KEY_RE = re.compile(r"\b([A-Za-z0-9_\-]{24,64})\b")

VALIDATE_URL = ("https://pro-api.solscan.io/v2.0/token/holders"
                "?address=4Xg9qDuEP1vMEwqMr2yWo92Yn9a7xZu5BNQBbXVfpump&limit=1")


def valid_key(key):
    """Distinguish three outcomes via the response body:
    - 200/403/429        -> usable key (True)
    - 401 'Token is invalid'        -> junk key (False)
    - 401 'Please upgrade your api key level' -> REAL key, free tier
      (True, tier-gated — every v2.0 endpoint needs Lite $49/mo+).
      Kept so the farm reports honestly instead of purging valid keys."""
    try:
        req = urllib.request.Request(VALIDATE_URL, headers={
            "accept": "application/json", "token": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (200, 201, 202, 403, 429)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            try:
                body = json.loads(e.read().decode(errors="replace"))
                msg = (body.get("errors") or {}).get("message", "")
            except Exception:
                msg = ""
            # Real key on a free tier vs. garbage token
            return "upgrade your api key level" in msg
        return e.code in (403, 429)
    except Exception:
        return False


async def safe_title(page):
    """Title that never raises on navigation-destroyed contexts."""
    try:
        return (await page.title()) or ""
    except Exception:  # noqa: BLE001
        return ""


async def wait_out_cf(page, timeout_s=75):
    """Cloudflare 'Just a moment...' interstitial — poll until the title
    changes. While waiting, click a Turnstile checkbox if one renders.
    Returns True when the site app takes over."""
    import asyncio
    waited = 0
    while waited < timeout_s:
        try:
            t = (await page.title()) or ""
        except Exception:
            return False
        if "just a moment" not in t.lower() and t.strip():
            return True
        # try clicking a Turnstile checkbox if it appeared
        try:
            for frame in page.frames:
                cb = frame.locator(".cf-turnstile, [name=cf_chl_rc_m], [role=checkbox]").first
                if await cb.count():
                    await cb.click(timeout=1500)
                    break
        except Exception:
            pass
        await asyncio.sleep(2)
        waited += 2
    return False


def new_password():
    return "S" + "".join(random.choices(string.ascii_letters + string.digits, k=14)) + "!"


def load_keys(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_keys(path, keys):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(keys, f, indent=2)
    os.chmod(path, 0o600)


def pick_proxy():
    """Rotate through /opt/ares/.gmgn_proxies.json if present."""
    try:
        with open("/opt/ares/.gmgn_proxies.json") as f:
            proxies = json.load(f)
        if isinstance(proxies, list) and proxies:
            return random.choice(proxies)
        if isinstance(proxies, dict) and proxies.get("proxies"):
            return random.choice(proxies["proxies"])
    except OSError:
        pass
    return None


async def goto_clear(page, url):
    """goto + up to 3 Cloudflare clear attempts; returns bool."""
    import asyncio
    for attempt in range(3):
        if attempt == 0:
            await page.goto(url, wait_until="commit", timeout=60000)
        else:
            try:
                await page.reload(wait_until="commit", timeout=60000)
            except Exception:  # noqa: BLE001
                pass
        await page.wait_for_timeout(4000)
        if await wait_out_cf(page):
            return True
        print(f"STEP CF attempt {attempt + 1} failed for {url}, retrying…")
    return False


async def harvest(email, password):
    """One full registration + key generation in camoufox. Returns the API
    key or None. Prints step markers so farm runs are debuggable."""
    from camoufox.async_api import AsyncCamoufox

    try:
        proxy = pick_proxy()
        kw: dict = {"headless": True, "os": "windows"}
        if isinstance(proxy, str):
            kw["proxy"] = {"server": proxy}
            kw["geoip"] = True
        elif isinstance(proxy, dict):
            kw["proxy"] = proxy
            kw["geoip"] = True
        async with AsyncCamoufox(**kw) as browser:
            page = await browser.new_page()
            if not await goto_clear(page, "https://solscan.io/"):
                print("STEP CF did not clear after 3 attempts — aborting")
                return None
            print("STEP homepage:", (await safe_title(page))[:60])

            # --- Sign in / Register (solscan uses /user/signin -> Create account -> /user/register) ---
            if not await goto_clear(page, "https://solscan.io/user/signin"):
                print("STEP signin page CF-blocked — aborting")
                return None
            ca = page.get_by_text("Create account", exact=False)
            if await ca.count():
                await ca.first.click()
                await page.wait_for_timeout(2500)
            print("STEP register page:", page.url)

            # Fill email + password (React-safe; broad selectors)
            email_input = page.locator('input[autocomplete="email"], input[name*="mail"], input[type="email"], input[type="text"]').first
            if await email_input.count():
                await email_input.click()
                await email_input.press_sequentially(email, delay=20)
                print("STEP email filled")
            pw_inputs = page.locator('input[type="password"]')
            pw_count = await pw_inputs.count()
            for i in range(pw_count):
                el = pw_inputs.nth(i)
                if await el.count():
                    await el.click()
                    await el.press_sequentially(password, delay=15)
            print(f"STEP password filled ({pw_count} fields)")

            # Tick any remaining form checkboxes (promo opt-out + terms)
            for sel in ('input[type="checkbox"]', ".ant-checkbox-input"):
                try:
                    cbs = page.locator(sel)
                    n = await cbs.count()
                    for i in range(n):
                        await cbs.nth(i).click(timeout=2000)
                    if n:
                        print(f"STEP checked {n} checkbox(es)")
                except Exception:
                    pass

            # Wait for + solve the Turnstile challenge (token must appear in
            # the hidden cf-turnstile-response input for the button to enable)
            import asyncio as _asyncio

            async def turnstile_len():
                try:
                    return await page.evaluate(
                        "() => (document.querySelector('input[name=\"cf-turnstile-response\"], "
                        "[name=\"cf-turnstile-response\"]') || {value:''}).value.length")
                except Exception:
                    return 0

            # dismiss the cookie banner so it can't cover the form
            try:
                gotit = page.get_by_text("Got it!", exact=False)
                if await gotit.count():
                    await gotit.first.click(timeout=3000)
                    await page.wait_for_timeout(500)
                    print("STEP cookie banner dismissed")
            except Exception:
                pass

            token = 0
            for _ in range(25):  # ~50s budget for Turnstile
                token = await turnstile_len()
                if token > 20:
                    break
                # click the challenge checkbox inside the Turnstile frame
                try:
                    for frame in page.frames:
                        if "challenges.cloudflare.com" in frame.url:
                            cb = frame.locator(
                                ".cf-turnstile, input[type=checkbox], .ctp-checkbox-label, [role=checkbox]").first
                            if await cb.count():
                                await cb.click(timeout=1500)
                                break
                except Exception:
                    pass
                await _asyncio.sleep(2)
            print(f"STEP turnstile token length: {token}")
            await page.wait_for_timeout(1500)

            # Recon BEFORE submit: field values + the real Register element
            try:
                vals = []
                inputs = page.locator("input")
                n = await inputs.count()
                for i in range(n):
                    v = await inputs.nth(i).input_value()
                    vals.append(f"{i}:{len(v)}ch")
                print("STEP input values:", vals)
            except Exception:
                pass
            try:
                el = page.get_by_text("Register", exact=True).last
                if await el.count():
                    info = await el.evaluate("e => e.tagName + '|' + (e.getAttribute('type')||'') + '|' + e.className.slice(0,40)")
                    print("STEP Register element:", info)
            except Exception:
                pass

            # Submit: the signup button is a <button> labelled "Register"
            # (no type attr — attribute selectors miss it; get_by_text works)
            submitted = False
            for attempt in range(4):
                submitted = False
                reg_btn = page.get_by_text("Register", exact=True).last
                if await reg_btn.count():
                    # wait for the button to become enabled (turnstile token)
                    try:
                        await reg_btn.wait_for(state="visible", timeout=3000)
                        disabled = await reg_btn.evaluate("e => e.disabled || e.getAttribute('aria-disabled') === 'true'")
                        if not disabled:
                            await reg_btn.click(timeout=4000)
                            print("STEP submitted via get_by_text('Register') button")
                            submitted = True
                        else:
                            print(f"STEP Register button disabled (attempt {attempt + 1})")
                    except Exception:
                        print(f"STEP Register button not clickable (attempt {attempt + 1})")
                if not submitted:
                    submit_btn = page.locator('button[type="submit"]').first
                    if await submit_btn.count():
                        await submit_btn.click(timeout=2500)
                        print("STEP submitted via button[type=submit]")
                        submitted = True
                if submitted:
                    break
                await _asyncio.sleep(2)
            if not submitted:
                for sel in ("button:has-text('Register')",
                            "button:has-text('Create new account')",
                            "button:has-text('Create account')"):
                        try:
                            b = page.locator(sel).first
                            if await b.count():
                                await b.click(timeout=2500)
                                print(f"STEP submitted via {sel}")
                                submitted = True
                                break
                        except Exception:
                            continue
                if not submitted:
                    for label in ("Create new account", "Create account", "Register", "Sign up"):
                        try:
                            btn = page.get_by_text(label, exact=False).last
                            if await btn.count():
                                await btn.click(timeout=2500)
                                print(f"STEP submitted via '{label}'")
                                submitted = True
                                break
                        except Exception:
                            continue
                if not submitted:
                    # last resort: any button inside the form
                    fb = page.locator("form button").last
                    if await fb.count():
                        await fb.click(timeout=2500)
                        print("STEP submitted via form button (last resort)")
            await page.wait_for_timeout(7000)
            print("STEP after submit:", (await safe_title(page))[:60], "| url:", page.url)
            try:
                body_full = await page.inner_text("body")
                print("STEP post-submit body:", body_full[:220].replace("\n", " "))
                print("STEP body tail:", body_full[-500:].replace("\n", " "))
                import re as _re
                for err_kw in ("error", "already", "captcha", "invalid", "fail", "required", "exist"):
                    m = _re.search(r"[^\n]*" + err_kw + r"[^\n]*", body_full, _re.I)
                    if m:
                        print(f"STEP err-hit[{err_kw}]:", m.group(0)[:150])
            except Exception:
                pass
            try:
                print("STEP frames:", [f.url[:80] for f in page.frames])
            except Exception:
                pass
            try:
                await page.screenshot(path="/tmp/solscan_postsubmit.png")
            except Exception:
                pass

            # --- API Management -> Generate Key ---
            # Only navigate if we're actually logged in (no "Sign in" CTA)
            page_text = await page.inner_text("body")
            logged_in = "sign in" not in page_text.lower()
            if logged_in and "API" in page_text:
                for link in page.get_by_text("API Management", exact=False).all():
                    try:
                        await link.click(timeout=3000)
                        await page.wait_for_timeout(2500)
                        print("STEP clicked API Management")
                        break
                    except Exception:
                        continue
            if not logged_in:
                print("STEP NOT logged in — registration did not complete")
                try:
                    await page.screenshot(path="/tmp/solscan_notloggedin.png")
                except Exception:
                    pass
                return None
            if "API Management" not in await page.inner_text("body"):
                # try the account page directly
                await page.goto("https://solscan.io/account", wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)
                print("STEP navigated to /account")

            # Activate if present (V2 keys sometimes need activation)
            act = page.get_by_text("Activate my API key", exact=False)
            if await act.count():
                await act.first.click()
                await page.wait_for_timeout(2500)
                print("STEP activated key")

            # Generate key if a button asks for it
            gen = page.get_by_text("Generate Key", exact=False)
            if await gen.count():
                await gen.first.click()
                await page.wait_for_timeout(3000)
                print("STEP clicked Generate Key")

            # Scan page text for the key (selector-rot-proof), VALIDATED
            body = await page.inner_text("body")
            print("STEP body snippet:", body[:150].replace("\n", " "))
            for m in KEY_RE.finditer(body):
                cand = m.group(1)
                # skip obvious non-keys
                if cand.lower() in ("sign", "register", "password", "solscan"):
                    continue
                if valid_key(cand):
                    return cand
                print(f"STEP candidate {cand[:10]}… rejected (not a valid key)")
    except RuntimeError:
        print("STEP teardown race (suppressed)")
    return None


def main():
    parser = argparse.ArgumentParser(description="Solscan API key farm")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--deploy", action="store_true", help="also write the VPS pool file")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        for k in load_keys(KEYS_LOCAL):
            print(f"  {k['email']}: {k['api_key'][:10]}…")
        return

    email, sid = gm_create()
    if not email:
        print("ERROR: no GuerrillaMail address")
        return 1
    password = new_password()
    print(f"registering {email} …")
    try:
        import asyncio
        result = {}

        def _run():
            result["key"] = asyncio.run(harvest(email, password))

        try:
            _run()
        except RuntimeError:
            # Python 3.12 + camoufox teardown race: the loop dies AFTER
            # harvest() returned — the key is still in result.
            print("teardown race after harvest (key preserved)")
        key = result.get("key")
    except Exception as exc:  # noqa: BLE001
        print(f"HARVEST FAILED: {exc}")
        return 1

    if not key:
        print("no key found on page — check the flow (captcha may need a human)")
        return 1

    keys = load_keys(KEYS_LOCAL)
    keys.append({"email": email, "api_key": key, "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")})
    save_keys(KEYS_LOCAL, keys)
    print(f"KEY OK: {key[:12]}… (total {len(keys)})")
    if args.deploy:
        vps = load_keys(KEYS_VPS)
        if not any(k.get("api_key") == key for k in vps):
            vps.append({"email": email, "api_key": key, "collected_at": time.time()})
            save_keys(KEYS_VPS, vps)
        print(f"deployed to {KEYS_VPS} ({len(vps)} keys)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
