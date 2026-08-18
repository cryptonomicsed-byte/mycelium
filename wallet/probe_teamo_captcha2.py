#!/usr/bin/env python3
"""Probe 2: trigger the Shumei captcha in camoufox and inspect its structure."""
import asyncio, time, json


async def main():
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(headless=True) as fox:
        page = await fox.new_page()
        await page.goto("https://teamorouter.com/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(4)
        # click Get API key
        await page.locator("text=Get API key").first.click()
        await asyncio.sleep(3)
        print("URL:", page.url)
        # find email input
        inputs = await page.locator("input").all()
        print("inputs:", len(inputs))
        for i, inp in enumerate(inputs):
            t = await inp.get_attribute("type")
            ph = await inp.get_attribute("placeholder")
            print(f"  [{i}] type={t} placeholder={ph}")
        # fill first email-looking input
        email = "teamo-farm-1@emalupe.com"
        filled = False
        for inp in inputs:
            t = (await inp.get_attribute("type")) or ""
            if "email" in t:
                await inp.fill(email)
                filled = True
                break
        if not filled:
            for inp in inputs:
                ph = (await inp.get_attribute("placeholder")) or ""
                if "mail" in ph.lower():
                    await inp.fill(email)
                    filled = True
                    break
        print("filled email:", filled)
        # look for a send-code / continue button
        for sel in ["text=Send code", "text=Continue", "text=Get code", "text=Sign in",
                    "button:has-text('Send')", "button:has-text('Continue')",
                    "button:has-text('Get verification')"]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=1200):
                    print("CLICK:", sel)
                    await el.click()
                    await asyncio.sleep(4)
                    break
            except Exception:
                continue
        await asyncio.sleep(2)
        # now scan for captcha iframe/widget
        frames = page.frames
        print("frames:", len(frames))
        for f in frames:
            print("  frame url:", f.url[:120])
        html = await page.content()
        for probe in ["smcaptcha", "shumei", "captcha", "X-Captcha", "slider",
                      "initSMCaptcha", "nc_", "iframe"]:
            if probe.lower() in html.lower():
                print("HTML HIT:", probe)
        # screenshot
        await page.screenshot(path="/tmp/teamo_captcha_triggered.png", full_page=True)
        print("shot /tmp/teamo_captcha_triggered.png")
        # dump visible text of any overlay/modal
        for sel in ["[class*=captcha]", "[class*=Captcha]", "[class*=modal]", "[class*=Modal]", "[role=dialog]"]:
            try:
                n = await page.locator(sel).count()
                if n:
                    print(f"LOCATOR {sel}: {n}")
                    for i in range(min(n, 3)):
                        txt = await page.locator(sel).nth(i).inner_text()
                        print(f"   [{i}] {txt[:200]}")
            except Exception:
                pass


asyncio.run(main())
