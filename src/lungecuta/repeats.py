"""Frames that repeat the image before them (docs/design/06-editing.md, Repeated images).
A shutter longer than a frame (1/5 s at 23.98p) leaves the camera writing one image for
several frames, each encoded again, so such a frame differs from the one before it by the
encoder's noise alone, and a new image comes on a fixed period. A cut that starts a few
frames into an image shows it for fewer frames than the others, and the cadence stumbles
at the cut; so a shot of such a clip starts on a frame where a new image begins.

Each frame's difference from the one before is measured at 160x90 in grey. A clip repeats
its images when, for some period of 2 to 24 frames and some phase, every frame on the
period differs from the one before more than every frame off it does: the two sets do not
overlap. No threshold of difference is needed, which matters, since a still scene in an
ordinary clip changes less from frame to frame than a dim new image does. On a real shoot,
seven clips at 1/4 to 1/10 s separated by a factor of 2.8 to 54 (smallest change on the
period over largest off it) and eight at 1/50 s and faster by at most 0.59
(`bench/repeats.py`, `bench/RESULTS.md`). The grey frames are ffmpeg's scale filter's,
which lungecuta does not use to show a frame (`decode.py`); it serves here for a measure of
change, which a small gain error would not reorder."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .files import json_text, write_atomic
from .media import Project, load_record, original
from .tools import run

SIZE = (160, 90)
RESOLUTION = 1 / (SIZE[0] * SIZE[1])  # the smallest mean difference there is: one code at one pixel
PERIODS = range(2, 25)  # frames an image may last: up to a second at 24p
CACHE = Path("cache") / "lungecuta" / "repeats"


def differences(path: Path) -> np.ndarray:
    """Each frame's mean absolute difference from the one before, in 8-bit grey codes at
    160x90 (the first frame has none)."""
    raw = run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-vf",
               f"scale={SIZE[0]}:{SIZE[1]},format=gray", "-f", "rawvideo", "-"]).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, SIZE[1], SIZE[0]).astype(np.float32)
    return np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))


def cadence(change: np.ndarray) -> tuple[float, int, int]:
    """The period and phase (of `change`'s indices) that separate best, and by how much:
    the smallest change on the period over the largest off it. Above 1, the clip repeats
    its images; a period needs three of itself in the clip to be tried."""
    change = change.astype(np.float64)
    best = (0.0, 0, 0)
    for period in PERIODS:
        if len(change) < 3 * period:
            break
        for phase in range(period):
            off = np.ones(len(change), bool)
            off[phase::period] = False
            # an exact repeat differs by nothing; the measure resolves one code at one pixel
            separation = float(change[phase::period].min() / max(float(change[off].max()), RESOLUTION))
            if separation > best[0]:
                best = (separation, period, phase)
    return best


def _cached(project: Project, key: str) -> Path:
    record = load_record(project, key)
    return project.directory / CACHE / f"{key}.{record.hash.removeprefix('sha256:')[:16]}.json"


def measured(project: Project, key: str) -> dict[str, Any] | None:
    """What `new_images` found for clip `key`, if it has been measured."""
    path = _cached(project, key)
    return json.loads(path.read_text()) if path.is_file() else None


def new_images(project: Project, key: str) -> dict[str, Any]:
    """Whether clip `key` repeats its images, and if so how many frames an image lasts and
    the frames where one begins (the first frame's image may have begun before the clip,
    so it is listed only when measured); kept in the cache under the original's hash."""
    record = load_record(project, key)
    video = record.probe.video
    assert video is not None and record.image is None, f"{key} is not footage"
    path = _cached(project, key)
    if path.is_file():
        return json.loads(path.read_text())
    change = differences(original(project, record))
    assert len(change) == video.frames - 1, f"{key}: {len(change) + 1} frames decoded for {video.frames}"
    separation, period, phase = cadence(change)
    repeats = separation > 1
    result = {"media": key, "frames": video.frames, "repeats": repeats, "separation": round(separation, 3),
              "every": period if repeats else None,
              "new": [k + 1 for k in range(phase, len(change), period)] if repeats else None}
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json_text(result))
    return result
