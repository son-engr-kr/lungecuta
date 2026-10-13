"""A comp being edited, as every operation opens, changes and saves it: the hash it was
read with, its rate and grid, and the changes in editing terms."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..comp import beat_grid, load_comp
from ..files import content_hash, json_text, replace_if_unchanged
from ..literals import Bar, BeatGrid, BeatPosition, BeatSpan, Field, Frames, Seconds, Timecode, TimeLiteral, format_literal, parse_literal, position_literal, resolve_position, resolve_span, source_literal, span_literal
from ..media import Project, load_record
from ..schema.comp import CompFile
from ..ticks import parse_rate


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
        unit = _unit(parse_literal(old, Field.LAYER)) if key in layer or given is None else \
            _unit(parse_literal(given, Field.STYLE))
        anchor = self.start(layer)
        new = format_literal(span_literal(ticks, unit, anchor=anchor, rate=self.rate, grid=self.grid))
        layer[key] = new
        self.changes.append(f"{layer['id']} {'in' if key == 'in' else 'out'} point moved {old} to {new} (from start)")

    def set_source(self, layer: dict, ticks: int) -> None:
        old = layer["source_start"]
        unit = _unit(parse_literal(old, Field.SOURCE))
        rate = self.media_rate(layer)
        new = format_literal(source_literal(ticks, unit, media_rate=rate))
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
