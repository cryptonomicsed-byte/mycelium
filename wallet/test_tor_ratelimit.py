#!/usr/bin/env python3
"""Test: fresh Tor exit per attempt — does it bypass the per-IP rate limit
on TeamoRouter's code send? Camoufox accepts the SOCKS5 proxy."""
import asyncio, sys, time

sys.path.insert(0, "/opt/ares/wallet_intel")


async def main():
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(headless=True, proxy={"server": "socks5://127.0.0.1:9050"}) as fox:
        page = await fox.new_page()
        # 1. confirm exit IP (long timeout — Tor is slow)
        try:
            await page.goto("https://api.ipify.org", wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)
            ip = (await page.locator("body").inner_text()).strip()
            print("Tor exit IP:", ip[:24])
        except Exception as e:
            print("ipify failed:", str(e)[:100])
        # 2. open teamorouter
        try:
            await page.goto("https://teamorouter.com/", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print("goto failed:", str(e)[:100])
        await asyncio.sleep(5)
        # 3. open sign-in dialog — try both text options
        clicked = False
        for sel in ["text=Get API key", "text=Sign in"]:
            try:
                el = page.locator(sel).first
                await el.wait_for(state="visible", timeout=15000)
                await el.click()
                clicked = True
                print("clicked:", sel)
                break
            except Exception:
                continue
        if not clicked:
            print("could not open dialog — page state:")
            try:
                print((await page.locator("body").inner_text())[:300])
            except Exception:
                pass
            return
        await asyncio.sleep(4)
        # 4. fill email
        email = f"torrate-{int(time.time())}@emalupe.com"
        try:
            el = page.locator("input[placeholder='name@example.com']").first
            await el.wait_for(state="visible", timeout=15000)
            await el.click()
            await el.press_sequentially(email, delay=20)
            print("email filled")
        except Exception as e:
            print("email fill failed:", str(e)[:120])
            return
        await asyncio.sleep(1)
        dlg = page.locator("[role=dialog]").last
        await dlg.locator("button:has-text('Continue')").first.click()
        print("clicked Continue")
        await asyncio.sleep(8)
        # 5. read state
        try:
            txt = await dlg.inner_text()
            print("DIALOG:", txt[:200].replace("\n", " | "))
            if "过于频繁" in txt or "frequently" in txt.lower():
                print("RESULT: STILL rate-limited on fresh Tor exit")
            elif "Code sent" in txt or "verification" in txt.lower() or "captcha" in txt.lower():
                print("RESULT: PASSED rate limit on fresh exit")
            else:
                print("RESULT: other state")
        except Exception as e:
            print("dialog read failed:", str(e)[:100])
        await page.screenshot(path="/tmp/teamo_tor_test.png", full_page=True)


asyncio.run(main())
