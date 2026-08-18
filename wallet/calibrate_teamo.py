#!/usr/bin/env python3
"""Calibration: trigger captcha, solve once (expected to fail), then measure
piece landing position vs computed target to find the offset error."""
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
        await el.press_sequentially(f"teamo-cal-{int(time.time())}@emalupe.com", delay=25)
        await asyncio.sleep(1)
        dlg = page.locator("[role=dialog]").last
        await dlg.locator("button:has-text('Continue')").first.click()
        await asyncio.sleep(6)

        popup = page.locator(".shumei_captcha_popup_wrapper").first
        fg = popup.locator("img.shumei_captcha_loaded_img_fg").first
        bg = popup.locator("img.shumei_captcha_loaded_img_bg").first

        # diagnostics: what img classes are inside the popup?
        try:
            imgs = popup.locator("img")
            n_imgs = await imgs.count()
            print(f"imgs in popup: {n_imgs}")
            for i in range(n_imgs):
                cls = await imgs.nth(i).get_attribute("class")
                print(f"  img[{i}] class={cls}")
        except Exception as e:
            print("img dump err:", e)

        # wait for the challenge images to actually load
        try:
            await fg.wait_for(state="visible", timeout=15000)
            await bg.wait_for(state="visible", timeout=15000)
        except Exception as e:
            print("captcha imgs not visible:", e)
            # diagnostics: what IS in the dialog?
            try:
                dlg = page.locator("[role=dialog]").last
                txt = await dlg.inner_text()
                print("DIALOG:", txt[:400].replace("\n", " | "))
            except Exception:
                pass
            for sel in ["[class*=shumei]", "iframe", "input"]:
                try:
                    n = await page.locator(sel).count()
                    print(f"  {sel}: {n}")
                except Exception:
                    pass
            await page.screenshot(path="/tmp/teamo_cal_nocap.png", full_page=True)
            return
        await asyncio.sleep(1.5)  # let images settle

        bg_src = await bg.get_attribute("src")
        fg_src = await fg.get_attribute("src")
        fg_box = await fg.bounding_box()
        bg_box = await bg.bounding_box()
        gap_src, meta = shumei_solver.find_gap_src_x(bg_src, fg_src)
        scale = bg_box["width"] / 600.0
        target = bg_box["x"] + gap_src * scale
        print(f"gap_src={gap_src} ({meta}) scale={scale:.2f} target_x={target:.1f} piece_start={fg_box['x']}")

        # drag SLOWLY and precisely (no overshoot) to measure pure landing error
        y = fg_box["y"] + fg_box["height"] / 2
        dx = target - fg_box["x"]
        print(f"dx={dx:.1f} — dragging slowly")
        await page.mouse.move(fg_box["x"] + 10, y)
        await page.mouse.down()
        await asyncio.sleep(0.2)
        steps = 60
        for i in range(1, steps + 1):
            t = i / steps
            x = fg_box["x"] + 10 + dx * t
            await page.mouse.move(x, y)
            await asyncio.sleep(0.012)
        await page.mouse.up()
        await asyncio.sleep(2)

        # measure where the piece ended
        fg_box2 = await fg.bounding_box()
        print(f"piece now at x={fg_box2['x']:.1f}  target={target:.1f}  ERROR={fg_box2['x'] - target:.1f}px")

        # did it pass? check fail button
        try:
            fail = popup.locator(".shumei_captcha_fail_refresh_btn").first
            if await fail.is_visible(timeout=1500):
                print("RESULT: FAIL (piece landed", round(fg_box2['x'] - target, 1), "px from target)")
            else:
                # widget gone?
                if not await popup.is_visible(timeout=1000):
                    print("RESULT: SUCCESS")
                else:
                    print("RESULT: still visible, checking...")
                    txt = await popup.inner_text()
                    print("popup text:", txt[:200])
        except Exception:
            print("RESULT: unknown")
        await page.screenshot(path="/tmp/teamo_cal.png", full_page=True)


asyncio.run(main())
