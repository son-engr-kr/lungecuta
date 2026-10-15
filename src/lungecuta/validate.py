"""`lungecuta validate`, Phase 0's part: every project file against its schema, every original
found and unchanged since import, every comp resolved to frames and every layer's grade
built (docs/design/03-project-format.md#validation). The first problem stops it with its
message, as every `lungecuta` command does."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import grade
from .comp import load_comp, resolve
from .media import Project, load_record, music_wav, original
from .schema.media import ColorDecided, TimeDecided


def validate(project: Project) -> dict[str, Any]:
    keys = sorted(path.stem for path in (project.directory / "media").glob("*.json"))
    undecided = []
    for key in keys:
        record = load_record(project, key)
        assert record.key == key, f"media/{key}.json holds media {record.key!r}"
        original(project, record)
        if record.wav is not None:
            music_wav(project, record)
        elif record.image is None and not (isinstance(record.color, ColorDecided) and isinstance(record.time, TimeDecided)):
            undecided.append(key)  # a photo has nothing to decide: it shows as a page shows it
    comps = []
    for path in project.file.comps:
        resolved = resolve(project, load_comp(project, Path(path).stem))
        for cut in resolved.cuts:  # every grade builds: LUTs found, spaces known, values in OCIO's ranges
            for node in grade.nodes(cut.layer.grade):
                for inner in grade.nodes(grade.look(project, node.look)) if node.look else [node]:
                    grade.processor(project, inner.ops)
        comps.append({
            "comp": resolved.comp.id,
            "frames": resolved.end,
            "cuts": len(resolved.cuts),
            "sounds": len(resolved.sounds),
            "beats": None if resolved.grid is None else len(resolved.grid.times),
        })
    return {"valid": True, "media": len(keys), "undecided": undecided, "comps": comps}
