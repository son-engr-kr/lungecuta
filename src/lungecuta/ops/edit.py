"""A comp being edited, as every operation opens, changes and saves it: the hash it was
read with, its rate and grid, and the changes in editing terms."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..comp import beat_grid, load_comp
from ..files import content_hash, json_text, replace_if_unchanged
from ..literals import TICKS_PER_SECONDS_STEP, Bar, BeatGrid, BeatPosition, BeatSpan, Field, Frames, Seconds, Timecode, TimeLiteral, format_literal, parse_literal, position_literal, resolve_position, resolve_span, source_literal, span_literal
from ..media import Project, load_record
from ..schema.comp import CompFile
from ..ticks import first_frame_at_or_after, parse_rate, ticks_per_frame


def _holds(unit: str, ticks: int, anchor: int, rate: Fraction, grid: BeatGrid | None) -> bool:
    """Whether a span of `ticks` from comp position `anchor` can be written in `unit`
    exactly: frames and timecode in whole frames, seconds to their digits, beats (to the
    nearest tick) when both ends are on the grid."""
    if unit in ("f", "tc"):
        return ticks % ticks_per_frame(rate) == 0
    if unit == "s":
        return ticks % TICKS_PER_SECONDS_STEP == 0
    return grid is not None and grid.times[0] <= min(anchor, anchor + ticks) and max(anchor, anchor + ticks) <= grid.times[-1]


def span_text(old: str | None, given: str | None, ticks: int, anchor: int, rate: Fraction, grid: BeatGrid | None,
              *, frames_land: bool = False, nearest_seconds: bool = False) -> str:
    """A span as an edit writes it: in the unit it has (`old`), unless that cannot hold the
    new length exactly, then in the unit of the time the edit was `given` in (a cut on a
    beat of a layer whose length is in frames is written in beats; a bar counts as beats).
    With `frames_land` (a footage layer's in and out), when neither holds it, in whole
    frames to the frame the time lands on, the first at or after it, which is the frame the
    cut shows either way: a beat cut on a layer that starts at 0 s, before the first beat.
    With `nearest_seconds` (a style's anchor, which is a point and not a cut), in seconds to
    the nearest of their 10 microsecond steps instead."""
    units = [] if old is None else [_unit(parse_literal(old, Field.LAYER))]
    if given is not None:  # a position or an amount: only its unit matters here
        units.append(next(unit for prefix, unit in (("bar:", "b"), ("b:", "b"), ("f:", "f"), ("tc:", "tc"))
                          if given.lstrip("-").startswith(prefix)) if ":" in given else "s")
    units = ["b" if unit == "bar" else unit for unit in units]
    for unit in units:
        if _holds(unit, ticks, anchor, rate, grid):
            return format_literal(span_literal(ticks, unit, anchor=anchor, rate=rate, grid=grid))
    if nearest_seconds:
        steps = (2 * ticks + TICKS_PER_SECONDS_STEP) // (2 * TICKS_PER_SECONDS_STEP)
        return format_literal(span_literal(steps * TICKS_PER_SECONDS_STEP, "s", anchor=anchor, rate=rate, grid=grid))
    tick = ticks_per_frame(rate)
    landed = first_frame_at_or_after(anchor + ticks, rate) * tick - anchor
    assert frames_land and landed % tick == 0, (
        f"a span of {ticks} ticks from {anchor} cannot be written exactly in {' or '.join(units) or 'its unit'}"
    )
    return format_literal(span_literal(landed, "f", anchor=anchor, rate=rate, grid=grid))


def _unit(literal: TimeLiteral) -> str:
    match literal:
        case Frames():
            return "f"
        case Seconds():
            return "s"
        case BeatPosition() | BeatSpan():
            return "b"
        case Bar():
            return "bar"
        case Timecode():
            return "tc"
    raise AssertionError(f"no unit for {literal}")


@dataclass
class Edit:
    """A comp being edited: its file, the hash it was read with, its rate and grid."""

    project: Project
    name: str
    comp: CompFile
    data: dict[str, Any]
    read_hash: str
    rate: Fraction
    grid: BeatGrid | None
    changes: list[str]

    @classmethod
    def open(cls, project: Project, name: str, expect: str | None = None) -> Edit:
        """The comp as it is now; with `expect`, only if its file still has that hash (the
        version the editor showed when the person made the edit)."""
        path = cls.path_of(project, name)
        read_hash = content_hash(path)
        assert expect is None or read_hash == expect, (
            f"comps/{name}.json changed since it was shown ({expect} then, {read_hash} now); nothing written"
        )
        comp = load_comp(project, name)
        rate = parse_rate(comp.rate)
        return cls(project, name, comp, comp.model_dump(mode="json", by_alias=True, exclude_unset=True),
                   read_hash, rate, beat_grid(project, comp, rate), [])

    @staticmethod
    def path_of(project: Project, name: str) -> Path:
        return project.directory / "comps" / f"{name}.json"

    def layer(self, layer_id: str) -> dict[str, Any]:
        found = [layer for layer in self.data["layers"] if layer["id"] == layer_id]
        assert len(found) == 1, f"comp {self.name} has no layer {layer_id!r}"
        return found[0]

    # Reading a layer's times in ticks.
    def start(self, layer: dict) -> int:
        return resolve_position(parse_literal(layer["start"], Field.POSITION), rate=self.rate, grid=self.grid)

    def span(self, layer: dict, key: str) -> int:
        text = layer.get(key, "0s")
        return resolve_span(parse_literal(text, Field.LAYER), anchor=self.start(layer), rate=self.rate, grid=self.grid)

    def media_rate(self, layer: dict) -> Fraction:
        record = load_record(self.project, layer["media"])
        return parse_rate(record.time.rate) if record.time is not None else self.rate

    # Writing times back in the unit they were written in.
    def set_start(self, layer: dict, ticks: int) -> None:
        old = layer["start"]
        unit = _unit(parse_literal(old, Field.POSITION))
        new = format_literal(position_literal(ticks, unit, rate=self.rate, grid=self.grid))
        layer["start"] = new
        self.changes.append(f"{layer['id']} start moved {old} to {new}")

    def set_span(self, layer: dict, key: str, ticks: int, given: str | None = None) -> None:
        """Write a span of the layer; a field never written takes the unit of the amount
        `given` (an absent `in` is 0 in no unit of its own)."""
        old = layer.get(key, "0s")
        anchor = self.start(layer)
        if key in layer or given is not None:
            new = span_text(layer.get(key), given, ticks, anchor, self.rate, self.grid,
                            frames_land=layer["type"] == "footage")
        else:
            new = format_literal(span_literal(ticks, _unit(parse_literal(old, Field.LAYER)), anchor=anchor,
                                              rate=self.rate, grid=self.grid))
        layer[key] = new
        self.changes.append(f"{layer['id']} {'in' if key == 'in' else 'out'} point moved {old} to {new} (from start)")

    def set_source(self, layer: dict, ticks: int) -> None:
        old = layer["source_start"]
        unit = _unit(parse_literal(old, Field.SOURCE_START))
        rate = self.media_rate(layer)
        new = format_literal(source_literal(ticks, unit, media_rate=rate, start=True))
        layer["source_start"] = new
        self.changes.append(f"{layer['id']} source start moved {old} to {new}")

    def save(self) -> dict[str, Any]:
        """Validate the edited comp as a whole, then write it if nobody changed it meanwhile."""
        CompFile.model_validate(self.data)
        path = self.path_of(self.project, self.name)
        replace_if_unchanged(path, self.read_hash, json_text(self.data))
        return {"comp": self.name, "changes": self.changes}


def _delta(edit: Edit, text: str, anchor: int) -> int:
    """A signed amount of comp time, written as a style span is (`f:-3`, `-0.5s`, `b:1`,
    `b:-0.5`): a beat amount counts beats on the grid from `anchor`."""
    return resolve_span(parse_literal(text, Field.STYLE), anchor=anchor, rate=edit.rate, grid=edit.grid)


def _resolves(project: Project, edit: Edit) -> None:
    """The edited comp resolves: values in range, enough source for every frame."""
    from ..comp import resolve

    resolve(project, CompFile.model_validate(edit.data))
