"""Operations on when layers play: move, trim, slip, roll, slide, ripple delete, split,
snap to beats, and placing media (06-editing.md#edit-operations)."""
from __future__ import annotations

from fractions import Fraction
from typing import Any

from ..literals import Field, Frames, Seconds, format_literal, parse_literal, resolve_position, resolve_source, span_literal
from ..media import Project, load_record
from ..ticks import parse_rate, ticks_per_frame
from .edit import Edit, _delta, _resolves, _unit


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


def place(project: Project, name: str, media: str, at: str, expect: str | None = None) -> dict[str, Any]:
    """Put media on the comp at position `at`, on top of the stack: footage from its first
    frame for as long as it lasts, music or a sound from its first sample. The layer is
    named after the media (with `.2`, `.3` for more). Sound goes in as an audio layer at
    unity gain."""
    edit = Edit.open(project, name, expect)
    record = load_record(project, media)
    taken = {layer["id"] for layer in edit.data["layers"]}
    layer_id = media if media not in taken else next(f"{media}.{k}" for k in range(2, 1000) if f"{media}.{k}" not in taken)
    start = resolve_position(parse_literal(at, Field.POSITION), rate=edit.rate, grid=edit.grid)
    end = resolve_position(parse_literal(edit.data["end"], Field.POSITION), rate=edit.rate, grid=edit.grid)
    assert start < end, f"{at} is at or after the comp's end ({edit.data['end']})"
    if record.wav is not None:
        seconds = Fraction(record.wav.samples, record.wav.rate)
    else:
        video = record.probe.video
        assert video is not None, f"{media} has neither video nor sound"
        seconds = Fraction(video.frames) / parse_rate(record.time.rate)
    # As long as the media lasts, cut off at the comp's end: whole comp frames from the start.
    frames = min(int(seconds * edit.rate), (end - start) // ticks_per_frame(edit.rate))
    kind, source = ("audio", "0s") if record.wav is not None else ("footage", "f:0")
    layer = {"id": layer_id, "type": kind, "media": media, "start": at, "source_start": source, "out": f"f:{frames}"}
    graphics = [k for k, other in enumerate(edit.data["layers"]) if other["type"] == "graphics"]
    # Footage goes under the graphics (they are top overlays); everything else on top.
    index = graphics[0] if graphics and layer["type"] == "footage" else len(edit.data["layers"])
    edit.data["layers"].insert(index, layer)
    edit.changes.append(f"{media} placed at {at} as {layer_id}, {layer['out']} long")
    return edit.save()
