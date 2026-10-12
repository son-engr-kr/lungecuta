"""Browser-rendered frame -> numpy over a local WebSocket (python `websockets` server).

The page (page.html) renders frame n from its number only, reads the pixels back
(Canvas2D getImageData, or WebGL2 readPixels) and sends them in 1 MiB binary chunks;
Python reassembles each frame and wraps it as a numpy array. One page, one frame in flight.

Single WebSocket messages of 33 MB or more stalled the Python receiver (100% CPU for
minutes) in both `websockets` and aiohttp; chunking avoids that.

Usage: uv run --with playwright==1.63.0,websockets,numpy python bench/graphics/run.py [--browser PATH]
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

import numpy as np
import websockets
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
PAGE = (HERE / "page.html").as_uri()
GPU_ARGS = ["--use-angle=metal", "--ignore-gpu-blocklist", "--enable-gpu-rasterization"]
WARMUP = 5
CHUNK = 1 << 20
SIZES = [(1080, 1920, 60), (3840, 2160, 24)]
MODES = ["2d", "2d-f16", "gl-u8", "gl-f16"]
DTYPES = {"2d": np.uint8, "gl-u8": np.uint8, "2d-f16": np.float16, "gl-f16": np.float32}


async def bench(page, w, h, mode, nframes, port):
    got = asyncio.get_running_loop().create_future()
    done = asyncio.get_running_loop().create_future()

    async def handler(conn):
        got.set_result(conn)
        await done

    dtype = DTYPES[mode]
    nbytes = w * h * 4 * np.dtype(dtype).itemsize
    async with websockets.serve(handler, "127.0.0.1", port, max_size=None, compression=None):
        info = await page.evaluate("([w,h,m,u]) => setup(w,h,m,u)", [w, h, mode, f"ws://127.0.0.1:{port}"])
        await page.evaluate("c => { window.WS_CHUNK = c; }", CHUNK)
        conn = await got

        async def frame(n):
            await conn.send(json.dumps({"frame": n}))
            buf = bytearray(nbytes)
            mv = memoryview(buf)
            off = 0
            while off < nbytes:
                part = await asyncio.wait_for(conn.recv(), 30)
                assert not isinstance(part, str), part  # the page reports render errors as text
                mv[off:off + len(part)] = part
                off += len(part)
            assert off == nbytes, (off, nbytes)
            return np.frombuffer(buf, dtype=dtype).reshape(h, w, 4)

        for n in range(WARMUP):
            await frame(n)
        t0 = time.perf_counter()
        for n in range(nframes):
            await frame(n)
        dt = time.perf_counter() - t0
        stats = await page.evaluate("getStats()")
        done.set_result(None)
    return {"fps": round(nframes / dt, 1), "ms_per_frame": round(1000 * dt / nframes, 1), **stats,
            "renderer": info.get("renderer")}


async def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--browser", default=None, help="Chrome binary; default: Playwright's bundled chrome-headless-shell")
    args = ap.parse_args()
    port = 8765
    async with async_playwright() as p:
        kw = {"headless": True, "args": GPU_ARGS}
        if args.browser:
            kw["executable_path"] = args.browser
        browser = await p.chromium.launch(**kw)
        print("browser", browser.version, flush=True)
        for (w, h, nf) in SIZES:
            for mode in MODES:
                ctx = await browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
                page = await ctx.new_page()
                page.on("pageerror", lambda e: print("PAGEERROR", e, flush=True))
                await page.goto(PAGE)
                port += 1
                r = await bench(page, w, h, mode, nf, port)
                print(json.dumps({"size": f"{w}x{h}", "transport": "ws-websockets", "mode": mode, **r}), flush=True)
                await ctx.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
