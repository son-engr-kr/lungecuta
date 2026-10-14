"""lungecuta clips: one tile a clip, to choose material from (src/lungecuta/clips.py)."""
import json

import numpy as np
from click.testing import CliRunner
from PIL import Image

from lungecuta.cli import main
from lungecuta.clips import TILE
from lungecuta.media import Project, load_record
from lungecuta.images import quantize
from lungecuta.still import render

from clips import clip_with_sound, make_clip, write_record


def test_every_clip_has_a_tile_through_its_colour_chain_and_undecided_ones_say_so(cut_project):
    write_record(cut_project, clip_with_sound(cut_project / "footage" / "C0002.mov"))
    write_record(cut_project, make_clip(cut_project / "footage" / "C0003.mov", frames=5), decided=False)
    write_record(cut_project, make_clip(cut_project / "footage" / "C0004.mov", rotation=90, frames=5))
    result = CliRunner().invoke(main, ["clips", "--columns", "2", "--project", str(cut_project)], catch_exceptions=False)
    printed = json.loads(result.output)
    assert sorted(c["key"] for c in printed["clips"]) == ["C0001", "C0002", "C0003", "C0004"]
    by_key = {c["key"]: c for c in printed["clips"]}
    assert by_key["C0002"]["sound"] and not by_key["C0001"]["sound"]
    assert not by_key["C0003"]["usable"] and by_key["C0003"]["colour"].startswith("undecided")
    assert by_key["C0001"]["seconds"] == round(70 * 1001 / 30000, 3)
    page = np.asarray(Image.open(printed["pages"][0]).convert("RGB"))
    k = [c["key"] for c in printed["clips"]].index("C0001")
    strip = (page.shape[0] // 2) - TILE[1]  # two rows of tiles, each with its label strip
    x, y = (k % 2) * TILE[0], (k // 2) * (TILE[1] + strip)
    # the 128x72 clip, smaller than a tile, sits at its own size in the middle of it: its
    # middle frame as lungecuta still shows it
    shown = quantize(render(Project.load(cut_project), load_record(Project.load(cut_project), "C0001"), 35))
    ox, oy = x + (TILE[0] - 128) // 2, y + (TILE[1] - 72) // 2
    assert np.array_equal(page[oy:oy + 72, ox:ox + 128], shown)
    undecided = [c["key"] for c in printed["clips"]].index("C0003")
    ux, uy = (undecided % 2) * TILE[0], (undecided // 2) * (TILE[1] + strip)
    assert page[uy + 30:uy + TILE[1], ux:ux + TILE[0]].max() == 0  # black under its note, never guessed
    rotated = [c["key"] for c in printed["clips"]].index("C0004")
    rx, ry = (rotated % 2) * TILE[0], (rotated // 2) * (TILE[1] + strip)
    columns_lit = np.flatnonzero(page[ry:ry + TILE[1], rx:rx + TILE[0]].max(axis=(0, 2)))
    assert columns_lit.size and columns_lit.size < TILE[0] / 2  # shown upright, fitted inside the tile
