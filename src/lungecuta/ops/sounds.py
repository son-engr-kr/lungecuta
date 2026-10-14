"""Placing sounds on events: a cut, a beat, a downbeat or a marker (07-audio.md, SFX
placement)."""
from __future__ import annotations

import math
from fractions import Fraction
from typing import Any

from ..comp import MIX_RATE
from ..literals import Field, Seconds, format_literal, parse_literal, resolve_position, resolve_span
from ..media import Project, load_record
from ..schema.comp import CompFile
from ..ticks import ticks_per_frame, ticks_per_sample
from .edit import Edit, _resolves


EVENTS = ("cuts", "beats", "downbeats", "markers")


ROTATION = 7  # sound k of n is (k * 7) mod n, the rule past edits used (07-audio.md, SFX placement)


def _events(edit: Edit, on: str, first: int, last: int) -> list[str]:
    """The comp positions of a kind of event inside [first, last), as literals: each cut (a
    footage layer's first frame, after the comp's), each beat or downbeat, each marker."""
    from ..comp import resolve

    resolved = resolve(edit.project, edit.comp)
    tick = ticks_per_frame(edit.rate)
    if on == "cuts":
        frames = sorted({cut.cut_frame for cut in resolved.cuts
                         if cut.cut_frame > 0 and first <= cut.cut_frame * tick < last})
        return [f"f:{frame}" for frame in frames]
    if on == "markers":
        return [f"f:{at.frame}" for at in resolved.markers if first <= at.frame * tick < last]
    assert edit.grid is not None, f"comp {edit.name} has no beat grid for {on}"
    if on == "beats":
        return [f"b:{k}" for k, t in enumerate(edit.grid.times) if first <= t < last]
    return [f"bar:{k}" for k, index in enumerate(edit.grid.downbeats) if first <= edit.grid.times[index] < last]


def sfx(project: Project, name: str, sounds: list[str], on: str, start: str | None = None, end: str | None = None,
        anchor: str = "rise", gain: float = 0.0, expect: str | None = None) -> dict[str, Any]:
    """Put a sound on every event of a kind in a span (07-audio.md, SFX placement): one
    audio layer an event, anchored there, the sounds taken in turn as sound (k * 7) mod n
    of the n given, so neighbours differ; each as long as its sound, cut at the comp's end."""
    assert on in EVENTS, f"{on!r} is not an event kind ({', '.join(EVENTS)})"
    assert sounds, "give at least one sound"
    assert len(sounds) == 1 or math.gcd(ROTATION, len(sounds)) == 1, (
        f"with {len(sounds)} sounds the rotation (k * {ROTATION}) mod n repeats one sound; give another number of them"
    )
    edit = Edit.open(project, name, expect)
    comp_end = resolve_position(parse_literal(edit.data["end"], Field.POSITION), rate=edit.rate, grid=edit.grid)
    first = 0 if start is None else resolve_position(parse_literal(start, Field.POSITION), rate=edit.rate, grid=edit.grid)
    last = comp_end if end is None else resolve_position(parse_literal(end, Field.POSITION), rate=edit.rate, grid=edit.grid)
    records = {key: load_record(project, key) for key in sounds}
    for key, record in records.items():
        assert record.wav is not None and record.beats is None, f"{key} is not a sound (lungecuta import --sound)"
    events = _events(edit, on, first, last)
    assert events, f"no {on} between {start or 'the start'} and {end or 'the end'} of {name}"
    taken = {layer["id"] for layer in edit.data["layers"]}
    added = []
    for k, at in enumerate(events):
        key = sounds[(k * ROTATION) % len(sounds)]
        layer_id = next(f"{key}.{j}" for j in range(1, 100000) if f"{key}.{j}" not in taken)
        taken.add(layer_id)
        seconds = Fraction(records[key].wav.samples, records[key].wav.rate)
        layer = {"id": layer_id, "type": "audio", "media": key, "start": at, "out": f"{float(seconds):g}s",
                 "source_start": "0s", "anchor": anchor}
        if gain:
            layer["gain"] = gain
        edit.data["layers"].append(layer)
        added.append(layer_id)
        edit.changes.append(f"{key} placed on {at} as {layer_id} by its {anchor}")
    _cut_at_end(edit, comp_end, set(added))
    _resolves(project, edit)
    return edit.save()


def _cut_at_end(edit: Edit, comp_end: int, layer_ids: set[str]) -> None:
    """The new sound layers that would run past the comp's end end there."""
    from ..comp import resolve

    resolved = resolve(edit.project, CompFile.model_validate(edit.data))
    for sound in resolved.sounds:
        if sound.layer.id not in layer_ids:
            continue
        over = sound.end * ticks_per_sample(MIX_RATE) - comp_end
        if over > 0:
            layer = edit.layer(sound.layer.id)
            length = resolve_span(parse_literal(layer["out"], Field.LAYER), anchor=edit.start(layer), rate=edit.rate,
                                  grid=edit.grid)
            layer["out"] = format_literal(Seconds(length - over))
