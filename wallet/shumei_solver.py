#!/usr/bin/env python3
"""shumei_solver.py — auto-solve the Shumei slide-atlas captcha.

Verified geometry (probed live 2026-08-18 on teamorouter.com):
  - bg source: 300(H) x 600(W) atlas, displayed at scale = disp_width/600
    (300x150 on teamorouter => scale 0.5)
  - fg source: 300(H) x 90(W) strip containing the 82x79 puzzle piece
  - The HOLE in the bg has a strong vertical LEFT border — the peak of the
    column-wise edge-energy profile of the bg atlas. Measured: cols 247-248
    (edge energy 3143/2758 vs ~500-1500 background) => hole at src x~247.
  - Piece current position (display px): fg_box.x - bg_box.x
  - Drag dx = hole_src_x * scale - (fg_box.x - bg_box.x)

Gap detection: column-edge profile of the full bg atlas; the hole border is
the strongest column. Fallback: normalized cross-correlation of the piece
against the bg when the edge peak is ambiguous.
"""
import asyncio
import io
import json
import math
import random
import time
import urllib.request

import numpy as np
from PIL import Image


def _load(url_or_bytes):
    if isinstance(url_or_bytes, bytes):
        return Image.open(io.BytesIO(url_or_bytes)).convert("RGB")
    req = urllib.request.Request(url_or_bytes, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")


def find_gap_src_x(bg_url, fg_url, fg_px_width=45):
    """Return the hole's left-edge x in BG SOURCE pixels.

    Method (verified 2026-08-18): the fg strip is a vertical slice of the bg
    at the piece's current position — the piece's y-band in the fg equals the
    hole's y-band in the bg. So: extract the piece via alpha bbox, restrict
    the bg scan to that y-band, and slide the piece's EDGE MAP across it
    (normalized cross-correlation). The argmax is the hole's left edge.
    Falls back to the global column-edge peak if NCC is flat."""
    bg = _load(bg_url)
    bg_g = np.asarray(bg.convert("L"), dtype=np.float32)
    fg_rgba = np.asarray(Image.open(_to_bytesio(fg_url)).convert("RGBA"), dtype=np.float32)

    # 1. extract the piece (alpha mask)
    alpha = fg_rgba[..., 3]
    ys, xs = np.nonzero(alpha > 40)
    if len(xs) < 100:
        return _edge_peak_fallback(bg_g), {"method": "edge-fallback", "reason": "no piece alpha"}
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    piece = fg_rgba[y0:y1 + 1, x0:x1 + 1, :3]
    piece_l = np.asarray(Image.fromarray(piece.astype("uint8")).convert("L"), dtype=np.float32)
    ph, pw = piece_l.shape

    def edges(img):
        gy, gx = np.gradient(img)
        return np.sqrt(gx ** 2 + gy ** 2)

    pe = edges(piece_l)
    # 2. restrict bg scan to the piece's y-band (hole is at same rows)
    band_lo = max(0, y0 - 4)
    band_hi = min(bg_g.shape[0], y1 + 5)
    band = bg_g[band_lo:band_hi, :]

    # 3. slide piece edge map across the band
    best_x, best_s = 0, -1e18
    scores = []
    for x in range(10, bg_g.shape[1] - pw):
        win = band[0:ph, x:x + pw]
        we = edges(win)
        wm = we - we.mean()
        pm = pe - pe.mean()
        denom = math.sqrt(float((wm ** 2).sum()) * float((pm ** 2).sum())) + 1e-9
        s = float((wm * pm).sum()) / denom
        scores.append((s, x))
        if s > best_s:
            best_s, best_x = s, x
    scores.sort(reverse=True)
    # 4. accept if the peak is clear (top score notably above the runner-up)
    if len(scores) >= 2 and best_s > 0.12 and best_s - scores[1][0] > 0.02:
        return best_x, {"method": "band-ncc", "score": round(best_s, 3)}
    if best_s > 0.08:
        return best_x, {"method": "band-ncc-weak", "score": round(best_s, 3)}
    return _edge_peak_fallback(bg_g), {"method": "edge-fallback", "score": round(best_s, 3)}


def _edge_peak_fallback(bg_g):
    """Global column-edge peak (hole border) — used when NCC is flat."""
    col_edge = np.abs(np.diff(bg_g, axis=1)).sum(axis=0)
    k = 3
    kernel = np.ones(k) / k
    col_edge = np.convolve(col_edge, kernel, mode="same")
    margin = 30
    if len(col_edge) > 2 * margin:
        return margin + int(np.argmax(col_edge[margin:-margin]))
    return int(np.argmax(col_edge))


def _to_bytesio(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return io.BytesIO(r.read())


async def human_drag(page, from_x, from_y, to_x, to_y, steps=None):
    """Human-like drag: ease-out curve + jitter + slight overshoot.
    ASYNC (camoufox async API — mouse calls are coroutines)."""
    if steps is None:
        steps = random.randint(28, 45)
    mid_y = from_y + random.uniform(-3, 3)
    pts = []
    for i in range(steps + 1):
        t = i / steps
        e = 1 - (1 - t) ** 2.2
        x = from_x + (to_x - from_x) * e + random.uniform(-0.6, 0.6)
        y = mid_y + (to_y - mid_y) * e + random.uniform(-0.4, 0.4)
        pts.append((x, y))
    await page.mouse.move(from_x, from_y)
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.08, 0.18))
    for x, y in pts[1:]:
        await page.mouse.move(x, y, steps=1)
        await asyncio.sleep(random.uniform(0.008, 0.022))
    await page.mouse.move(to_x + random.uniform(0.5, 1.5), to_y, steps=2)
    await asyncio.sleep(random.uniform(0.05, 0.1))
    await page.mouse.up()
    await asyncio.sleep(random.uniform(0.3, 0.6))


async def solve(page, popup, max_attempts=4):
    """Locate piece + bg, compute drag, execute. Returns True on success.
    ASYNC version (camoufox async API)."""
    for attempt in range(max_attempts):
        fg = popup.locator("img.shumei_captcha_loaded_img_fg").first
        bg = popup.locator("img.shumei_captcha_loaded_img_bg").first
        fg_src = await fg.get_attribute("src")
        bg_src = await bg.get_attribute("src")
        fg_box = await fg.bounding_box()
        bg_box = await bg.bounding_box()
        if not fg_src or not bg_src or not fg_box or not bg_box:
            print(f"  [solve] attempt {attempt}: missing img/src")
            try:
                await popup.locator(".refresh-btn").first.click()
                await asyncio.sleep(2.5)
            except Exception:
                pass
            continue

        gap_src_x, meta = find_gap_src_x(bg_src, fg_src)
        # bg source width is 600 (atlas); displayed width is bg_box['width']
        scale = bg_box["width"] / 600.0
        target_x = bg_box["x"] + gap_src_x * scale
        cur_x = fg_box["x"]
        drag_dx = target_x - cur_x
        y = fg_box["y"] + fg_box["height"] / 2
        print(f"  [solve] attempt {attempt}: gap_src={gap_src_x} ({meta}) "
              f"scale={scale:.2f} cur={cur_x:.0f} target={target_x:.0f} dx={drag_dx:.1f}")
        if abs(drag_dx) < 3:
            print("  [solve] already aligned")
            return True
        await human_drag(page, cur_x + 10, y, cur_x + 10 + drag_dx, y)

        await asyncio.sleep(1.5)
        try:
            fail_btn = popup.locator(".shumei_captcha_fail_refresh_btn").first
            if await fail_btn.is_visible(timeout=800):
                print("  [solve] failed — refresh + retry")
                await fail_btn.click()
                await asyncio.sleep(2.0)
                continue
        except Exception:
            pass
        try:
            if not await popup.is_visible(timeout=1000):
                print("  [solve] SUCCESS — widget gone")
                return True
        except Exception:
            return True
        await asyncio.sleep(1.5)
        try:
            if not await popup.is_visible(timeout=1000):
                print("  [solve] SUCCESS (after verify wait)")
                return True
        except Exception:
            return True
    print("  [solve] gave up after", max_attempts, "attempts")
    return False
