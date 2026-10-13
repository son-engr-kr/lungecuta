"""`lc lint` (docs/design/09-agent-interface.md, Lint): the past projects' review rules that
a machine can check, as warnings, each with its threshold in `project.json`'s `lint`.

Checked so far: graphics layers on screen long enough to read, no source frame shown by
two layers, and a licence record on every music file a comp uses. Flashes are checked on
a render, by `lc flash FILE` (`flash.py`), since any layer can make one. Not checked yet,
and listed in every report so their absence is not mistaken for a pass: SFX variety and
the dead gap before a hit (no SFX layers yet), sources for on-screen numbers and font
licences (no text layers yet).
"""
from __future__ import annotations

from typing import Any

from .comp import load_comp, resolve
from .media import Project

NOT_CHECKED = [
    "flashes are not strobing: checked on the render, by lc flash FILE",
    "SFX vary, no dead gap before the hit: there are no SFX layers yet",
    "on-screen claims have sources, fonts are licensed: there are no text layers yet",
]


def _ranges(frames: list[int]) -> list[list[int]]:
    """Sorted frame numbers as runs [first, last]."""
    runs: list[list[int]] = []
    for frame in sorted(set(frames)):
        if runs and frame == runs[-1][1] + 1:
            runs[-1][1] = frame
        else:
            runs.append([frame, frame])
    return runs


def lint(project: Project, name: str) -> dict[str, Any]:
    settings = project.file.lint
    resolved = resolve(project, load_comp(project, name))
    rate = float(resolved.rate)
    warnings = []
    if settings.graphics_min_seconds is not None:
        for overlay in resolved.overlays:
            seconds = (overlay.end - overlay.first) / rate
            if seconds < settings.graphics_min_seconds:
                warnings.append({"rule": "graphics_min_seconds", "layers": [overlay.layer.id],
                                 "message": f"{overlay.layer.id} is on screen {seconds:.2f} s, under "
                                            f"{settings.graphics_min_seconds} s"})
    if settings.repeated_source:
        cuts = resolved.cuts
        for k, a in enumerate(cuts):
            for b in cuts[k + 1:]:
                if a.record.key != b.record.key:
                    continue
                shared = set(a.all_samples()) & set(b.all_samples())
                if shared:
                    runs = _ranges(list(shared))
                    warnings.append({"rule": "repeated_source", "layers": [a.layer.id, b.layer.id],
                                     "message": f"{a.layer.id} and {b.layer.id} both show {a.record.key} source "
                                                f"frames {', '.join(f'{r[0]} to {r[1]}' for r in runs)}"})
    if settings.music_licence:
        for key in sorted({sound.record.key for sound in resolved.sounds}):
            record = next(sound.record for sound in resolved.sounds if sound.record.key == key)
            if record.licence is None:
                warnings.append({"rule": "music_licence", "layers": [s.layer.id for s in resolved.sounds
                                                                     if s.record.key == key],
                                 "message": f"{key} has no licence record (lc licence {key} --name ... --source ...)"})
    return {"comp": name, "warnings": warnings, "settings": settings.model_dump(), "not_checked": NOT_CHECKED}
