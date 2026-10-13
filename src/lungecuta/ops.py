"""Edit operations (docs/design/06-editing.md#edit-operations): the functions `lc op` and
the editor both call, so an agent never ripples a timeline by editing every later
layer's JSON by hand.

Each operation changes the fields After Effects' model says it changes (D11,
03-project-format.md#layer-timing): a move changes `start` and carries the keyframes; a
trim changes `in` or `out`; a slip changes `source_start`; slide, roll and ripple follow
from those. A value keeps the unit it was written in, so a beat-anchored edit stays
beat-anchored: the new time is computed in ticks and written back in the field's own
unit, which must hold it exactly (or, for beats, to the nearest tick). The comp file is
written only if it is still the one read, and each change is reported in editing terms.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from .comp import beat_grid, load_comp
from .files import content_hash, json_text, replace_if_unchanged
from .literals import (
    TICKS_PER_SECONDS_STEP,
    Bar,
    BeatGrid,
    BeatPosition,
    BeatSpan,
    Field,
    Frames,
    Seconds,
    Timecode,
    TimeLiteral,
    format_literal,
    parse_literal,
    position_literal,
    resolve_position,
    resolve_source,
    resolve_span,
    source_literal,
    span_literal,
)
from .media import Project, load_record
from .schema.comp import CompFile
from .ticks import parse_rate, ticks_per_frame


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


def move(project: Project, name: str, layer_id: str, by: str, expect: str | None = None) -> dict[str, Any]:
    """Move a layer in the comp: its start changes, and everything in layer time (keys,
    in and out points, markers) comes along."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    start = edit.start(layer)
    edit.set_start(layer, start + _delta(edit, by, start))
    return edit.save()


def trim(project: Project, name: str, layer_id: str, side: str, by: str, expect: str | None = None) -> dict[str, Any]:
    """Move a layer's in or out point by `by`; the start, the keys and the source stay."""
    assert side in ("in", "out"), f"a trim moves the in or the out point, not {side!r}"
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    point = edit.start(layer) + edit.span(layer, side)
    moved = point + _delta(edit, by, point)
    edit.set_span(layer, side, moved - edit.start(layer), given=by)
    assert edit.span(layer, "in") <= edit.span(layer, "out"), f"{layer_id}: the trim puts in after out"
    return edit.save()


def slip(project: Project, name: str, layer_id: str, by: str, expect: str | None = None) -> dict[str, Any]:
    """Move the content under a layer's fixed in and out points: its source start
    changes by `by` of source time (f: at the media's rate, or seconds)."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    rate = edit.media_rate(layer)
    literal = parse_literal(by, Field.STYLE)
    assert isinstance(literal, Frames | Seconds), f"a slip is in source frames or seconds, not {by}"
    amount = literal.count * ticks_per_frame(rate) if isinstance(literal, Frames) else literal.ticks
    source = resolve_source(parse_literal(layer["source_start"], Field.SOURCE), media_rate=rate)
    edit.set_source(layer, source + amount)
    return edit.save()


def roll(project: Project, name: str, left_id: str, right_id: str, by: str, expect: str | None = None) -> dict[str, Any]:
    """Move the cut between two adjacent layers: the left one's out point and the right
    one's in point move together."""
    edit = Edit.open(project, name, expect)
    left, right = edit.layer(left_id), edit.layer(right_id)
    cut = edit.start(left) + edit.span(left, "out")
    assert cut == edit.start(right) + edit.span(right, "in"), f"{left_id} and {right_id} do not meet at a cut"
    moved = cut + _delta(edit, by, cut)
    edit.set_span(left, "out", moved - edit.start(left), given=by)
    edit.set_span(right, "in", moved - edit.start(right), given=by)
    for layer in (left, right):
        assert edit.span(layer, "in") <= edit.span(layer, "out"), f"{layer['id']}: the roll puts in after out"
    return edit.save()


def slide(project: Project, name: str, layer_id: str, by: str, expect: str | None = None) -> dict[str, Any]:
    """Slide a footage layer between its neighbours: it moves as a whole (its content and
    keys come along), the layer that ends where it began has its out point moved with it,
    and the one that begins where it ended has its in point moved with it, so no gap
    opens and nothing overlaps. One amount in ticks, counted from the layer's in point,
    moves all three, so the cuts stay exactly shared."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == "footage", f"{layer_id} is not a footage layer"
    start = edit.start(layer)
    begins, ends = start + edit.span(layer, "in"), start + edit.span(layer, "out")
    delta = _delta(edit, by, begins)
    footage = [other for other in edit.data["layers"] if other["type"] == "footage" and other is not layer]
    before = [o for o in footage if edit.start(o) + edit.span(o, "out") == begins]
    after = [o for o in footage if edit.start(o) + edit.span(o, "in") == ends]
    assert before or after, f"{layer_id} meets no other footage layer at a cut; move it instead"
    for other in before:
        edit.set_span(other, "out", begins + delta - edit.start(other), given=by)
    for other in after:
        assert ends + delta >= edit.start(other), (
            f"{other['id']}'s in point would move before its start, where its layer time begins; "
            f"move {other['id']} earlier first"
        )
        edit.set_span(other, "in", ends + delta - edit.start(other), given=by)
    edit.set_start(layer, start + delta)
    for other in before + after:
        assert edit.span(other, "in") <= edit.span(other, "out"), f"{other['id']}: the slide puts its in after its out"
    _resolves(project, edit)
    return edit.save()


def ripple_delete(project: Project, name: str, layer_id: str, expect: str | None = None) -> dict[str, Any]:
    """Remove a layer and close the gap: every layer that starts at or after its out point
    moves earlier by its visible length."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    visible_in = edit.start(layer) + edit.span(layer, "in")
    visible_out = edit.start(layer) + edit.span(layer, "out")
    length = visible_out - visible_in
    edit.data["layers"] = [other for other in edit.data["layers"] if other["id"] != layer_id]
    edit.changes.append(f"{layer_id} removed")
    for other in edit.data["layers"]:
        if other["type"] == "audio" and edit.comp.beats and other["id"] in edit.comp.beats.from_layers:
            continue  # the music the grid comes from stays where it is
        if edit.start(other) + edit.span(other, "in") >= visible_out:
            edit.set_start(other, edit.start(other) - length)
    return edit.save()


def split(project: Project, name: str, layer_id: str, at: str, expect: str | None = None) -> dict[str, Any]:
    """Cut a layer in two at comp position `at`: the first part ends there and a copy,
    `ID.2`, starts there, both with the same start, source and keys, as After Effects'
    split does."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    point = resolve_position(parse_literal(at, Field.POSITION), rate=edit.rate, grid=edit.grid)
    start = edit.start(layer)
    assert start + edit.span(layer, "in") < point < start + edit.span(layer, "out"), (
        f"{at} is not inside {layer_id}'s visible range"
    )
    second = dict(layer, id=f"{layer_id}.2")
    assert all(other["id"] != second["id"] for other in edit.data["layers"]), f"{second['id']} exists already"
    edit.set_span(layer, "out", point - start, given=at)
    unit = _unit(parse_literal(layer["in"], Field.LAYER)) if "in" in layer else _unit(parse_literal(at, Field.POSITION))
    unit = "b" if unit == "bar" else unit
    second["in"] = format_literal(span_literal(point - start, unit, anchor=start, rate=edit.rate, grid=edit.grid))
    edit.data["layers"].insert(edit.data["layers"].index(layer) + 1, second)
    edit.changes.append(f"{layer_id} split at {at}: {second['id']} from {second['in']}")
    return edit.save()


def snap(project: Project, name: str, layer_id: str, expect: str | None = None) -> dict[str, Any]:
    """Put a layer's visible in and out points on the nearest beats of the grid, written as
    beat spans; the start stays."""
    edit = Edit.open(project, name, expect)
    assert edit.grid is not None, f"comp {name} has no beat grid"
    layer = edit.layer(layer_id)
    start = edit.start(layer)
    for side in ("in", "out"):
        point = start + edit.span(layer, side)
        nearest = min(edit.grid.times, key=lambda beat: abs(beat - point))
        assert nearest >= start, f"{layer_id}: the nearest beat to its {side} point is before its start"
        old = layer.get(side, "0s")
        layer[side] = format_literal(span_literal(nearest - start, "b", anchor=start, rate=edit.rate, grid=edit.grid))
        edit.changes.append(f"{layer_id} {side} point snapped {old} to {layer[side]} (from start)")
    assert edit.span(layer, "in") <= edit.span(layer, "out"), f"{layer_id}: snapping puts in after out"
    return edit.save()


def style(project: Project, name: str, layer_id: str, style_id: str, at: str, intensity: float | None = None,
          expect: str | None = None) -> dict[str, Any]:
    """Place style `style_id` on a footage layer, its anchor on the comp beat `at` (written
    as a beat span from the layer's start, so it moves with the layer). A layer has one
    style instance: placing one replaces the one before, as Pickapicka's presets do. The
    layer must play from a source start (not remap keys or a constant speed)."""
    from .comp import load_style, resolve

    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == "footage", f"{layer_id} is not a footage layer"
    old = layer.get("time")
    assert old is None or "style" in old, f"{layer_id} has its own time ({old}); a style replaces only a style"
    load_style(project, style_id)
    literal = parse_literal(at, Field.POSITION)
    assert edit.grid is not None, f"comp {name} has no beat grid"
    point = resolve_position(literal, rate=edit.rate, grid=edit.grid)
    start = edit.start(layer)
    assert point >= start, f"{at} is before {layer_id}'s start"
    instance: dict[str, Any] = {
        "style": style_id,
        "at": format_literal(span_literal(point - start, "b", anchor=start, rate=edit.rate, grid=edit.grid)),
    }
    if intensity is not None:
        instance["overrides"] = {"intensity": intensity}
    layer["time"] = instance
    resolve(project, CompFile.model_validate(edit.data))  # the placement compiles: keys on the grid, values in range
    verb = "replaced" if old else "placed"
    edit.changes.append(f"{layer_id}: style {style_id} {verb} at {at} ({instance['at']} from start)"
                        + ("" if intensity is None else f", intensity {intensity}"))
    return edit.save()


def unstyle(project: Project, name: str, layer_id: str, expect: str | None = None) -> dict[str, Any]:
    """Take a layer's style instance off: it plays at 100 % from its source start again."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    old = layer.get("time")
    assert old is not None and "style" in old, f"{layer_id} has no style instance"
    del layer["time"]
    edit.changes.append(f"{layer_id}: style {old['style']} taken off")
    return edit.save()


def transition(project: Project, name: str, kind: str, layer_ids: list[str], length: str,
               expect: str | None = None) -> dict[str, Any]:
    """Put a transition on a cut (a dissolve or a dip between two adjacent footage layers)
    or on one layer's in or out point (a fade); one of the same kind on the same layers is
    replaced. The comp is resolved before it is written, so a dissolve without the source
    handles it needs is refused."""
    edit = Edit.open(project, name, expect)
    for layer_id in layer_ids:
        edit.layer(layer_id)
    transitions = edit.data.setdefault("transitions", [])
    kept = [t for t in transitions if not (t["type"] == kind and t["layers"] == layer_ids)]
    verb = "replaced" if len(kept) < len(transitions) else "added"
    kept.append({"type": kind, "layers": layer_ids, "length": length})
    edit.data["transitions"] = kept
    _resolves(project, edit)
    edit.changes.append(f"{kind} of {length} {verb} on {' to '.join(layer_ids)}")
    return edit.save()


def untransition(project: Project, name: str, kind: str, layer_ids: list[str], expect: str | None = None) -> dict[str, Any]:
    """Take a transition off."""
    edit = Edit.open(project, name, expect)
    transitions = edit.data.get("transitions", [])
    kept = [t for t in transitions if not (t["type"] == kind and t["layers"] == layer_ids)]
    assert len(kept) < len(transitions), f"comp {name} has no {kind} on {' to '.join(layer_ids)}"
    if kept:
        edit.data["transitions"] = kept
    else:
        del edit.data["transitions"]
    edit.changes.append(f"{kind} on {' to '.join(layer_ids)} taken off")
    return edit.save()


KEYED = ("position", "scale", "opacity", "rotation", "remap", "gain")
LAYER_OF = {"position": "footage", "scale": "footage", "opacity": "footage", "rotation": "footage", "remap": "footage",
            "gain": "audio"}


def _key_time(edit: Edit, layer: dict, at: str) -> tuple[int, str]:
    """A comp position as a key's layer time: its tick and the span from the layer's start,
    written in the unit `at` was given in (a bar as beats, a timecode as frames), except
    that a frame position on a layer whose start is not on a frame is written in beats,
    since no whole number of frames spans from a beat to a frame."""
    literal = parse_literal(at, Field.POSITION)
    point = resolve_position(literal, rate=edit.rate, grid=edit.grid)
    start = edit.start(layer)
    assert point >= start, f"{at} is before {layer['id']}'s start, where layer time begins"
    unit = _unit(literal)
    unit = "b" if unit in ("b", "bar") else "f" if unit == "tc" else unit
    if unit == "f" and start % ticks_per_frame(edit.rate):
        assert edit.grid is not None, f"{layer['id']} starts between frames and the comp has no beat grid"
        unit = "b"
    return point, format_literal(span_literal(point - start, unit, anchor=start, rate=edit.rate, grid=edit.grid))


def _keys(edit: Edit, layer: dict, prop: str) -> list[dict]:
    """The property's keys as the file holds them, a constant turned into none."""
    if prop == "remap":
        time = layer.get("time") or {}
        assert "remap" in time, f"{layer['id']} is not remapped; enable time remapping first (lc op remap)"
        return time["remap"]
    current = layer.get("gain") if prop == "gain" else (layer.get("transform") or {}).get(prop)
    return list(current["keys"]) if isinstance(current, dict) else []


def _key_tick(edit: Edit, layer: dict, key: dict) -> int:
    return edit.start(layer) + resolve_span(parse_literal(key["t"], Field.LAYER), anchor=edit.start(layer),
                                            rate=edit.rate, grid=edit.grid)


def _put_keys(layer: dict, prop: str, keys: list[dict]) -> None:
    if prop == "remap":
        layer["time"]["remap"] = keys
    elif prop == "gain":
        layer["gain"] = {"keys": keys}
    else:
        layer.setdefault("transform", {})[prop] = {"keys": keys}


def key(project: Project, name: str, layer_id: str, prop: str, at: str, value: Any, ease: str | None = None,
        expect: str | None = None) -> dict[str, Any]:
    """Set a key of a footage layer's property at comp position `at`, replacing a key at
    that time or adding one, as After Effects' stopwatch does: the first key of a constant
    property makes it animated (one key is still a constant). `value` is a number or a pair
    for the transform, a source time literal for `remap`."""
    assert prop in KEYED, f"{prop!r} is not a property with keys ({', '.join(KEYED)})"
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == LAYER_OF[prop], f"{prop} is a property of {LAYER_OF[prop]} layers, and {layer_id} is not one"
    point, t = _key_time(edit, layer, at)
    keys = _keys(edit, layer, prop)
    new = {"t": t, "v": value}
    if ease is not None:
        new["ease"] = ease
    ticks = [_key_tick(edit, layer, k) for k in keys]
    if point in ticks:
        old = keys[ticks.index(point)]
        keys[ticks.index(point)] = new
        edit.changes.append(f"{layer_id} {prop} key at {t} changed {old['v']} to {value}"
                            + (f", ease {ease}" if ease else ""))
    else:
        keys.insert(sum(tick < point for tick in ticks), new)
        edit.changes.append(f"{layer_id} {prop} key added at {t}: {value}" + (f", ease {ease}" if ease else ""))
    _put_keys(layer, prop, keys)
    _resolves(project, edit)
    return edit.save()


def _resolves(project: Project, edit: Edit) -> None:
    """The edited comp resolves: values in range, enough source for every frame."""
    from .comp import resolve

    resolve(project, CompFile.model_validate(edit.data))


def unkey(project: Project, name: str, layer_id: str, prop: str, at: str, expect: str | None = None) -> dict[str, Any]:
    """Delete the key of a property at comp position `at`. The last transform key leaves
    its value as the property's constant; a remap keeps at least two keys."""
    assert prop in KEYED, f"{prop!r} is not a property with keys ({', '.join(KEYED)})"
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    point, _ = _key_time(edit, layer, at)
    keys = _keys(edit, layer, prop)
    ticks = [_key_tick(edit, layer, k) for k in keys]
    assert point in ticks, f"{layer_id} has no {prop} key at {at}"
    removed = keys.pop(ticks.index(point))
    if prop == "remap":
        assert len(keys) >= 2, f"a remap needs two keys; {layer_id} would keep {len(keys)}"
        _put_keys(layer, prop, keys)
    elif keys:
        _put_keys(layer, prop, keys)
    elif prop == "gain":
        layer["gain"] = removed["v"]
    else:
        layer["transform"][prop] = removed["v"]
    edit.changes.append(f"{layer_id} {prop} key at {removed['t']} deleted"
                        + ("" if keys else f"; {prop} stays {removed['v']}"))
    _resolves(project, edit)
    return edit.save()


def key_position(edit: Edit, layer: dict, key: dict) -> str:
    """A key's comp position, as `key` and `unkey` take it: in its own time's unit, or in
    beats where that unit cannot hold the position (the rule `_key_time` writes by)."""
    tick = _key_tick(edit, layer, key)
    unit = _unit(parse_literal(key["t"], Field.LAYER))
    if unit == "f" and tick % ticks_per_frame(edit.rate) == 0:
        return format_literal(position_literal(tick, "f", rate=edit.rate))
    if unit == "s" and tick % TICKS_PER_SECONDS_STEP == 0:
        return format_literal(position_literal(tick, "s", rate=edit.rate))
    assert edit.grid is not None, f"{layer['id']}: the key at {key['t']} has no comp position to write"
    return format_literal(position_literal(tick, "b", rate=edit.rate, grid=edit.grid))


def set_value(project: Project, name: str, layer_id: str, prop: str, value: Any,
              expect: str | None = None) -> dict[str, Any]:
    """Set a property that has no keys to a constant (one with keys takes a key instead, as
    in After Effects): a footage layer's transform, an audio layer's gain in dB, or its
    fades as spans (None takes a fade off)."""
    owners = {"position": "footage", "scale": "footage", "opacity": "footage", "rotation": "footage",
              "anchor": "footage", "gain": "audio", "fade_in": "audio", "fade_out": "audio"}
    assert prop in owners, f"{prop!r} is not a property `set` takes ({', '.join(owners)})"
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == owners[prop], f"{prop} is a property of {owners[prop]} layers, and {layer_id} is not one"
    holder = layer.setdefault("transform", {}) if owners[prop] == "footage" else layer
    old = holder.get(prop)
    assert not isinstance(old, dict), f"{layer_id}'s {prop} has keys; set a key at a time instead"
    if value is None:
        holder.pop(prop, None)
    else:
        holder[prop] = value
    edit.changes.append(f"{layer_id} {prop} {'set to' if old is None else f'{old} to'} {value}")
    _resolves(project, edit)
    return edit.save()


def remap(project: Project, name: str, layer_id: str, expect: str | None = None) -> dict[str, Any]:
    """Enable time remapping on a footage layer, as After Effects does: two keys, at the
    visible in and out points, holding the source times shown there; the layer plays as
    before (checked frame by frame) and its source start and speed go. The source times
    are written in seconds to five places, so the check is what proves nothing moved."""
    from .comp import resolve

    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == "footage", f"{layer_id} is not a footage layer"
    time = layer.get("time")
    assert time is None or "speed" in time, f"{layer_id} has {time}; only a layer at 100 % or a constant speed remaps"
    before = {c.layer.id: c for c in resolve(project, edit.comp).cuts}[layer_id]
    start = edit.start(layer)
    keys = []
    for side in ("in", "out"):
        t = layer.get(side, "0s")
        at = start + edit.span(layer, side)
        source = before.source_start + before.speed * (at - start)
        step = TICKS_PER_SECONDS_STEP
        keys.append({"t": t, "v": format_literal(Seconds(int(round(source / step)) * step))})
    layer["time"] = {"remap": keys}
    del layer["source_start"]
    after = {c.layer.id: c for c in resolve(project, CompFile.model_validate(edit.data)).cuts}[layer_id]
    moved = [f for f in range(before.first, before.end) if after.samples(f) != before.samples(f)]
    assert not moved, f"remapping {layer_id} would change comp frames {moved[:5]}; nothing written"
    edit.changes.append(f"{layer_id} time remapped: {keys[0]['v']} at {keys[0]['t']}, {keys[1]['v']} at {keys[1]['t']}")
    return edit.save()


def set_grade(project: Project, name: str, layer_id: str, grade: list[dict] | dict,
              expect: str | None = None) -> dict[str, Any]:
    """Replace a footage layer's grade with `grade` (operations in order, or a node graph),
    after checking that every node builds (LUTs found, OCIO's ranges)."""
    from .schema.comp import FootageLayer

    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == "footage", f"{layer_id} is not a footage layer"
    old = layer.get("grade", [])
    if grade:
        layer["grade"] = grade
    else:
        layer.pop("grade", None)
    _builds(project, FootageLayer.model_validate(layer).grade)
    edit.changes.append(f"{layer_id} grade {_describe(old)} to {_describe(grade)}")
    return edit.save()


def _builds(project: Project, grade) -> None:
    """Every node of a grade builds: its LUTs found, its values in OCIO's ranges, its looks
    looks."""
    from . import grade as grading

    for node in grading.nodes(grade):
        for inner in grading.nodes(grading.look(project, node.look)) if node.look else [node]:
            grading.processor(project, inner.ops)


def _as_graph(grade: list[dict] | dict | None) -> dict:
    """A layer's grade as a node graph: a list of operations becomes its first node."""
    if not grade:
        return {"nodes": []}
    return grade if isinstance(grade, dict) else {"nodes": [{"ops": grade}]}


def look(project: Project, name: str, layer_id: str, look_id: str, mix: float = 1.0,
         expect: str | None = None) -> dict[str, Any]:
    """Grade footage layer `layer_id` with look `look_id` (a style with a grade), as a node
    after its own, mixed by `mix`; the look already there is given the new mix instead of
    a second node, as a style placed again replaces itself."""
    from .schema.comp import FootageLayer

    assert 0 <= mix <= 1, f"a mix of {mix} is outside 0 to 1"
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == "footage", f"{layer_id} is not a footage layer"
    graph = _as_graph(layer.get("grade"))
    same = [node for node in graph["nodes"] if node.get("look") == look_id]
    if same:
        before = same[0].get("mix", 1.0)
        same[0]["mix"] = mix
        edit.changes.append(f"{layer_id} look {look_id} mix {before} to {mix}")
    else:
        graph["nodes"].append({"look": look_id, "mix": mix} if mix != 1 else {"look": look_id})
        edit.changes.append(f"{layer_id} look {look_id} added" + (f", mix {mix}" if mix != 1 else ""))
    layer["grade"] = graph
    _builds(project, FootageLayer.model_validate(layer).grade)
    return edit.save()


def unlook(project: Project, name: str, layer_id: str, look_id: str, expect: str | None = None) -> dict[str, Any]:
    """Take look `look_id` off a footage layer's grade."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    graph = _as_graph(layer.get("grade"))
    kept = [node for node in graph["nodes"] if node.get("look") != look_id]
    assert len(kept) < len(graph["nodes"]), f"{layer_id}'s grade has no look {look_id}"
    if kept:
        layer["grade"] = {"nodes": kept}
    else:
        layer.pop("grade")
    edit.changes.append(f"{layer_id} look {look_id} taken off")
    return edit.save()


def set_masks(project: Project, name: str, layer_id: str, masks: list[dict], expect: str | None = None) -> dict[str, Any]:
    """Replace a footage layer's masks with `masks`, in order ([] for none)."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == "footage", f"{layer_id} is not a footage layer"
    old = layer.get("masks", [])
    if masks:
        layer["masks"] = masks
    else:
        layer.pop("masks", None)
    shapes = lambda items: ", ".join(f"{m['shape']} ({m.get('mode', 'add')})" for m in items) or "none"
    edit.changes.append(f"{layer_id} masks {shapes(old)} to {shapes(masks)}")
    _resolves(project, edit)
    return edit.save()


def _describe(grade: list[dict] | dict) -> str:
    if not grade:
        return "none"
    if isinstance(grade, dict):
        nodes = [f"look {node['look']}" if "look" in node else _describe(node["ops"]) for node in grade["nodes"]]
        return f"{len(grade['nodes'])} nodes ({'; '.join(nodes)})"
    parts = []
    for op in grade:
        fields = ", ".join(f"{k} {v}" for k, v in op.items() if k != "op")
        parts.append(f"{op['op']} ({fields})" if fields else op["op"])
    return "; ".join(parts)


def place(project: Project, name: str, media: str, at: str, expect: str | None = None) -> dict[str, Any]:
    """Put media on the comp at position `at`, on top of the stack: footage from its first
    frame for as long as it lasts, music from its first sample. The layer is named after
    the media (with `.2`, `.3` for more). Music goes in as an audio layer at unity gain."""
    edit = Edit.open(project, name, expect)
    record = load_record(project, media)
    taken = {layer["id"] for layer in edit.data["layers"]}
    layer_id = media if media not in taken else next(f"{media}.{k}" for k in range(2, 1000) if f"{media}.{k}" not in taken)
    start = resolve_position(parse_literal(at, Field.POSITION), rate=edit.rate, grid=edit.grid)
    end = resolve_position(parse_literal(edit.data["end"], Field.POSITION), rate=edit.rate, grid=edit.grid)
    assert start < end, f"{at} is at or after the comp's end ({edit.data['end']})"
    if record.beats is not None:
        seconds = Fraction(record.wav.samples, record.wav.rate)
    else:
        video = record.probe.video
        assert video is not None, f"{media} has neither video nor a beat map"
        seconds = Fraction(video.frames) / parse_rate(record.time.rate)
    # As long as the media lasts, cut off at the comp's end: whole comp frames from the start.
    frames = min(int(seconds * edit.rate), (end - start) // ticks_per_frame(edit.rate))
    kind, source = ("audio", "0s") if record.beats is not None else ("footage", "f:0")
    layer = {"id": layer_id, "type": kind, "media": media, "start": at, "source_start": source, "out": f"f:{frames}"}
    graphics = [k for k, other in enumerate(edit.data["layers"]) if other["type"] == "graphics"]
    # Footage goes under the graphics (they are top overlays); everything else on top.
    index = graphics[0] if graphics and layer["type"] == "footage" else len(edit.data["layers"])
    edit.data["layers"].insert(index, layer)
    edit.changes.append(f"{media} placed at {at} as {layer_id}, {layer['out']} long")
    return edit.save()


def new_comp(project: Project, name: str, *, size: tuple[int, int], rate: str, end: str,
             music: str | None = None) -> dict[str, Any]:
    """A new comp, `comps/NAME.json`, listed in project.json: empty, or with MUSIC from 0s
    as the layer its beat grid comes from (03-project-format.md#beat-maps)."""
    from .files import write_new
    from .project import PROJECT_FILE
    from .schema.project import ProjectFile

    path = Edit.path_of(project, name)
    layers = []
    data: dict[str, Any] = {"id": name, "size": list(size), "rate": rate, "end": end, "layers": layers}
    if music is not None:
        record = load_record(project, music)
        assert record.beats is not None, f"{music} has no beat map; import a music file"
        parse_rate(rate)
        end_ticks = resolve_position(parse_literal(end, Field.POSITION), rate=parse_rate(rate))
        seconds = Fraction(record.wav.samples, record.wav.rate)
        frames = min(int(seconds * parse_rate(rate)), end_ticks // ticks_per_frame(parse_rate(rate)))
        layers.append({"id": "music", "type": "audio", "media": music, "start": "0s", "out": f"f:{frames}",
                       "source_start": "0s"})
        data["beats"] = {"from_layers": ["music"]}
    comp = CompFile.model_validate(data)
    project_path = project.directory / PROJECT_FILE
    read_hash = content_hash(project_path)
    settings = ProjectFile.model_validate_json(project_path.read_text())
    relative = f"comps/{name}.json"
    assert relative not in settings.comps, f"{relative} is listed already"
    write_new(path, json_text(comp.model_dump(mode="json", by_alias=True, exclude_unset=True)))
    settings.comps.append(relative)
    replace_if_unchanged(project_path, read_hash, json_text(settings.model_dump(mode="json")))
    return {"comp": name, "changes": [f"comp {name} created ({size[0]}x{size[1]}, {rate}, ends at {end})"
                                      + (f" with {music} as its music" if music else "")]}


# Every edit operation by the name `lc op` and the editor give it: the one table both use.
OPERATIONS = {
    "move": move, "trim": trim, "slip": slip, "slide": slide, "roll": roll, "ripple-delete": ripple_delete,
    "split": split, "snap": snap, "place": place, "style": style, "unstyle": unstyle, "key": key, "unkey": unkey,
    "set": set_value, "remap": remap, "grade": set_grade, "masks": set_masks, "transition": transition,
    "untransition": untransition, "look": look, "unlook": unlook,
}
