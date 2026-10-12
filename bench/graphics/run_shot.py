"""CDP Page.captureScreenshot throughput: seek-then-capture, transparent PNG, decoded to numpy.

The transparent background is set with Emulation.setDefaultBackgroundColorOverride, which
is what Puppeteer's `omitBackground: true` sends. Runs 1 and 4 parallel pages.

Usage: uv run --with playwright==1.63.0,numpy,pillow python bench/graphics/run_shot.py [--browser PATH]
"""
import argparse
import asyncio
import base64
import io
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
PAGE = (HERE / "page.html").as_uri()
GPU_ARGS = ["--use-angle=metal", "--ignore-gpu-blocklist", "--enable-gpu-rasterization", "--force-color-profile=srgb"]
WARMUP = 3
SIZES = [(1080, 1920, 60), (3840, 2160, 30)]


async def one(browser, w, h, frames):
    ctx = await browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
    page = await ctx.new_page()
    await page.goto(PAGE)
    await page.evaluate("([w,h]) => setup(w,h,'screenshot',null)", [w, h])
    cdp = await ctx.new_cdp_session(page)
    await cdp.send("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
    params = {"format": "png", "optimizeForSpeed": True}
    for n in range(WARMUP):
        await page.evaluate("n => renderOnly(n)", n)
        await cdp.send("Page.captureScreenshot", params)
    t0 = time.perf_counter()
    for n in frames:
        await page.evaluate("n => renderOnly(n)", n)
        r = await asyncio.wait_for(cdp.send("Page.captureScreenshot", params), 60)
        arr = np.asarray(Image.open(io.BytesIO(base64.b64decode(r["data"]))))
        assert arr.shape == (h, w, 4), arr.shape
    dt = time.perf_counter() - t0
    await ctx.close()
    return len(frames) / dt


async def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--browser", default=None, help="Chrome binary; default: Playwright's bundled chrome-headless-shell")
    args = ap.parse_args()
    async with async_playwright() as p:
        kw = {"headless": True, "args": GPU_ARGS}
        if args.browser:
            kw["executable_path"] = args.browser
        browser = await p.chromium.launch(**kw)
        print("browser", browser.version, flush=True)
        for (w, h, nf) in SIZES:
            for P in (1, 4):
                rates = await asyncio.gather(*[one(browser, w, h, list(range(i, nf * P, P))) for i in range(P)])
                print(json.dumps({"size": f"{w}x{h}", "mode": "cdp-screenshot-png+decode", "pages": P,
                                  "fps_per_page": round(sum(rates) / P, 1), "fps_aggregate": round(sum(rates), 1)}), flush=True)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
