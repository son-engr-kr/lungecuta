# Motion-graphics render paths: browser capture vs native Skia

These scripts measure how fast a motion-graphics frame can get from a renderer into a
numpy array in Python, which is what the engine's compositor consumes. Two families:

- **Browser**: a page (`page.html`) draws frame n as a pure function of n; Python asks
  for a frame and receives the pixels by CDP screenshot, WebSocket or HTTP POST.
- **Native**: the same scene drawn by skia-python in-process.

All numbers come from **one light synthetic scene**: a full-width gradient band, 150
rotated rounded rectangles with varying alpha, and 12 lines of rotated Latin + Hangul text
(Apple SD Gothic Neo). Chrome draws it on the GPU almost for free, so the browser figures
below are dominated by readback and transport, not by drawing. A comp with heavy blurs,
masks or shaders would cost more to draw in both families, and the gap would narrow. Treat
the table as a measure of the pixel pipeline, not of real comps.

## Machine and versions (measured 2026-10-10)

| item | version |
| --- | --- |
| machine | Apple M5 Pro (5 performance + 10 efficiency cores), 24 GB, macOS 26.4 (Darwin 25.4.0) |
| capture browser | chrome-headless-shell 153.0.8010.12, as bundled by Playwright 1.63.0, ANGLE Metal renderer |
| other browsers (probe.py, text_diff.py) | Chrome for Testing 153.0.8010.12 (bundled by Playwright 1.63.0), Google Chrome 155.0.8059.39 |
| Python | CPython 3.12.13 (uv-managed) |
| Python packages | playwright 1.63.0, skia-python 144.0.post2, websockets 17.2, aiohttp 3.14.4, numpy 2.5.3, pillow 12.3.0 |

Browser flags: `--use-angle=metal --ignore-gpu-blocklist --enable-gpu-rasterization`, plus
`--force-color-profile=srgb` for the screenshot scripts. Every browser script launches
Playwright's bundled chrome-headless-shell unless `--browser PATH` is given. Install the
pinned browsers once with `uv run --with playwright==1.63.0 playwright install chromium`.

## Scripts

Run from the repository root.

| script | measures | command |
| --- | --- | --- |
| `page.html` | the scene and the in-page readback modes (`2d`, `2d-f16`, `gl-u8`, `gl-f16`, `screenshot`) | loaded by the scripts |
| `run_shot.py` | CDP `Page.captureScreenshot` PNG on a transparent background, decoded with Pillow; 1 and 4 parallel pages | `uv run --with playwright==1.63.0,numpy,pillow python bench/graphics/run_shot.py` |
| `run.py` | in-page readback sent over a WebSocket in 1 MiB chunks to a python `websockets` server; one page | `uv run --with playwright==1.63.0,websockets,numpy python bench/graphics/run.py` |
| `run_aio.py` | the same over an aiohttp WebSocket (1 and 4 pages), and `fetch()` POST of the raw frame | `uv run --with playwright==1.63.0,aiohttp,numpy python bench/graphics/run_aio.py` |
| `skia_bench.py` | the scene drawn natively by skia-python, 8-bit sRGB and F16 linear Rec.2020, one process | `uv run --with skia-python==144.0.post2,numpy python bench/graphics/skia_bench.py` |
| `skia_parallel.py` | the same with 1, 4 and 8 worker processes | `uv run --with skia-python==144.0.post2,numpy python bench/graphics/skia_parallel.py` |
| `probe.py` | which canvas colour spaces / colour types a build accepts; float16 readback | `uv run --with playwright==1.63.0 python bench/graphics/probe.py --browser "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"` |
| `alpha_probe.py` | whether transparent screenshot PNGs are straight or premultiplied; 8-bit damage to a soft glow | `uv run --with playwright==1.63.0,numpy,pillow python bench/graphics/alpha_probe.py` |
| `text_diff.py` | whether the same text page rasterises identically in different Chrome builds | `uv run --with playwright==1.63.0,numpy,pillow python bench/graphics/text_diff.py --with-bundled-full --browser "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"` |

`skia_bench.py` and the page both use Apple SD Gothic Neo, so they run as written on macOS only.

## Results: frame into numpy, frames per second

Each cell is the range over two runs on the same day. "Aggregate" sums the per-page or
per-process rates.

| path | 1080x1920 | 3840x2160 |
| --- | --- | --- |
| CDP `captureScreenshot` PNG + decode, 1 page (`run_shot.py`) | 22.7-23.5 | 9.9-10.0 |
| same, 4 pages in parallel, aggregate | 48.1-48.3 | 16.4-19.1 |
| Canvas2D `getImageData` RGBA8, WebSocket, `websockets` server (`run.py`, `2d`) | 36.1-38.4 | 10.7-10.9 |
| WebGL2 `readPixels` RGBA8, same transport (`run.py`, `gl-u8`) | 37.3 | 11.4 |
| Canvas2D float16, `getImageData` `rgba-float16` (`run.py`, `2d-f16`) | 20.7-22.0 | 5.1-5.4 |
| WebGL2 RGBA16F buffer, `readPixels` FLOAT (`run.py`, `gl-f16`) | 10.7-10.9 | 2.7-2.8 |
| Canvas2D RGBA8, aiohttp WebSocket, 1 page (`run_aio.py`) | 18.1 | 4.8 |
| same, 4 pages, aggregate (one Python receiver) | 24.2-24.7 | 5.9 |
| Canvas2D float16, aiohttp WebSocket, 4 pages, aggregate | 13.9-14.4 | 3.2-3.3 |
| `fetch()` POST of the raw RGBA8 frame (uvicorn/h11 or aiohttp) | 2.6-2.7 | 0.6-0.7 |
| skia-python CPU raster, RGBA8 sRGB, 1 process (`skia_bench.py`, `skia_parallel.py`) | 279-313 | 40-43 |
| skia-python CPU raster, F16 linear Rec.2020, 1 process | 234-270 | 42-44 |
| skia-python RGBA8, 8 processes, aggregate | 1140-1591 | 261-263 |
| skia-python F16, 8 processes, aggregate | 1236-1239 | 247-248 |

For reference, the previous web-page pipeline rendered 30 s of 1080x1920 at 60 fps in
about 8.5 minutes, i.e. about 3.5 fps. Its code was not inspected, so the cause is unknown.

In-page readback alone, median per frame:

| readback | 1080x1920 | 3840x2160 |
| --- | --- | --- |
| Canvas2D `getImageData` RGBA8 | 6.1-6.6 ms | 15.8-17.0 ms |
| WebGL2 `readPixels` RGBA8 | 4.2-4.3 ms | 11.2-11.5 ms |
| Canvas2D `getImageData` `rgba-float16` | 7.0-7.4 ms | 33.6-35.2 ms |
| WebGL2 `readPixels` FLOAT from RGBA16F | 14.6-15.1 ms | 53.0-56.1 ms |

What the numbers say:

- In the browser, readback is cheap and **transport into Python is the cost**. Pipelining
  four frames in flight over one WebSocket gave no gain (30.6 vs 31.2 fps in an earlier
  unchunked run), and four pages into one aiohttp receiver barely beat one page.
- **Do not POST frames with `fetch()`**: an 8 MB upload took about 200 ms with both
  servers, so this is the browser's upload path, not the server.
- **Do not send a frame as one large WebSocket message**: single messages of 33 MB or more
  stalled both Python receivers at 100% CPU for minutes. 1 MiB chunks fixed it. The cause
  inside the libraries was not investigated.
- Screenshots scale with parallel pages because PNG encoding runs in the browser's
  processes; in-page readback into one Python receiver does not.
- Native Skia was 6 to 30 times faster than the best browser path, and F16 linear cost
  about the same as 8-bit.
- `gl-f16` reads float values, but its texture is uploaded from an 8-bit 2D canvas, so it
  measures the cost of a float readback, not float content.

## Results: capability probes

`probe.py`, identical in chrome-headless-shell 153.0.8010.12 and Chrome 155.0.8059.39:

- 2D canvas accepts `srgb` and `display-p3`, each with `unorm8` and `float16`.
- `srgb-linear` and `display-p3-linear` are **rejected** (TypeError), although the HTML
  spec now lists them.
- A float16 `srgb` canvas keeps extended, sRGB-encoded values: `color(srgb 1.5 0.5 0.25)`
  reads back as `[1.5, 0.5, 0.25, 1]` via `getImageData(..., {pixelFormat: 'rgba-float16'})`.
- In headless with these flags: `navigator.gpu` absent, `drawElementImage` undefined.

`alpha_probe.py`, chrome-headless-shell 153.0.8010.12, transparent background set the way
Puppeteer's `omitBackground: true` sets it (`Emulation.setDefaultBackgroundColorOverride`
with alpha 0):

- The PNG is **straight (non-premultiplied)** alpha: `rgba(255,128,0,0.25)` comes out as
  `[255,128,0,64]`, not `[64,32,0,64]`.
- Faint pixels lose their colour. A glow of constant `rgb(255,140,0)` reads, in its last
  pixels, `[191,128,0,4]`, `[255,85,0,3]`, `[255,128,0,2]`, `[255,255,0,1]`. The canvas
  stores premultiplied 8-bit, so at alpha k only k+1 levels per channel exist; dividing
  them back out gives a wrong hue.
- Composited straight onto a background, the error stays within about one code value. It
  shows once the result is pushed: over black, +4 stops (x16), the faint tail of the glow
  (alpha < 16/255, 56 px) has 23 distinct output levels and steps of up to 71 code values
  via the 8-bit PNG or 8-bit `getImageData`. The same drawing on a float16 canvas has 56
  levels and steps of at most 12. The +4 stop push is a stress test; scaling SDR graphics
  white to BT.2408's 203 cd/m2 is about one stop.
- Pure additive light (a premultiplied WebGL canvas cleared to `(0.5, 0.3, 0, 0)`) comes
  out as `[0,0,0,0]`: straight alpha cannot represent it, and it is lost.

`text_diff.py`: a page of DOM text and Canvas2D text (Latin + Hangul at 12-120 px,
letter-spacing, rotation, and a `font-variation-settings` weight sweep on system-ui).
It rendered **pixel-identically** in chrome-headless-shell 153.0.8010.12, Chrome for
Testing 153.0.8010.12 (new headless) and Chrome 155.0.8059.39, at device scale 1 and 2.
`measureText` widths were equal to four decimals. That covers one two-milestone gap, in
headless, with `--force-color-profile=srgb`. It does not cover a headed window on a P3
display with the system colour profile.

## Caveats

- One light synthetic scene on one machine. Re-measure with real comps before using these
  numbers to size anything.
- Chrome's 2D canvas is GPU-backed here, so its drawing cost is deferred into readback.
  skia-python was measured on CPU raster only; its GPU backends were not tested.
- `skia_parallel.py` keeps each worker's frames in that worker. A real renderer must also
  move them to the compositor (shared memory makes that cheap but not free).
- The aiohttp and `websockets` receivers differ by about two times on identical input. A
  receiver built for this (native code, shared memory) would move the browser rows; Remotion
  went further and ships a patched Chromium for shared-memory capture.
