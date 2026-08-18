#!/usr/bin/env python3
"""Probe TeamoRouter signup page with camoufox/playwright-stealth to see the
Shumei captcha widget structure — can we auto-solve it?"""
import sys, time, json

sys.path.insert(0, "/opt/ares/venv/lib/python3.12/site-packages")

from playwright.sync_api import sync_playwright

URL = "https://teamorouter.com/"

with sync_playwright() as p:
    # try camoufox first (anti-detect), fall back to chromium
    browser = None
    try:
        from camoufox.sync_api import Camoufox
        with Camoufox(headless=True) as fox:
            page = fox.new_page()
            page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            time.sleep(4)
            print("CAMOUFOX LOADED")
            # find the signup / get api key flow
            for sel in ["text=Get API key", "text=Sign up", "text=Get started",
                        "button:has-text('Get API')", "text=Login", "text=Sign in"]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=1500):
                        print("FOUND:", sel)
                        el.click()
                        time.sleep(3)
                        break
                except Exception:
                    continue
            # dump visible inputs/buttons + any captcha iframe
            page.wait_for_timeout(3000)
            for sel in ["iframe", "input", "button"]:
                try:
                    n = page.locator(sel).count()
                    if n:
                        print(f"{sel}: {n}")
                except Exception:
                    pass
            # screenshot
            page.screenshot(path="/tmp/teamo_signup.png")
            print("shot saved /tmp/teamo_signup.png")
            print("URL now:", page.url)
            # any shumei/smcaptcha elements?
            body_html = page.content()
            for probe in ["smcaptcha", "shumei", "captcha", "X-Captcha", "slider", "nc_"]:
                if probe.lower() in body_html.lower():
                    print("HTML HIT:", probe)
    except Exception as e:
        print("camoufox failed:", e)
        try:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
            page = ctx.new_page()
            page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            time.sleep(4)
            print("CHROMIUM LOADED, URL:", page.url)
            page.screenshot(path="/tmp/teamo_signup_chromium.png")
            print("shot saved")
        finally:
            if browser:
                browser.close()
