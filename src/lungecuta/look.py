"""`lc still COMP`, `lc sheet COMP` and `lc scope COMP`: looking at a comp the way it renders
(docs/design/09-agent-interface.md), so an agent can see an edit without rendering it.

Each frame is the frame `lc render` delivers (`compose.frame_at`: final quality, graphics
included), at the comp's size; the numbers printed come from that float frame. A smaller
picture (`--height`) is that frame area-averaged on its display-encoded values, as a
player shows a smaller copy of the deliverable. A position lands on the first frame at or
after it, as cuts and markers do (D12, *proposed*).
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from .comp import Resolved, load_comp, resolve
from .compose import frame_at, overlay_pages
from .files import write_atomic
from .geometry import area_resize, scaled_size
from .images import png, png8, quantize
from .literals import BeatSpan, Field, parse_literal, resolve_position, resolve_span
from .media import Project
from .scope import SCOPE_DIR, scope_image, scope_numbers
from .sheet import COLUMNS, ROWS, SHEET_DIR, page_paths, pages, tile_size
from .still import STILL_DIR, code_numbers
from .ticks import first_frame_at_or_after, ticks_per_frame


def comp_frame(resolved: Resolved, at: str) -> int:
    """The comp frame a position literal lands on."""
    ticks = resolve_position(parse_literal(at, Field.POSITION), rate=resolved.rate, grid=resolved.grid)
    index = first_frame_at_or_after(ticks, resolved.rate)
    assert index < resolved.end, f"{at} is frame {index}, and the comp has frames 0 to {resolved.end - 1}"
    return index


def smaller(frame: np.ndarray, height: int | None) -> np.ndarray:
    """The frame at `height` lines, area-averaged on its display-encoded values, or as it is."""
    if height is None or height == frame.shape[0]:
        return frame
    return area_resize(frame, scaled_size((frame.shape[1], frame.shape[0]), height))


def shown_layers(resolved: Resolved, index: int) -> list[str]:
    """The layers on screen at comp frame `index`, bottom first."""
    on = {cut.layer.id for cut in resolved.stack(index)}
    on |= {overlay.layer.id for overlay in resolved.overlays if overlay.first <= index < overlay.end}
    on |= {nest.layer.id for nest in resolved.nests_at(index)}
    return [layer.id for layer in resolved.comp.layers if layer.id in on]


def place_of(resolved: Resolved, index: int) -> dict[str, Any]:
    """Where comp frame `index` is: its frame, its seconds, its place on the beat grid (a
    beat coordinate, the beat's index plus the fraction of the next interval), its layers."""
    ticks = index * ticks_per_frame(resolved.rate)
    place: dict[str, Any] = {"frame": index, "seconds": round(float(index / resolved.rate), 6)}
    grid = resolved.grid
    if grid is not None and grid.times[0] <= ticks <= grid.times[-1]:
        place["beat"] = round(float(grid.coordinate_at(ticks)), 4)
    place["layers"] = shown_layers(resolved, index)
    return place


def label(place: dict[str, Any]) -> str:
    beat = f"  b:{place['beat']:.2f}" if "beat" in place else ""
    return f"f:{place['frame']}  {place['seconds']:.3f}s{beat}\n{', '.join(place['layers']) or '(nothing)'}"


def comp_still(project: Project, name: str, at: str, *, height: int | None = None, threads: int | None = None,
               out: Path | None = None) -> dict[str, Any]:
    resolved = resolve(project, load_comp(project, name))
    index = comp_frame(resolved, at)
    frame = frame_at(project, resolved, index, threads)
    picture = smaller(frame, height)
    size = (picture.shape[1], picture.shape[0])
    if out is None:
        out = project.directory / STILL_DIR / f"{name}_f{index}_{size[0]}x{size[1]}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, png(picture))
    settings = project.file.color
    return {"png": str(out), "comp": name, **place_of(resolved, index), "size": list(size),
            "display": settings.display, "view": settings.view, "codes": code_numbers(frame)}


def sheet_frames(resolved: Resolved, every: str, start: str | None, end: str | None) -> list[int]:
    """The comp frames a sheet shows: positions `every` apart from `start` to before `end`,
    each on the first frame at or after it, a frame two positions land on shown once. A
    beat interval steps along the beat grid, from beat 0 or from `start`, so `b:1` is a
    frame on every beat and the comp before the first beat is not in it."""
    rate, grid = resolved.rate, resolved.grid
    first = 0 if start is None else resolve_position(parse_literal(start, Field.POSITION), rate=rate, grid=grid)
    stop = resolved.end * ticks_per_frame(rate)
    if end is not None:
        stop = min(stop, resolve_position(parse_literal(end, Field.POSITION), rate=rate, grid=grid))
    interval = parse_literal(every, Field.LAYER)
    positions = []
    if isinstance(interval, BeatSpan):
        assert grid is not None, f"{every}: the comp has no beat grid"
        assert interval.count > 0, f"an interval of {every} does not move"
        coordinate = Fraction(0) if start is None else grid.coordinate_at(first)
        while grid.contains(coordinate) and (ticks := grid.time_at(coordinate)) < stop:
            positions.append(ticks)
            coordinate += interval.count
    else:
        position = first
        while position < stop:
            positions.append(position)
            step = resolve_span(interval, anchor=position, rate=rate, grid=grid)
            assert step > 0, f"an interval of {every} does not move"
            position += step
    indices: list[int] = []
    for ticks in positions:
        index = first_frame_at_or_after(ticks, rate)
        if index < resolved.end and (not indices or index != indices[-1]):
            indices.append(index)
    assert indices, f"no frame of {resolved.comp.id} lies in the span"
    return indices


def comp_sheet(project: Project, name: str, every: str, *, start: str | None = None, end: str | None = None,
               columns: int = COLUMNS, rows: int = ROWS, height: int | None = None, threads: int | None = None,
               out: Path | None = None) -> dict[str, Any]:
    """A frame at every interval of the comp (a span literal: `b:1`, `f:30`, `0.1s`; see
    `sheet_frames`), each labelled with its frame, seconds, beat and the layers on screen."""
    assert columns >= 1 and rows >= 1, "a page needs at least one column and one row"
    resolved = resolve(project, load_comp(project, name))
    indices = sheet_frames(resolved, every, start, end)
    size = tile_size(tuple(resolved.comp.size), height)
    tiles, numbers = [], []
    per_page = columns * rows
    with overlay_pages(project, resolved) as opened:
        for k, index in enumerate(indices):
            frame = frame_at(project, resolved, index, threads, opened)
            tile = area_resize(frame, size) if size != tuple(resolved.comp.size) else frame
            tiles.append(quantize(tile))
            numbers.append(place_of(resolved, index) | {"page": k // per_page, "codes": code_numbers(frame)})
    images = pages(tiles, [label(place) for place in numbers], columns=columns, rows=rows)
    if out is None:
        out = project.directory / SHEET_DIR / f"{name}_{indices[0]}-{indices[-1]}_{len(indices)}_{size[0]}x{size[1]}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    paths = page_paths(out, len(images))
    for path, image in zip(paths, images):
        write_atomic(path, png8(image))
    settings = project.file.color
    return {"pages": [str(path) for path in paths], "comp": name, "every": every, "size": list(size),
            "columns": columns, "rows": rows, "display": settings.display, "view": settings.view, "tiles": numbers}


def comp_scope(project: Project, name: str, at: str, *, height: int | None = None, threads: int | None = None,
               out: Path | None = None) -> dict[str, Any]:
    resolved = resolve(project, load_comp(project, name))
    index = comp_frame(resolved, at)
    frame = smaller(frame_at(project, resolved, index, threads), height)
    size = (frame.shape[1], frame.shape[0])
    if out is None:
        out = project.directory / SCOPE_DIR / f"{name}_f{index}_{size[0]}x{size[1]}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, png8(scope_image(frame)))
    settings = project.file.color
    return {"png": str(out), "comp": name, **place_of(resolved, index), "size": list(size),
            "display": settings.display, "view": settings.view, **scope_numbers(frame)}
