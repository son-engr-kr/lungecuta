"""`lungecuta flash FILE` (docs/design/09-agent-interface.md, Lint): WCAG 2.2's general flash
threshold checked on a rendered file, the frames a viewer is shown.

WCAG 2.2 (https://www.w3.org/TR/WCAG22/, "general flash and red flash thresholds", read
2026-10-13):
a general flash is "a pair of opposing changes in relative luminance of 10% or more of the
maximum relative luminance (1.0) where the relative luminance of the darker image is below
0.80", and content passes if there are "no more than three general flashes ... within any
one-second period", or if "the combined area of flashes occurring concurrently occupies no
more than ... 25% of any 10 degree visual field on the screen". A transition is the change
"between adjacent peaks and valleys in a plot of relative luminance ... against time. A
flash consists of two opposing transitions."

Read from the definition, not chosen: relative luminance is the display's own (WCAG uses
sRGB "unless it is known that another color space will be used"), here CIE Y of the
decoded R'G'B' through the display's EOTF as OCIO's config gives it, which for
`sRGB - Display` is WCAG's formula; a transition is at least 0.1, so a peak or a valley is
confirmed once the luminance has turned back by 0.1; more than three flashes is more than
six transitions.

Chosen where the definition gives no number for a video frame (*proposed*):
- the frame is the screen, filling its height, so the 10 degree field (WCAG's Note 1: a
  341 x 256 rectangle of a 1024 x 768 screen) is a rectangle of that shape a third of the
  frame's height;
- luminance is averaged over cells a three-hundredth of the frame's height, a tenth of a
  degree of that field: WCAG excepts "a fine, balanced, pattern" whose squares are smaller
  than 0.1 degree, and averaging over cells that size is what removes one;
- flashes are concurrent when each cell's own more-than-six transitions fall in a
  one-second span that holds the frame.

Not checked: red flashes. WCAG 2.2's working definition takes "R/(R + G + B)" from
ISO 9241-391 without saying whether R, G and B are linear or encoded, or in which
primaries, and that standard is not read.
"""
from __future__ import annotations

import functools
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import PyOpenColorIO as ocio

from . import color
from .decode import frame_rgb, raw_frames
from .geometry import area_resize
from .tools import run

CHANGE = 0.1  # "10% or more of the maximum relative luminance (1.0)"
DARKER_BELOW = 0.8  # "where the relative luminance of the darker image is below 0.80"
TRANSITIONS = 6  # "no more than three general flashes", two transitions each
AREA = 0.25  # "25% of any 10 degree visual field on the screen"
FIELD_ROWS = 100  # the field's height in cells: 10 degrees in cells of 0.1 degree
CELL_ROWS = 3 * FIELD_ROWS  # the field is a third of the frame's height (Note 1: 256 of 768)
FIELD_SHAPE = Fraction(341, 256)  # Note 1's rectangle, width over height
TAG_MATRIX = {"bt709": "bt709", "bt2020nc": "bt2020nc"}
TAG_RANGE = {"tv": "limited", "pc": "full"}


@functools.cache
def luminance_processor(config_name: str, display: str, view: str) -> ocio.CPUProcessor:
    """Display-encoded R'G'B' to CIE XYZ through the display's own EOTF (the inverse of the
    display colour space's transform from OCIO's display reference); Y is relative
    luminance, 1 at the display's white."""
    cfg = color.config(config_name)
    name = cfg.getDisplayViewColorSpaceName(display, view)
    name = display if name == "<USE_DISPLAY_NAME>" else name
    space = cfg.getColorSpace(name)
    assert space is not None, f"{name!r} is not a colour space of {config_name}"
    assert space.getReferenceSpaceType() == ocio.REFERENCE_SPACE_DISPLAY, f"{name!r} is not a display colour space"
    transform = space.getTransform(ocio.COLORSPACE_DIR_FROM_REFERENCE)
    assert transform is not None, f"{name!r} has no transform from the display reference"
    return cfg.getProcessor(transform, ocio.TRANSFORM_DIR_INVERSE).getDefaultCPUProcessor()


class Transitions:
    """Every cell's transitions, found as its luminance runs: a peak or a valley is
    confirmed once the luminance has come back from it by `CHANGE`, so every transition
    between two confirmed extremes is at least `CHANGE`. Each is kept as the frame it ends
    on, and only when its darker end is below `DARKER_BELOW`."""

    def __init__(self, count: int) -> None:
        self.direction = np.zeros(count, np.int8)  # 0 before the first extreme, 1 rising, -1 falling
        self.last = np.zeros(count, np.float32)  # the last confirmed extreme
        self.extreme = np.zeros(count, np.float32)  # the extreme of the run in progress
        self.at = np.zeros(count, np.int64)  # the frame it was reached on
        self.low = np.zeros(count, np.float32)  # before the first extreme: the lowest and highest
        self.high = np.zeros(count, np.float32)
        self.ends: list[tuple[np.ndarray, np.ndarray]] = []  # (cells, frames)

    def push(self, frame: int, value: np.ndarray) -> None:
        if frame == 0:
            self.low[:] = self.high[:] = value
            return
        start = self.direction == 0
        np.minimum(self.low, value, out=self.low, where=start)
        np.maximum(self.high, value, out=self.high, where=start)
        rises, falls = start & (value - self.low >= CHANGE), start & (self.high - value >= CHANGE)
        for begun, direction, origin in ((rises, 1, self.low), (falls & ~rises, -1, self.high)):
            self.direction[begun], self.last[begun] = direction, origin[begun]
            self.extreme[begun], self.at[begun] = value[begun], frame

        rising, falling = self.direction == 1, self.direction == -1
        further = (rising & (value > self.extreme)) | (falling & (value < self.extreme))
        self.extreme[further], self.at[further] = value[further], frame
        turned = (rising & (self.extreme - value >= CHANGE)) | (falling & (value - self.extreme >= CHANGE))
        self._end(turned)
        self.last[turned] = self.extreme[turned]
        self.direction[turned] *= -1
        self.extreme[turned], self.at[turned] = value[turned], frame

    def _end(self, cells: np.ndarray) -> None:
        darker = np.minimum(self.last, self.extreme)
        kept = cells & (darker < DARKER_BELOW)
        if kept.any():
            self.ends.append((np.flatnonzero(kept), self.at[kept].copy()))

    def finish(self) -> tuple[np.ndarray, np.ndarray]:
        """The run in progress at the end is a transition too; then every transition kept,
        as (cells, frames) sorted by cell and frame."""
        self._end(self.direction != 0)
        if not self.ends:
            return np.zeros(0, np.int64), np.zeros(0, np.int64)
        cells = np.concatenate([c for c, _ in self.ends])
        frames = np.concatenate([f for _, f in self.ends])
        order = np.lexsort((frames, cells))
        return cells[order], frames[order]


def flashing_spans(cells: np.ndarray, frames: np.ndarray, rate: Fraction) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(cells, first frames, last frames) wherever a cell has more than `TRANSITIONS`
    transitions inside one second: the first and the last of them less than a second apart."""
    n = TRANSITIONS
    if len(cells) <= n:
        empty = np.zeros(0, np.int64)
        return empty, empty, empty
    same = cells[n:] == cells[:-n]
    within = (frames[n:] - frames[:-n]) * rate.denominator < rate.numerator  # under a second
    hits = np.flatnonzero(same & within)
    return cells[hits], frames[hits], frames[hits + n]


def _largest_share(flashing: np.ndarray, field: tuple[int, int]) -> tuple[float, tuple[int, int]]:
    """The largest share of any field-sized rectangle the flashing cells cover, and where
    that rectangle's top-left cell is (column, row)."""
    columns, rows = field
    summed = np.zeros((flashing.shape[0] + 1, flashing.shape[1] + 1), np.int64)
    summed[1:, 1:] = flashing.cumsum(0).cumsum(1)
    boxes = summed[rows:, columns:] - summed[:-rows, columns:] - summed[rows:, :-columns] + summed[:-rows, :-columns]
    row, column = np.unravel_index(int(boxes.argmax()), boxes.shape)
    return float(boxes[row, column]) / (rows * columns), (int(column), int(row))


def probe(file: Path) -> dict[str, Any]:
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height,pix_fmt,r_frame_rate,nb_frames,color_space,color_range,chroma_location",
               "-of", "json", str(file)]).stdout
    return json.loads(out)["streams"][0]


def flash(file: Path, *, config_name: str, display: str, view: str, threads: int | None = None) -> dict[str, Any]:
    video = probe(file)
    width, height = int(video["width"]), int(video["height"])
    assert video.get("color_space") in TAG_MATRIX, f"{file.name}: matrix tag {video.get('color_space')!r} is not one lungecuta reads"
    assert video.get("color_range") in TAG_RANGE, f"{file.name}: range tag {video.get('color_range')!r} is not one lungecuta reads"
    assert str(video.get("nb_frames", "")).isdigit(), f"{file.name}: the container gives no frame count"
    count = int(video["nb_frames"])
    num, den = (int(x) for x in video["r_frame_rate"].split("/"))
    rate = Fraction(num, den)
    if height > CELL_ROWS:
        grid = (max(1, round(width * CELL_ROWS / height)), CELL_ROWS)
    else:  # every pixel is a tenth of a degree or more already: no finer pattern exists
        grid = (width, height)
    field_rows = max(1, round(grid[1] * FIELD_ROWS / CELL_ROWS))
    field = (round(field_rows * FIELD_SHAPE), field_rows)
    assert field[0] <= grid[0], f"{file.name} is narrower than WCAG's 10 degree field at its height"
    processor = luminance_processor(config_name, display, view)
    tracker = Transitions(grid[0] * grid[1])
    location = video.get("chroma_location", "unspecified")
    for index, data in enumerate(raw_frames(file, pix_fmt=video["pix_fmt"], size=(width, height), count=count)):
        rgb = frame_rgb(data, (width, height), pix_fmt=video["pix_fmt"], chroma_location=location,
                        matrix=TAG_MATRIX[video["color_space"]], range_=TAG_RANGE[video["color_range"]])
        np.clip(rgb, 0, 1, out=rgb)  # what a display can show of codes outside nominal black and white
        color.apply(processor, rgb, threads)
        luminance = np.ascontiguousarray(rgb[..., 1:2])
        cells = area_resize(luminance, grid) if grid != (width, height) else luminance
        tracker.push(index, cells.reshape(-1))
    cells, frames = tracker.finish()
    spans = flashing_spans(cells, frames, rate)

    span_cells, firsts, lasts = spans
    cover = np.zeros(count + 1, np.int64)
    np.add.at(cover, firsts, 1)
    np.add.at(cover, lasts + 1, -1)
    shares: dict[int, tuple[float, tuple[int, int]]] = {}
    for frame in np.flatnonzero(np.cumsum(cover)[:count] > 0):
        flashing = np.zeros(grid[0] * grid[1], bool)
        flashing[span_cells[(firsts <= frame) & (lasts >= frame)]] = True
        shares[int(frame)] = _largest_share(flashing.reshape(grid[1], grid[0]), field)
    failing = [frame for frame, (share, _) in shares.items() if share > AREA]
    failures = []
    for frame in failing:
        share, (column, row) = shares[frame]
        if failures and frame == failures[-1]["to_frame"] + 1:
            run_ = failures[-1]
            run_["to_frame"] = frame
            if share > run_["share"]:
                run_["share"], run_["field_centre"] = round(share, 3), _centre(column, row, field, grid)
        else:
            failures.append({"from_frame": frame, "to_frame": frame, "share": round(share, 3),
                             "field_centre": _centre(column, row, field, grid)})
    return {
        "file": file.name,
        "standard": "WCAG 2.2, general flash threshold (success criterion 2.3.1)",
        "ok": not failures,
        "failures": failures,
        "frames": count,
        "transitions": int(len(frames)),
        "cells_flashing_somewhere": int(len(np.unique(span_cells))),
        "largest_share": round(max((s for s, _ in shares.values()), default=0.0), 3),
        "cells": list(grid),
        "field_cells": list(field),
        "display": display,
        "not_checked": ["red flashes: WCAG 2.2's working definition comes from ISO 9241-391, not read"],
    }


def _centre(column: int, row: int, field: tuple[int, int], grid: tuple[int, int]) -> list[float]:
    """The middle of a field-sized rectangle at a top-left cell, in fractions of the frame."""
    return [round((column + field[0] / 2) / grid[0], 3), round((row + field[1] / 2) / grid[1], 3)]
