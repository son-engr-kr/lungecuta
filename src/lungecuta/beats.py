"""`lc beats`: the beat analysis of a music record run again, applied only if it keeps
every beat and bar literal of the project on its musical beat
(docs/design/03-project-format.md#beat-maps)."""
from __future__ import annotations

import bisect
from typing import Any

from . import music
from .comp import literals_past
from .files import content_hash, json_text, replace_if_unchanged
from .media import Project, load_record, music_wav
from .schema.media import MediaRecord


def first_changed(result: music.Reanalysis) -> int:
    """The first index of the current map that the new analysis renumbers or moves a
    downbeat at: a removed beat, the place an inserted beat goes, or a changed downbeat."""
    current = result.current
    candidates = list(result.removed)
    candidates += [bisect.bisect_left(current.times, result.new.times[j]) for j in result.inserted]
    candidates += sorted(set(current.downbeats) ^ set(result.new.downbeats))[:1]
    candidates += [k for k, j in result.pairs.items() if k != j][:1]
    return min(candidates) if candidates else len(current.times)


def reanalyse(project: Project, key: str, *, replace: bool = False) -> dict[str, Any]:
    """Run the beat analysis again on a music record's WAV and apply it only if it keeps
    every beat and downbeat index (docs/design/03-project-format.md#beat-maps), or when
    `replace` says the person has decided to take the shift."""
    record_path = project.directory / "media" / f"{key}.json"
    read_hash = content_hash(record_path)
    record = load_record(project, key)
    assert record.beats is not None, f"{key} is not music; it has no beat map"
    signal, rate = music.read_wav(music_wav(project, record))
    result = music.match(record.beats, music.analyse(signal, rate))
    applied = result.keeps_every_index or replace
    literals = [] if result.keeps_every_index else literals_past(project, key, first_changed(result))
    if applied:
        updated = record.model_copy(update={"beats": result.merged()})
        updated = MediaRecord.model_validate(updated.model_dump())
        replace_if_unchanged(record_path, read_hash, json_text(updated.model_dump(mode="json")))
    return {
        "media": key,
        "applied": applied,
        "keeps_every_index": result.keeps_every_index,
        "analyzer": result.new.analyzer.model_dump(),
        "beats": {"current": len(record.beats.times), "new": len(result.new.times)},
        "inserted": [{"new_index": j, "time": result.new.times[j]} for j in result.inserted],
        "removed": [{"index": k, "time": record.beats.times[k]} for k in result.removed],
        "downbeats_changed": result.new.downbeats != record.beats.downbeats,
        "dropped_manual": result.dropped_manual,
        "literals": literals,
    }
