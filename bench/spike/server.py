"""The Phase 1 spike's server: the viewer page and one WebSocket that renders frames on
request (a throwaway; the editor's server is FastAPI, 01-architecture.md)."""
from __future__ import annotations

import asyncio
import json
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aiohttp import WSMsgType, web

from engine import Engine

HERE = Path(__file__).resolve().parent


def make_app(engine: Engine) -> web.Application:
    pool = ThreadPoolExecutor(1)  # one render at a time; OCIO threads inside it

    async def page(request: web.Request) -> web.FileResponse:
        return web.FileResponse(HERE / "viewer.html")

    async def socket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=0)
        await ws.prepare(request)
        loop = asyncio.get_running_loop()
        async for message in ws:
            assert message.type == WSMsgType.TEXT, message
            ask = json.loads(message.data)
            start = time.perf_counter()
            data, stages = await loop.run_in_executor(pool, lambda: engine.render(
                ask["frame"], ask["second"], ask["exposure"], ask["saturation"], cached=ask["cached"],
                decoder=ask.get("decoder", "pyav")))
            header = json.dumps({"width": engine.size[0], "height": engine.size[1], "stages": stages,
                                 "engine_ms": (time.perf_counter() - start) * 1000}).encode()
            await ws.send_bytes(struct.pack("<I", len(header)) + header + data)
        return ws

    app = web.Application()
    app.router.add_get("/", page)
    app.router.add_get("/ws", socket)
    return app


def start(engine: Engine) -> tuple[str, threading.Thread]:
    """Serve on 127.0.0.1 at a free port in a background thread; returns the URL."""
    ready = threading.Event()
    holder = {}

    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        runner = web.AppRunner(make_app(engine))
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 0)
        loop.run_until_complete(site.start())
        holder["port"] = site._server.sockets[0].getsockname()[1]
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    ready.wait()
    return f"http://127.0.0.1:{holder['port']}", thread
