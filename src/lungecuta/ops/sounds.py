"""Placing sounds on events: a cut, a beat, a downbeat or a marker (07-audio.md, SFX
placement); a footage layer's own sound (07-audio.md, Location sound); and the music
lowered under it."""
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


def sound(project: Project, name: str, layer_id: str, on: bool = True, gain: float | None = None,
          pan: float | None = None, fade_in: str | None = None, fade_out: str | None = None,
          expect: str | None = None) -> dict[str, Any]:
    """Play a footage layer's own sound with its picture, or with `on` false take it off.
    `gain` (dB), `pan` (-1 to 1) and the fades (spans from the visible in and out points,
    `none` for no fade) change only what is given. The sound cuts on the layer's frames and
    moves, trims, slips and splits with it; only a layer at 100 % plays it (*proposed*)."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == "footage", f"{layer_id} is not a footage layer"
    if not on:
        assert "sound" in layer, f"{layer_id} plays no sound of its own"
        del layer["sound"]
        edit.changes.append(f"{layer_id} plays no sound of its own")
    else:
        assert load_record(project, layer["media"]).probe.audio, f"{layer['media']} has no sound of its own"
        settings = dict(layer.get("sound") or {})
        for field, value in (("gain", gain), ("pan", pan), ("fade_in", fade_in), ("fade_out", fade_out)):
            if value == "none":
                settings.pop(field, None)
            elif value is not None:
                settings[field] = value
        layer["sound"] = settings
        described = ", ".join(f"{field.replace('_', ' ')} {value}{' dB' if field == 'gain' else ''}"
                              for field, value in settings.items())
        edit.changes.append(f"{layer_id} plays its own sound" + (f" ({described})" if described else ""))
    _resolves(project, edit)
    return edit.save()


def duck(project: Project, name: str, layer_id: str, by: float, ramp: str, under: str | None = None,
         start: str | None = None, end: str | None = None, expect: str | None = None) -> dict[str, Any]:
    """Lower an audio layer (the music) by `by` dB while layer `under` plays (a shot with
    its own sound, a sound), or from `start` to `end`: four gain keys, the outer two `ramp`
    before and after the span (on the nearest frames) at the level the layer had there, the
    inner two on the span's first frame and the frame after it, lowered by `by`. A gain key
    already inside the duck is refused rather than merged: duck once over neighbouring shots."""
    from ..comp import resolve
    from .properties import _key_tick, _key_time, _keys, _put_keys

    assert (under is None) != (start is None and end is None), "give --under, or --from and --to"
    assert under is not None or (start is not None and end is not None), "a span needs both --from and --to"
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == "audio", f"{layer_id} is not an audio layer"
    resolved = resolve(project, edit.comp)
    tick = ticks_per_frame(edit.rate)
    if under is not None:
        spans = {cut.layer.id: (cut.first, cut.end) for cut in resolved.cuts}
        spans |= {o.layer.id: (o.first, o.end) for o in resolved.overlays}
        spans |= {s.layer.id: (-(-s.first * ticks_per_sample(MIX_RATE) // tick), -(-s.end * ticks_per_sample(MIX_RATE) // tick))
                  for s in resolved.sounds if not s.footage}
        assert under in spans and under != layer_id, f"comp {name} has no other layer {under!r} to duck under"
        first, last = spans[under]
    else:
        at = lambda text: -(-resolve_position(parse_literal(text, Field.POSITION), rate=edit.rate, grid=edit.grid) // tick)
        first, last = at(start), at(end)
        assert first < last, f"{start} to {end} is no span"
    t0, t1 = first * tick, last * tick
    length = resolve_span(parse_literal(ramp, Field.LAYER), anchor=t0, rate=edit.rate, grid=edit.grid)
    assert length > 0, f"a ramp of {ramp}"
    nearest = lambda ticks: (2 * ticks + tick) // (2 * tick) * tick  # the nearest frame, halves up
    a, d = nearest(t0 - length), nearest(t1 + length)
    assert a >= edit.start(layer), f"the duck's ramp would start before {layer_id} does"
    sound = next(s for s in resolved.sounds if s.layer.id == layer_id)
    level = lambda ticks: 0.0 if sound.gain is None else float(sound.gain.at(ticks)[0])
    keys = _keys(edit, layer, "gain")
    inside = [key["t"] for key in keys if a <= _key_tick(edit, layer, key) <= d]
    assert not inside, (f"{layer_id} has gain keys at {', '.join(inside)} inside the duck; duck once over the whole "
                        "span, or take those keys off first")
    new = [{"t": _key_time(edit, layer, f"f:{ticks // tick}")[1], "v": round(level(ticks) + shift, 2)}
           for ticks, shift in ((a, 0.0), (t0, by), (t1, by), (d, 0.0))]
    ticks_of = [_key_tick(edit, layer, key) for key in keys]
    _put_keys(layer, "gain", [key for key, k in zip(keys, ticks_of) if k < a] + new
              + [key for key, k in zip(keys, ticks_of) if k > d])
    edit.changes.append(f"{layer_id} ducked {by:g} dB from f:{first} to f:{last}"
                        + (f" under {under}" if under else "") + f", ramps of {ramp}")
    _resolves(project, edit)
    return edit.save()
