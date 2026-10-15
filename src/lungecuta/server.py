"""The editor's server (docs/design/01-architecture.md): FastAPI serving the UI, preview
frames over a WebSocket, and the API over what `editor.py` holds for the project (comp
timelines, the audio mixdown, edit operations with their journal, file watching, so an
edit the agent makes appears in the editor).

Graphics pages for the live overlay are served from a second origin on another port,
only the project's `graphics/`, with a Content-Security-Policy that allows the page's own
origin and nothing else, so a project's code cannot reach the editor's API or the network
(09-agent-interface.md#sandboxing). The editor drives them through a wrapper page on that
origin by `postMessage`.
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import json
import struct
import threading
import traceback
import urllib.parse
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import journal, view
from .editor import Editor
from .media import Project

WEB_DIR = Path(__file__).parent / "web"
GRAPHICS_POLICY = "default-src 'self' 'unsafe-inline' data: blob:"


class GraphicsHandler(http.server.SimpleHTTPRequestHandler):
    """The project's graphics/, the wrapper page and the project's photos (by media key,
    read where they lie), with a policy that keeps them local."""

    def __init__(self, *args: Any, project: Path, **kwargs: Any):
        self.project = project
        super().__init__(*args, directory=str(project / "graphics"), **kwargs)

    def translate_path(self, path: str) -> str:
        from .media import MEDIA_URL, load_record, original

        path = path.split("?", 1)[0]
        if path == "/__lungecuta_frame.html":
            return str(WEB_DIR / "frame.html")
        if path.startswith(MEDIA_URL):
            # only a photo's original is served here: any other key is not found
            key = urllib.parse.unquote(path.removeprefix(MEDIA_URL))
            if "/" not in key and (self.project / "media" / f"{key}.json").is_file():
                project = Project.load(self.project)
                record = load_record(project, key)
                if record.image is not None:
                    return str(original(project, record))
            return str(self.project / "graphics" / ".missing")
        return super().translate_path(path)

    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy", GRAPHICS_POLICY)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass


def serve_graphics(directory: Path) -> tuple[str, http.server.ThreadingHTTPServer]:
    handler = functools.partial(GraphicsHandler, project=directory)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", server


class EditRequest(BaseModel):
    comp: str
    op: str
    args: dict[str, Any]
    expect: str


class ViewReport(BaseModel):
    """What the editor shows (view.py); `in` and `out` are the marks, comp frames."""
    comp: str
    frame: int
    selected: str | None
    mark_in: int | None = Field(alias="in")
    mark_out: int | None = Field(alias="out")


def create_app(directory: Path) -> FastAPI:
    editor = Editor(directory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        origin, graphics_server = serve_graphics(editor.directory)
        editor.graphics_origin = origin
        task = asyncio.create_task(editor.watch(editor.snapshot()))
        yield
        task.cancel()
        view.remove_view(editor.directory)
        graphics_server.shutdown()
        for preview in editor.previews.values():
            preview.close()

    app = FastAPI(title="lungecuta", lifespan=lifespan)
    app.state.editor = editor

    @app.exception_handler(AssertionError)
    def failed(_request, exc: AssertionError) -> JSONResponse:
        # An engine check that failed is the person's to read, with the engine's message;
        # the trace goes to the server's log as it would on the command line.
        traceback.print_exception(exc)
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response

    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/project")
    def project() -> dict[str, Any]:
        return editor.summary()

    @app.get("/api/comp/{name}")
    def timeline(name: str) -> dict[str, Any]:
        return editor.timeline(name)

    @app.get("/api/comp/{name}/layer/{layer_id}/curves")
    def curves(name: str, layer_id: str) -> dict[str, Any]:
        return editor.curves(name, layer_id)

    @app.get("/api/comp/{name}/scope")
    def scope(name: str, frame: int) -> dict[str, Any]:
        """The scope numbers of a preview frame, as `lungecuta scope` prints them (on the proxy)."""
        from .scope import scope_numbers

        return {"frame": frame, "on": "proxy", **scope_numbers(editor.preview(name).display(frame))}

    @app.get("/api/comp/{name}/scope.png")
    def scope_png(name: str, frame: int) -> Response:
        """The waveform, vectorscope and parade `lungecuta scope` draws, of a preview frame."""
        from .images import png8
        from .scope import scope_image

        return Response(png8(scope_image(editor.preview(name).display(frame))), media_type="image/png")

    @app.get("/api/comp/{name}/layer/{layer_id}/thumb.png")
    def thumb(name: str, layer_id: str, key: str) -> Response:
        return Response(editor.thumbnail(name, layer_id, key), media_type="image/png",
                        headers={"Cache-Control": "max-age=31536000, immutable"})

    @app.get("/api/comp/{name}/peaks")
    def peaks(name: str) -> dict[str, Any]:
        return editor.peaks(name)

    @app.get("/api/styles")
    def styles() -> list[dict[str, Any]]:
        return editor.styles()

    @app.get("/api/comp/{name}/audio.wav")
    def audio(name: str, preset: str = "web-h264") -> Response:
        return Response(editor.mixdown(name, preset), media_type="audio/wav")

    @app.post("/api/edit")
    def edit(request: EditRequest) -> dict[str, Any]:
        return editor.edit(request.comp, request.op, request.args, request.expect)

    @app.post("/api/view")
    def report_view(report: ViewReport) -> dict[str, Any]:
        view.write_view(editor.directory, report.comp, report.frame, report.selected, report.mark_in, report.mark_out)
        return {}

    @app.post("/api/undo")
    def undo() -> dict[str, Any]:
        return editor.step("undo")

    @app.post("/api/redo")
    def redo() -> dict[str, Any]:
        return editor.step("redo")

    @app.get("/api/journal")
    def journal_entries() -> dict[str, Any]:
        return {"entries": [{k: e[k] for k in ("seq", "time", "kind", "file", "changes")}
                            for e in journal.entries(editor.directory)]}

    @app.websocket("/ws")
    async def frames(socket: WebSocket) -> None:
        await socket.accept()
        editor.sockets.add(socket)
        try:
            while True:
                ask = json.loads(await socket.receive_text())
                if ask.get("type") != "frame":
                    continue
                preview = editor.preview(ask["comp"])
                try:
                    data = await anyio.to_thread.run_sync(preview.frame, ask["frame"])
                except AssertionError as error:
                    await socket.send_text(json.dumps({"type": "error", "id": ask["id"], "detail": str(error)}))
                    continue
                header = json.dumps({"id": ask["id"], "comp": ask["comp"], "frame": ask["frame"],
                                     "version": preview.version, "width": preview.size[0],
                                     "height": preview.size[1]}).encode()
                await socket.send_bytes(struct.pack("<I", len(header)) + header + data)
        except WebSocketDisconnect:
            editor.sockets.discard(socket)

    return app


def serve(directory: Path, host: str, port: int, open_browser: bool) -> None:
    import uvicorn

    app = create_app(directory)
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(f"http://{host}:{port}/",)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
