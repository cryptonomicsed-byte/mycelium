#!/usr/bin/env python3
"""Test shumei_solver gap detection on real images — capture a fresh
challenge and verify find_gap_x returns a sane value."""
import asyncio, json, sys, time

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
        await el.press_sequentially(f"teamo-solver-test-{int(time.time())}@emalupe.com", delay=25)
        await asyncio.sleep(1)
        dlg = page.locator("[role=dialog]").last
        await dlg.locator("button:has-text('Continue')").first.click()
        await asyncio.sleep(6)

        popup = page.locator(".shumei_captcha_popup_wrapper").first
        fg = popup.locator("img.shumei_captcha_loaded_img_fg").first
        bg = popup.locator("img.shumei_captcha_loaded_img_bg").first
        fg_src = await fg.get_attribute("src")
        bg_src = await bg.get_attribute("src")
        fg_box = await fg.bounding_box()
        bg_box = await bg.bounding_box()
        print("fg src:", fg_src[:100])
        print("bg src:", bg_src[:100])
        print("fg_box:", fg_box)
        print("bg_box:", bg_box)

        gap = shumei_solver.find_gap_x(bg_src, fg_src, fg_px_width=int(fg_box["width"]))
        print("GAP X (bg px):", gap)
        scale = bg_box["width"] / 300.0
        target = bg_box["x"] + gap * scale
        cur = fg_box["x"]
        dx = target - cur
        print(f"screen: target={target:.0f} cur={cur:.0f} dx={dx:.1f}")
        print("dx range check (0..300px sane):", 0 <= gap <= 260)

        # save images for inspection
        import urllib.request
        for name, url in (("fg", fg_src), ("bg", bg_src)):
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                open(f"/tmp/solver_{name}.png", "wb").write(r.read())
        print("saved /tmp/solver_fg.png + /tmp/solver_bg.png")


asyncio.run(main())
