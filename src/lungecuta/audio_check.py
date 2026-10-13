"""`lc audio-check` (docs/design/07-audio.md, Analysis and checks): what an agent that cannot
hear measures before it calls an audio change done.

Written so far, over a span of the comp:
- every cut (a footage layer's first frame) with its signed offset in milliseconds to the
  nearest beat of the comp's grid, and whether that beat is a downbeat;
- its signed offset in milliseconds to the nearest onset of the mix (before the master
  chain), found by SuperFlux (`onsets.py`); onsets come one or two frames (5 to 10 ms)
  before the hit on a synthetic drum track, a lead measured and reported, not corrected;
- the RMS level of the mix, before the master chain, on every half beat, in dBFS, and its
  least-squares slope in dB per beat, which says whether a build climbs. No threshold
  turns the slope into a pass or a fail: none is published or measured here.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from .comp import MIX_RATE, Resolved, load_comp, resolve
from .literals import Field, parse_literal, resolve_position
from .media import Project
from .mixdown import mix
from .onsets import THRESHOLD, onsets
from .ticks import TICKS_PER_SECOND, ticks_per_frame

METHOD = "SuperFlux (Boeck and Widmer, DAFx-13), offline peak picking; src/lungecuta/onsets.py"


def _span(resolved: Resolved, start: str | None, end: str | None) -> tuple[int, int]:
    """The span in comp ticks, the whole comp by default."""
    tick = ticks_per_frame(resolved.rate)
    first = 0 if start is None else resolve_position(parse_literal(start, Field.POSITION), rate=resolved.rate,
                                                     grid=resolved.grid)
    last = resolved.end * tick if end is None else resolve_position(parse_literal(end, Field.POSITION),
                                                                     rate=resolved.rate, grid=resolved.grid)
    assert first < last, f"the span {start} to {end} is empty"
    return first, last


def cuts(resolved: Resolved, first: int, last: int, onset_times: list[float] | None = None) -> list[dict[str, Any]]:
    tick = ticks_per_frame(resolved.rate)
    grid = resolved.grid
    downbeats = set() if grid is None else {grid.times[k] for k in grid.downbeats}
    out = []
    for cut in resolved.cuts:
        at = cut.first * tick
        if not first <= at < last or cut.first == 0:
            continue
        row: dict[str, Any] = {"layer": cut.layer.id, "frame": cut.first, "seconds": round(at / TICKS_PER_SECOND, 6)}
        if grid is not None:
            nearest = min(range(len(grid.times)), key=lambda k: abs(grid.times[k] - at))
            row |= {"nearest_beat": nearest, "offset_ms": round((at - grid.times[nearest]) / TICKS_PER_SECOND * 1000, 2),
                    "downbeat": grid.times[nearest] in downbeats}
        if onset_times:
            seconds = at / TICKS_PER_SECOND
            onset = min(onset_times, key=lambda t: abs(t - seconds))
            row |= {"nearest_onset_seconds": round(onset, 3), "onset_offset_ms": round((seconds - onset) * 1000, 2)}
        out.append(row)
    return out


def half_beat_rms(mixed: np.ndarray, resolved: Resolved, first: int, last: int) -> tuple[list[dict], float | None]:
    """RMS in dBFS of the mix (both channels) on every half beat inside the span, and the
    least-squares slope of those levels in dB per beat (None with fewer than two)."""
    grid = resolved.grid
    if grid is None:
        return [], None
    signal = mixed.astype(np.float64)
    rows = []
    for k in range(len(grid.times) - 1):
        a, b = grid.times[k], grid.times[k + 1]
        for half, (t0, t1) in enumerate(((a, (a + b) // 2), ((a + b) // 2, b))):
            if t0 < first or t1 > last:
                continue
            s0, s1 = round(t0 * MIX_RATE / TICKS_PER_SECOND), round(t1 * MIX_RATE / TICKS_PER_SECOND)
            piece = signal[s0:s1]
            if not piece.size:
                continue
            rms = math.sqrt(float(np.mean(piece ** 2)))
            rows.append({"beat": k + half / 2, "db": round(20 * math.log10(rms), 2) if rms > 0 else None})
    levels = [(row["beat"], row["db"]) for row in rows if row["db"] is not None]
    if len(levels) < 2:
        return rows, None
    beats, dbs = np.array(levels).T
    return rows, round(float(np.polyfit(beats, dbs, 1)[0]), 3)


def audio_check(project: Project, name: str, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    resolved = resolve(project, load_comp(project, name))
    first, last = _span(resolved, start, end)
    signal = mix(project, resolved)
    rows, slope = half_beat_rms(signal, resolved, first, last)
    found = onsets(signal, MIX_RATE) if any(True for _ in resolved.every_sound()) else []
    span = [first / TICKS_PER_SECOND, last / TICKS_PER_SECOND]
    inside = [round(t, 3) for t in found if span[0] <= t < span[1]]
    return {
        "comp": name,
        "span_seconds": [round(span[0], 6), round(span[1], 6)],
        "cuts": cuts(resolved, first, last, found),
        "rms_half_beats": rows,
        "rms_slope_db_per_beat": slope,
        "onsets": {"method": METHOD, "threshold": THRESHOLD, "count": len(inside), "seconds": inside},
    }
