import subprocess

import numpy as np
import pytest

from lungecuta import color
from lungecuta.encode import ycbcr
from lungecuta.flash import AREA, flash, luminance_processor
from lungecuta.tools import tool

CONFIG = "studio-config-v5.0.0_aces-v2.1_ocio-v2.6"
SRGB = "sRGB - Display"


def view(display: str) -> str:
    return list(color.config(CONFIG).getViews(display))[0]


def srgb_code(luminance: float) -> float:
    """The sRGB code value of a grey with this relative luminance (WCAG's formula inverted)."""
    return 1.055 * luminance ** (1 / 2.4) - 0.055


def write(path, frames: list[np.ndarray], rate: str = "30/1") -> None:
    """R'G'B' frames as ProRes 4444 XQ, tagged BT.709, limited range (FFV1 in MOV loses the
    range tag)."""
    height, width = frames[0].shape[:2]
    data = bytearray()
    for rgb in frames:
        for plane in ycbcr(rgb.astype(np.float32), bits=10, matrix="bt709"):
            data += np.round(plane).astype("<u2").tobytes()
    subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv444p10le", "-s",
                    f"{width}x{height}", "-r", rate, "-i", "-", "-c:v", "prores_ks", "-profile:v", "5", "-pix_fmt", "yuv444p10le",
                    "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
                    str(path)], input=bytes(data), check=True)


def run(path):
    return flash(path, config_name=CONFIG, display=SRGB, view=view(SRGB))


def alternating(size, period: int, count: int, low: float = 0.0, high: float = 1.0, where=None):
    """Grey frames switching between two code values every `period` frames, over `where`
    (a boolean mask) or the whole frame."""
    width, height = size
    frames = []
    for k in range(count):
        frame = np.full((height, width, 3), low, np.float32)
        if (k // period) % 2:
            if where is None:
                frame[:] = high
            else:
                frame[where] = high
        frames.append(frame)
    return frames


def test_relative_luminance_is_wcags_formula_on_an_srgb_display():
    codes = np.linspace(0, 1, 256, dtype=np.float32)
    rgb = np.ascontiguousarray(np.repeat(codes[:, None, None], 3, axis=2).reshape(16, 16, 3))
    color.apply(luminance_processor(CONFIG, SRGB, view(SRGB)), rgb)
    wcag = np.where(codes <= 0.04045, codes / 12.92, ((codes + 0.055) / 1.055) ** 2.4)
    np.testing.assert_allclose(rgb[..., 1].reshape(-1), wcag, atol=1e-4)
    grey = np.full((1, 1, 3), 0.5, np.float32)
    rec1886 = "Rec.1886 Rec.709 - Display"
    color.apply(luminance_processor(CONFIG, rec1886, view(rec1886)), grey)
    assert grey[0, 0, 1] == pytest.approx(0.5 ** 2.4, abs=1e-4)  # BT.1886's gamma 2.4, black at 0


def test_more_than_three_flashes_a_second_over_the_frame_fail(tmp_path):
    write(tmp_path / "fast.mov", alternating((64, 36), 4, 60))  # a transition every 4 frames at 30 fps
    result = run(tmp_path / "fast.mov")
    assert not result["ok"] and result["failures"][0]["share"] == 1.0
    assert result["transitions"] == 64 * 36 * 14  # every pixel is a cell below 300 rows


def test_three_flashes_a_second_pass(tmp_path):
    # A transition every 5 frames: seven of them span exactly one second, which is not "within" it.
    write(tmp_path / "three.mov", alternating((64, 36), 5, 60))
    result = run(tmp_path / "three.mov")
    assert result["ok"] and result["largest_share"] == 0 and result["transitions"] == 64 * 36 * 11


def test_a_flashing_area_under_a_quarter_of_the_field_passes(tmp_path):
    # 36 rows: the field is 12 rows by round(12 * 341 / 256) = 16 columns, 192 cells.
    small = np.zeros((36, 64), bool)
    small[10:16, 20:26] = True  # 36 cells, 19 % of the field
    large = np.zeros((36, 64), bool)
    large[10:18, 20:28] = True  # 64 cells, 33 %
    write(tmp_path / "small.mov", alternating((64, 36), 2, 40, where=small))
    write(tmp_path / "large.mov", alternating((64, 36), 2, 40, where=large))
    passed, failed = run(tmp_path / "small.mov"), run(tmp_path / "large.mov")
    assert passed["ok"] and passed["largest_share"] == pytest.approx(36 / 192, abs=1e-3)
    assert 36 / 192 < AREA < 64 / 192
    assert not failed["ok"] and failed["failures"][0]["share"] == pytest.approx(64 / 192, abs=1e-3)
    assert failed["field_cells"] == [16, 12] and failed["cells"] == [64, 36]


def test_changes_between_bright_images_are_not_flashes(tmp_path):
    bright = srgb_code(0.85)  # the darker image at a relative luminance of 0.85, not below 0.8
    write(tmp_path / "bright.mov", alternating((64, 36), 2, 40, low=bright))
    assert run(tmp_path / "bright.mov")["transitions"] == 0
    dim = srgb_code(0.75)
    write(tmp_path / "dim.mov", alternating((64, 36), 2, 40, low=dim))
    assert not run(tmp_path / "dim.mov")["ok"]


def test_a_pattern_finer_than_a_tenth_of_a_degree_is_excepted(tmp_path):
    # 600 rows: cells of 2 pixels, so a checkerboard of 1-pixel squares averages to a steady
    # grey in every cell, and one of 4-pixel squares does not.
    rows, columns = np.indices((600, 400))
    fine = (rows + columns) % 2 == 0
    coarse = (rows // 4 + columns // 4) % 2 == 0
    for name, pattern in (("fine", fine), ("coarse", coarse)):
        frames = []
        for k in range(30):
            frame = np.zeros((600, 400, 3), np.float32)
            frame[pattern if (k // 2) % 2 else ~pattern] = 1.0
            frames.append(frame)
        write(tmp_path / f"{name}.mov", frames)
    fine_result, coarse_result = run(tmp_path / "fine.mov"), run(tmp_path / "coarse.mov")
    assert fine_result["cells"] == [200, 300] and fine_result["field_cells"] == [133, 100]
    assert fine_result["ok"] and fine_result["transitions"] == 0
    assert not coarse_result["ok"]
