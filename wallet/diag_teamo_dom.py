#!/usr/bin/env python3
"""Diagnose where the TeamoRouter email input lives: iframe vs shadow DOM."""
import asyncio, json


async def main():
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(headless=True) as fox:
        page = await fox.new_page()
        await page.goto("https://teamorouter.com/", wait_until="domcontentloaded", timeout=120000)
        await asyncio.sleep(8)
        await page.locator("text=Get API key").first.click(force=True, timeout=60000)
        await asyncio.sleep(3)

        print("=== FRAMES ===")
        for i, f in enumerate(page.frames):
            print(i, f.url[:120])

        # Inputs visible per frame
        print("=== INPUTS PER FRAME (main) ===")
        for i, f in enumerate(page.frames):
            try:
                n = await f.locator("input").count()
                print(f"frame {i}: {n} inputs")
                for j in range(min(n, 8)):
                    try:
                        el = f.locator("input").nth(j)
                        ph = await el.get_attribute("placeholder")
                        vis = await el.is_visible()
                        print(f"   input[{j}] placeholder={ph!r} visible={vis}")
                    except Exception as e:
                        print(f"   input[{j}] err {type(e).__name__}")
            except Exception as e:
                print(f"frame {i} err {type(e).__name__}: {str(e)[:120]}")

        # Dialog HTML — check for shadow roots
        print("=== DIALOG DUMP ===")
        try:
            dlg = page.locator("[role=dialog]").last
            html = await dlg.evaluate("(el) => el.outerHTML.slice(0, 3000)")
            print(html)
        except Exception as e:
            print("dialog err", type(e).__name__, str(e)[:150])
        try:
            body = await page.locator("body").inner_text()
            print("=== BODY TEXT ===")
            print(body[:600].replace("\n", " | "))
        except Exception as e:
            print("body err", type(e).__name__, str(e)[:150])


asyncio.run(main())
