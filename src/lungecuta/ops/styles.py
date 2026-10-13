"""Operations that place styles in time and transitions on cuts (03-project-
format.md#styles, 06-editing.md#transitions)."""
from __future__ import annotations

from typing import Any

from ..literals import Field, format_literal, parse_literal, resolve_position, span_literal
from ..media import Project
from ..schema.comp import CompFile
from .edit import Edit, _resolves


def style(project: Project, name: str, layer_id: str, style_id: str, at: str, intensity: float | None = None,
          expect: str | None = None) -> dict[str, Any]:
    """Place style `style_id` on a footage layer, its anchor on the comp beat `at` (written
    as a beat span from the layer's start, so it moves with the layer). A layer has one
    style instance: placing one replaces the one before, as Pickapicka's presets do. The
    layer must play from a source start (not remap keys or a constant speed)."""
    from ..comp import load_style, resolve

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
