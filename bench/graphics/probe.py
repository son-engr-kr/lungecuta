"""Which canvas colour spaces / colour types a Chrome build accepts, and a float16 readback check.

Probes Playwright's bundled chrome-headless-shell, plus any binaries given with --browser
(repeatable), e.g. --browser "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome".

Usage: uv run --with playwright==1.63.0 python bench/graphics/probe.py [--browser PATH ...]
"""
import argparse
import asyncio
import json

from playwright.async_api import async_playwright

JS = r"""() => {
  const out = {};
  for (const cs of ['srgb', 'srgb-linear', 'display-p3', 'display-p3-linear']) {
    for (const ct of ['unorm8', 'float16']) {
      let r;
      try {
        const a = document.createElement('canvas').getContext('2d', {colorSpace: cs, colorType: ct}).getContextAttributes();
        r = a.colorSpace + '/' + a.colorType;
      } catch (e) { r = 'REJECTED: ' + e.name; }
      out[cs + '/' + ct] = r;
    }
  }
  const c = document.createElement('canvas'); c.width = 2; c.height = 2;
  const x = c.getContext('2d', {colorSpace: 'srgb', colorType: 'float16'});
  x.fillStyle = 'color(srgb 1.5 0.5 0.25)'; x.fillRect(0, 0, 2, 2);
  const id = x.getImageData(0, 0, 1, 1, {pixelFormat: 'rgba-float16'});
  out['float16 srgb canvas, fill color(srgb 1.5 0.5 0.25), readback'] = Array.from(id.data).map(v => +v.toFixed(4));
  out.imagedata_colorspace = id.colorSpace;
  out.imagedata_pixelFormat = id.pixelFormat;
  out.layoutSubtree = 'layoutSubtree' in document.createElement('canvas');
  out.drawElementImage = typeof document.createElement('canvas').getContext('2d').drawElementImage;
  out.webgpu = !!navigator.gpu;
  out.ua = navigator.userAgent;
  return out;
}"""


async def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--browser", action="append", default=[], help="extra Chrome binary to probe (repeatable)")
    args = ap.parse_args()
    async with async_playwright() as p:
        for exe in [None] + args.browser:
            kw = {"headless": True, "args": ["--use-angle=metal"]}
            if exe:
                kw["executable_path"] = exe
            b = await p.chromium.launch(**kw)
            pg = await b.new_page()
            print(exe or "playwright-bundled-headless-shell", b.version)
            print(json.dumps(await pg.evaluate(JS), indent=1), flush=True)
            await b.close()


if __name__ == "__main__":
    asyncio.run(main())
