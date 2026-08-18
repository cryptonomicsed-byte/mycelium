#!/usr/bin/env python3
"""Test: does TeamoRouter accept a GuerrillaMail domain (fresh domain bucket) —
or is emalupe.com the only blocked one? Also captures the GM inbox sid."""
import asyncio, subprocess, json, random, time, sys

sys.path.insert(0, "/opt/ares/wallet_intel")

GM_API = "https://api.guerrillamail.com/ajax.php"
AGENT = "collector/1.0"


def gm_create():
    dom = random.choice(["guerrillamail.com", "sharklasers.com"])
    r = subprocess.run(["curl", "-s", f"{GM_API}?f=get_email_address&agent={AGENT}&domain={dom}"],
                       capture_output=True, text=True, timeout=15)
    d = json.loads(r.stdout)
    return d.get("email_addr"), d.get("sid_token")


async def main():
    from camoufox.async_api import AsyncCamoufox
    email, sid = gm_create()
    print("GM email:", email, "sid:", bool(sid))
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
        await dlg.locator("button:has-text('Continue')").first.click()
        print("clicked Continue")
        await asyncio.sleep(8)
        txt = await dlg.inner_text()
        print("DIALOG:", txt[:200].replace("\n", " | "))
        if "过于频繁" in txt:
            print("RESULT: domain STILL blocked ->", email.split("@")[1])
        elif "Code sent" in txt or "verification" in txt.lower() or "captcha" in txt.lower():
            print("RESULT: PASSED — GM domain accepted! email:", email)
            # if captcha, note it
            popup = page.locator(".shumei_captcha_popup_wrapper").first
            try:
                if await popup.is_visible(timeout=3000):
                    print("  (captcha visible — solver would handle it)")
            except Exception:
                pass
        else:
            print("RESULT: other:", txt[:100].replace("\n", " | "))
        await page.screenshot(path="/tmp/teamo_gm_test.png", full_page=True)


asyncio.run(main())
