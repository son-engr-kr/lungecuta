"""Operations on a layer's properties: keys and constants of its transform, shutter and
gain, time remapping, and masks."""
from __future__ import annotations

import math
from typing import Any

from ..literals import TICKS_PER_SECONDS_STEP, Field, Seconds, format_literal, parse_literal, position_literal, resolve_position, resolve_span, span_literal
from ..media import Project
from ..schema.comp import CompFile
from ..ticks import TICKS_PER_SECOND, ticks_per_frame
from .edit import Edit, _resolves, _unit


KEYED = ("position", "scale", "opacity", "rotation", "remap", "gain", "shutter")


TRANSFORMED = ("footage", "graphics", "comp")  # the layer types with a transform and masks
# The layer types each property belongs to, as After Effects gives every visual layer one transform
OWNERS = {"position": TRANSFORMED, "scale": TRANSFORMED, "opacity": TRANSFORMED, "rotation": TRANSFORMED,
          "anchor": TRANSFORMED, "remap": ("footage",), "shutter": ("footage", "graphics"), "samples": ("graphics",),
          "resolution": ("graphics",), "gain": ("audio",), "fade_in": ("audio",), "fade_out": ("audio",)}
SETTABLE = tuple(prop for prop in OWNERS if prop != "remap")


def _owned(layer: dict, prop: str) -> None:
    assert layer["type"] in OWNERS[prop], (
        f"{prop} is a property of {' and '.join(OWNERS[prop])} layers, and {layer['id']} is a {layer['type']} layer")


def _holder(layer: dict, prop: str, create: bool = False) -> tuple[dict, str]:
    """Where a property's value sits in the layer's JSON, and its field there: the
    transform's, the layer's own (gain, fades), or the motion blur's shutter."""
    if prop in ("shutter", "samples"):
        blur = layer.setdefault("motion_blur", {}) if create else layer.get("motion_blur") or {}
        return blur, "shutter_deg" if prop == "shutter" else "samples"
    if prop in ("gain", "fade_in", "fade_out", "resolution"):
        return layer, prop
    return (layer.setdefault("transform", {}) if create else layer.get("transform") or {}), prop


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
        assert "remap" in time, f"{layer['id']} is not remapped; enable time remapping first (lungecuta op remap)"
        return time["remap"]
    holder, field = _holder(layer, prop)
    current = holder.get(field)
    return list(current["keys"]) if isinstance(current, dict) else []


def _key_tick(edit: Edit, layer: dict, key: dict) -> int:
    return edit.start(layer) + resolve_span(parse_literal(key["t"], Field.LAYER), anchor=edit.start(layer),
                                            rate=edit.rate, grid=edit.grid)


def _put_keys(layer: dict, prop: str, keys: list[dict]) -> None:
    if prop == "remap":
        layer["time"]["remap"] = keys
    else:
        holder, field = _holder(layer, prop, create=True)
        holder[field] = {"keys": keys}


def key(project: Project, name: str, layer_id: str, prop: str, at: str, value: Any, ease: str | None = None,
        expect: str | None = None) -> dict[str, Any]:
    """Set a key of a layer's property at comp position `at`, replacing a key at
    that time or adding one, as After Effects' stopwatch does: the first key of a constant
    property makes it animated (one key is still a constant). `value` is a number or a pair
    for the transform, a source time literal for `remap`."""
    assert prop in KEYED, f"{prop!r} is not a property with keys ({', '.join(KEYED)})"
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    _owned(layer, prop)
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


def move_key(project: Project, name: str, layer_id: str, prop: str, at: str, to: str,
             expect: str | None = None) -> dict[str, Any]:
    """Move the key of a property at comp position `at` to comp position `to`, its value
    and ease kept, as dragging a key along the graph editor's time does; a key already at
    `to` is refused, not replaced, and the keys stay in time order."""
    assert prop in KEYED, f"{prop!r} is not a property with keys ({', '.join(KEYED)})"
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    point, _ = _key_time(edit, layer, at)
    target, t = _key_time(edit, layer, to)
    keys = _keys(edit, layer, prop)
    ticks = [_key_tick(edit, layer, k) for k in keys]
    assert point in ticks, f"{layer_id} has no {prop} key at {at}"
    assert target not in ticks or target == point, f"{layer_id} has a {prop} key at {to} already"
    moved = keys.pop(ticks.index(point))
    ticks.remove(point)
    old = moved["t"]
    moved = moved | {"t": t}
    keys.insert(sum(tick < target for tick in ticks), moved)
    _put_keys(layer, prop, keys)
    edit.changes.append(f"{layer_id} {prop} key moved {old} to {t}")
    _resolves(project, edit)
    return edit.save()


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
    else:
        holder, field = _holder(layer, prop, create=True)
        holder[field] = removed["v"]
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
    in After Effects): a footage, graphics or precomp layer's transform, a footage or
    graphics layer's motion blur shutter, a graphics layer's motion blur samples and capture
    resolution, an audio layer's gain in dB, or its fades as spans (None takes a fade, the
    shutter, the samples or the resolution off)."""
    assert prop in SETTABLE, f"{prop!r} is not a property `set` takes ({', '.join(SETTABLE)})"
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    _owned(layer, prop)
    holder, field = _holder(layer, prop, create=True)
    old = holder.get(field)
    assert not isinstance(old, dict), f"{layer_id}'s {prop} has keys; set a key at a time instead"
    if value is None:
        holder.pop(field, None)
        if prop == "shutter":
            layer.pop("motion_blur")  # no shutter is no motion blur
    else:
        holder[field] = value
    edit.changes.append(f"{layer_id} {prop} {'set to' if old is None else f'{old} to'} {value}")
    _resolves(project, edit)
    return edit.save()


def remap(project: Project, name: str, layer_id: str, expect: str | None = None) -> dict[str, Any]:
    """Enable time remapping on a footage layer, as After Effects does: two keys, at the
    visible in and out points, holding the source times shown there; the layer plays as
    before (checked frame by frame) and its source start and speed go. The source times
    are written in seconds to five places, so the check is what proves nothing moved."""
    edit = Edit.open(project, name, expect)
    _remapped(project, edit, edit.layer(layer_id))
    return edit.save()


def _cut(project: Project, edit: Edit, layer_id: str):
    """A footage layer of the comp as edited so far, resolved."""
    from ..comp import resolve

    return {c.layer.id: c for c in resolve(project, CompFile.model_validate(edit.data)).cuts}[layer_id]


def _seconds(ticks: float) -> str:
    """A source time in seconds to five places, as remap keys are written."""
    step = TICKS_PER_SECONDS_STEP
    return format_literal(Seconds(int(round(ticks / step)) * step))


def _remapped(project: Project, edit: Edit, layer: dict) -> None:
    """`remap` on a layer of an open edit."""
    layer_id = layer["id"]
    assert layer["type"] == "footage", f"{layer_id} is not a footage layer"
    time = layer.get("time")
    assert time is None or "speed" in time, f"{layer_id} has {time}; only a layer at 100 % or a constant speed remaps"
    before = _cut(project, edit, layer_id)
    start = edit.start(layer)
    keys = []
    for side in ("in", "out"):
        at = start + edit.span(layer, side)
        keys.append({"t": layer.get(side, "0s"), "v": _seconds(before.source_start + before.speed * (at - start))})
    layer["time"] = {"remap": keys}
    del layer["source_start"]
    after = _cut(project, edit, layer_id)
    moved = [f for f in range(before.first, before.end) if after.samples(f) != before.samples(f)]
    assert not moved, f"remapping {layer_id} would change comp frames {moved[:5]}; nothing written"
    edit.changes.append(f"{layer_id} time remapped: {keys[0]['v']} at {keys[0]['t']}, {keys[1]['v']} at {keys[1]['t']}")


def speed(project: Project, name: str, layer_id: str, factor: float, at: str | None = None,
          start: str | None = None, end: str | None = None, expect: str | None = None) -> dict[str, Any]:
    """Play a footage layer at `factor` (source seconds per comp second: 0.5 is 50 %, 0
    holds a frame, -1 plays backwards) over the remap segment around comp position `at`,
    or from `start` to `end` (taken inside the layer's in and out points), as dragging a
    segment of the speed graph does (06-editing.md, Speed). Keys are put at both ends, the
    first holding the source shown there; keys between them go; every later key moves by
    the same source time, so the rest of the layer keeps its speeds and shows later or
    earlier source. A layer not remapped is remapped first. A new key may not split an
    eased segment, whose shape it would change, and the source check refuses a speed that
    runs out of footage."""
    assert (at is None) != (start is None and end is None), "give --at, or --from and --to"
    assert at is not None or (start is not None and end is not None), "a range needs both --from and --to"
    assert math.isfinite(factor), f"a speed of {factor}"
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == "footage", f"{layer_id} is not a footage layer"
    if "remap" not in (layer.get("time") or {}):
        _remapped(project, edit, layer)
    cut = _cut(project, edit, layer_id)
    keys = layer["time"]["remap"]
    ticks = [_key_tick(edit, layer, k) for k in keys]
    point = lambda literal: resolve_position(parse_literal(literal, Field.POSITION), rate=edit.rate, grid=edit.grid)
    if at is not None:
        here = point(at)
        assert ticks[0] <= here < ticks[-1], f"{at} is outside {layer_id}'s remap keys ({keys[0]['t']} to {keys[-1]['t']} from its start)"
        k = max(i for i, tick in enumerate(ticks) if tick <= here)
        a, b = ticks[k], ticks[k + 1]
        ends = key_position(edit, layer, keys[k]), key_position(edit, layer, keys[k + 1])
    else:
        visible = edit.start(layer) + edit.span(layer, "in"), edit.start(layer) + edit.span(layer, "out")
        a, b = max(point(start), visible[0]), min(point(end), visible[1])
        assert a < b, f"{start} to {end} does not overlap {layer_id}, which is visible from its in to its out point"
        ends = start if a == point(start) else "its in point", end if b == point(end) else "its out point"
    for tick, literal in zip((a, b), ends):
        later = [i for i, t in enumerate(ticks) if t > tick]
        if tick not in ticks and later and ticks[0] < tick:
            assert keys[later[0]].get("ease") is None, (
                f"{literal} falls inside the eased remap segment arriving at {keys[later[0]]['t']}: a key there "
                "would change its shape; take its ease off or start and end the range on keys")
    source = lambda tick: cut.remap.at(tick)[0]
    shift = source(a) + factor * (b - a) - source(b)
    removed = [key["t"] for key, tick in zip(keys, ticks) if a < tick < b]
    eased = [key["t"] for key, tick in zip(keys, ticks) if tick == b and key.get("ease") is not None]
    out = [key for key, tick in zip(keys, ticks) if tick <= a]
    if a not in ticks:
        out.append({"t": _key_time(edit, layer, start if ends[0] == start else _position_of(edit, a))[1],
                    "v": _seconds(source(a))})
    at_end = next((key for key, tick in zip(keys, ticks) if tick == b), None)
    t_end = at_end["t"] if at_end is not None else _key_time(edit, layer, end if ends[1] == end else _position_of(edit, b))[1]
    out.append({"t": t_end, "v": _seconds(source(a) + factor * (b - a))})  # no ease: the range is one speed
    moved = [key | {"v": _seconds(source(tick) + shift)} for key, tick in zip(keys, ticks) if tick > b]
    layer["time"]["remap"] = out + moved
    change = f"{layer_id} plays at {factor * 100:g} % from {ends[0]} to {ends[1]}"
    if removed:
        change += f"; remap keys at {', '.join(removed)} between them deleted"
    if eased:
        change += f"; the ease of the key at {eased[0]} taken off"
    if moved:
        change += f"; {len(moved)} later key{'s' if len(moved) > 1 else ''} moved {shift / TICKS_PER_SECOND:+.5f} s in source"
    edit.changes.append(change)
    _resolves(project, edit)
    return edit.save()


def _position_of(edit: Edit, tick: int) -> str:
    """A comp tick as a position literal `_key_time` takes: in frames when it is on one,
    else in beats."""
    if tick % ticks_per_frame(edit.rate) == 0:
        return format_literal(position_literal(tick, "f", rate=edit.rate))
    assert edit.grid is not None, f"tick {tick} is between frames and the comp has no beat grid"
    return format_literal(position_literal(tick, "b", rate=edit.rate, grid=edit.grid))


def set_masks(project: Project, name: str, layer_id: str, masks: list[dict], expect: str | None = None) -> dict[str, Any]:
    """Replace a footage, graphics or precomp layer's masks with `masks`, in order ([] for none)."""
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] in TRANSFORMED, f"{layer_id} is a {layer['type']} layer, which has no masks"
    old = layer.get("masks", [])
    if masks:
        layer["masks"] = masks
    else:
        layer.pop("masks", None)
    shapes = lambda items: ", ".join(f"{m['shape']} ({m.get('mode', 'add')})" for m in items) or "none"
    edit.changes.append(f"{layer_id} masks {shapes(old)} to {shapes(masks)}")
    _resolves(project, edit)
    return edit.save()
