"""A card of known colour patches, and reading a frame's codes back at those patches
(Phase 0 task 7): does Resolve send a Rec.709 graphic in a DWG/DI timeline through the
output tone mapping? The card is rendered by the graphics renderer to ProRes 4444 with
alpha; the person puts it over footage in Resolve and exports, and `lc patches` reads the
export at the patches.

The patch values are a test pattern, not measurements: greys in steps of 32 codes with
16 and 235 (video black and white in 8-bit limited range), the six colour bars at 100 %
and 75 %, and four mixed colours. Each patch is opaque, so straight or premultiplied alpha
does not change its codes; the space between patches is transparent.
"""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from .decode import read_rgb
from .detect import RANGES
from .probe import probe
from .ticks import parse_rate

GREYS = [0, 16, 32, 64, 96, 128, 160, 192, 224, 235, 255]
BARS = {"R": (1, 0, 0), "Yl": (1, 1, 0), "G": (0, 1, 0), "Cy": (0, 1, 1), "B": (0, 0, 1), "Mg": (1, 0, 1)}
MIXED = [(230, 150, 120), (100, 150, 220), (60, 120, 60), (200, 80, 160)]
COLUMNS = 9
MARGIN = Fraction(1, 20)  # of the card's width and height, around the grid
GUTTER = Fraction(1, 4)  # of a cell, between patches
INTERIOR = Fraction(1, 2)  # the middle of a patch that is measured, away from its edges
FILE_MATRIX = {"bt709": "bt709"}


def patch_list() -> list[dict[str, Any]]:
    patches = [{"name": f"grey {v}", "rgb": [v, v, v]} for v in GREYS]
    for level, code in ((100, 255), (75, 191)):
        patches += [{"name": f"{name} {level}%", "rgb": [code * c for c in rgb]} for name, rgb in BARS.items()]
    patches += [{"name": f"mixed {i + 1}", "rgb": list(rgb)} for i, rgb in enumerate(MIXED)]
    return patches


def card(size: tuple[int, int]) -> dict[str, Any]:
    """The card: its size and every patch with its 8-bit sRGB code and its rectangle as
    fractions of the frame (left, top, right, bottom)."""
    patches = patch_list()
    rows = -(-len(patches) // COLUMNS)
    cell_w = (1 - 2 * MARGIN) / COLUMNS
    cell_h = (1 - 2 * MARGIN) / rows
    for k, patch in enumerate(patches):
        row, column = divmod(k, COLUMNS)
        left = MARGIN + column * cell_w + cell_w * GUTTER / 2
        top = MARGIN + row * cell_h + cell_h * GUTTER / 2
        patch["rect"] = [float(left), float(top), float(left + cell_w * (1 - GUTTER)), float(top + cell_h * (1 - GUTTER))]
    return {"size": list(size), "patches": patches}


def pixel_rect(rect: list[float], size: tuple[int, int], portion: Fraction = Fraction(1)) -> tuple[int, int, int, int]:
    """A patch's pixels (x0, y0, x1, y1), the middle `portion` of it in each direction."""
    width, height = size
    left, top, right, bottom = rect
    shrink_x = (right - left) * float(1 - portion) / 2
    shrink_y = (bottom - top) * float(1 - portion) / 2
    return (round((left + shrink_x) * width), round((top + shrink_y) * height),
            round((right - shrink_x) * width), round((bottom - shrink_y) * height))


def card_html(spec: dict[str, Any]) -> str:
    """A page that draws the card with the renderer's contract (`ready`, `renderAt`)."""
    width, height = spec["size"]
    rects = []
    for patch in spec["patches"]:
        x0, y0, x1, y1 = pixel_rect(patch["rect"], (width, height))
        rects.append({"rgb": patch["rgb"], "box": [x0, y0, x1 - x0, y1 - y0]})
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>html,body{{margin:0;background:transparent}}canvas{{display:block}}</style></head>
<body><canvas id="card" width="{width}" height="{height}"></canvas>
<script>
const PATCHES = {json.dumps(rects)};
const ctx = document.getElementById("card").getContext("2d");
window.renderAt = (t) => {{
  ctx.clearRect(0, 0, {width}, {height});
  for (const p of PATCHES) {{
    ctx.fillStyle = `rgb(${{p.rgb[0]}}, ${{p.rgb[1]}}, ${{p.rgb[2]}})`;
    ctx.fillRect(p.box[0], p.box[1], p.box[2], p.box[3]);
  }}
}};
window.ready = true;
</script></body></html>
"""


def read_patches(file: Path, spec: dict[str, Any], *, index: int, matrix: str | None, range_: str | None,
                 chroma_location: str | None = None) -> dict[str, Any]:
    """The mean code of every patch's interior in frame `index` of `file`, against the
    patch's own code, in 8-bit units (and the standard deviation, which a smooth patch
    keeps small). The matrix and range come from the file's tags unless given."""
    probed = probe(file)
    video = probed.video
    assert video is not None, f"{file.name} has no video"
    tags = video.tags
    if matrix is None:
        assert tags.color_space in FILE_MATRIX, (
            f"{file.name} is tagged matrix {tags.color_space!r}; give the matrix it was written with"
        )
        matrix = FILE_MATRIX[tags.color_space]
    if range_ is None:
        assert tags.color_range in RANGES, f"{file.name} is tagged range {tags.color_range!r}; give its range"
        range_ = RANGES[tags.color_range]
    width, height = video.size
    frame = read_rgb(file, index, rate=parse_rate(video.rate), size=video.size, pix_fmt=video.pix_fmt,
                     matrix=matrix, range_=range_, chroma_location=chroma_location or video.chroma_location)
    results = []
    for patch in spec["patches"]:
        x0, y0, x1, y1 = pixel_rect(patch["rect"], (width, height), INTERIOR)
        pixels = frame[y0:y1, x0:x1].reshape(-1, 3).astype(np.float64) * 255
        mean = pixels.mean(axis=0)
        results.append({
            "name": patch["name"],
            "expected": patch["rgb"],
            "measured": [round(float(v), 3) for v in mean],
            "difference": [round(float(m - e), 3) for m, e in zip(mean, patch["rgb"])],
            "spread": round(float(pixels.std(axis=0).max()), 3),
        })
    worst = max(abs(d) for result in results for d in result["difference"])
    return {
        "file": str(file), "frame": index, "size": [width, height], "matrix": matrix, "range": range_,
        "largest_difference": round(worst, 3), "patches": results,
    }


def write_card(directory: Path, *, size: tuple[int, int], rate: Fraction, frames: int) -> dict[str, Any]:
    """The card's spec, page and ProRes 4444 file in `directory`, which must not hold one yet."""
    from .graphics import render

    directory.mkdir(parents=True, exist_ok=True)
    spec = card(size)
    paths = {name: directory / name for name in ("card.json", "card.html", "card.mov")}
    for path in paths.values():
        assert not path.exists(), f"{path} exists already"
    paths["card.json"].write_text(json.dumps(spec, indent=2) + "\n")
    paths["card.html"].write_text(card_html(spec))
    rendered = render(paths["card.html"], paths["card.mov"], size=size, rate=rate, frames=frames)
    check = read_patches(paths["card.mov"], spec, index=0, matrix=None, range_=None)
    assert check["largest_difference"] < 0.5, (
        f"the card's own file reads back {check['largest_difference']} codes off; it would not tell Resolve's change apart"
    )
    return {"card": str(paths["card.json"]), "page": str(paths["card.html"]), **rendered,
            "patches": len(spec["patches"]), "own_largest_difference": check["largest_difference"]}
