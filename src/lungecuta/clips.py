"""`lungecuta clips`: the imported footage at a glance, to choose material from
(docs/design/09-agent-interface.md). One tile a clip: its middle frame through the clip's
input transform and the project's output view, as `lungecuta still --media` shows it,
fitted into the tile, labelled with its key, length, the time it was recorded and whether
it has sound. A clip whose colour space is undecided is a black tile that says so; it is
never shown through a guessed transform."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .files import write_atomic
from .images import draw_text, png8, quantize
from .media import Project, load_record
from .parallel import in_order
from .proxy import PARITY_READS
from .schema.media import ColorDecided, MediaRecord
from .sheet import COLUMNS, LABEL_COLOUR, LABEL_PAD, SHEET_DIR, page_paths, pages
from .still import open_clip
from .ticks import parse_rate

TILE = (320, 180)  # a 16:9 tile 320 wide, the sheet's long side; other shapes are fitted inside
ROWS = 5


def footage(project: Project) -> list[MediaRecord]:
    """Every imported clip with a picture, in the order it was recorded (by key where a file
    does not say when)."""
    records = [load_record(project, path.stem) for path in sorted((project.directory / "media").glob("*.json"))]
    return sorted((r for r in records if r.probe.video is not None),
                  key=lambda r: (r.probe.creation_time or "", r.key))


def fitted_size(shown: tuple[int, int], box: tuple[int, int]) -> tuple[int, int]:
    """The largest size of `shown`'s shape inside `box`, rounded to whole pixels (a
    thumbnail), and never larger than the frame itself."""
    scale = min(1.0, box[0] / shown[0], box[1] / shown[1])
    return max(1, round(shown[0] * scale)), max(1, round(shown[1] * scale))


def tile(project: Project, record: MediaRecord, box: tuple[int, int]) -> np.ndarray:
    """The clip's middle frame fitted into `box` on black, display-encoded, 8-bit; black for
    a clip whose colour space is undecided."""
    out = np.zeros((box[1], box[0], 3), np.uint8)
    if not isinstance(record.color, ColorDecided):
        draw_text(out, [((LABEL_PAD, LABEL_PAD), "colour undecided", LABEL_COLOUR)])
        return out
    clip = open_clip(project, record)
    size = fitted_size(clip.shown, box)
    frame = quantize(clip.show(clip.read(clip.video.frames // 2), size, threads=1))
    x, y = (box[0] - size[0]) // 2, (box[1] - size[1]) // 2
    out[y:y + size[1], x:x + size[0]] = frame
    return out


def describe(record: MediaRecord) -> dict[str, Any]:
    video = record.probe.video
    recorded = record.probe.creation_time
    usable = isinstance(record.color, ColorDecided) and record.time.status == "decided"
    return {"key": record.key, "seconds": round(float(video.frames / parse_rate(video.rate)), 3),
            "recorded": recorded, "sound": bool(record.probe.audio), "usable": usable,
            "colour": record.color.space if isinstance(record.color, ColorDecided) else f"undecided: {record.color.reason}",
            "time": record.time.status if record.time.status == "decided" else f"undecided: {record.time.reason}"}


def label(described: dict[str, Any]) -> str:
    clock = ""
    if described["recorded"]:
        clock = datetime.fromisoformat(described["recorded"].replace("Z", "+00:00")).astimezone().strftime("  %H:%M")
    flags = ("  sound" if described["sound"] else "") + ("" if described["usable"] else "  UNDECIDED")
    return f"{described['key']}\n{described['seconds']:.1f}s{clock}{flags}"


def overview(project: Project, keys: list[str] | None = None, *, columns: int = COLUMNS, rows: int = ROWS,
             out: Path | None = None) -> dict[str, Any]:
    """Write the pages of the clips' sheet and return what `lungecuta clips` prints."""
    records = footage(project) if not keys else [load_record(project, key) for key in keys]
    assert records, "no footage is imported in this project"
    assert all(r.probe.video is not None for r in records), "only clips with a picture have a tile"
    described = [describe(record) for record in records]
    # several at once, as many as parity reads: each is an ffmpeg seek (bench/RESULTS.md)
    tiles = list(in_order(tile, ((project, record, TILE) for record in records), workers=PARITY_READS,
                          ahead=2 * PARITY_READS))
    images = pages(tiles, [label(d) for d in described], columns=columns, rows=rows)
    if out is None:
        out = project.directory / SHEET_DIR / "clips.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    paths = page_paths(out, len(images))
    for path, image in zip(paths, images):
        write_atomic(path, png8(image))
    per_page = columns * rows
    settings = project.file.color
    return {"pages": [str(path) for path in paths], "columns": columns, "rows": rows, "tile": list(TILE),
            "display": settings.display, "view": settings.view,
            "clips": [d | {"page": k // per_page} for k, d in enumerate(described)]}
