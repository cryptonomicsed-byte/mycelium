#!/usr/bin/env python3
"""Probe: after captcha solve, dump the dialog + any code-entry UI + look
for an in-page code or 'sent to email' state."""
import asyncio, sys, time, re

sys.path.insert(0, "/opt/ares/wallet_intel")


async def main():
    from camoufox.async_api import AsyncCamoufox
    import shumei_solver

    async with AsyncCamoufox(headless=True) as fox:
        page = await fox.new_page()
        await page.goto("https://teamorouter.com/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(3)
        await page.locator("text=Get API key").first.click()
        await asyncio.sleep(2)
        el = page.locator("input[placeholder='name@example.com']").first
        await el.click()
        await el.press_sequentially(f"teamo-probe-{int(time.time())}@emalupe.com", delay=25)
        await asyncio.sleep(1)
        dlg = page.locator("[role=dialog]").last
        await dlg.locator("button:has-text('Continue')").first.click()
        await asyncio.sleep(5)

        popup = page.locator(".shumei_captcha_popup_wrapper").first
        try:
            if await popup.is_visible(timeout=5000):
                print("captcha visible — solving")
                ok = await shumei_solver.solve(page, popup, max_attempts=3)
                print("solve:", ok)
        except Exception as e:
            print("captcha err:", e)

        # NOW dump the full dialog state over 15s (the UI may transition)
        for i in range(5):
            await asyncio.sleep(3)
            print(f"--- t+{3*(i+1)}s ---")
            try:
                txt = await dlg.inner_text()
                print("DIALOG:", txt[:400].replace("\n", " | "))
            except Exception:
                pass
            # all inputs
            for j, inp in enumerate(await page.locator("input").all()):
                try:
                    ph = await inp.get_attribute("placeholder")
                    typ = await inp.get_attribute("type")
                    vis = await inp.is_visible()
                    print(f"  input[{j}] type={typ} ph={ph} vis={vis}")
                except Exception:
                    pass
            # look for any 6-digit code on the page (sometimes shown inline)
            body = await page.locator("body").inner_text()
            m = re.search(r"\b(\d{6})\b", body)
            if m:
                print("  PAGE HAS 6-DIGIT CODE:", m.group(1))
            # any error/toast?
            for kw in ("sent", "code", "error", "verify", "expired", "try again", "failed"):
                for line in body.split("\n"):
                    if kw in line.lower() and len(line.strip()) < 120:
                        print(f"  TEXT[{kw}]:", line.strip()[:100])
        await page.screenshot(path="/tmp/teamo_post_solve_dump.png", full_page=True)


asyncio.run(main())
