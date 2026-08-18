#!/usr/bin/env python3
"""Probe TeamoRouter signup with camoufox (async) — see the Shumei widget."""
import asyncio, time, sys

CHROMIUM = "/root/.cache/ms-playwright/chromium-1148/chrome-linux/chrome"


async def main():
    try:
        from camoufox.async_api import AsyncCamoufox
        async with AsyncCamoufox(headless=True) as fox:
            page = await fox.new_page()
            await page.goto("https://teamorouter.com/", wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(4)
            print("CAMOUFOX LOADED:", page.url)
            for sel in ["text=Get API key", "text=Sign up", "text=Get started",
                        "button:has-text('Get API')", "text=Login", "text=Sign in"]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=1500):
                        print("FOUND:", sel)
                        await el.click()
                        await asyncio.sleep(3)
                        break
                except Exception:
                    continue
            await asyncio.sleep(3)
            for sel in ["iframe", "input", "button", "[class*=captcha]", "[class*=Captcha]"]:
                try:
                    n = await page.locator(sel).count()
                    if n:
                        print(f"{sel}: {n}")
                except Exception:
                    pass
            await page.screenshot(path="/tmp/teamo_camoufox.png")
            print("shot /tmp/teamo_camoufox.png | URL:", page.url)
            html = await page.content()
            for probe in ["smcaptcha", "shumei", "captcha", "X-Captcha", "slider", "nc_", "initSMCaptcha"]:
                if probe.lower() in html.lower():
                    print("HTML HIT:", probe)
    except Exception as e:
        print("camoufox failed:", type(e).__name__, str(e)[:300])


asyncio.run(main())
