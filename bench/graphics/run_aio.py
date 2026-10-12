"""Browser-rendered frame -> numpy through an aiohttp server: chunked WebSocket and fetch() POST.

transport=ws: the page sends each frame in 1 MiB binary WebSocket chunks.
transport=post: the page POSTs each frame as one fetch() body (measured at ~40 MB/s; do not use).
P pages run in parallel on disjoint frame slices; one Python process receives everything,
so the aggregate figure is bounded by that receiver.

Usage: uv run --with playwright==1.63.0,aiohttp,numpy python bench/graphics/run_aio.py [--browser PATH]
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

import numpy as np
from aiohttp import WSMsgType, web
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
GPU_ARGS = ["--use-angle=metal", "--ignore-gpu-blocklist", "--enable-gpu-rasterization"]
PORT = 8811
WARMUP = 3
CHUNK = 1 << 20
FRAMES = {}
WS = {}
SIZES = [(1080, 1920, 60), (3840, 2160, 24)]
CASES = [("ws", m, P) for (m, P) in (("2d", 1), ("gl-u8", 1), ("2d-f16", 1), ("gl-f16", 1), ("2d", 4), ("2d-f16", 4))] + [("post", "2d", 1)]
DTYPES = {"2d": np.uint8, "gl-u8": np.uint8, "2d-f16": np.float16, "gl-f16": np.float32}


async def index(request):
    return web.FileResponse(HERE / "page.html")


async def frame(request):
    FRAMES[(request.query["page"], int(request.query["n"]))] = await request.read()
    return web.Response(status=204)


async def ws_handler(request):
    ws = web.WebSocketResponse(max_msg_size=0)
    await ws.prepare(request)
    q = asyncio.Queue()
    WS[request.query["page"]] = (ws, q)
    async for msg in ws:
        assert msg.type == WSMsgType.BINARY, (msg.type, msg.data)  # the page reports render errors as text
        await q.put(msg.data)
    return ws


async def run_page(browser, w, h, mode, frames, tag, transport):
    ctx = await browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
    page = await ctx.new_page()
    page.on("pageerror", lambda e: print("PAGEERROR", tag, e, flush=True))
    await asyncio.wait_for(page.goto(f"http://127.0.0.1:{PORT}/"), 30)
    dtype = DTYPES[mode]
    nbytes = w * h * 4 * np.dtype(dtype).itemsize
    if transport == "ws":
        await asyncio.wait_for(page.evaluate("([w,h,m,u]) => setup(w,h,m,u)", [w, h, mode, f"ws://127.0.0.1:{PORT}/ws?page={tag}"]), 30)
        await page.evaluate("c => { window.WS_CHUNK = c; }", CHUNK)
        ws, q = WS[tag]

        async def get(n):
            await ws.send_str(json.dumps({"frame": n}))
            buf = bytearray(nbytes)
            mv = memoryview(buf)
            off = 0
            while off < nbytes:
                part = await asyncio.wait_for(q.get(), 30)
                mv[off:off + len(part)] = part
                off += len(part)
            assert off == nbytes, (off, nbytes)
            return buf
    else:
        await asyncio.wait_for(page.evaluate("([w,h,m]) => setup(w,h,m,null)", [w, h, mode]), 30)
        await page.evaluate("t => { const f = window.fetch; window.fetch = (u, o) => f(u + '&page=' + t, o); }", tag)

        async def get(n):
            await asyncio.wait_for(page.evaluate("n => renderAndPost(n)", n), 30)
            return FRAMES.pop((tag, n))

    for n in range(WARMUP):
        await get(n)
    t0 = time.perf_counter()
    for n in frames:
        arr = np.frombuffer(await get(n), dtype=dtype).reshape(h, w, 4)
        assert arr.shape == (h, w, 4)
    dt = time.perf_counter() - t0
    stats = await page.evaluate("getStats()")
    await ctx.close()
    return dt, stats


async def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--browser", default=None, help="Chrome binary; default: Playwright's bundled chrome-headless-shell")
    args = ap.parse_args()
    app = web.Application(client_max_size=1 << 30)
    app.add_routes([web.get("/", index), web.post("/frame", frame), web.get("/ws", ws_handler)])
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    async with async_playwright() as p:
        kw = {"headless": True, "args": GPU_ARGS}
        if args.browser:
            kw["executable_path"] = args.browser
        browser = await p.chromium.launch(**kw)
        print("browser", browser.version, flush=True)
        for (w, h, nf) in SIZES:
            for transport, mode, P in CASES:
                slices = [list(range(i, nf * P, P)) for i in range(P)]
                results = await asyncio.gather(*[run_page(browser, w, h, mode, sl, f"{transport}{mode}{P}p{i}", transport)
                                                 for i, sl in enumerate(slices)])
                per_page = [len(sl) / r[0] for sl, r in zip(slices, results)]
                print(json.dumps({"size": f"{w}x{h}", "transport": f"{transport}-aiohttp", "mode": mode, "pages": P,
                                  "fps_per_page": round(sum(per_page) / P, 1), "fps_aggregate": round(sum(per_page), 1),
                                  **results[0][1]}), flush=True)
        await browser.close()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
