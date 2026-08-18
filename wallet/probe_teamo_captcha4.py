#!/usr/bin/env python3
"""Probe 4: precise DOM dump after Continue — find what the dialog shows.
Dumps all input/button text in the sign-in dialog + any captcha container."""
import asyncio, re, json


async def dump_dialog(page, tag):
    print(f"===== {tag} =====")
    # every input with name/type/placeholder
    for i, inp in enumerate(await page.locator("input").all()):
        try:
            name = await inp.get_attribute("name")
            typ = await inp.get_attribute("type")
            ph = await inp.get_attribute("placeholder")
            vis = await inp.is_visible()
            print(f"  input[{i}] name={name} type={typ} ph={ph} visible={vis}")
        except Exception:
            pass
    # dialog text
    dlg = page.locator("[role=dialog]").last
    try:
        txt = await dlg.inner_text()
        print("  DIALOG TEXT:\n" + "\n".join(f"    {l}" for l in txt.split("\n") if l.strip())[:800])
    except Exception:
        pass
    # any element whose class/id mentions captcha
    for sel in ["[class*=captcha]", "[id*=captcha]", "[class*=verify]", "[class*=slider]", "iframe"]:
        try:
            n = await page.locator(sel).count()
            if n:
                print(f"  {sel}: {n}")
                for i in range(min(n, 2)):
                    cls = await page.locator(sel).nth(i).get_attribute("class")
                    print(f"    [{i}] class={str(cls)[:120]}")
        except Exception:
            pass


async def main():
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(headless=True) as fox:
        page = await fox.new_page()
        await page.goto("https://teamorouter.com/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(3)
        await page.locator("text=Get API key").first.click()
        await asyncio.sleep(2)
        await dump_dialog(page, "after open")
        el = page.locator("input[placeholder='name@example.com']").first
        await el.fill("teamo-farm-1@emalupe.com")
        print("email filled")
        b = page.locator("[role=dialog] button:has-text('Continue')").first
        await b.click()
        print("clicked Continue")
        await asyncio.sleep(3)
        await dump_dialog(page, "after Continue (3s)")
        await asyncio.sleep(5)
        await dump_dialog(page, "after Continue (8s)")
        await page.screenshot(path="/tmp/teamo_probe4.png", full_page=True)


asyncio.run(main())
