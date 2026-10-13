"""`lc diff` (docs/design/09-agent-interface.md): what changed in a comp between two
revisions, in editing terms ("shot3 out point moved b:68 to b:67+0.5").

A revision is a git revision of the repository the project lives in, a path to a comp
file, or `working` for the file as it is now. Fields are compared with their defaults
filled in, so a field written out at its default is not a change; times are reported as
the literals the file holds, which is what an edit changed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .media import Project
from .schema.comp import CompFile, Transform
from .tools import run

WORDS = {"start": "start", "in": "in point", "out": "out point", "source_start": "source start"}
TIMES = {"start": "", "in": " (from start)", "out": " (from start)", "source_start": ""}
DEFAULT_TRANSFORM = Transform().model_dump(mode="json")


def comp_at(project: Project, name: str, revision: str) -> CompFile:
    """Comp `name` at a revision: `working`, a comp file's path, or a git revision."""
    relative = f"comps/{name}.json"
    if revision == "working":
        return CompFile.model_validate_json((project.directory / relative).read_text())
    if Path(revision).is_file():
        return CompFile.model_validate_json(Path(revision).read_text())
    prefix = run(["git", "-C", str(project.directory), "rev-parse", "--show-prefix"]).stdout.decode().strip()
    text = run(["git", "-C", str(project.directory), "show", f"{revision}:{prefix}{relative}"]).stdout.decode()
    return CompFile.model_validate_json(text)


def _short(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, separators=(", ", ": "))


def _layer_changes(before: dict, after: dict) -> list[str]:
    name = after["id"]
    changes = []
    for key in sorted(set(before) | set(after), key=lambda k: (k not in WORDS, k)):
        a, b = before.get(key), after.get(key)
        if a == b or key == "id":
            continue
        if key in WORDS:
            changes.append(f"{name} {WORDS[key]} moved {_short(a)} to {_short(b)}{TIMES[key]}")
        elif key == "transform":  # an absent transform is the default one, property by property
            a, b = a or DEFAULT_TRANSFORM, b or DEFAULT_TRANSFORM
            for prop in sorted(set(a) | set(b)):
                if a.get(prop) != b.get(prop):
                    changes.append(f"{name} {prop} changed {_short(a.get(prop))} to {_short(b.get(prop))}")
        elif a is None:
            changes.append(f"{name} {key} set to {_short(b)}")
        elif b is None:
            changes.append(f"{name} {key} taken off (was {_short(a)})")
        else:
            changes.append(f"{name} {key} changed {_short(a)} to {_short(b)}")
    return changes


def diff(project: Project, name: str, before_rev: str, after_rev: str) -> dict[str, Any]:
    before, after = comp_at(project, name, before_rev), comp_at(project, name, after_rev)
    a, b = before.model_dump(mode="json", by_alias=True), after.model_dump(mode="json", by_alias=True)
    changes = []
    for key in ("size", "rate", "end", "beats", "markers", "transitions"):
        if a[key] != b[key]:
            changes.append(f"comp {key} changed {_short(a[key])} to {_short(b[key])}")
    layers_a = {layer["id"]: layer for layer in a["layers"]}
    layers_b = {layer["id"]: layer for layer in b["layers"]}
    for layer in b["layers"]:
        if layer["id"] not in layers_a:
            what = layer.get("media") or layer.get("page")
            changes.append(f"{layer['id']} added: {layer['type']} {what} from {layer['start']}")
    for layer in a["layers"]:
        if layer["id"] not in layers_b:
            changes.append(f"{layer['id']} removed (was {layer['type']} {layer.get('media') or layer.get('page')})")
    kept = [layer for layer in a["layers"] if layer["id"] in layers_b]
    order_a = [layer["id"] for layer in kept]
    order_b = [layer["id"] for layer in b["layers"] if layer["id"] in layers_a]
    if order_a != order_b:
        changes.append(f"stack order changed (bottom first) {', '.join(order_a)} to {', '.join(order_b)}")
    for layer in b["layers"]:
        if layer["id"] in layers_a:
            changes += _layer_changes(layers_a[layer["id"]], layer)
    return {"comp": name, "from": before_rev, "to": after_rev, "changes": changes}
