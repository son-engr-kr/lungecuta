"""`lungecuta framing COMP LAYER` (docs/design/09-agent-interface.md): where the comp's frame sits in
a footage layer's own frame, the check a reframe needs (a 9:16 cut from 16:9 footage, a
push-in that must not show the source's edge).

The comp's corners are taken back through the layer's transform on a frame (position,
anchor, scale, rotation, as `compose.transform_at` gives them and `geometry.place`
applies them) into the layer's frame as shown, in fractions of its width and height. The
layer is a rectangle and the comp's view of it a parallelogram, so the comp is covered
exactly when all four corners fall inside the layer; every visible frame is checked that
way, and the frames where the source's edge shows are reported as runs. Masks, opacity and
transitions also uncover the frame, deliberately, and are not counted here.

The picture is the layer's frame as shown on its first visible frame, through the output
view, with a grid every tenth of its width and height, the comp's window on its first
visible frame (green), on its last (red) and on each asked-for position (yellow).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from . import color
from .comp import Cut, load_comp, resolve
from .compose import layer_frame, transform_at
from .files import write_atomic
from .geometry import area_resize, scaled_size, shown_size
from .images import png8, quantize
from .look import comp_frame
from .media import Project

FRAMING_DIR = Path("cache") / "lungecuta" / "framing"
GRID = 0.1  # the grid's spacing, in fractions of the frame (the design's "0.1 grid")
TOLERANCE = 1e-6  # pixels: a corner on the edge is inside
COLOURS = {"first": (80, 220, 120), "last": (240, 80, 80), "at": (250, 220, 60)}
GRID_COLOUR = (90, 90, 90)
DEFAULT_HEIGHT = 540  # lines of the picture, the project's default proxy height


def window(cut: Cut, index: int, comp: tuple[int, int]) -> list[tuple[float, float]]:
    """The comp's corners (top left, top right, bottom right, bottom left) on comp frame
    `index`, in fractions of the layer's frame as shown."""
    width, height = shown_size(cut.record.probe.video.size, cut.record.probe.video.rotation)
    position, scale, _, anchor, rotation = transform_at(cut.placement, cut.comp_rate, index)
    turn = math.radians(rotation)
    cos, sin = math.cos(turn), math.sin(turn)
    out = []
    for cx, cy in ((0, 0), (comp[0], 0), (comp[0], comp[1]), (0, comp[1])):
        dx, dy = cx - position[0] * comp[0], cy - position[1] * comp[1]
        local_x = (cos * dx + sin * dy) / scale[0] + anchor[0] * width
        local_y = (-sin * dx + cos * dy) / scale[1] + anchor[1] * height
        out.append((local_x / width, local_y / height))
    return out


def covered(corners: list[tuple[float, float]], size: tuple[int, int]) -> bool:
    width, height = size
    return all(-TOLERANCE <= x * width <= width + TOLERANCE and -TOLERANCE <= y * height <= height + TOLERANCE
               for x, y in corners)


def _runs(frames: list[int]) -> list[dict[str, int]]:
    runs: list[dict[str, int]] = []
    for frame in frames:
        if runs and frame == runs[-1]["to_frame"] + 1:
            runs[-1]["to_frame"] = frame
        else:
            runs.append({"from_frame": frame, "to_frame": frame})
    return runs


def framing(project: Project, name: str, layer_id: str, at: tuple[str, ...] = (), *, height: int = DEFAULT_HEIGHT,
            threads: int | None = None, out: Path | None = None) -> dict[str, Any]:
    resolved = resolve(project, load_comp(project, name))
    cuts = {cut.layer.id: cut for cut in resolved.cuts}
    assert layer_id in cuts, f"comp {name} has no footage layer {layer_id!r}"
    cut = cuts[layer_id]
    comp = tuple(resolved.comp.size)
    video = cut.record.probe.video
    size = shown_size(video.size, video.rotation)
    marks = [("first", cut.first), ("last", cut.end - 1)]
    for text in at:
        index = comp_frame(resolved, text)
        assert cut.first <= index < cut.end, f"{text} is frame {index}, where {layer_id} is not visible"
        marks.append(("at", index))
    windows = [{"which": which, "frame": index, "seconds": round(float(index / resolved.rate), 6),
                "corners": [[round(x, 5), round(y, 5)] for x, y in window(cut, index, comp)]}
               for which, index in marks]
    for entry in windows:
        entry["covered"] = covered(entry["corners"], size)
    uncovered = [index for index in range(cut.first, cut.end) if not covered(window(cut, index, comp), size)]

    frame = layer_frame(project, cut, cut.first, threads)
    picture_size = scaled_size(size, min(height, size[1]))
    if picture_size != size:
        frame = area_resize(frame, picture_size)
    settings = project.file.color
    color.apply(color.view_processor(settings.config, settings.working, settings.display, settings.view), frame, threads)
    image = Image.fromarray(quantize(frame))
    draw = ImageDraw.Draw(image)
    w, h = picture_size
    for k in range(1, round(1 / GRID)):
        draw.line([(k * GRID * w, 0), (k * GRID * w, h)], fill=GRID_COLOUR)
        draw.line([(0, k * GRID * h), (w, k * GRID * h)], fill=GRID_COLOUR)
    for entry in windows:
        points = [(x * w, y * h) for x, y in entry["corners"]]
        draw.line(points + points[:1], fill=COLOURS[entry["which"]], width=2)
    if out is None:
        out = project.directory / FRAMING_DIR / f"{name}_{layer_id}_{w}x{h}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, png8(np.asarray(image)))
    return {"png": str(out), "comp": name, "layer": layer_id, "media": cut.record.key, "source_size": list(size),
            "windows": windows, "uncovered": _runs(uncovered), "grid": GRID}
