"""`lungecuta scope --media`: the scope numbers on frames whose answers are known, the scope images'
geometry, and the command on a synthetic clip (tests/clips.py)."""
import json

import numpy as np
import pytest
from click.testing import CliRunner

from lungecuta.cli import main
from lungecuta.media import Project
from lungecuta.project import init_project
from lungecuta.scope import (
    PARADE_COLUMNS,
    ROWS,
    VECTOR_SIZE,
    WAVEFORM_COLUMNS,
    bar_targets,
    scope_image,
    scope_numbers,
    vector_position,
    vectorscope_counts,
    waveform_counts,
)
from lungecuta.still import render

from clips import make_clip, stored, write_record


def test_scope_numbers_on_red_and_grey():
    # A quarter of the frame 100 % red, the rest grey at 0.5.
    frame = np.full((8, 8, 3), 0.5, np.float32)
    frame[:2] = (1, 0, 0)
    numbers = scope_numbers(frame)
    red_luma = round(float(np.float32(1) * np.float32(0.2126)) * 1023, 4)
    assert numbers["luma"] == {"p1": red_luma, "p50": 511.5, "p99": 511.5}
    # Red has one channel at 1 and two at 0: it counts as clipped both ways.
    assert numbers["clipped"] == {"any": 0.25, "low": 0.25, "high": 0.25}
    # BT.709: red is at Cb = -0.2126 / 1.8556, Cr = 0.7874 / 1.5748 = 0.5; grey is at 0.
    red_chroma = np.hypot(-0.2126 / 1.8556, 0.5) * 1023
    assert numbers["chroma"]["p50"] == 0
    assert numbers["chroma"]["p99"] == pytest.approx(red_chroma, abs=1e-3)
    assert numbers["codes"]["G"]["max"] == 511.5 and numbers["codes"]["R"]["max"] == 1023


def test_nothing_is_clipped_strictly_inside_the_range():
    numbers = scope_numbers(np.full((4, 4, 3), 0.5, np.float32))
    assert numbers["clipped"] == {"any": 0, "low": 0, "high": 0}


def test_the_waveform_puts_code_1023_on_top_and_follows_the_columns():
    ramp = np.tile(np.linspace(0, 1, 2048, dtype=np.float32), (3, 1))
    counts = waveform_counts(ramp, 1024)
    assert counts.shape == (ROWS, 1024) and counts.sum() == ramp.size
    # Each band holds two frame columns; the lowest band is at code 0 (bottom row).
    assert counts[ROWS - 1, 0] == 6 and counts[0, 1023] > 0
    top_rows = counts.argmax(axis=0)
    assert np.all(np.diff(top_rows) <= 0), "brighter columns land higher"
    # A frame narrower than the panel is widened, band by band.
    narrow = waveform_counts(np.array([[0.0, 1.0]], np.float32), 4)
    assert narrow[ROWS - 1].tolist() == [1, 1, 0, 0] and narrow[0].tolist() == [0, 0, 1, 1]
    # Out of range lands on the edges.
    edges = waveform_counts(np.array([[-0.2, 1.3]], np.float32), 2)
    assert edges[ROWS - 1, 0] == 1 and edges[0, 1] == 1


def test_the_vectorscope_has_red_up_and_blue_to_the_right():
    full = bar_targets(1.0)
    assert full["R"][1] == 0, "100 % red has Cr = 0.5, the top row"
    assert full["B"][0] == VECTOR_SIZE - 1, "100 % blue has Cb = 0.5, the right edge"
    assert full["Yl"][0] == 0 and full["Cy"][1] == VECTOR_SIZE - 1
    assert vector_position(0.0, 0.0) == (VECTOR_SIZE // 2, VECTOR_SIZE // 2)
    for name, rgb in (("R", (0.75, 0, 0)), ("Cy", (0, 0.75, 0.75))):
        counts = vectorscope_counts(np.full((3, 3, 3), rgb, np.float32))
        row, column = np.unravel_index(counts.argmax(), counts.shape)
        assert (column, row) == bar_targets(0.75)[name]
        assert counts.sum() == 9


def test_the_scope_image_is_waveform_and_vectorscope_over_the_parade():
    image = scope_image(np.random.default_rng(0).random((90, 160, 3), dtype=np.float32))
    assert image.shape == (2 * ROWS, WAVEFORM_COLUMNS + VECTOR_SIZE, 3) == (2 * ROWS, 3 * PARADE_COLUMNS, 3)
    assert image.dtype == np.uint8


@pytest.fixture
def project(tmp_path):
    directory = init_project(tmp_path / "edit.lungecuta")
    (directory / "footage").mkdir()
    return directory


def test_scope_prints_the_numbers_of_the_still(project):
    key = write_record(project, make_clip(project / "footage" / "C0001.mov"))
    result = CliRunner().invoke(main, ["scope", "--media", key, "--at", "f:2", "--height", "36",
                                       "--project", str(project)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    still = render(Project.load(project), stored(project, key), 2, height=36)
    expected = scope_numbers(still)
    assert {name: printed[name] for name in expected} == expected
    assert (printed["frame"], printed["size"]) == (2, [64, 36])
    assert printed["png"] == str(project / "cache" / "lungecuta" / "scope" / f"{key}_f2_64x36.png")
