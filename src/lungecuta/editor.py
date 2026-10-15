"""What the editor's server holds for one open project (docs/design/01-architecture.md): its
comps' previews, what the timeline, the graph editor and the waveforms draw, the audio
mixdown, the UI's edits with their journal, and the watching that tells the UI when the
agent changed a file. `server.py` serves it over HTTP and a WebSocket."""
from __future__ import annotations

import asyncio
import json
import math
import threading
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from . import journal, ops
from .encode import PRESETS
from .files import content_hash
from .media import Project
from .mixdown import mastered
from .music import wav_bytes
from .preview import Preview
from .ticks import TICKS_PER_SECOND, format_rate, nearest_sample, ticks_per_frame, ticks_per_sample

POLL_SECONDS = 0.25  # how often project files are looked at; a UI setting, not a measurement
THUMB_HEIGHT = 24  # lines: a timeline row, less its margins (web/timeline.js, ROW - 6)
THUMBS_KEPT = 1000


def thumb_key(preview: Preview, cut) -> str:
    """What decides a footage layer's thumbnail: the clip and the source frame its first
    frame shows, the layer's look and placement (its JSON without its timing), the styles
    and LUTs it may read, the comp's size and the project's colour settings."""
    import hashlib

    timing = {"start", "in", "out", "markers", "sound"}
    layer = {k: v for k, v in cut.layer.model_dump(mode="json", by_alias=True, exclude_unset=True).items() if k not in timing}
    described = json.dumps([cut.record.key, cut.samples(cut.first), layer, repr(preview.inputs), preview.size,
                            preview.project.file.color.model_dump()], sort_keys=True, default=str)
    return hashlib.sha256(described.encode()).hexdigest()[:16]


class Editor:
    """What the server holds for one open project."""

    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.previews: dict[str, Preview] = {}
        self.audio: dict[tuple[str, str], bytes] = {}
        self.peaks_of: dict[str, tuple[str, dict[str, Any]]] = {}  # comp -> (its hash, its peaks)
        self.thumbs: dict[str, bytes] = {}  # thumb_key -> PNG, oldest first
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
                   "sound": None if cut.layer.sound is None else cut.layer.sound.model_dump(mode="json", exclude_unset=True),
                   "thumb": thumb_key(preview, cut)}
                  for cut in resolved.cuts]
        # Audio lands on samples, not frames: its extent in (fractional) frames, for drawing.
        layers += [{"id": s.layer.id, "type": "audio", "media": s.record.key, "first": s.first / 48000 * float(rate),
                    "end": s.end / 48000 * float(rate), "start": None, "row": order[s.layer.id],
                    "gain": s.layer.gain if isinstance(s.layer.gain, float) else None,
                    "fade_in": s.layer.fade_in, "fade_out": s.layer.fade_out,
                    "fades": [s.fade_in / 48000 * float(rate), s.fade_out / 48000 * float(rate)]}
                   for s in resolved.sounds if not s.footage]
        edit = ops.Edit.open(preview.project, name)
        layers += [{"id": o.layer.id, "type": "graphics", "page": o.layer.page.removeprefix("graphics/"),
                    "params": o.layer.params, "first": o.first, "end": o.end, "start": o.start / frame,
                    "start_seconds": o.start / TICKS_PER_SECOND, "top": o.top, "row": order[o.layer.id],
                    "masks": [m.model_dump(mode="json") for m in o.layer.masks],
                    "samples": None if o.layer.motion_blur is None else o.layer.motion_blur.samples,
                    "resolution": o.layer.resolution, "keys": _key_frames(edit, o, frame),
                    "anchor": None if o.placement is None else list(o.placement.anchor),
                    "placed": _placed_frames(o, frame)}
                   for o in resolved.overlays]
        layers += [{"id": n.layer.id, "type": "comp", "media": n.layer.comp, "first": n.first, "end": n.end,
                    "start": n.start / frame, "row": order[n.layer.id],
                    "masks": [m.model_dump(mode="json") for m in n.layer.masks], "keys": _key_frames(edit, n, frame)}
                   for n in resolved.nests]
        # The exact in and out points (fractional frames), for trims that snap to beats.
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

    def mask(self, name: str, layer_id: str) -> bytes:
        """A graphics layer's masks as a grey PNG at the preview's size, white where the
        layer is seen, for the editor to cut its live page by (CSS `mask-mode: luminance`)."""
        from .geometry import mask_alpha
        from .images import png8

        preview = self.preview(name)
        overlay = next(o for o in preview.resolved.overlays if o.layer.id == layer_id)
        assert overlay.layer.masks, f"{layer_id} has no masks"
        alpha = mask_alpha(overlay.layer.masks, tuple(preview.size))
        return png8(np.repeat(np.round(alpha * 255).astype(np.uint8), 3, axis=2))

    def thumbnail(self, name: str, layer_id: str, key: str) -> bytes:
        """A footage layer's first frame as a PNG THUMB_HEIGHT lines tall, kept while what
        decides it (`thumb_key`) stays the same, so a shot moved along the timeline is not
        drawn again."""
        from .images import png8

        preview = self.preview(name)
        cut = next(c for c in preview.resolved.cuts if c.layer.id == layer_id)
        current = thumb_key(preview, cut)
        assert key == current, f"{layer_id}'s thumbnail is {current} now, not {key}"
        if key not in self.thumbs:
            self.thumbs[key] = png8(preview.thumbnail(layer_id, THUMB_HEIGHT))
            while len(self.thumbs) > THUMBS_KEPT:
                self.thumbs.pop(next(iter(self.thumbs)))
        return self.thumbs[key]

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
        placed = {o.layer.id: o for o in preview.resolved.overlays} | {n.layer.id: n for n in preview.resolved.nests}
        if layer_id in placed:
            part = placed[layer_id]
            tick = ticks_per_frame(preview.resolved.rate)
            frames = list(range(part.first, part.end))
            out = {"layer": layer_id, "first": part.first, "end": part.end,
                   "keys": _key_frames(ops.Edit.open(preview.project, name), part, tick)}
            if part.placement is not None:
                out |= _placement_curves(part.placement, frames, tick)
            if getattr(part, "shutter", None) is not None:
                out["shutter"] = [part.shutter_deg(f * tick) for f in frames]
            return out
        assert layer_id in cuts, f"comp {name} has no layer {layer_id!r}"
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
            out |= _placement_curves(cut.placement, frames, tick)
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


def _placement_curves(placement, frames: list[int], tick: int) -> dict[str, list]:
    """A transform's values on each of `frames`, for the graph editor and the inspector."""
    values = [placement.at(f * tick) for f in frames]
    return {"position": [list(v[0]) for v in values], "scale": [list(v[1]) for v in values],
            "opacity": [v[2] for v in values], "rotation": [placement.rotation_at(f * tick) for f in frames]}


def _placed_frames(overlay, tick: int) -> list[list[float]] | None:
    """A graphics layer's transform on each frame it is visible, as x, y, scale x, scale y,
    opacity and rotation (position as fractions of the comp), for the editor to place its
    live page by; None when it has no transform."""
    if overlay.placement is None:
        return None
    out = []
    for f in range(overlay.first, overlay.end):
        position, scale, opacity = overlay.placement.at(f * tick)
        out.append([*position, *scale, opacity, overlay.placement.rotation_at(f * tick)])
    return out


def _key_frames(edit: ops.Edit, cut, tick: int) -> list[dict[str, Any]]:
    """Every key of a footage, graphics or precomp layer: the comp frame (fractional) it
    sits on, and its comp position as `lungecuta op key` and `unkey` take it."""
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
    if isinstance(blur.get("shutter_deg"), dict):  # only footage and graphics layers have motion blur
        keys += [{"prop": "shutter", "frame": t / tick, "value": v[0], "ease": e, "at": ops.key_position(edit, layer, key)}
                 for t, v, e, key in zip(cut.shutter.times, cut.shutter.values, cut.shutter.eases,
                                         blur["shutter_deg"]["keys"])]
    if getattr(cut, "remap", None) is not None:
        keys += [{"prop": "remap", "frame": t / tick, "value": v[0] / TICKS_PER_SECOND, "ease": e,
                  "at": ops.key_position(edit, layer, key)}
                 for t, v, e, key in zip(cut.remap.times, cut.remap.values, cut.remap.eases, layer["time"]["remap"])]
    return keys
