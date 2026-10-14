"""`lungecuta scope --media`: waveform, RGB parade and vectorscope of one frame, with the numbers
behind them (docs/design/09-agent-interface.md#scope-numbers). Everything is computed on
the display-encoded output of the project's display and view, as a grading application's
scopes show it."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .files import write_atomic
from .geometry import scaled_size
from .images import draw_text, png8, text_height
from .media import Project, load_record
from .still import OUTPUT_CODES, PERCENTILES, code_numbers, open_clip, source_frame

SCOPE_DIR = Path("cache") / "lungecuta" / "scope"
# BT.709-6 (docs/research/color-sources.md, section 7): EY = 0.2126 ER + 0.7152 EG +
# 0.0722 EB, ECB = (EB - EY) / 1.8556, ECR = (ER - EY) / 1.5748, on the encoded R'G'B',
# which is the definition of video luma.
KR, KB = 0.2126, 0.0722
LUMA = np.array([KR, 1 - KR - KB, KB], np.float32)
CB_SCALE, CR_SCALE = 2 * (1 - KB), 2 * (1 - KR)
# Layout, not measurements: 512 rows hold the 1024 output codes two to a row; the luma
# waveform is 1024 columns of the frame wide, each parade channel 512.
ROWS = 512
WAVEFORM_COLUMNS = 1024
PARADE_COLUMNS = 512
VECTOR_SIZE = 512
GRATICULE_CODES = (0, 128, 256, 384, 512, 640, 768, 896, 1023)
GRATICULE = (60, 60, 60)
TEXT = (150, 150, 150)
TRACES = {"Y'": (255, 255, 255), "R'": (255, 64, 64), "G'": (64, 255, 64), "B'": (96, 128, 255)}
# Colour-bar targets: the primaries and secondaries at 100 % and 75 % of full scale.
BARS = {"R": (1, 0, 0), "Yl": (1, 1, 0), "G": (0, 1, 0), "Cy": (0, 1, 1), "B": (0, 0, 1), "Mg": (1, 0, 1)}


def luma(frame: np.ndarray) -> np.ndarray:
    return frame @ LUMA


def chroma(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Cb and Cr by the BT.709 matrix, each in [-0.5, 0.5] for R'G'B' in [0, 1]."""
    y = luma(frame)
    return (frame[..., 2] - y) / np.float32(CB_SCALE), (frame[..., 0] - y) / np.float32(CR_SCALE)


def scope_numbers(frame: np.ndarray) -> dict[str, Any]:
    """Luma percentiles, clipped fractions, per-channel numbers and chroma magnitude, in
    10-bit output codes (fractions of pixels for `clipped`), before any quantization."""
    low = (frame <= 0).any(axis=-1)
    high = (frame >= 1).any(axis=-1)
    cb, cr = chroma(frame)
    magnitude = np.hypot(cb, cr)
    return {
        "luma": dict(zip(("p1", "p50", "p99"), _codes(np.percentile(luma(frame), PERCENTILES)))),
        "clipped": {"any": _fraction(low | high), "low": _fraction(low), "high": _fraction(high)},
        "codes": code_numbers(frame),
        "chroma": dict(zip(("p50", "p99"), _codes(np.percentile(magnitude, (50, 99))))),
    }


def _codes(values: np.ndarray) -> list[float]:
    return [round(float(value) * OUTPUT_CODES, 4) for value in values]


def _fraction(mask: np.ndarray) -> float:
    return round(float(mask.mean()), 6)


def waveform_counts(values: np.ndarray, columns: int, rows: int = ROWS) -> np.ndarray:
    """How many pixels of each frame column band land on each row, code 1023 at the top row
    and code 0 at the bottom; values outside [0, 1] are counted on the edge rows. A frame
    narrower than `columns` gets one band per frame column, widened to fill the panel."""
    height, width = values.shape
    bands = min(columns, width)
    band = np.arange(width) * bands // width
    row = rows - 1 - np.clip(np.floor(values * np.float32(rows - 1) + np.float32(0.5)), 0, rows - 1).astype(np.int64)
    counts = np.bincount((row * bands + band[None, :]).ravel(), minlength=rows * bands).reshape(rows, bands)
    return counts[:, np.arange(columns) * bands // columns]


def vector_position(cb: np.ndarray | float, cr: np.ndarray | float, size: int = VECTOR_SIZE) -> tuple[Any, Any]:
    """Pixel column and row of a chroma value: Cb to the right, Cr up, the centre neutral."""
    scale = size - 1
    column = np.clip(np.floor((np.asarray(cb) + 0.5) * scale + 0.5), 0, scale).astype(np.int64)
    row = np.clip(np.floor((0.5 - np.asarray(cr)) * scale + 0.5), 0, scale).astype(np.int64)
    return column, row


def vectorscope_counts(frame: np.ndarray, size: int = VECTOR_SIZE) -> np.ndarray:
    column, row = vector_position(*chroma(frame), size)
    return np.bincount((row * size + column).ravel(), minlength=size * size).reshape(size, size)


def bar_targets(level: float) -> dict[str, tuple[int, int]]:
    """Where each colour bar at `level` of full scale lands on the vectorscope."""
    targets = {}
    for name, rgb in BARS.items():
        cb, cr = chroma(np.array(rgb, np.float32) * np.float32(level))
        column, row = vector_position(cb, cr)
        targets[name] = (int(column), int(row))
    return targets


def _trace(counts: np.ndarray, colour: tuple[int, int, int]) -> np.ndarray:
    """Counts as brightness on a log scale, the busiest bin at full brightness."""
    peak = counts.max()
    level = np.log1p(counts) / np.log1p(peak) if peak else np.zeros(counts.shape)
    return (level[..., None] * np.array(colour, np.float64)).round().astype(np.uint8)


def _graticule_row(code: int) -> int:
    return ROWS - 1 - int(np.floor(code / OUTPUT_CODES * (ROWS - 1) + 0.5))


def _waveform_panel(values: np.ndarray, columns: int, title: str) -> tuple[np.ndarray, list]:
    panel = np.zeros((ROWS, columns, 3), np.uint8)
    for code in GRATICULE_CODES:
        panel[_graticule_row(code)] = GRATICULE
    panel = np.maximum(panel, _trace(waveform_counts(values, columns), TRACES[title]))
    line = text_height()
    texts = [((6, 4), title, TRACES[title])]
    for code in GRATICULE_CODES[1:]:
        row = _graticule_row(code)
        texts.append(((columns - 44, row + 2 if code == GRATICULE_CODES[-1] else row - line - 1), str(code), TEXT))
    return panel, texts


def _vector_panel(frame: np.ndarray) -> tuple[np.ndarray, list]:
    panel = np.zeros((VECTOR_SIZE, VECTOR_SIZE, 3), np.uint8)
    centre = VECTOR_SIZE // 2
    panel[centre, :] = GRATICULE
    panel[:, centre] = GRATICULE
    panel = np.maximum(panel, _trace(vectorscope_counts(frame), TRACES["Y'"]))
    texts = [((6, 4), "Cb Cr, BT.709", TRACES["Y'"])]
    for level, half in ((1.0, 2), (0.75, 5)):
        for name, (column, row) in bar_targets(level).items():
            box = panel[max(row - half, 0):row + half + 1, max(column - half, 0):column + half + 1]
            box[[0, -1], :] = TEXT
            box[:, [0, -1]] = TEXT
            if level == 1.0:
                # Named a little way in from the 100 % target, towards the centre.
                x, y = column + (centre - column) * 0.15, row + (centre - row) * 0.15
                texts.append(((round(x) - 8, round(y) - 8), name, TEXT))
    return panel, texts


def scope_image(frame: np.ndarray) -> np.ndarray:
    """Luma waveform and vectorscope above, the R'G'B' parade below, on black."""
    waveform, waveform_texts = _waveform_panel(luma(frame), WAVEFORM_COLUMNS, "Y'")
    vector, vector_texts = _vector_panel(frame)
    parade = [_waveform_panel(frame[..., c], PARADE_COLUMNS, name) for c, name in enumerate(("R'", "G'", "B'"))]
    top = np.concatenate([waveform, vector], axis=1)
    bottom = np.concatenate([panel for panel, _ in parade], axis=1)
    image = np.ascontiguousarray(np.concatenate([top, bottom], axis=0))
    texts = waveform_texts + [((x + WAVEFORM_COLUMNS, y), t, c) for (x, y), t, c in vector_texts]
    for k, (_, panel_texts) in enumerate(parade):
        texts += [((x + k * PARADE_COLUMNS, y + ROWS), t, c) for (x, y), t, c in panel_texts]
    # Panel edges.
    image[ROWS, :] = TEXT
    image[:ROWS, WAVEFORM_COLUMNS] = TEXT
    for k in (1, 2):
        image[ROWS:, k * PARADE_COLUMNS] = TEXT
    draw_text(image, texts)
    return image


def scope(
    project: Project, key: str, at: str, *, height: int | None = None, threads: int | None = None,
    out: Path | None = None,
) -> dict[str, Any]:
    """Write the scope image of one frame of media `key` and return what `lungecuta scope` prints."""
    clip = open_clip(project, load_record(project, key))
    index = source_frame(at, rate=clip.rate, frames=clip.video.frames)
    size = scaled_size(clip.shown, height) if height is not None else None
    frame = clip.show(clip.read(index), size, threads)
    shown = (frame.shape[1], frame.shape[0])
    if out is None:
        out = project.directory / SCOPE_DIR / f"{key}_f{index}_{shown[0]}x{shown[1]}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, png8(scope_image(frame)))
    settings = project.file.color
    return {
        "png": str(out),
        "media": key,
        "frame": index,
        "size": list(shown),
        "rotation": clip.video.rotation,
        "space": clip.decision.space,
        "display": settings.display,
        "view": settings.view,
        **scope_numbers(frame),
    }
