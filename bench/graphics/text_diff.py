"""Does the same text page rasterise identically in different Chrome builds?

Renders DOM text and Canvas2D text (Latin + Hangul, several sizes, a variable-font weight
sweep on system-ui) in each browser given, at device scale 1 and 2, and compares the
screenshots pixel by pixel against the first browser. Also compares measureText() advances,
which change if shaping or font selection changes.

The first browser is Playwright's bundled chrome-headless-shell; add others with --browser
(repeatable). Playwright's bundled full Chromium ("Chrome for Testing", same version as the
shell) is at `playwright.chromium.executable_path`; pass --with-bundled-full to include it.

Usage: uv run --with playwright==1.63.0,numpy,pillow python bench/graphics/text_diff.py \
         --with-bundled-full --browser "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"""
import argparse
import asyncio
import io
import json

import numpy as np
from PIL import Image
from playwright.async_api import async_playwright

W, H = 1200, 900
HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;background:#fff;color:#111}
.d{font-family:"Apple SD Gothic Neo","Helvetica Neue",sans-serif;white-space:nowrap;margin:0 0 4px 8px}
.v{font-family:system-ui;white-space:nowrap;margin:0 0 2px 8px;font-size:28px}
canvas{display:block}
</style></head><body>
<div class="d" style="font-size:12px">12px Typography AVATAR Wave 1/2 0123 안녕하세요 모션 그래픽 키네틱 타이포</div>
<div class="d" style="font-size:24px">24px Typography AVATAR Wave 안녕하세요 모션 그래픽</div>
<div class="d" style="font-size:54px">54px AVATAR 안녕하세요 그래픽</div>
<div class="d" style="font-size:120px;letter-spacing:4px">Wave 한글</div>
<div class="v" style="font-variation-settings:'wght' 100">system-ui wght 100 Variable Axis</div>
<div class="v" style="font-variation-settings:'wght' 450">system-ui wght 450 Variable Axis</div>
<div class="v" style="font-variation-settings:'wght' 900">system-ui wght 900 Variable Axis</div>
<canvas id="c" width="1200" height="260"></canvas>
<script>
const c = document.getElementById('c').getContext('2d');
c.fillStyle = '#111'; c.textBaseline = 'top';
const lines = [[14, 'canvas 14px Typography AVATAR 안녕하세요 모션 그래픽'], [32, 'canvas 32px AVATAR Wave 한글 타이포'],
               [72, 'canvas 72px 한글 Wave']];
let y = 4; window.widths = [];
for (const [s, t] of lines) {
  c.font = s + 'px "Apple SD Gothic Neo", "Helvetica Neue", sans-serif';
  c.fillText(t, 8, y); window.widths.push(+c.measureText(t).width.toFixed(4)); y += s * 1.3;
}
c.save(); c.translate(600, 230); c.rotate(-0.05); c.font = '40px "Apple SD Gothic Neo"'; c.letterSpacing = '3px';
c.fillText('회전 rotated 텍스트', 0, 0); c.restore();
</script></body></html>"""


async def shoot(p, exe, dpr):
    kw = {"headless": True, "args": ["--use-angle=metal", "--force-color-profile=srgb"]}
    if exe:
        kw["executable_path"] = exe
    b = await p.chromium.launch(**kw)
    ctx = await b.new_context(viewport={"width": W, "height": H}, device_scale_factor=dpr)
    page = await ctx.new_page()
    await page.set_content(HTML)
    await page.evaluate("document.fonts.ready")
    png = await page.screenshot(type="png")
    widths = await page.evaluate("window.widths")
    version = b.version
    await b.close()
    return version, np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int), widths


async def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--browser", action="append", default=[], help="extra Chrome binary (repeatable)")
    ap.add_argument("--with-bundled-full", action="store_true", help="also test Playwright's bundled full Chromium")
    args = ap.parse_args()
    async with async_playwright() as p:
        exes = [None] + ([p.chromium.executable_path] if args.with_bundled_full else []) + args.browser
        for dpr in (1, 2):
            ref = None
            for exe in exes:
                version, img, widths = await shoot(p, exe, dpr)
                name = (exe or "bundled-headless-shell").split("/")[-1] + " " + version
                if ref is None:
                    ref = (name, img, widths)
                    print(json.dumps({"dpr": dpr, "reference": name, "canvas_widths": widths}))
                    continue
                d = np.abs(img - ref[1]).max(axis=2)
                print(json.dumps({"dpr": dpr, "vs": ref[0], "browser": name,
                                  "pixels_differing_pct": round(100 * float((d > 0).mean()), 3),
                                  "pixels_diff_gt_32_pct": round(100 * float((d > 32).mean()), 3),
                                  "max_abs_diff": int(d.max()),
                                  "canvas_widths_equal": widths == ref[2], "canvas_widths": widths}))


if __name__ == "__main__":
    asyncio.run(main())
