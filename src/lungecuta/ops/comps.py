"""Making a new comp (03-project-format.md#compositions-and-layers)."""
from __future__ import annotations

from fractions import Fraction
from typing import Any

from ..files import content_hash, json_text, replace_if_unchanged
from ..literals import Field, parse_literal, resolve_position
from ..media import Project, load_record
from ..schema.comp import CompFile
from ..ticks import parse_rate, ticks_per_frame
from .edit import Edit


def new_comp(project: Project, name: str, *, size: tuple[int, int], rate: str, end: str,
             music: str | None = None) -> dict[str, Any]:
    """A new comp, `comps/NAME.json`, listed in project.json: empty, or with MUSIC from 0s
    as the layer its beat grid comes from (03-project-format.md#beat-maps)."""
    from ..files import write_new
    from ..project import PROJECT_FILE
    from ..schema.project import ProjectFile

    path = Edit.path_of(project, name)
    layers = []
    data: dict[str, Any] = {"id": name, "size": list(size), "rate": rate, "end": end, "layers": layers}
    if music is not None:
        record = load_record(project, music)
        assert record.beats is not None, f"{music} has no beat map; import a music file"
        parse_rate(rate)
        end_ticks = resolve_position(parse_literal(end, Field.POSITION), rate=parse_rate(rate))
        seconds = Fraction(record.wav.samples, record.wav.rate)
        frames = min(int(seconds * parse_rate(rate)), end_ticks // ticks_per_frame(parse_rate(rate)))
        layers.append({"id": "music", "type": "audio", "media": music, "start": "0s", "out": f"f:{frames}",
                       "source_start": "0s"})
        data["beats"] = {"from_layers": ["music"]}
    comp = CompFile.model_validate(data)
    project_path = project.directory / PROJECT_FILE
    read_hash = content_hash(project_path)
    settings = ProjectFile.model_validate_json(project_path.read_text())
    relative = f"comps/{name}.json"
    assert relative not in settings.comps, f"{relative} is listed already"
    write_new(path, json_text(comp.model_dump(mode="json", by_alias=True, exclude_unset=True)))
    settings.comps.append(relative)
    replace_if_unchanged(project_path, read_hash, json_text(settings.model_dump(mode="json")))
    return {"comp": name, "changes": [f"comp {name} created ({size[0]}x{size[1]}, {rate}, ends at {end})"
                                      + (f" with {music} as its music" if music else "")]}
