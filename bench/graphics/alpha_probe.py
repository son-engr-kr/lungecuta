"""Is a transparent CDP screenshot PNG straight or premultiplied, and what does 8-bit do to a soft glow?

The page draws, on a transparent background:
  - patch A: rgba(255,128,0,0.25), patch B: rgba(255,128,0,2/255)  (straight vs premultiplied test)
  - a horizontal glow: constant colour rgb(255,140,0), alpha falling linearly from 1 to 0 over 896 px
  - patch C: a premultipliedAlpha WebGL canvas cleared to (0.5,0.3,0,0), i.e. pure additive light
The glow is captured three ways: CDP Page.captureScreenshot PNG (transparent background, as
Puppeteer's omitBackground does), Canvas2D getImageData (8-bit), and the same drawing on a
float16 canvas read back as rgba-float16 (reference). Each is linearised, premultiplied,
composited over black, pushed +4 stops (x16, standing in for a grade or an HDR graphics-white
scale) and re-encoded to 8-bit sRGB; the tail of the glow is then checked for banding.

Usage: uv run --with playwright==1.63.0,numpy,pillow python bench/graphics/alpha_probe.py [--browser PATH]
"""
import argparse
import asyncio
import base64
import io
import json

import numpy as np
from PIL import Image
from playwright.async_api import async_playwright

W, H = 1024, 48
G0, G1 = 128, 1024  # glow span in x
HTML = """<!doctype html><html><head><style>html,body{margin:0;background:transparent}
canvas{position:absolute;left:0;top:0}</style></head><body>
<canvas id="c" width="1024" height="48"></canvas>
<canvas id="g" width="64" height="16" style="left:960px;top:0"></canvas>
<script>
function draw(ctx) {
  ctx.clearRect(0, 0, 1024, 48);
  ctx.fillStyle = 'rgba(255,128,0,0.25)'; ctx.fillRect(0, 0, 64, 16);
  ctx.fillStyle = 'rgba(255,128,0,' + (2/255) + ')'; ctx.fillRect(64, 0, 64, 16);
  const g = ctx.createLinearGradient(128, 0, 1024, 0);
  g.addColorStop(0, 'rgba(255,140,0,1)'); g.addColorStop(1, 'rgba(255,140,0,0)');
  ctx.fillStyle = g; ctx.fillRect(128, 16, 896, 32);
}
draw(document.getElementById('c').getContext('2d'));
const gl = document.getElementById('g').getContext('webgl', {premultipliedAlpha: true, alpha: true});
gl.clearColor(0.5, 0.3, 0.0, 0.0); gl.clear(gl.COLOR_BUFFER_BIT);
window.readU8 = () => Array.from(document.getElementById('c').getContext('2d').getImageData(0, 32, 1024, 1).data);
window.readF16 = () => {
  const c = document.createElement('canvas'); c.width = 1024; c.height = 48;
  const x = c.getContext('2d', {colorType: 'float16'}); draw(x);
  return Array.from(x.getImageData(0, 32, 1024, 1, {pixelFormat: 'rgba-float16'}).data);
};
</script></body></html>"""


def srgb_to_lin(v):
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(v):
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055)


def graded_tail(straight_rgba):
    """straight 0..1 RGBA row -> premultiplied linear over black, x16, 8-bit sRGB; stats on the faint tail."""
    rgb, a = straight_rgba[:, :3], straight_rgba[:, 3:4]
    out = np.round(lin_to_srgb(srgb_to_lin(rgb) * a * 16.0) * 255).astype(int)
    alpha_ideal = 1 - (np.arange(W) - G0) / (G1 - G0)
    tail = (np.arange(W) >= G0) & (alpha_ideal < 16 / 255)  # where alpha < 16/255
    g = out[tail, 1]  # green channel carries the 140/255 colour component
    return {"distinct_levels": int(len(np.unique(g))), "max_step": int(np.abs(np.diff(g)).max()), "pixels": int(tail.sum())}


async def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--browser", default=None, help="Chrome binary; default: Playwright's bundled chrome-headless-shell")
    args = ap.parse_args()
    async with async_playwright() as p:
        kw = {"headless": True, "args": ["--use-angle=metal", "--force-color-profile=srgb"]}
        if args.browser:
            kw["executable_path"] = args.browser
        b = await p.chromium.launch(**kw)
        ctx = await b.new_context(viewport={"width": W, "height": H}, device_scale_factor=1)
        page = await ctx.new_page()
        await page.set_content(HTML)
        cdp = await ctx.new_cdp_session(page)
        await cdp.send("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
        r = await cdp.send("Page.captureScreenshot", {"format": "png"})
        png = np.asarray(Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGBA")).astype(int)
        u8 = np.array(await page.evaluate("readU8()"), dtype=float).reshape(W, 4) / 255.0
        f16 = np.array(await page.evaluate("readF16()"), dtype=float).reshape(W, 4)
        print("browser", b.version)
        print("patch A rgba(255,128,0,0.25) in PNG:", png[8, 32].tolist(), " straight would be ~[255,128,0,64], premultiplied ~[64,32,0,64]")
        print("patch B rgba(255,128,0,2/255) in PNG:", png[8, 96].tolist())
        print("patch C additive WebGL (0.5,0.3,0,0) in PNG:", png[8, 992].tolist())
        row = png[32].astype(float) / 255.0
        tail_x = [x for x in range(G0, G1) if row[x, 3] > 0][-12:]
        print("PNG glow tail, x: straight rgba:", {x: png[32, x].tolist() for x in tail_x[::3]})
        print(json.dumps({"png_8bit": graded_tail(row), "getImageData_8bit": graded_tail(u8), "float16_canvas": graded_tail(f16)}))
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
