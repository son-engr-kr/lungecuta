"""Looking at a comp: lungecuta still, sheet and scope with a comp (tests/clips.py's `cut_project`)."""
import json

import numpy as np
import pytest
from click.testing import CliRunner
from PIL import Image

from lungecuta.cli import main
from lungecuta.comp import load_comp, resolve
from lungecuta.compose import frame_at
from lungecuta.images import quantize
from lungecuta.look import sheet_frames
from lungecuta.media import Project
from lungecuta.scope import scope_numbers
from lungecuta.still import code_numbers


def run(*args):
    result = CliRunner().invoke(main, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_a_comp_still_is_the_frame_the_render_delivers(cut_project):
    printed = run("still", "main", "--at", "b:4", "--project", str(cut_project))
    # b:4 is at 2.25 s, between frames 67 and 68 at 29.97: it lands on frame 68, inside shot2
    assert printed["frame"] == 68 and printed["layers"] == ["shot2"]
    assert printed["beat"] == pytest.approx((68 * 1001 / 30000 - 0.25) / 0.5, abs=1e-4)
    project = Project.load(cut_project)
    frame = frame_at(project, resolve(project, load_comp(project, "main")), 68)
    assert np.array_equal(np.asarray(Image.open(printed["png"])), quantize(frame))
    assert printed["codes"] == code_numbers(frame) and printed["size"] == [128, 72]


def test_a_smaller_still_keeps_the_full_frames_numbers(cut_project):
    full = run("still", "main", "--at", "f:30", "--project", str(cut_project))
    small = run("still", "main", "--at", "f:30", "--height", "36", "--project", str(cut_project))
    assert small["size"] == [64, 36] and Image.open(small["png"]).size == (64, 36)
    assert small["codes"] == full["codes"]
    before = run("still", "main", "--at", "f:3", "--project", str(cut_project))
    assert before["layers"] == [] and before["codes"]["G"]["max"] == 0  # before shot1: black


def test_a_still_names_a_comp_or_a_clip(cut_project):
    for args in (["still", "--at", "f:0"], ["still", "main", "--media", "C0001", "--at", "f:0"]):
        result = CliRunner().invoke(main, [*args, "--project", str(cut_project)])
        assert result.exit_code != 0 and "not both and not neither" in str(result.exception)


def test_a_beat_sheet_has_a_frame_on_every_beat(cut_project):
    project = Project.load(cut_project)
    resolved = resolve(project, load_comp(project, "main"))
    beats = sheet_frames(resolved, "b:1", None, None)
    # beats at 0.25 + 0.5 k s up to the comp's end at b:12, each on the first frame at or after it
    expected = [-(-(0.25 + 0.5 * k) * 30000 // 1001) for k in range(12)]
    assert beats == [int(f) for f in expected]
    assert sheet_frames(resolved, "b:2", "b:3", "b:8") == [int(f) for f in expected[3:8:2]]
    assert sheet_frames(resolved, "f:60", None, None) == [0, 60, 120, 180]  # the comp ends at frame 188


def test_a_comp_sheet_labels_each_tile_with_its_place(cut_project):
    printed = run("sheet", "main", "--every", "b:2", "--columns", "3", "--rows", "1", "--project", str(cut_project))
    assert [tile["frame"] for tile in printed["tiles"]] == [8, 38, 68, 98, 128, 158]
    assert [tile["page"] for tile in printed["tiles"]] == [0, 0, 0, 1, 1, 1] and len(printed["pages"]) == 2
    assert printed["tiles"][0]["layers"] == ["shot1"] and printed["tiles"][2]["layers"] == ["shot2"]
    assert printed["tiles"][-1]["layers"] == []  # after shot2's out point at b:8
    page = Image.open(printed["pages"][0])
    assert page.width == 3 * 128 and page.height > 72  # tiles at the comp's own size, with a label strip


def test_a_comp_scope_reads_the_delivered_frame(cut_project):
    printed = run("scope", "main", "--at", "b:1", "--project", str(cut_project))
    project = Project.load(cut_project)
    frame = frame_at(project, resolve(project, load_comp(project, "main")), printed["frame"])
    numbers = scope_numbers(frame)
    assert printed["luma"] == numbers["luma"] and printed["layers"] == ["shot1"]
    assert Image.open(printed["png"]).size[0] > 0
