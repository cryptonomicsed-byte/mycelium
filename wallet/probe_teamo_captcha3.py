#!/usr/bin/env python3
"""Probe 3: fill email, hit Continue, capture the Shumei captcha widget."""
import asyncio, time, json


async def main():
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(headless=True) as fox:
        page = await fox.new_page()
        await page.goto("https://teamorouter.com/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(3)
        await page.locator("text=Get API key").first.click()
        await asyncio.sleep(2)
        # the email input is type=None, placeholder=name@example.com
        el = page.locator("input[placeholder='name@example.com']").first
        await el.wait_for(state="visible", timeout=8000)
        await el.fill("teamo-farm-1@emalupe.com")
        print("email filled")
        await asyncio.sleep(1)
        # click Continue inside the dialog (there are 2 dialogs; pick the one with the form)
        for btn in ["button:has-text('Continue')"]:
            try:
                b = page.locator(btn).first
                if await b.is_visible(timeout=2000):
                    await b.click()
                    print("clicked Continue")
                    break
            except Exception:
                pass
        # wait and watch for captcha iframe/widget
        for i in range(6):
            await asyncio.sleep(2)
            frames = page.frames
            for f in frames:
                if "captcha" in f.url.lower() or "sm" in f.url.lower() or "shumei" in f.url.lower() or "nc" in f.url.lower():
                    print("CAPTCHA FRAME:", f.url[:150])
            html = await page.content()
            for probe in ["smcaptcha", "shumei", "captcha_required", "X-Captcha", "slider", "nc_", "initSMCaptcha"]:
                if probe.lower() in html.lower():
                    print("HTML HIT:", probe)
            # check for visible iframes
            try:
                n = await page.locator("iframe").count()
                if n:
                    print(f"  iframes visible: {n}")
            except Exception:
                pass
            # any error text?
            try:
                body_txt = await page.locator("body").inner_text()
                for line in body_txt.split("\n"):
                    if any(k in line.lower() for k in ("captcha", "verify", "code", "sent", "error")):
                        print("  TEXT:", line.strip()[:120])
            except Exception:
                pass
        await page.screenshot(path="/tmp/teamo_captcha_final.png", full_page=True)
        print("final shot /tmp/teamo_captcha_final.png")


asyncio.run(main())
