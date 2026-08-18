#!/usr/bin/env python3
"""Probe 5: trigger Shumei properly (React-safe typing + Enter) and capture
the challenge widget — drag slider or click puzzle?"""
import asyncio


async def main():
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(headless=True) as fox:
        page = await fox.new_page()
        await page.goto("https://teamorouter.com/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(3)
        await page.locator("text=Get API key").first.click()
        await asyncio.sleep(2)

        # React-safe fill: click, then type char by char
        el = page.locator("input[placeholder='name@example.com']").first
        await el.click()
        await el.press_sequentially("teamo-farm-2@emalupe.com", delay=30)
        print("typed email, value:", await el.input_value())
        await asyncio.sleep(1)

        # find Continue INSIDE the sign-in dialog (last dialog)
        dlg = page.locator("[role=dialog]").last
        btn = dlg.locator("button:has-text('Continue')").first
        await btn.click()
        print("clicked Continue in dialog")
        await asyncio.sleep(6)

        # check the shumei popup visibility
        popup = page.locator(".shumei_captcha_popup_wrapper").first
        try:
            vis = await popup.is_visible()
            print("shumei popup visible:", vis)
        except Exception as e:
            print("popup check err:", e)

        # dump the shumei container HTML structure
        html = await page.locator("[class*=shumei]").first.evaluate("(el) => el.outerHTML")
        print("SHUMEI HTML:", html[:1500])

        # screenshot the widget area
        try:
            await popup.screenshot(path="/tmp/teamo_shumei_widget.png")
            print("widget shot /tmp/teamo_shumei_widget.png")
        except Exception as e:
            print("widget shot err:", e)
        # full page
        await page.screenshot(path="/tmp/teamo_probe5.png", full_page=True)

        # look for canvas / img elements inside shumei (challenge type)
        for sel in ["canvas", "img", "[class*=slider]", "[class*=bg]", "[class*=puzzle]"]:
            try:
                n = await popup.locator(sel).count()
                if n:
                    print(f"  widget {sel}: {n}")
            except Exception:
                pass


asyncio.run(main())
