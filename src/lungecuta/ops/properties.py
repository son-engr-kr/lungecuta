"""Operations on a layer's properties: keys and constants of its transform, shutter and
gain, time remapping, and masks."""
from __future__ import annotations

from typing import Any

from ..literals import TICKS_PER_SECONDS_STEP, Field, Seconds, format_literal, parse_literal, position_literal, resolve_position, resolve_span, span_literal
from ..media import Project
from ..schema.comp import CompFile
from ..ticks import ticks_per_frame
from .edit import Edit, _resolves, _unit


KEYED = ("position", "scale", "opacity", "rotation", "remap", "gain", "shutter")


LAYER_OF = {"position": "footage", "scale": "footage", "opacity": "footage", "rotation": "footage", "remap": "footage",
            "gain": "audio", "shutter": "footage"}


def _holder(layer: dict, prop: str, create: bool = False) -> tuple[dict, str]:
    """Where a property's value sits in the layer's JSON, and its field there: the
    transform's, the layer's own (gain, fades), or the motion blur's shutter."""
    if prop == "shutter":
        return (layer.setdefault("motion_blur", {}) if create else layer.get("motion_blur") or {}), "shutter_deg"
    if prop in ("gain", "fade_in", "fade_out"):
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
        assert "remap" in time, f"{layer['id']} is not remapped; enable time remapping first (lc op remap)"
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
    in After Effects): a footage layer's transform, an audio layer's gain in dB, or its
    fades as spans (None takes a fade off)."""
    owners = {"position": "footage", "scale": "footage", "opacity": "footage", "rotation": "footage",
              "anchor": "footage", "shutter": "footage", "gain": "audio", "fade_in": "audio", "fade_out": "audio"}
    assert prop in owners, f"{prop!r} is not a property `set` takes ({', '.join(owners)})"
    edit = Edit.open(project, name, expect)
    layer = edit.layer(layer_id)
    assert layer["type"] == owners[prop], f"{prop} is a property of {owners[prop]} layers, and {layer_id} is not one"
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
    from ..comp import resolve

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
