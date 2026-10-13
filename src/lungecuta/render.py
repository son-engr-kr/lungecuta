"""Rendering a comp to a deliverable (M2): every comp frame made as `compose.py` makes it,
`workers` at a time, encoded by a delivery preset with the comp's sound as `mixdown.py`
delivers it, and a manifest of the versions that decided the pixels and samples
(docs/design/01-architecture.md, 08-deliver.md).
"""
from __future__ import annotations

import os
import platform
import subprocess
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np

from . import grade
from .comp import Resolved, load_comp, resolve
from .compose import captured, compose, overlay_pages, raw_layers
from .encode import PRESETS, TAGS, Preset, frame_bytes
from .files import content_hash, json_text, write_atomic
from .media import Project
from .mixdown import delivered_audio
from .ticks import TICKS_PER_SECOND, format_rate, ticks_per_frame
from .tools import run, tool

RENDER_DIR = Path("cache") / "lc" / "render"


def manifest(project: Project, resolved: Resolved, preset: Preset, out: Path, clipped: float,
             audio: dict[str, Any]) -> dict:
    """What decided the file's pixels and samples (01-architecture.md, Playback)."""
    comp_path = project.directory / "comps" / f"{resolved.comp.id}.json"
    ffmpeg = run(["ffmpeg", "-hide_banner", "-version"]).stdout.decode().splitlines()[0]
    settings = project.file.color
    return {
        "file": out.name,
        "hash": content_hash(out),
        "preset": preset.name,
        "comp": {"id": resolved.comp.id, "hash": content_hash(comp_path), "frames": resolved.end,
                 "rate": format_rate(resolved.rate), "size": list(resolved.comp.size)},
        "precomps": sorted((name, content_hash(project.directory / "comps" / f"{name}.json"))
                           for name in resolved.precomps()),
        "media": sorted({(c.record.key, c.record.hash) for c in resolved.every_cut()}
                        | {(s.record.key, s.record.wav.hash) for s in resolved.every_sound()}),
        "luts": sorted({(op.path, content_hash(project.directory / op.path))
                        for c in resolved.every_cut() for op in grade.operations(c.layer.grade) if op.op == "lut"}),
        "color": settings.model_dump(),
        "versions": {"lungecuta": version("lungecuta"), "opencolorio": version("opencolorio"),
                     "numpy": np.__version__, "ffmpeg": ffmpeg, "python": platform.python_version()},
        "clipped_pixels": round(clipped, 6),
        "audio": audio,
    }


def render(project: Project, name: str, preset_name: str, out: Path | None = None,
           threads: int | None = None, workers: int | None = None) -> dict[str, Any]:
    """Render comp `name` with a delivery preset, `workers` frames at a time (by default
    the machine's core count); returns what `lc render` prints."""
    workers = workers or os.cpu_count() or 1
    assert preset_name in PRESETS, f"unknown preset {preset_name!r} (presets: {', '.join(PRESETS)})"
    preset = PRESETS[preset_name]
    resolved = resolve(project, load_comp(project, name))
    if out is None:
        out = project.directory / RENDER_DIR / f"{name}.{preset_name}{preset.suffix}"
    assert out.suffix == preset.suffix, f"a {preset_name} file is a {preset.suffix} file, not {out.name}"
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(f".{out.stem}.partial{out.suffix}")
    width, height = resolved.comp.size
    inputs = ["-f", "rawvideo", "-pix_fmt", preset.pix_fmt, "-s", f"{width}x{height}",
              "-framerate", format_rate(resolved.rate), "-i", "-"]
    maps, audio_args = ["-map", "0:v:0"], []
    mix_path, chain = None, {"none": "the preset has no audio"}
    if preset.audio:
        mix_path, audio_args, chain = delivered_audio(project, resolved, preset, out)
        inputs += ["-i", str(mix_path)]
        maps += ["-map", "1:a:0"]
    encoder = subprocess.Popen([
        tool("ffmpeg"), "-v", "error", "-y", *inputs, *maps,
        "-vf", TAGS, *preset.video, "-pix_fmt", preset.pix_fmt, *audio_args, str(partial),
    ], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    clipped, count = 0.0, 0

    def job(index: int, raws: list, captures: dict[str, bytes]) -> tuple[bytes, float]:
        frame = compose(project, resolved, index, raws, captures, threads=1 if workers > 1 else threads,
                        alpha=preset.alpha)
        return frame_bytes(frame, preset, seed=index)

    # Frames are read and graphics captured in order here; their colour work runs on
    # `workers` threads (numpy and OCIO release the GIL) and they are written in order.
    with overlay_pages(project, resolved) as pages, ThreadPoolExecutor(workers) as pool:
        pending: deque = deque()
        for index, raws in enumerate(raw_layers(project, resolved)):
            pending.append(pool.submit(job, index, raws, captured(resolved, index, pages)))
            while len(pending) > 2 * workers or (pending and pending[0].done()):
                data, outside = pending.popleft().result()
                encoder.stdin.write(data)
                clipped, count = clipped + outside, count + 1
        while pending:
            data, outside = pending.popleft().result()
            encoder.stdin.write(data)
            clipped, count = clipped + outside, count + 1
    encoder.stdin.close()
    error = encoder.stderr.read().decode(errors="replace").strip()
    assert encoder.wait() == 0 and not error, f"ffmpeg failed encoding {out.name}: {error}"
    assert count == resolved.end, f"{count} frames rendered for {resolved.end}"
    if mix_path is not None:
        mix_path.unlink()
    partial.rename(out)
    record = manifest(project, resolved, preset, out, clipped / count, chain)
    write_atomic(out.with_name(out.name + ".manifest.json"), json_text(record))
    return {"file": str(out), "manifest": str(out.with_name(out.name + ".manifest.json")), "frames": count,
            "seconds": round(resolved.end * ticks_per_frame(resolved.rate) / TICKS_PER_SECOND, 6),
            "clipped_pixels": record["clipped_pixels"], "audio": chain}
