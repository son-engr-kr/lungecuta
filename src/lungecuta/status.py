"""`lc status` (docs/design/09-agent-interface.md): what is in a project, for an agent that
has just arrived. Its media (footage with its colour decision, music with its tempo and
whether its licence is recorded), its comps (size, rate, length, layers by type, markers,
transitions), how many editor changes the agent has not seen yet (`lc changes` would
list them), and the renders in the cache with whether each still matches its comp and
media. Nothing is changed: the journal cursor stays where it is.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from . import journal, music
from .comp import load_comp, resolve
from .files import content_hash
from .media import Project, load_record
from .render import RENDER_DIR
from .schema.media import ColorDecided, TimeDecided
from .ticks import TICKS_PER_SECOND, ticks_per_frame


def _media(project: Project) -> dict[str, list[dict[str, Any]]]:
    footage, tracks, sounds = [], [], []
    for path in sorted((project.directory / "media").glob("*.json")):
        record = load_record(project, path.stem)
        if record.beats is not None:
            tracks.append({"key": record.key, "beats": len(record.beats.times),
                           "tempo_bpm": music.tempo(record.beats), "licence": record.licence is not None})
            continue
        if record.wav is not None:
            sounds.append({"key": record.key, "seconds": round(record.wav.samples / record.wav.rate, 3),
                           "licence": record.licence is not None})
            continue
        video = record.probe.video
        decided = isinstance(record.color, ColorDecided) and isinstance(record.time, TimeDecided)
        footage.append({"key": record.key, "size": list(video.size), "rate": video.rate, "frames": video.frames,
                        "space": record.color.space if isinstance(record.color, ColorDecided) else None,
                        "usable": decided})
    return {"footage": footage, "music": tracks, "sounds": sounds}


def _comps(project: Project) -> list[dict[str, Any]]:
    out = []
    for path in project.file.comps:
        comp = load_comp(project, Path(path).stem)
        resolved = resolve(project, comp)
        ticks = resolved.end * ticks_per_frame(resolved.rate)
        out.append({
            "comp": comp.id, "size": list(comp.size), "rate": comp.rate, "frames": resolved.end,
            "seconds": round(ticks / TICKS_PER_SECOND, 3),
            "beats": None if resolved.grid is None else len(resolved.grid.times),
            "layers": dict(Counter(layer.type for layer in comp.layers)),
            "markers": [at.marker.name for at in resolved.markers],
            "transitions": len(comp.transitions),
        })
    return out


def _renders(project: Project) -> list[dict[str, Any]]:
    """Each render in the cache, and whether its comp and media are still what it was made from."""
    out = []
    for manifest_path in sorted((project.directory / RENDER_DIR).glob("*.manifest.json")):
        manifest = json.loads(manifest_path.read_text())
        file = manifest_path.with_name(manifest["file"])
        comp_file = project.directory / "comps" / f"{manifest['comp']['id']}.json"
        stale = []
        if not file.is_file():
            stale.append("the file is gone")
        if not comp_file.is_file() or content_hash(comp_file) != manifest["comp"]["hash"]:
            stale.append("the comp changed")
        for name, hash_ in manifest.get("precomps", []):
            precomp = project.directory / "comps" / f"{name}.json"
            if not precomp.is_file() or content_hash(precomp) != hash_:
                stale.append(f"precomp {name} changed")
        for name, hash_ in manifest.get("looks", []):
            style = project.directory / "styles" / f"{name}.json"
            if not style.is_file() or content_hash(style) != hash_:
                stale.append(f"look {name} changed")
        for path, hash_ in manifest.get("luts", []):
            lut = project.directory / path
            if not lut.is_file() or content_hash(lut) != hash_:
                stale.append(f"{path} changed")
        for key, hash_ in manifest["media"]:
            record_path = project.directory / "media" / f"{key}.json"
            if not record_path.is_file():
                stale.append(f"{key} is no longer imported")
                continue
            record = load_record(project, key)
            if hash_ not in (record.hash, record.wav.hash if record.wav else None):
                stale.append(f"{key} changed")
        out.append({"file": str(file), "preset": manifest["preset"], "comp": manifest["comp"]["id"],
                    "current": not stale, "why_not": stale})
    return out


def status(project: Project) -> dict[str, Any]:
    settings = project.file.color
    unseen = journal.changes(project.directory, peek=True)["entries"]
    return {
        "project": str(project.directory),
        "color": {"working": settings.working, "display": settings.display, "view": settings.view},
        "media": _media(project),
        "comps": _comps(project),
        "unseen_editor_changes": len(unseen),
        "renders": _renders(project),
    }
