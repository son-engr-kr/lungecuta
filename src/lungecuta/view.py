"""What the person has on screen in the editor (docs/design/06-editing.md, What the agent
sees of the editor): the comp open, the playhead, the selected layer and the range marked
with I and O. The editor reports it as it changes, into `cache/editor.json`, and removes
the file when it stops; `lungecuta changes` prints it, so an agent knows what "here", "this
shot" and "this part" mean when the person says them. It is not an edit and is not
journalled."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .comp import load_comp, resolve
from .files import json_text, write_atomic
from .look import place_of
from .media import Project

VIEW = Path("cache") / "editor.json"


def write_view(directory: Path, comp: str, frame: int, selected: str | None, mark_in: int | None,
               mark_out: int | None) -> None:
    """Record what the editor shows: frames are comp frame indices, the marks inclusive."""
    assert (directory / "comps" / f"{comp}.json").is_file(), f"no comp {comp!r} in {directory}"
    assert frame >= 0, f"the playhead is at frame {frame}"
    for mark in (mark_in, mark_out):
        assert mark is None or mark >= 0, f"a mark at frame {mark}"
    assert mark_in is None or mark_out is None or mark_in <= mark_out, f"in f:{mark_in} is after out f:{mark_out}"
    write_atomic(directory / VIEW, json_text({
        "comp": comp, "frame": frame, "selected": selected, "in": mark_in, "out": mark_out,
        "reported": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }))


def remove_view(directory: Path) -> None:
    (directory / VIEW).unlink(missing_ok=True)


def read_view(project: Project) -> dict[str, Any] | None:
    """What the open editor last reported, each frame placed in the comp as it is now (its
    seconds, beat and layers on screen), or None when no editor is open. The marked range
    is also given as `from` and `to` positions, `to` the frame after the out mark, as
    `lungecuta sheet --from --to` takes them."""
    path = project.directory / VIEW
    if not path.is_file():
        return None
    view = json.loads(path.read_text())
    resolved = resolve(project, load_comp(project, view["comp"]))

    def place(frame: int | None) -> dict[str, Any] | None:
        if frame is None:
            return None
        if frame >= resolved.end:
            return {"frame": frame, "past_end": resolved.end}  # the comp was shortened since the report
        return place_of(resolved, frame)

    out: dict[str, Any] = {"comp": view["comp"], "playhead": place(view["frame"]), "reported": view["reported"]}
    if view["selected"] is not None:
        out["selected"] = {"layer": view["selected"],
                           "in_comp": any(layer.id == view["selected"] for layer in resolved.comp.layers)}
    if view["in"] is not None or view["out"] is not None:
        first = 0 if view["in"] is None else view["in"]
        last = resolved.end - 1 if view["out"] is None else view["out"]
        out["marked"] = {"in": place(view["in"]), "out": place(view["out"]),
                         "from": f"f:{first}", "to": f"f:{last + 1}"}
    return out
