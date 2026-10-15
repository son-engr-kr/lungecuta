"""The editor's server (docs/design/01-architecture.md): FastAPI serving the UI, preview
frames over a WebSocket, comp timelines, the audio mixdown, edit operations from the UI
with their journal, and file watching, so an edit the agent makes appears in the editor.

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
import math
import struct
import threading
import traceback
import urllib.parse
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import journal, ops, view
from .files import content_hash
from .media import Project
from .music import wav_bytes
from .preview import Preview
from .encode import PRESETS
from .mixdown import mastered
from .ticks import TICKS_PER_SECOND, format_rate, nearest_sample, ticks_per_frame, ticks_per_sample

WEB_DIR = Path(__file__).parent / "web"
POLL_SECONDS = 0.25  # how often project files are looked at; a UI setting, not a measurement
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


class Editor:
    """What the server holds for one open project."""

    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.previews: dict[str, Preview] = {}
        self.audio: dict[tuple[str, str], bytes] = {}
        self.peaks_of: dict[str, tuple[str, dict[str, Any]]] = {}  # comp -> (its hash, its peaks)
        self.known: dict[str, str] = {}  # file -> the hash the server last read or wrote
        self.sockets: set[WebSocket] = set()
        self.lock = threading.Lock()
        self.graphics_origin = ""

    @property
    def project(self) -> Project:
        return Project.load(self.directory)

    def comp_hash(self, name: str) -> str:
        return content_hash(self.directory / "comps" / f"{name}.json")

    def preview(self, name: str) -> Preview:
        with self.lock:
            preview = self.previews.get(name)
            if preview is None:
                preview = self.previews[name] = Preview(self.project, name)
            elif preview.stale():
                preview.reload()
            # Reading a comp does not mark it known: only the server's own writes are kept
            # from the watcher, which must still tell the UI about a change the agent made,
            # even when a frame request read it first.
            return preview

    def summary(self) -> dict[str, Any]:
        project = self.project
        media = []
        for path in sorted((self.directory / "media").glob("*.json")):
            record = json.loads(path.read_text())
            media.append({"key": record["key"], "music": record.get("beats") is not None,
                          "audio": bool(record["probe"]["audio"]), "photo": record.get("image") is not None,
                          "color": (record.get("color") or {}).get("status"),
                          "frames": (record["probe"].get("video") or {}).get("frames")})
        return {"directory": str(self.directory), "comps": [Path(c).stem for c in project.file.comps],
                "media": media, "graphics_origin": self.graphics_origin}

    def timeline(self, name: str) -> dict[str, Any]:
        """What the timeline draws: layers by frame, the beat grid, markers, overlays."""
        preview = self.preview(name)
        resolved = preview.resolved
        rate = resolved.rate
        frame = ticks_per_frame(rate)
        order = {layer.id: k for k, layer in enumerate(resolved.comp.layers)}
        layers = [{"id": cut.layer.id, "type": "footage", "media": cut.record.key, "first": cut.first,
                   "end": cut.end, "start": cut.start / frame, "source_frames": list(cut.source_frames),
                   "speed": None if cut.remap is not None else float(cut.speed), "remapped": cut.remap is not None,
                   "style": None if cut.speed_track is None else cut.layer.time.model_dump(exclude_unset=True),
                   "grade": [op.model_dump(mode="json", exclude_unset=True) | {"op": op.op} for op in cut.layer.grade]
                   if isinstance(cut.layer.grade, list) else cut.layer.grade.model_dump(mode="json", exclude_unset=True),
                   "masks": [m.model_dump(mode="json") for m in cut.layer.masks],
                   "retime": cut.retime, "ramps": [list(r) for r in cut.ramps], "row": order[cut.layer.id],
                   "sound": None if cut.layer.sound is None else cut.layer.sound.model_dump(mode="json", exclude_unset=True)}
                  for cut in resolved.cuts]
        # Audio lands on samples, not frames: its extent in (fractional) frames, for drawing.
        layers += [{"id": s.layer.id, "type": "audio", "media": s.record.key, "first": s.first / 48000 * float(rate),
                    "end": s.end / 48000 * float(rate), "start": None, "row": order[s.layer.id],
                    "gain": s.layer.gain if isinstance(s.layer.gain, float) else None,
                    "fade_in": s.layer.fade_in, "fade_out": s.layer.fade_out,
                    "fades": [s.fade_in / 48000 * float(rate), s.fade_out / 48000 * float(rate)]}
                   for s in resolved.sounds if not s.footage]
        layers += [{"id": o.layer.id, "type": "graphics", "page": o.layer.page.removeprefix("graphics/"),
                    "params": o.layer.params, "first": o.first, "end": o.end, "start": o.start / frame,
                    "start_seconds": o.start / TICKS_PER_SECOND, "top": o.top, "row": order[o.layer.id]}
                   for o in resolved.overlays]
        layers += [{"id": n.layer.id, "type": "comp", "media": n.layer.comp, "first": n.first, "end": n.end,
                    "start": n.start / frame, "row": order[n.layer.id]} for n in resolved.nests]
        # The exact in and out points (fractional frames), for trims that snap to beats.
        edit = ops.Edit.open(preview.project, name)
        for layer, cut in zip(layers, resolved.cuts):
            layer["keys"] = _key_frames(edit, cut, frame)
        sounds = {sound.layer.id: sound for sound in resolved.sounds}
        for layer in layers:
            if layer["type"] == "audio":
                layer["keys"] = _gain_keys(edit, sounds[layer["id"]], frame)
        exact = {}
        for layer in edit.data["layers"]:
            start = edit.start(layer)
            exact[layer["id"]] = ((start + edit.span(layer, "in")) / frame, (start + edit.span(layer, "out")) / frame)
        for layer in layers:
            layer["in_at"], layer["out_at"] = exact[layer["id"]]
        grid = resolved.grid
        beats = [] if grid is None else [
            {"index": k, "frame": t / frame, "downbeat": k in set(grid.downbeats)} for k, t in enumerate(grid.times)]
        return {
            "name": name, "hash": preview.hash, "version": preview.version, "comp": json.loads((self.directory / "comps" / f"{name}.json").read_text()),
            "rate": format_rate(rate), "fps": float(rate), "frames": resolved.end, "size": list(resolved.comp.size),
            "preview": list(preview.size), "layers": sorted(layers, key=lambda layer: layer["row"]), "beats": beats,
            "markers": [{"name": m.marker.name, "note": m.marker.note, "frame": m.frame, "layer": m.layer}
                        for m in resolved.markers],
        }

    def curves(self, name: str, layer_id: str) -> dict[str, Any]:
        """A footage layer's properties on every comp frame it is visible: position, scale,
        opacity, and the source time with the speed it plays at (for the graph editor)."""
        preview = self.preview(name)
        cuts = {cut.layer.id: cut for cut in preview.resolved.cuts}
        sounds = {sound.layer.id: sound for sound in preview.resolved.sounds}
        if layer_id in sounds:
            sound = sounds[layer_id]
            tick = ticks_per_frame(preview.resolved.rate)
            first = math.ceil(sound.first / 48000 * float(preview.resolved.rate))
            end = math.ceil(sound.end / 48000 * float(preview.resolved.rate))
            ticks = np.arange(first, end, dtype=np.int64) * tick
            gain = sound.gain.many(ticks) if sound.gain is not None else np.zeros(len(ticks))
            return {"layer": layer_id, "first": first, "end": end, "gain": [round(float(g), 3) for g in gain],
                    "keys": _gain_keys(ops.Edit.open(preview.project, name), sound, tick)}
        assert layer_id in cuts, f"comp {name} has no footage or audio layer {layer_id!r}"
        cut = cuts[layer_id]
        tick = ticks_per_frame(cut.comp_rate)
        media_tick = ticks_per_frame(cut.media_rate)
        frames = list(range(cut.first, cut.end))
        out: dict[str, Any] = {"layer": layer_id, "first": cut.first, "end": cut.end,
                               "media_fps": float(cut.media_rate),
                               "source": [float(cut.source_ticks(f) / media_tick) for f in frames],
                               "speed": _speeds(cut, frames, tick),
                               "keys": _key_frames(ops.Edit.open(preview.project, name), cut, tick)}
        if cut.placement is not None:
            values = [cut.placement.at(f * tick) for f in frames]
            out |= {"position": [list(v[0]) for v in values], "scale": [list(v[1]) for v in values],
                    "opacity": [v[2] for v in values],
                    "rotation": [cut.placement.rotation_at(f * tick) for f in frames]}
        if cut.blurred:
            out["shutter"] = [cut.shutter_deg(f * tick) for f in frames]
        return out

    def styles(self) -> list[dict[str, Any]]:
        from .comp import load_style

        project = self.project
        return [load_style(project, path.stem).model_dump(mode="json")
                for path in sorted((self.directory / "styles").glob("*.json"))]

    def mixdown(self, name: str, preset: str) -> bytes:
        """The comp's sound as the preset will deliver it: the mix through its master chain."""
        assert preset in PRESETS, f"unknown preset {preset!r} (presets: {', '.join(PRESETS)})"
        preview = self.preview(name)
        key = (name, preview.version, preset)  # the version, so a precomp's change is heard
        if key not in self.audio:
            signal, _ = mastered(preview.project, preview.resolved, PRESETS[preset])
            self.audio[key] = wav_bytes(signal, 48000)
        return self.audio[key]

    def peaks(self, name: str) -> dict[str, Any]:
        """Each audio layer's waveform for the timeline: the largest sample magnitude it adds
        to the mix on each comp frame (after its effects, gain and fades), from the frame
        its first sample falls in."""
        from .comp import MIX_RATE
        from .mixdown import comp_samples, layer_sound

        preview = self.preview(name)
        if name in self.peaks_of and self.peaks_of[name][0] == preview.hash:
            return self.peaks_of[name][1]
        resolved = preview.resolved
        tick, sample = ticks_per_frame(resolved.rate), ticks_per_sample(MIX_RATE)
        total = comp_samples(resolved)
        layers = {}
        for sound in resolved.sounds:
            magnitude = np.abs(layer_sound(preview.project, sound, total)).max(axis=1)
            if not len(magnitude):
                continue
            first = sound.first * sample // tick
            last = (sound.first + len(magnitude) - 1) * sample // tick
            starts = [max(0, nearest_sample(f * tick, MIX_RATE) - sound.first) for f in range(first, last + 1)]
            layers[sound.layer.id] = {"first": first, "peaks": [round(float(v), 3)
                                                                 for v in np.maximum.reduceat(magnitude, starts)]}
        self.peaks_of[name] = (preview.hash, {"layers": layers})
        return self.peaks_of[name][1]

    def edit(self, name: str, operation: str, args: dict[str, Any], expect: str) -> dict[str, Any]:
        """Apply an edit operation for the UI, on the version it showed, and journal it."""
        assert operation in ops.OPERATIONS, f"unknown operation {operation!r}"
        relative = f"comps/{name}.json"
        path = self.directory / relative
        before = path.read_text(encoding="utf-8")
        result = ops.OPERATIONS[operation](self.project, name, **args, expect=expect)
        after = path.read_text(encoding="utf-8")
        entry = journal.record(self.directory, relative, before, after, result["changes"], op=operation)
        self.known[relative] = entry["after_hash"]
        return {"changes": result["changes"], "seq": entry["seq"], "timeline": self.timeline(name)}

    def step(self, which: str) -> dict[str, Any]:
        entry = journal.undo(self.directory) if which == "undo" else journal.redo(self.directory)
        self.known[entry["file"]] = entry["after_hash"]
        return {"changes": entry["changes"], "seq": entry["seq"], "file": entry["file"]}

    async def broadcast(self, message: dict[str, Any]) -> None:
        for socket in list(self.sockets):
            try:
                await socket.send_text(json.dumps(message))
            except (WebSocketDisconnect, RuntimeError):
                self.sockets.discard(socket)

    def snapshot(self) -> dict[str, tuple[int, int]]:
        """Size and modification time of every watched project file."""
        files = [self.directory / "project.json", *(self.directory / "comps").glob("*.json"),
                 *(self.directory / "media").glob("*.json"), *(self.directory / "styles").glob("*.json"),
                 *(self.directory / "luts").glob("*")]
        return {path.relative_to(self.directory).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
                for path in files if path.is_file()}

    async def watch(self, seen: dict[str, tuple[int, int]]) -> None:
        """Tell the editor when a project file changes underneath it (the agent's edits).
        The files are few, so they are polled, from a snapshot taken before the server
        accepts requests: a change can never fall between start-up and the first look."""
        while True:
            await asyncio.sleep(POLL_SECONDS)
            now = self.snapshot()
            for relative, stamp in now.items():
                if seen.get(relative) == stamp:
                    continue
                digest = content_hash(self.directory / relative)
                if self.known.get(relative) != digest:
                    self.known[relative] = digest
                    await self.broadcast({"type": "changed", "file": relative, "hash": digest})
            seen = now


def _speeds(cut, frames: list[int], tick: int) -> list[float]:
    """The speed each frame plays at: the source time to the next shown frame, per comp
    tick; the last frame, with no next one shown, keeps the speed it was reached at."""
    source = [cut.source_ticks(f) for f in frames]
    speeds = [float((b - a) / tick) for a, b in zip(source, source[1:])]
    return speeds + speeds[-1:] if speeds else [float(cut.speed)]


def _gain_keys(edit: ops.Edit, sound, tick: int) -> list[dict[str, Any]]:
    """An audio layer's gain keys, as `_key_frames` gives a footage layer's."""
    layer = edit.layer(sound.layer.id)
    if not isinstance(layer.get("gain"), dict):
        return []
    return [{"prop": "gain", "frame": t / tick, "value": v[0], "ease": e, "at": ops.key_position(edit, layer, key)}
            for t, v, e, key in zip(sound.gain.times, sound.gain.values, sound.gain.eases, layer["gain"]["keys"])]


def _key_frames(edit: ops.Edit, cut, tick: int) -> list[dict[str, Any]]:
    """Every key of a footage layer: the comp frame (fractional) it sits on, and its comp
    position as `lungecuta op key` and `unkey` take it."""
    layer = edit.layer(cut.layer.id)
    keys = []
    transform = layer.get("transform") or {}
    for prop in ("position", "scale", "opacity", "rotation"):
        if isinstance(transform.get(prop), dict):
            track = getattr(cut.placement, prop)
            keys += [{"prop": prop, "frame": t / tick, "value": list(v) if len(v) > 1 else v[0], "ease": e,
                      "at": ops.key_position(edit, layer, key)}
                     for t, v, e, key in zip(track.times, track.values, track.eases, transform[prop]["keys"])]
    blur = layer.get("motion_blur") or {}
    if isinstance(blur.get("shutter_deg"), dict):
        keys += [{"prop": "shutter", "frame": t / tick, "value": v[0], "ease": e, "at": ops.key_position(edit, layer, key)}
                 for t, v, e, key in zip(cut.shutter.times, cut.shutter.values, cut.shutter.eases,
                                         blur["shutter_deg"]["keys"])]
    if cut.remap is not None:
        keys += [{"prop": "remap", "frame": t / tick, "value": v[0] / TICKS_PER_SECOND, "ease": e,
                  "at": ops.key_position(edit, layer, key)}
                 for t, v, e, key in zip(cut.remap.times, cut.remap.values, cut.remap.eases, layer["time"]["remap"])]
    return keys


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
