#!/usr/bin/env python3
"""Full GM-domain round trip: Continue -> captcha (if any) -> 'Code sent' ->
poll GuerrillaMail inbox for the code."""
import asyncio, subprocess, json, random, time, re, sys

sys.path.insert(0, "/opt/ares/wallet_intel")

GM_API = "https://api.guerrillamail.com/ajax.php"
AGENT = "collector/1.0"
UA = "Mozilla/5.0 (Linux; Android 14) Chrome/126.0 Mobile Safari/537.36"


def gm_create():
    dom = random.choice(["guerrillamail.com", "sharklasers.com"])
    r = subprocess.run(["curl", "-s", f"{GM_API}?f=get_email_address&agent={AGENT}&domain={dom}"],
                       capture_output=True, text=True, timeout=15)
    d = json.loads(r.stdout)
    return d.get("email_addr"), d.get("sid_token")


def gm_check(sid):
    r = subprocess.run(["curl", "-s", "-A", UA, f"{GM_API}?f=get_email_list&sid_token={sid}"],
                       capture_output=True, text=True, timeout=15)
    if not r.stdout.strip():
        return None, None
    try:
        d = json.loads(r.stdout)
    except Exception:
        return None, None
    msgs = d.get("list", []) if isinstance(d, dict) else []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        mid = m.get("mail_id")
        r2 = subprocess.run(["curl", "-s", "-A", UA,
                             f"{GM_API}?f=fetch_email&sid_token={sid}&email_id={mid}"],
                            capture_output=True, text=True, timeout=15)
        if not r2.stdout.strip():
            continue
        try:
            full = json.loads(r2.stdout)
        except Exception:
            continue
        text = full.get("mail_text", "") or full.get("body", "") or ""
        cm = re.search(r"(?:verification code[:\s]*|is[:\s]*|code[:\s]*)(\d{6})\b", text, re.IGNORECASE)
        if not cm:
            cm = re.search(r"\b(\d{6})\b", text)
        if cm:
            return cm.group(1), text[:200]
    return None, None


async def main():
    from camoufox.async_api import AsyncCamoufox
    import shumei_solver
    email, sid = gm_create()
    print("GM email:", email)
    async with AsyncCamoufox(headless=True) as fox:
        page = await fox.new_page()
        await page.goto("https://teamorouter.com/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(4)
        await page.locator("text=Get API key").first.click()
        await asyncio.sleep(2)
        el = page.locator("input[placeholder='name@example.com']").first
        await el.click()
        await el.press_sequentially(email, delay=25)
        await asyncio.sleep(1)
        dlg = page.locator("[role=dialog]").last
        btn = dlg.locator("button:has-text('Continue')").first
        await btn.click()
        print("clicked Continue")
        await asyncio.sleep(6)
        # captcha?
        popup = page.locator(".shumei_captcha_popup_wrapper").first
        try:
            if await popup.is_visible(timeout=4000):
                print("captcha visible — solving")
                ok = await shumei_solver.solve(page, popup, max_attempts=3)
                print("solve:", ok)
        except Exception:
            pass
        await asyncio.sleep(2)
        txt = await dlg.inner_text()
        print("DIALOG:", txt[:180].replace("\n", " | "))
        # poll GM inbox for the code (up to ~2 min)
        got = None
        for i in range(12):
            time.sleep(8)
            code, snippet = gm_check(sid)
            if code:
                got = (code, snippet)
                break
        if got:
            print("CODE:", got[0])
            print("SNIPPET:", got[1][:160])
        else:
            print("no code arrived in GM inbox")
        await page.screenshot(path="/tmp/teamo_gm_full.png", full_page=True)


asyncio.run(main())
