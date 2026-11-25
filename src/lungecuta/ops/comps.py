"""Making a new comp (03-project-format.md#compositions-and-layers)."""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..files import content_hash, json_text, replace_if_unchanged
from ..literals import Field, parse_literal, resolve_position
from ..media import Project, load_record
from ..schema.comp import CompFile
from ..ticks import parse_rate, ticks_per_frame
from .edit import Edit


REMOVED_DIR = Path("cache") / "lungecuta" / "removed"


def _set_aside(project: Project, name: str) -> str:
    """Move comp `name`'s file out of comps/ into REMOVED_DIR, named by its hash, so a comp
    removed or replaced can still be read back; returns where it went."""
    path = Edit.path_of(project, name)
    assert path.is_file(), f"comps/{name}.json does not exist"
    kept = project.directory / REMOVED_DIR / f"{name}.{content_hash(path).removeprefix('sha256:')[:16]}.json"
    kept.parent.mkdir(parents=True, exist_ok=True)
    path.replace(kept)
    return kept.relative_to(project.directory).as_posix()


def _users(project: Project, name: str) -> list[str]:
    """The comps that hold comp `name` as a precomp layer."""
    users = []
    for other in sorted((project.directory / "comps").glob("*.json")):
        layers = json.loads(other.read_text()).get("layers", [])
        if other.stem != name and any(layer.get("type") == "comp" and layer.get("comp") == name for layer in layers):
            users.append(other.stem)
    return users


def remove_comp(project: Project, name: str) -> dict[str, Any]:
    """Take comp `name` out of project.json and its file out of comps/, kept in
    REMOVED_DIR; refused while another comp holds it as a precomp."""
    from ..project import PROJECT_FILE
    from ..schema.project import ProjectFile

    users = _users(project, name)
    assert not users, f"comp {name} is a precomp of {', '.join(users)}; take those layers off first"
    project_path = project.directory / PROJECT_FILE
    read_hash = content_hash(project_path)
    settings = ProjectFile.model_validate_json(project_path.read_text())
    relative = f"comps/{name}.json"
    assert relative in settings.comps, f"{relative} is not listed in project.json"
    kept = _set_aside(project, name)
    settings.comps.remove(relative)
    replace_if_unchanged(project_path, read_hash, json_text(settings.model_dump(mode="json")))
    return {"comp": name, "changes": [f"comp {name} removed, its file kept as {kept}"]}


def new_comp(project: Project, name: str, *, size: tuple[int, int], rate: str, end: str,
             music: str | None = None, replace: bool = False) -> dict[str, Any]:
    """A new comp, `comps/NAME.json`, listed in project.json: empty, or with MUSIC from 0s
    as the layer its beat grid comes from (03-project-format.md#beat-maps). With `replace`,
    a comp of that name is replaced, its file kept in REMOVED_DIR; comps that hold it as a
    precomp then hold the new one."""
    from ..files import write_new
    from ..project import PROJECT_FILE
    from ..schema.project import ProjectFile

    path = Edit.path_of(project, name)
    layers = []
    data: dict[str, Any] = {"id": name, "size": list(size), "rate": rate, "end": end, "layers": layers}
    if music is not None:
        from ..comp import beat_grid

        record = load_record(project, music)
        assert record.beats is not None, f"{music} has no beat map; import a music file"
        frame_rate = parse_rate(rate)
        seconds = Fraction(record.wav.samples, record.wav.rate)
        whole = int(seconds * frame_rate)
        layers.append({"id": "music", "type": "audio", "media": music, "start": "0s", "out": f"f:{whole}",
                       "source_start": "0s"})
        data["beats"] = {"from_layers": ["music"]}
        # the end may be a beat or a bar of this music: its grid is the comp's
        grid = beat_grid(project, CompFile.model_validate(data | {"end": f"f:{whole}"}), frame_rate)
        end_ticks = resolve_position(parse_literal(end, Field.POSITION), rate=frame_rate, grid=grid)
        layers[0]["out"] = f"f:{min(whole, -(-end_ticks // ticks_per_frame(frame_rate)))}"
    comp = CompFile.model_validate(data)
    project_path = project.directory / PROJECT_FILE
    read_hash = content_hash(project_path)
    settings = ProjectFile.model_validate_json(project_path.read_text())
    relative = f"comps/{name}.json"
    replaced = None
    if replace and relative in settings.comps:
        replaced = _set_aside(project, name)
    else:
        assert relative not in settings.comps, f"{relative} is listed already; --replace replaces it"
        settings.comps.append(relative)
    write_new(path, json_text(comp.model_dump(mode="json", by_alias=True, exclude_unset=True)))
    replace_if_unchanged(project_path, read_hash, json_text(settings.model_dump(mode="json")))
    return {"comp": name, "changes": [f"comp {name} created ({size[0]}x{size[1]}, {rate}, ends at {end})"
                                      + (f" with {music} as its music" if music else "")
                                      + (f", replacing the one kept as {replaced}" if replaced else "")]}
