"""`lungecuta sheet --media`: a contact sheet of one clip, frames at an interval through the same
chain as `lungecuta still`, each labelled with its frame number and seconds
(docs/design/09-agent-interface.md)."""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from .decode import read_every
from .files import write_atomic
from .geometry import scaled_size
from .images import draw_text, font, png8, quantize, text_height
from .literals import Field, parse_literal, resolve_source
from .media import Project, load_record
from .still import code_numbers, open_clip
from .ticks import format_rate, ticks_per_frame

SHEET_DIR = Path("cache") / "lungecuta" / "sheet"
# Layout defaults, not measurements: six columns as in a past project's review sheet, six
# rows a page, and tiles 320 pixels on their long side (320x180 for 16:9).
COLUMNS = 6
ROWS = 6
TILE_LONG_SIDE = 320
LABEL_PAD = 3
LABEL_COLOUR = (255, 255, 0)
LINE_GAP = 4  # pixels between label lines: Pillow's own spacing for multiline text


def source_step(text: str, *, rate: Fraction) -> int:
    """An interval in source time as a whole number of frames: `f:N`, or seconds that are a
    whole number of frames."""
    ticks = resolve_source(parse_literal(text, Field.SOURCE), media_rate=rate)
    step, between = divmod(ticks, ticks_per_frame(rate))
    assert not between, f"{text} is not a whole number of frames at {format_rate(rate)}; give the interval as f:N"
    assert step >= 1, f"an interval of {text} is not at least one frame"
    return step


def tile_size(shown: tuple[int, int], height: int | None) -> tuple[int, int]:
    """`height` lines with the frame's shape kept, or by default 320 pixels on the long side
    (a smaller frame keeps its own size)."""
    if height is not None:
        return scaled_size(shown, height)
    width, own_height = shown
    if max(shown) <= TILE_LONG_SIDE:
        return shown
    if width < own_height:
        return scaled_size(shown, TILE_LONG_SIDE)
    lines = Fraction(TILE_LONG_SIDE * own_height, width)
    assert lines.denominator == 1, (
        f"a {width}x{own_height} frame {TILE_LONG_SIDE} wide is {float(lines):.2f} lines, not a whole number; "
        "give the tile height"
    )
    return TILE_LONG_SIDE, int(lines)


def label(index: int, rate: Fraction) -> str:
    """Frame number and seconds from the clip's first frame, to the millisecond."""
    return f"f:{index}  {float(index / rate):.3f}s"


def fitted(text: str, width: int) -> str:
    """Each line of `text` cut to `width` pixels, ending in an ellipsis where it was cut."""
    lines = []
    for line in text.split("\n"):
        if font().getlength(line) > width:
            while line and font().getlength(line + "...") > width:
                line = line[:-1]
            line += "..."
        lines.append(line)
    return "\n".join(lines)


def pages(tiles: list[np.ndarray], labels: list[str], *, columns: int, rows: int) -> list[np.ndarray]:
    """Tiles left to right and top to bottom, `columns` by `rows` a page, each with its label
    (one line or several) in a strip below it, on black."""
    height, width, _ = tiles[0].shape
    lines = max(label.count("\n") + 1 for label in labels)
    strip = lines * text_height() + (lines - 1) * LINE_GAP + 2 * LABEL_PAD
    labels = [fitted(label, width - 2 * LABEL_PAD) for label in labels]
    per_page = columns * rows
    out = []
    for first in range(0, len(tiles), per_page):
        chunk = tiles[first:first + per_page]
        used_rows = -(-len(chunk) // columns)
        page = np.zeros((used_rows * (height + strip), columns * width, 3), np.uint8)
        texts = []
        for k, tile in enumerate(chunk):
            x, y = (k % columns) * width, (k // columns) * (height + strip)
            page[y:y + height, x:x + width] = tile
            texts.append(((x + LABEL_PAD, y + height + LABEL_PAD), labels[first + k], LABEL_COLOUR))
        draw_text(page, texts)
        out.append(page)
    return out


def page_paths(base: Path, count: int) -> list[Path]:
    """`base` for the first page, then `_p1`, `_p2`, ... before the suffix."""
    return [base if k == 0 else base.with_name(f"{base.stem}_p{k}{base.suffix}") for k in range(count)]


def sheet(
    project: Project, key: str, every: str, *, columns: int = COLUMNS, rows: int = ROWS, height: int | None = None,
    threads: int | None = None, out: Path | None = None,
) -> dict[str, Any]:
    """Write the pages of the contact sheet of media `key` and return what `lungecuta sheet` prints."""
    assert columns >= 1 and rows >= 1, "a page needs at least one column and one row"
    clip = open_clip(project, load_record(project, key))
    step = source_step(every, rate=clip.rate)
    indices = list(range(0, clip.video.frames, step))
    size = tile_size(clip.shown, height)
    decoded = read_every(clip.path, step, len(indices), size=clip.video.size, pix_fmt=clip.video.pix_fmt,
                         matrix=clip.decision.matrix, range_=clip.decision.range,
                         chroma_location=clip.video.chroma_location)
    tiles, numbers = [], []
    per_page = columns * rows
    for k, (index, coded) in enumerate(zip(indices, decoded, strict=True)):
        tile = clip.show(coded, size, threads)
        tiles.append(quantize(tile))
        numbers.append({
            "frame": index, "seconds": round(float(index / clip.rate), 6), "page": k // per_page,
            "codes": code_numbers(tile),
        })
    images = pages(tiles, [label(index, clip.rate) for index in indices], columns=columns, rows=rows)
    if out is None:
        out = project.directory / SHEET_DIR / f"{key}_every{step}f_{size[0]}x{size[1]}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    paths = page_paths(out, len(images))
    for path, image in zip(paths, images):
        write_atomic(path, png8(image))
    settings = project.file.color
    return {
        "pages": [str(path) for path in paths],
        "media": key,
        "every": step,
        "size": list(size),
        "columns": columns,
        "rows": rows,
        "rotation": clip.video.rotation,
        "space": clip.decision.space,
        "display": settings.display,
        "view": settings.view,
        "tiles": numbers,
    }
