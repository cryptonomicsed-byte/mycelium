#!/usr/bin/env python3
"""End-to-end live test: trigger captcha, auto-solve, report result."""
import asyncio, sys, time

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
        await el.press_sequentially(f"teamo-e2e-{int(time.time())}@emalupe.com", delay=25)
        await asyncio.sleep(1)
        dlg = page.locator("[role=dialog]").last
        await dlg.locator("button:has-text('Continue')").first.click()
        await asyncio.sleep(6)

        popup = page.locator(".shumei_captcha_popup_wrapper").first
        vis = await popup.is_visible()
        print("captcha visible:", vis)
        if vis:
            ok = await shumei_solver.solve(page, popup, max_attempts=3)
            print("SOLVE RESULT:", ok)
            await page.screenshot(path="/tmp/teamo_solve_outcome.png", full_page=True)
            print("shot /tmp/teamo_solve_outcome.png")
            # if solved, what does the dialog show now?
            if ok:
                await asyncio.sleep(2)
                dlg2 = page.locator("[role=dialog]").last
                try:
                    txt = await dlg2.inner_text()
                    print("DIALOG AFTER SOLVE:\n", txt[:600])
                except Exception:
                    pass
        else:
            print("no captcha — check email input / error state")
            await page.screenshot(path="/tmp/teamo_nocaptcha.png", full_page=True)


asyncio.run(main())
