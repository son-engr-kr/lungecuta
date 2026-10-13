"""`lc sheet --media` on small synthetic clips (tests/clips.py); the samples are in test_samples.py."""
import json
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from lungecuta.cli import main
from lungecuta.decode import read_every, read_rgb
from lungecuta.images import quantize, text_height
from lungecuta.media import Project
from lungecuta.project import init_project
from lungecuta.sheet import LABEL_PAD, label, page_paths, pages, source_step, tile_size
from lungecuta.still import code_numbers, render
from lungecuta.tools import run

from clips import SIZE, make_clip, stored, write_record

NTSC = Fraction(30000, 1001)


@pytest.fixture
def project(tmp_path):
    directory = init_project(tmp_path / "edit.lc")
    (directory / "footage").mkdir()
    return directory


def test_an_interval_is_a_whole_number_of_frames():
    assert source_step("f:30", rate=NTSC) == 30
    assert source_step("1.001s", rate=NTSC) == 30
    assert source_step("0.5s", rate=Fraction(60)) == 30
    with pytest.raises(AssertionError, match="not a whole number of frames"):
        source_step("0.5s", rate=NTSC)
    with pytest.raises(AssertionError, match="not at least one frame"):
        source_step("f:0", rate=NTSC)


def test_tiles_are_320_on_the_long_side_unless_told():
    assert tile_size((3840, 2160), None) == (320, 180)
    assert tile_size((1920, 1080), None) == (320, 180)
    assert tile_size((2160, 3840), None) == (180, 320)
    assert tile_size((128, 72), None) == (128, 72)  # never enlarged
    assert tile_size((3840, 2160), 90) == (160, 90)
    with pytest.raises(AssertionError, match="not a whole number"):
        tile_size((2940, 1912), None)


def test_labels_and_page_names():
    assert label(30, NTSC) == "f:30  1.001s"
    assert label(1, NTSC) == "f:1  0.033s"
    base = Path("sheets/C0001.png")
    assert page_paths(base, 3) == [base, Path("sheets/C0001_p1.png"), Path("sheets/C0001_p2.png")]


def test_tiles_fill_pages_left_to_right_with_a_label_strip_below():
    tiles = [np.full((4, 6, 3), 10 * (k + 1), np.uint8) for k in range(7)]
    laid = pages(tiles, [f"f:{k}" for k in range(7)], columns=3, rows=2)
    strip = text_height() + 2 * LABEL_PAD
    assert [page.shape for page in laid] == [(2 * (4 + strip), 18, 3), (4 + strip, 18, 3)]
    first, second = laid
    assert np.all(first[0:4, 6:12] == 20) and np.all(first[4 + strip:8 + strip, 0:6] == 40)
    assert np.all(second[0:4, 0:6] == 70) and not second[0:4, 6:].any()
    assert first[4:4 + strip].any(), "the label strip holds text"


def test_frames_from_one_decode_are_the_seeked_frames(project):
    clip = make_clip(project / "footage" / "C0001.mov", frames=10)
    sequential = list(read_every(clip, 3, 4, size=SIZE, pix_fmt="yuv422p10le", matrix="bt709", range_="limited",
                         chroma_location="left"))
    for index, frame in zip((0, 3, 6, 9), sequential, strict=True):
        assert np.array_equal(frame, read_rgb(clip, index, rate=NTSC, size=SIZE, pix_fmt="yuv422p10le", matrix="bt709", range_="limited",
                         chroma_location="left"))
    with pytest.raises(AssertionError, match="4 of 5 frames arrived"):
        list(read_every(clip, 3, 5, size=SIZE, pix_fmt="yuv422p10le", matrix="bt709", range_="limited",
                         chroma_location="left"))


def test_lc_sheet_tiles_are_the_stills_at_the_tile_size(project):
    key = write_record(project, make_clip(project / "footage" / "C0001.mov", frames=7))
    result = CliRunner().invoke(main, ["sheet", "--media", key, "--every", "f:2", "--height", "36", "--columns", "3",
                                       "--rows", "1", "--project", str(project)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert [tile["frame"] for tile in printed["tiles"]] == [0, 2, 4, 6]
    assert [tile["page"] for tile in printed["tiles"]] == [0, 0, 0, 1]
    assert printed["size"] == [64, 36] and printed["every"] == 2
    first = project / "cache" / "lc" / "sheet" / f"{key}_every2f_64x36.png"
    assert printed["pages"] == [str(first), str(first.with_name(f"{first.stem}_p1.png"))]
    record = stored(project, key)
    page = decode_png(first)
    for k, tile in enumerate(printed["tiles"][:3]):
        still = render(Project.load(project), record, tile["frame"], height=36)
        assert tile["codes"] == code_numbers(still)
        assert np.array_equal(page[:36, 64 * k:64 * (k + 1)], quantize(still))


def test_a_rotated_clip_gets_upright_tiles(project):
    key = write_record(project, make_clip(project / "footage" / "R90.mov", 90, frames=3))
    result = CliRunner().invoke(main, ["sheet", "--media", key, "--every", "f:1", "--project", str(project)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert (printed["size"], printed["rotation"]) == ([72, 128], 90)
    still = render(Project.load(project), stored(project, key), 1)
    assert printed["tiles"][1]["codes"] == code_numbers(still)


def decode_png(path: Path) -> np.ndarray:
    probe = json.loads(run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "json",
                            str(path)]).stdout)["streams"][0]
    raw = run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout
    return np.frombuffer(raw, np.uint8).reshape(probe["height"], probe["width"], 3)
