"""Operations on a footage layer's grade: replacing it, and putting looks on and taking
them off (04-color.md#node-graph)."""
from __future__ import annotations

from typing import Any

from ..media import Project
from .edit import Edit


def set_grade(project: Project, name: str, layer_id: str, grade: list[dict] | dict,
              expect: str | None = None) -> dict[str, Any]:
    """Replace a footage layer's grade with `grade` (operations in order, or a node graph),
    after checking that every node builds (LUTs found, OCIO's ranges)."""
    from ..schema.comp import FootageLayer

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
    from .. import grade as grading

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
    from ..schema.comp import FootageLayer

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
