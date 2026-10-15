"""`lungecuta levels`: a media file's sound over time, for an agent that cannot hear it
(docs/design/09-agent-interface.md). Music is measured bar by bar from its beat map (the
stretch before the first downbeat is a row of its own, as is the one after the last), or
beat by beat; a clip's own sound and a sound at an interval in seconds. Each row has the
RMS and the peak of both channels in dBFS, the BS.1770 integrated loudness where the row
is at least the 400 ms block that loudness is measured in, and how many onsets SuperFlux
finds in it (`onsets.py`). A chart of the RMS goes with it, a bar a row."""
from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from .audio import loudness
from .files import write_atomic
from .images import draw_text, png8
from .literals import BeatSpan, Field, Seconds, parse_literal
from .media import Project, footage_wav, load_record, music_wav
from .music import read_wav
from .onsets import onsets
from .sheet import LABEL_COLOUR, SHEET_DIR
from .ticks import TICKS_PER_SECOND

BLOCK_SECONDS = 0.4  # BS.1770's gating block: a shorter row has no loudness
# How the chart is drawn, not measurements: its bottom (quieter rows are drawn at it) and size
FLOOR_DB = -60.0
CHART = (1200, 240)


def _db(value: float) -> float | None:
    return round(20 * math.log10(value), 2) if value > 0 else None


def boundaries(record, every: str | None, samples: int, rate: int) -> list[tuple[int, int, dict[str, Any]]]:
    """The rows as sample ranges with what names them: bars (or every N beats with `b:N`)
    of a music record, or steps of `every` seconds from the start."""
    at = lambda seconds: min(samples, max(0, round(seconds * rate)))
    if every is None or isinstance(parse_literal(every, Field.STYLE), BeatSpan):
        assert record.beats is not None, f"{record.key} has no beat map; give --every in seconds (0.5s)"
        times = record.beats.times
        if every is None:
            name, marks = "bar", [(k, times[i]) for k, i in enumerate(record.beats.downbeats)]
        else:
            step = parse_literal(every, Field.STYLE).count
            assert step == int(step) and step >= 1, f"--every {every}: a whole number of beats"
            name, marks = "beat", [(k, times[k]) for k in range(0, len(times), int(step))]
        edges = [(None, 0), *((k, at(t)) for k, t in marks), (None, samples)]
        return [(a, b, {name: k} if k is not None else {"before": f"the first {name}"})
                for (k, a), (_, b) in zip(edges, edges[1:]) if b > a]
    literal = parse_literal(every, Field.SOURCE)
    assert isinstance(literal, Seconds), f"--every {every}: seconds (0.5s), or beats (b:1) for music"
    step = Fraction(literal.ticks, TICKS_PER_SECOND)
    assert step > 0, f"--every {every} does not move"
    count = math.ceil(samples / (step * rate))
    return [(round(k * step * rate), min(samples, round((k + 1) * step * rate)), {"from_seconds": float(k * step)})
            for k in range(count)]


def levels(project: Project, key: str, every: str | None = None, *, out: Path | None = None) -> dict[str, Any]:
    """The rows and the chart `lungecuta levels` prints and writes."""
    record = load_record(project, key)
    assert record.image is None, f"{key} is a photo, which has no sound"
    path = music_wav(project, record) if record.wav is not None else footage_wav(project, record)
    signal, rate = read_wav(path)
    found = np.array(onsets(signal, rate))
    rows = []
    for a, b, label in boundaries(record, every, len(signal), rate):
        piece = signal[a:b].astype(np.float64)
        row = dict(label) | {"from": round(a / rate, 3), "to": round(b / rate, 3),
                             "rms_db": _db(math.sqrt(float(np.mean(piece ** 2)))),
                             "peak_db": _db(float(np.abs(piece).max())),
                             "onsets": int(((found >= a / rate) & (found < b / rate)).sum())}
        if (b - a) / rate >= BLOCK_SECONDS:
            measured = loudness(piece, rate)
            row["lufs"] = round(measured, 2) if math.isfinite(measured) else None
        rows.append(row)
    if out is None:
        out = project.directory / SHEET_DIR / f"{key}_levels{'' if every is None else '_' + every.replace(':', '')}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, png8(chart(rows)))
    whole = loudness(signal.astype(np.float64), rate)
    return {"media": key, "png": str(out), "rows": rows, "seconds": round(len(signal) / rate, 3),
            "lufs": round(whole, 2) if math.isfinite(whole) else None,
            "loudest": max((r for r in rows if r["rms_db"] is not None), key=lambda r: r["rms_db"], default=None)}


def chart(rows: list[dict[str, Any]]) -> np.ndarray:
    """A bar a row, its height the RMS over the range the rows span (whole 3 dB steps
    around it, so a mastered song's few dB of movement show), labelled every eighth row."""
    width, height = CHART
    image = np.zeros((height, width, 3), np.uint8)
    plot = height - 20
    measured = [row["rms_db"] for row in rows if row["rms_db"] is not None]
    low = max(FLOOR_DB, 3 * math.floor((min(measured, default=FLOOR_DB) - 3) / 3))
    high = min(0.0, 3 * math.ceil((max(measured, default=0.0) + 1) / 3))
    y = lambda db: round(plot * (high - max(low, db)) / (high - low))
    step = width / max(1, len(rows))
    for k, row in enumerate(rows):
        top = y(low if row["rms_db"] is None else row["rms_db"])
        image[top:plot, round(k * step):max(round(k * step) + 1, round((k + 1) * step) - 1)] = (90, 170, 120)
    for db in range(int(high) - 3, int(low), -3):  # a line every 3 dB
        image[y(db), :] = (55, 55, 55)
    labels = [((round(k * step), plot + 2), str(row.get("bar", row.get("beat", row.get("from_seconds", "")))), LABEL_COLOUR)
              for k, row in enumerate(rows) if k % 8 == 1 or len(rows) <= 16]
    draw_text(image, labels + [((width - 90, 2), f"{high:g} dBFS", LABEL_COLOUR),
                               ((width - 90, plot - 16), f"{low:g} dBFS", LABEL_COLOUR)])
    return image
