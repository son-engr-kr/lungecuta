"""Rendering a comp to a deliverable (M2): every comp frame made as `compose.py` makes it,
`workers` at a time, encoded by a delivery preset with the comp's sound as `mixdown.py`
delivers it, and a manifest of the versions that decided the pixels and samples
(docs/design/01-architecture.md, 08-deliver.md).
"""
from __future__ import annotations

import json
import math
import os
import platform
import subprocess
from contextlib import closing
from importlib.metadata import version
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from . import grade, overlays
from .comp import Resolved, load_comp, resolve
from .compose import captured, compose, overlay_pages, photo_keys, raw_layers
from .encode import PRESETS, TAGS, Preset, frame_bytes
from .files import content_hash, content_hash_text, json_text, write_atomic
from .media import Project, load_record
from .mixdown import delivered_audio
from .parallel import in_order
from .preview import Preview, preview_divisor
from .ticks import TICKS_PER_SECOND, format_rate, ticks_per_frame
from .tools import run, tool

RENDER_DIR = Path("cache") / "lungecuta" / "render"
# A draft's frames a second at most (*proposed*, 08-deliver.md, Drafts): it shows every
# `draft_step`th comp frame, so a 50 fps comp drafts at 25 and a 23.976 one at its own rate.
DRAFT_RATE = 30


def draft_step(rate: Fraction) -> int:
    """The comp frames a draft frame stands for: the fewest that bring `rate` to DRAFT_RATE or under."""
    return math.ceil(rate / DRAFT_RATE)


def draft_of(project: Project, resolved: Resolved, preset: Preset) -> dict[str, Any] | None:
    """What a draft preset makes of the comp: the preview's divisor and size, the step
    between the comp frames it shows and its rate; None for a delivery."""
    if not preset.draft:
        return None
    divisor = preview_divisor(tuple(resolved.comp.size), project.file.proxy_height)
    step = draft_step(resolved.rate)
    return {"divisor": divisor, "size": [resolved.comp.size[0] // divisor, resolved.comp.size[1] // divisor],
            "step": step, "rate": format_rate(resolved.rate / step), "from": "proxies"}


def read(project: Project, name: str) -> tuple[Resolved, dict[str, Any]]:
    """The comp resolved, and the hashes of the files that decided it, each taken before
    the file was read: a write after a hash can only make the render look stale, never
    current. A write to a precomp, a look or a LUT while the comp is read sends it round
    again."""
    comp_path = project.directory / "comps" / f"{name}.json"
    files: dict[str, Any] = {}
    while True:
        comp_hash = content_hash(comp_path)
        before = {path: content_hash(project.directory / path) for path in files}
        resolved = resolve(project, load_comp(project, name))
        files = {f"comps/{c}.json": None for c in resolved.precomps()}
        files |= {op.path: None for c in resolved.every_cut() for op in grade.operations(project, c.layer.grade)
                  if op.op == "lut"}
        files |= {f"styles/{look}.json": None for c in resolved.every_cut() for look in grade.looks(project, c.layer.grade)}
        if set(before) == set(files) and content_hash(comp_path) == comp_hash and all(
                content_hash(project.directory / path) == digest for path, digest in before.items()):
            break
    hashes = {"comp": comp_hash,
              "precomps": sorted((Path(p).stem, d) for p, d in before.items() if p.startswith("comps/")),
              "luts": sorted((p, d) for p, d in before.items() if not p.startswith(("comps/", "styles/"))),
              "looks": sorted((Path(p).stem, d) for p, d in before.items() if p.startswith("styles/"))}
    return resolved, hashes


def _engine() -> str:
    """lungecuta's own code, as a hash of its Python files: a change to how frames are made
    is a change to every picture, whatever version number the package carries."""
    package = Path(__file__).parent
    return content_hash_text("\n".join(f"{path.relative_to(package).as_posix()} {content_hash(path)}"
                                       for path in sorted(package.rglob("*.py"))))


def _picture_comp(resolved: Resolved) -> dict[str, Any]:
    """A comp as far as it decides pixels: no audio layers, no clip sound, no markers, and
    its beat grid as resolved in place of where it comes from (beat literals land on it)."""
    data = resolved.comp.model_dump(mode="json", by_alias=True, exclude_unset=True)
    data.pop("markers", None)
    data.pop("beats", None)
    data["layers"] = [{key: value for key, value in layer.items() if key != "sound"}
                      for layer in data["layers"] if layer["type"] != "audio"]
    grid = resolved.grid
    return {"comp": data, "grid": None if grid is None else [list(grid.times), list(grid.downbeats)],
            "precomps": [_picture_comp(nest.resolved) for nest in resolved.nests]}


def picture(project: Project, resolved: Resolved, hashes: dict[str, Any], draft: dict | None = None) -> str | None:
    """What decides a render's pixels and none of its sound, as one hash: the comp and its
    precomps as `_picture_comp` keeps them, the footage and photos, LUTs, looks, colour
    settings, the code and versions that make frames, and each graphics layer's capture
    entry with every file its page loaded (overlays.py). None when a graphics layer's
    captures cannot be vouched for (not made yet, or a file its page loaded changed), since
    then nothing says what the page draws. A draft's (`draft_of`) holds what it makes too."""
    entries = overlays._entries(project, resolved, 1 if draft is None else draft["divisor"])
    if not all(overlays._fresh(project, entry) for entry in entries.values()):
        return None
    decided = {
        "comp": _picture_comp(resolved),
        "media": sorted({(c.record.key, c.record.hash) for c in resolved.every_cut()}
                        | {(key, load_record(project, key).hash) for key in photo_keys(resolved)}),
        "luts": hashes["luts"], "looks": hashes["looks"],
        "color": project.file.color.model_dump(mode="json"),
        "graphics": sorted((path, entry.directory.name, sorted(entry.served.items()))
                           for path, entry in entries.items()),
        "engine": _engine(),
        "draft": draft,
        "versions": {"opencolorio": version("opencolorio"), "numpy": np.__version__,
                     "ffmpeg": run(["ffmpeg", "-hide_banner", "-version"]).stdout.decode().splitlines()[0]},
    }
    return content_hash_text(json.dumps(decided, sort_keys=True, default=list))


def manifest(project: Project, resolved: Resolved, hashes: dict[str, Any], preset: Preset, out: Path,
             clipped: float, audio: dict[str, Any]) -> dict:
    """What decided the file's pixels and samples (01-architecture.md, Playback); for a
    draft, what it made of the comp (`draft_of`)."""
    draft = draft_of(project, resolved, preset)
    ffmpeg = run(["ffmpeg", "-hide_banner", "-version"]).stdout.decode().splitlines()[0]
    settings = project.file.color
    return {
        "file": out.name,
        "hash": content_hash(out),
        "preset": preset.name,
        "comp": {"id": resolved.comp.id, "hash": hashes["comp"], "frames": resolved.end,
                 "rate": format_rate(resolved.rate), "size": list(resolved.comp.size)},
        "precomps": hashes["precomps"],
        "media": sorted({(c.record.key, c.record.hash) for c in resolved.every_cut()}
                        | {(s.record.key, s.record.wav.hash) for s in resolved.every_sound() if not s.footage}
                        | {(key, load_record(project, key).hash) for key in photo_keys(resolved)}),
        "luts": hashes["luts"],
        "looks": hashes["looks"],
        "color": settings.model_dump(),
        "versions": {"lungecuta": version("lungecuta"), "opencolorio": version("opencolorio"),
                     "numpy": np.__version__, "ffmpeg": ffmpeg, "python": platform.python_version()},
        "clipped_pixels": round(clipped, 6),
        "audio": audio,
        "picture": picture(project, resolved, hashes, draft),
        **({} if draft is None else {"draft": draft}),
    }


def _same_picture(project: Project, resolved: Resolved, hashes: dict[str, Any], preset: Preset,
                  earlier: Path) -> tuple[dict | None, str]:
    """The manifest of an earlier render whose video this render would make again, frame
    for frame, or None and why not: its manifest gives the same preset and the same
    `picture`, and the file is still the one it describes."""
    manifest_path = earlier.with_name(earlier.name + ".manifest.json")
    if not earlier.is_file() or not manifest_path.is_file():
        return None, f"{earlier.name} has no manifest beside it"
    old = json.loads(manifest_path.read_text())
    if old["preset"] != preset.name:
        return None, f"{earlier.name} is a {old['preset']} render, not {preset.name}"
    current = picture(project, resolved, hashes, draft_of(project, resolved, preset))
    if current is None or old.get("picture") != current:
        return None, f"the picture is not the one {earlier.name} was rendered from"
    if content_hash(earlier) != old["hash"]:
        return None, f"{earlier.name} changed since it was rendered"
    return old, ""


def _with_new_sound(project: Project, resolved: Resolved, preset: Preset, earlier: Path, partial: Path,
                    out: Path) -> dict[str, Any]:
    """The earlier render's video stream, copied as it is, with the comp's sound delivered
    as a full render delivers it; returns what the master chain did."""
    mix_path, audio_args, chain = delivered_audio(project, resolved, preset, out)
    flags = ["-movflags", "+faststart"] if "-movflags" in preset.video else []
    run([tool("ffmpeg"), "-v", "error", "-y", "-i", str(earlier), "-i", str(mix_path), "-map", "0:v:0",
         "-map", "1:a:0", "-c:v", "copy", *audio_args, *flags, str(partial)])
    mix_path.unlink()
    return chain


def render(project: Project, name: str, preset_name: str, out: Path | None = None,
           threads: int | None = None, workers: int | None = None, picture_from: Path | None = None,
           again: bool = False) -> dict[str, Any]:
    """Render comp `name` with a delivery preset, `workers` frames at a time (by default
    the machine's core count); returns what `lungecuta render` prints.

    When only the sound changed, the picture is not rendered again: an earlier render of
    the same preset whose manifest gives the same `picture` (the file at `out`, or
    `picture_from`) lends its video stream, copied, and the sound is delivered anew. With
    `picture_from` the picture must match; `again` renders every frame regardless."""
    workers = workers or os.cpu_count() or 1
    assert preset_name in PRESETS, f"unknown preset {preset_name!r} (presets: {', '.join(PRESETS)})"
    assert not (again and picture_from), "--again renders the picture; --picture-from reuses one"
    preset = PRESETS[preset_name]
    resolved, hashes = read(project, name)
    if out is None:
        out = project.directory / RENDER_DIR / f"{name}.{preset_name}{preset.suffix}"
    assert out.suffix == preset.suffix, f"a {preset_name} file is a {preset.suffix} file, not {out.name}"
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(f".{out.stem}.partial{out.suffix}")
    earlier = picture_from or (None if again else out)
    old, why = _same_picture(project, resolved, hashes, preset, earlier) if earlier is not None else (None, "")
    assert picture_from is None or old is not None, f"--picture-from {picture_from.name}: {why}"
    if old is not None:
        chain = _with_new_sound(project, resolved, preset, earlier, partial, out)
        partial.rename(out)
        record = manifest(project, resolved, hashes, preset, out, old["clipped_pixels"], chain)
        record["picture_from"] = {"file": earlier.name, "hash": old["hash"]}
        write_atomic(out.with_name(out.name + ".manifest.json"), json_text(record))
        return {"file": str(out), "manifest": str(out.with_name(out.name + ".manifest.json")),
                "frames": old["comp"]["frames"], "picture": f"the video of {earlier.name}, unchanged; sound delivered anew",
                "seconds": round(resolved.end * ticks_per_frame(resolved.rate) / TICKS_PER_SECOND, 6),
                "clipped_pixels": record["clipped_pixels"], "audio": chain}
    draft = draft_of(project, resolved, preset)
    width, height = resolved.comp.size if draft is None else draft["size"]
    step = 1 if draft is None else draft["step"]
    indices = range(0, resolved.end, step)
    inputs = ["-f", "rawvideo", "-pix_fmt", preset.pix_fmt, "-s", f"{width}x{height}",
              "-framerate", format_rate(resolved.rate / step), "-i", "-"]
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

    def job(index: int, raws: list, captures: dict) -> tuple[bytes, float]:
        frame = compose(project, resolved, index, raws, captures, threads=1 if workers > 1 else threads,
                        alpha=preset.alpha)
        return frame_bytes(frame, preset, seed=index)

    # Frames are read and graphics captured in order here; their colour work runs on
    # `workers` threads (numpy and OCIO release the GIL) and they are written in order.
    if draft is None:
        with overlay_pages(project, resolved, indices) as pages:
            jobs = ((index, raws, captured(resolved, index, pages))
                    for index, raws in enumerate(raw_layers(project, resolved)))
            for data, outside in in_order(job, jobs, workers=workers, ahead=2 * workers):
                encoder.stdin.write(data)
                clipped, count = clipped + outside, count + 1
    else:
        # the editor's preview chain on the proxies, with the graphics captured at its scale
        pages = overlays.captures(project, resolved, indices, divisor=draft["divisor"])
        with closing(Preview(project, name)) as preview:
            assert preview.hash == hashes["comp"], f"comp {name} changed while the draft began"

            def draft_job(index: int, captures: dict) -> tuple[bytes, float]:
                return frame_bytes(preview.draft(index, captures), preset, seed=index)

            jobs = ((index, captured(resolved, index, pages)) for index in indices)
            for data, outside in in_order(draft_job, jobs, workers=workers, ahead=2 * workers):
                encoder.stdin.write(data)
                clipped, count = clipped + outside, count + 1
    encoder.stdin.close()
    error = encoder.stderr.read().decode(errors="replace").strip()
    assert encoder.wait() == 0 and not error, f"ffmpeg failed encoding {out.name}: {error}"
    assert count == len(indices), f"{count} frames rendered for {len(indices)}"
    if mix_path is not None:
        mix_path.unlink()
    partial.rename(out)
    record = manifest(project, resolved, hashes, preset, out, clipped / count, chain)
    write_atomic(out.with_name(out.name + ".manifest.json"), json_text(record))
    return {"file": str(out), "manifest": str(out.with_name(out.name + ".manifest.json")), "frames": count,
            "seconds": round(resolved.end * ticks_per_frame(resolved.rate) / TICKS_PER_SECOND, 6),
            "clipped_pixels": record["clipped_pixels"], "audio": chain,
            **({} if draft is None else {"draft": draft})}
