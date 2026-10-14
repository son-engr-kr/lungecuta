"""`lungecuta still --media` on small synthetic clips made with ffmpeg; the samples are in test_samples.py."""
import json
import os
import struct
from fractions import Fraction
from pathlib import Path

import numpy as np
import PyOpenColorIO as ocio
import pytest
from click.testing import CliRunner

from lungecuta.cli import main
from lungecuta.decode import frame_rgb, read_rgb
from lungecuta.geometry import area_resize, oriented, turns
from lungecuta.media import Project
from lungecuta.probe import probe
from lungecuta.project import DEFAULT_COLOR, init_project
from lungecuta.images import png
from lungecuta.still import code_numbers, render, source_frame
from lungecuta.tools import run

from clips import SIZE, SPACE, make_clip, stored, write_record

@pytest.fixture
def project(tmp_path):
    directory = init_project(tmp_path / "edit.lungecuta")
    (directory / "footage").mkdir()
    return directory


def sequential(path: Path, index: int, size: tuple[int, int], *, autorotate: bool = False) -> np.ndarray:
    """Frame `index` from a decode from the start, independent of lungecuta's seeking."""
    raw = run(["ffmpeg", "-v", "error", *([] if autorotate else ["-noautorotate"]), "-i", str(path),
               "-map", "0:v:0", "-vf", f"select=eq(n\\,{index})", "-frames:v", "1", "-f", "rawvideo",
               "-pix_fmt", "yuv422p10le", "-"]).stdout
    return frame_rgb(raw, size, pix_fmt="yuv422p10le", chroma_location="left", matrix="bt709", range_="limited")


def reference(frame: np.ndarray, rotation: int = 0, height: int | None = None) -> np.ndarray:
    """The still's pipeline built from the OCIO config directly, on the whole frame on one thread."""
    config = ocio.Config.CreateFromBuiltinConfig(DEFAULT_COLOR.config)
    config.getProcessor(SPACE, DEFAULT_COLOR.working).getDefaultCPUProcessor().applyRGB(frame)
    frame = np.ascontiguousarray(np.rot90(frame, turns(rotation)))
    if height is not None:
        frame = area_resize(frame, (frame.shape[1] * height // frame.shape[0], height))
    view = ocio.DisplayViewTransform()
    view.setSrc(DEFAULT_COLOR.working)
    view.setDisplay(DEFAULT_COLOR.display)
    view.setView(DEFAULT_COLOR.view)
    config.getProcessor(view).getDefaultCPUProcessor().applyRGB(frame)
    return frame


def decoded_png(path: Path, size: tuple[int, int]) -> np.ndarray:
    raw = run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout
    return np.frombuffer(raw, np.uint8).reshape(size[1], size[0], 3)


def test_a_source_time_names_a_frame():
    rate = Fraction(25)
    assert source_frame("f:2", rate=rate, frames=3) == 2
    assert source_frame("0.04s", rate=rate, frames=3) == 1
    # Between frames 1 and 2: the frame whose interval holds it.
    assert source_frame("0.07999s", rate=rate, frames=3) == 1
    with pytest.raises(AssertionError, match="the clip has frames 0 to 2"):
        source_frame("f:3", rate=rate, frames=3)
    with pytest.raises(AssertionError):
        source_frame("b:1", rate=rate, frames=3)


def test_still_is_the_frame_through_the_input_transform_and_the_view(project):
    key = write_record(project, make_clip(project / "footage" / "C0001.mov"))
    result = CliRunner().invoke(main, ["still", "--media", key, "--at", "f:1", "--project", str(project)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    expected = reference(sequential(project / "footage" / "C0001.mov", 1, SIZE))
    assert np.array_equal(render(Project.load(project), stored(project, key), 1), expected)
    assert printed["codes"] == code_numbers(expected)
    assert (printed["frame"], printed["size"], printed["rotation"]) == (1, list(SIZE), 0)
    assert (printed["space"], printed["view"]) == (SPACE, DEFAULT_COLOR.view)
    path = Path(printed["png"])
    assert path == project / "cache" / "lungecuta" / "still" / f"{key}_f1_128x72.png"
    eight = np.floor(np.clip(expected, 0, 1) * 255 + 0.5).astype(np.uint8)
    assert np.array_equal(decoded_png(path, SIZE), eight)


@pytest.mark.parametrize("rotation", [90, 180, -90])
def test_a_rotated_clip_is_turned_the_way_ffmpeg_autorotates_it(project, rotation):
    clip = make_clip(project / "footage" / f"R{rotation % 360}.mov", rotation)
    tagged = probe(clip).video.rotation
    coded = read_rgb(clip, 1, rate=Fraction(30000, 1001), size=SIZE, pix_fmt="yuv422p10le", matrix="bt709",
                     range_="limited", chroma_location="left")
    shown_by_ffmpeg = sequential(clip, 1, SIZE[::-1] if rotation % 180 else SIZE, autorotate=True)
    # ffmpeg's transpose filter resamples 4:2:2 chroma, so the turns are compared by distance.
    distance = {k: np.abs(np.rot90(coded, k) - shown_by_ffmpeg).mean()
                for k in range(4) if np.rot90(coded, k).shape == shown_by_ffmpeg.shape}
    assert min(distance, key=distance.get) == turns(tagged)
    assert np.array_equal(oriented(coded, tagged), np.rot90(coded, turns(tagged)))


def test_a_still_is_shown_turned_and_downscaled_in_the_working_space(project):
    clip = make_clip(project / "footage" / "R90.mov", 90)
    key = write_record(project, clip)
    result = CliRunner().invoke(main, ["still", "--media", key, "--at", "f:2", "--height", "64",
                                       "--project", str(project)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert (printed["size"], printed["rotation"]) == ([36, 64], 90)
    expected = reference(sequential(clip, 2, SIZE), rotation=90, height=64)
    assert printed["codes"] == code_numbers(expected)
    with pytest.raises(AssertionError, match="only shrinks"):
        render(Project.load(project), stored(project, key), 2, height=256)


def test_a_mirrored_clip_is_refused(project):
    clip = make_clip(project / "footage" / "M.mov", hflip=True)
    with pytest.raises(AssertionError, match="mirrors the picture"):
        probe(clip)


def test_an_undecided_clip_has_no_still(project):
    key = write_record(project, make_clip(project / "footage" / "C0002.mov"), decided=False)
    with pytest.raises(AssertionError, match="colour space is undecided"):
        render(Project.load(project), stored(project, key), 0)


def test_a_changed_original_makes_the_record_stale(project):
    clip = make_clip(project / "footage" / "C0003.mov")
    key = write_record(project, clip)
    os.utime(clip, ns=(clip.stat().st_atime_ns, clip.stat().st_mtime_ns + 10**9))
    render(Project.load(project), stored(project, key), 0)  # touched only: the hash still agrees
    with clip.open("ab") as handle:
        handle.write(b"\0")
    with pytest.raises(AssertionError, match="has changed since it was imported"):
        render(Project.load(project), stored(project, key), 0)


def test_the_png_carries_no_colour_chunks():
    frame = np.linspace(-0.1, 1.1, 4 * 5 * 3, dtype=np.float32).reshape(4, 5, 3)
    data = png(frame)
    kinds, at = [], 8
    while at < len(data):
        (length,) = struct.unpack(">I", data[at:at + 4])
        kinds.append(data[at + 4:at + 8])
        at += 12 + length
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and kinds == [b"IHDR", b"IDAT", b"IEND"]


def test_numbers_are_codes_of_the_float_frame_out_of_range_included():
    frame = np.zeros((10, 10, 3), np.float32)
    frame[..., 0] = np.linspace(-0.01, 1.01, 100, dtype=np.float32).reshape(10, 10)
    frame[..., 1] = 0.5
    numbers = code_numbers(frame)
    assert numbers["G"] == {"min": 511.5, "p1": 511.5, "p50": 511.5, "p99": 511.5, "max": 511.5}
    assert numbers["R"]["min"] == round(float(np.float32(-0.01)) * 1023, 4) < 0
    assert numbers["R"]["max"] > 1023
    assert numbers["B"]["max"] == 0
