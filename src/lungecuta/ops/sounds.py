"""Placing sounds on events: a cut, a beat, a downbeat or a marker (07-audio.md, SFX
placement); a footage layer's own sound (07-audio.md, Location sound); and the music
lowered under it."""
from __future__ import annotations

import math
from fractions import Fraction
from typing import Any, Sequence

import numpy as np

from ..comp import MIX_RATE
from ..literals import Field, Seconds, format_literal, parse_literal, resolve_position, resolve_span
from ..media import Project, load_record
from ..schema.comp import CompFile
from ..ticks import ticks_per_frame, ticks_per_sample
from .edit import Edit, _resolves


EVENTS = ("cuts", "beats", "downbeats", "markers")


ROTATION = 7  # sound k of n is (k * 7) mod n, the rule past edits used (07-audio.md, SFX placement)


def _events(edit: Edit, on: str, first: int, last: int, layer: str | None = None, named: str | None = None) -> list[str]:
    """The comp positions of a kind of event inside [first, last), as literals: each cut (a
    footage layer's first frame, after the comp's), each beat or downbeat, each marker
    (only those of layer `layer`, `""` for the comp's own, and only those named `named`, if
    given): a graphics layer's markers are its page's events (05-motion-graphics.md)."""
    from ..comp import resolve

    resolved = resolve(edit.project, edit.comp)
    tick = ticks_per_frame(edit.rate)
    if on == "cuts":
        frames = sorted({cut.cut_frame for cut in resolved.cuts
                         if cut.cut_frame > 0 and first <= cut.cut_frame * tick < last})
        return [f"f:{frame}" for frame in frames]
    assert on == "markers" or (layer is None and named is None), "--layer and --named pick markers"
    if on == "markers":
        assert layer in (None, "") or any(layer == other.id for other in edit.comp.layers), (
            f"comp {edit.name} has no layer {layer!r}")
        return [f"f:{at.frame}" for at in resolved.markers if first <= at.frame * tick < last
                and (layer is None or at.layer == (layer or None)) and (named is None or at.marker.name == named)]
    assert edit.grid is not None, f"comp {edit.name} has no beat grid for {on}"
    if on == "beats":
        return [f"b:{k}" for k, t in enumerate(edit.grid.times) if first <= t < last]
    return [f"bar:{k}" for k, index in enumerate(edit.grid.downbeats) if first <= edit.grid.times[index] < last]


def sfx(project: Project, name: str, sounds: list[str], on: str, start: str | None = None, end: str | None = None,
        anchor: str = "rise", gain: float = 0.0, layer: str | None = None, named: str | None = None,
        expect: str | None = None) -> dict[str, Any]:
    """Put a sound on every event of a kind in a span (07-audio.md, SFX placement), markers
    only of `layer` or named `named` if given: one
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
    events = _events(edit, on, first, last, layer, named)
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


def _voices(resolved, layer_id: str) -> list[str]:
    """The comp's voice-over lines: audio layers whose sound `lungecuta voice` said."""
    return [s.layer.id for s in resolved.sounds if not s.footage and s.record.speech is not None and s.layer.id != layer_id]


def _under(resolved, name: str, layer_id: str, under: Sequence[str], voices: bool) -> list[str]:
    assert not (under and voices), "give --under or --under-voices, not both"
    if voices:
        ids = _voices(resolved, layer_id)
        assert ids, f"comp {name} has no voice-over lines (lungecuta voice) besides {layer_id}"
        return ids
    assert layer_id not in under, f"{layer_id} cannot go down under itself"
    return list(under)


def duck(project: Project, name: str, layer_id: str, by: float, ramp: str, under: Sequence[str] = (),
         start: str | None = None, end: str | None = None, voices: bool = False,
         expect: str | None = None) -> dict[str, Any]:
    """Lower an audio layer (the music) by `by` dB while layers `under` play (shots with
    their own sound, sounds, titles; `voices` for every voice-over line), or from `start`
    to `end`: per span four gain keys, the outer two `ramp` before and after it (on the
    nearest frames) at the level the layer had there, the inner two on the span's first
    frame and the frame after it, lowered by `by`. Spans whose ramps meet or overlap are
    one span, so the music stays down between lines close together. A gain key already
    inside a duck is refused rather than merged."""
    from ..comp import resolve
    from .properties import _key_tick, _key_time, _keys, _put_keys

    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == "audio", f"{layer_id} is not an audio layer"
    resolved = resolve(project, edit.comp)
    tick = ticks_per_frame(edit.rate)
    under = _under(resolved, name, layer_id, under, voices) if (under or voices) else []
    assert bool(under) != (start is not None or end is not None), "give --under or --under-voices, or --from and --to"
    if under:
        spans = {cut.layer.id: (cut.first, cut.end) for cut in resolved.cuts}
        spans |= {o.layer.id: (o.first, o.end) for o in resolved.overlays}
        spans |= {s.layer.id: (-(-s.first * ticks_per_sample(MIX_RATE) // tick), -(-s.end * ticks_per_sample(MIX_RATE) // tick))
                  for s in resolved.sounds if not s.footage and s.layer.id != layer_id}
        missing = [one for one in under if one not in spans]
        assert not missing, f"comp {name} has no other layer {', '.join(map(repr, missing))} to duck under"
        frames = sorted(spans[one] for one in under)
    else:
        assert start is not None and end is not None, "a span needs both --from and --to"
        at = lambda text: -(-resolve_position(parse_literal(text, Field.POSITION), rate=edit.rate, grid=edit.grid) // tick)
        frames = [(at(start), at(end))]
        assert frames[0][0] < frames[0][1], f"{start} to {end} is no span"
    nearest = lambda ticks: (2 * ticks + tick) // (2 * tick) * tick  # the nearest frame, halves up
    ramps = []  # (a, t0, t1, d) per span in ticks, spans whose ramps meet merged
    for first, last in frames:
        t0, t1 = first * tick, last * tick
        length = resolve_span(parse_literal(ramp, Field.LAYER), anchor=t0, rate=edit.rate, grid=edit.grid)
        assert length > 0, f"a ramp of {ramp}"
        a, d = nearest(t0 - length), nearest(t1 + length)
        if ramps and a <= ramps[-1][3]:
            pa, p0, p1, pd = ramps[-1]
            ramps[-1] = (pa, p0, max(p1, t1), max(pd, d))
        else:
            ramps.append((a, t0, t1, d))
    assert ramps[0][0] >= edit.start(layer), f"the duck's ramp would start before {layer_id} does"
    sound = next(s for s in resolved.sounds if s.layer.id == layer_id)
    level = lambda ticks: 0.0 if sound.gain is None else float(sound.gain.at(ticks)[0])
    keys = _keys(edit, layer, "gain")
    ticks_of = [_key_tick(edit, layer, key) for key in keys]
    inside = [key["t"] for key, k in zip(keys, ticks_of) if any(a <= k <= d for a, _, _, d in ramps)]
    assert not inside, (f"{layer_id} has gain keys at {', '.join(inside)} inside the duck; duck once over the whole "
                        "span, or take those keys off first")
    new = [{"t": _key_time(edit, layer, f"f:{ticks // tick}")[1], "v": round(level(ticks) + shift, 2)}
           for a, t0, t1, d in ramps for ticks, shift in ((a, 0.0), (t0, by), (t1, by), (d, 0.0))]
    kept = [(k, key) for key, k in zip(keys, ticks_of)]
    merged = sorted(kept + [(_key_tick(edit, layer, key), key) for key in new], key=lambda pair: pair[0])
    _put_keys(layer, "gain", [key for _, key in merged])
    what = ", ".join(f"f:{t0 // tick} to f:{t1 // tick}" for _, t0, t1, _ in ramps)
    edit.changes.append(f"{layer_id} ducked {by:g} dB from {what}"
                        + (f" under {', '.join(under)}" if under else "") + f", ramps of {ramp}")
    _resolves(project, edit)
    return edit.save()


def _loudness(project: Project, sounds: list, first: int, end: int) -> float:
    """BS.1770 integrated loudness of these layers summed, as the comp mixes them, over
    comp samples [first, end)."""
    from ..audio import loudness
    from ..mixdown import layer_sound

    total = np.zeros((end - first, 2), dtype=np.float64)
    for one in sounds:
        if one.end <= first or one.first >= end:
            continue
        piece = layer_sound(project, one, end)
        lo = max(one.first, first)
        total[lo - first:min(one.end, end) - first] += piece[lo - one.first:]
    return loudness(total, MIX_RATE)


def bed(project: Project, name: str, layer_id: str, below: float, under: Sequence[str] = (), voices: bool = False,
        expect: str | None = None) -> dict[str, Any]:
    """Set an audio layer (the music) `below` LU under the voice: the BS.1770 integrated
    loudness of layers `under` (`voices` for every voice-over line) summed, and of the
    layer as the comp plays it, both measured from the voice's first sample to its last,
    and the difference added to the layer's gain (its constant, or every key, so ducks
    keep their depth). Measured again after, and both numbers returned."""
    from ..comp import resolve

    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == "audio", f"{layer_id} is not an audio layer"
    resolved = resolve(project, edit.comp)
    ids = _under(resolved, name, layer_id, under, voices)
    assert ids, "give --under or --under-voices"
    sounds = {s.layer.id: s for s in resolved.sounds}
    missing = [one for one in ids if one not in sounds]
    assert not missing, f"comp {name} has no sound layer {', '.join(map(repr, missing))} to measure"
    lines = [sounds[one] for one in ids]
    first, end = min(s.first for s in lines), max(s.end for s in lines)
    music = sounds[layer_id]
    assert music.first < end and music.end > first, f"{layer_id} does not play where {', '.join(ids)} do"
    voice = _loudness(project, lines, first, end)
    before = _loudness(project, [music], first, end)
    assert math.isfinite(voice) and math.isfinite(before), f"the voice measures {voice} LUFS and {layer_id} {before}"
    shift = round(voice - below - before, 2)
    gain = layer.get("gain", 0.0)
    if isinstance(gain, dict):
        layer["gain"] = {**gain, "keys": [{**key, "v": round(key["v"] + shift, 2)} for key in gain["keys"]]}
    else:
        layer["gain"] = round(gain + shift, 2)
    after_music = next(s for s in resolve(project, CompFile.model_validate(edit.data)).sounds if s.layer.id == layer_id)
    after = _loudness(project, [after_music], first, end)
    edit.changes.append(f"{layer_id} {shift:+g} dB: {after:.2f} LUFS, {voice - after:.2f} LU under "
                        f"{', '.join(ids)} ({voice:.2f} LUFS) from {first / MIX_RATE:.3f} s to {end / MIX_RATE:.3f} s")
    _resolves(project, edit)
    result = edit.save()
    result["bed"] = {"voice_lufs": round(voice, 2), "music_lufs_before": round(before, 2),
                     "music_lufs": round(after, 2), "gain_shift_db": shift,
                     "from_s": round(first / MIX_RATE, 3), "to_s": round(end / MIX_RATE, 3)}
    return result
