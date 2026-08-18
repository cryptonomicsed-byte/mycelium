#!/usr/bin/env python3
"""Probe 6: extract the Shumei puzzle piece + background image data."""
import asyncio, json, base64


async def main():
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(headless=True) as fox:
        page = await fox.new_page()
        await page.goto("https://teamorouter.com/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(3)
        await page.locator("text=Get API key").first.click()
        await asyncio.sleep(2)
        el = page.locator("input[placeholder='name@example.com']").first
        await el.click()
        await el.press_sequentially("teamo-farm-3@emalupe.com", delay=25)
        await asyncio.sleep(1)
        dlg = page.locator("[role=dialog]").last
        await dlg.locator("button:has-text('Continue')").first.click()
        await asyncio.sleep(6)

        popup = page.locator(".shumei_captcha_popup_wrapper").first
        # dump all imgs inside the widget with their src (data URLs / urls)
        imgs = await popup.locator("img").all()
        print("imgs in widget:", len(imgs))
        for i, img in enumerate(imgs):
            src = await img.get_attribute("src")
            style = await img.get_attribute("style")
            cls = await img.get_attribute("class")
            print(f"  img[{i}] class={cls}")
            print(f"    style={style}")
            if src:
                print(f"    src[:120]={src[:120]} len={len(src)}")
            # element position
            try:
                box = await img.bounding_box()
                print(f"    box={box}")
            except Exception:
                pass
            # save data URLs
            if src and src.startswith("data:"):
                try:
                    hdr, b64 = src.split(",", 1)
                    data = base64.b64decode(b64)
                    fn = f"/tmp/shumei_img_{i}.png"
                    open(fn, "wb").write(data)
                    print(f"    SAVED {fn} ({len(data)} bytes)")
                except Exception as e:
                    print("    save err:", e)

        # also get the slider button element
        for sel in ["[class*=slider]", "[class*=btn]", "[class*=drag]"]:
            try:
                n = await popup.locator(sel).count()
                if n:
                    for i in range(min(n, 3)):
                        cls = await popup.locator(sel).nth(i).get_attribute("class")
                        box = await popup.locator(sel).nth(i).bounding_box()
                        print(f"  widget {sel}[{i}] class={str(cls)[:100]} box={box}")
            except Exception:
                pass


asyncio.run(main())
