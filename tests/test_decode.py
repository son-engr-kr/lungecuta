"""Y'CbCr to R'G'B' as lc does it: the decoder's own planes, chroma brought to every pixel
by linear interpolation where the stream says it sits, then ITU-T H.273 V4."""
import hashlib
import subprocess

import numpy as np
import pytest

from lungecuta.decode import KR_KB, Layout, frame_rgb, to_rgb, upsample


def codes(y, cb, cr):
    return (np.full((1, 1), y, np.float32), np.full((1, 1), cb, np.float32), np.full((1, 1), cr, np.float32))


def test_the_layout_comes_from_the_pixel_format():
    assert Layout.of("yuv422p10le") == Layout(10, 2, 1, False)
    assert Layout.of("yuva444p12le") == Layout(12, 1, 1, True)
    assert Layout.of("yuv420p") == Layout(8, 2, 2, False)
    assert Layout.of("yuv422p10le").frame_bytes((8, 2)) == (8 * 2 + 2 * 4 * 2) * 2
    with pytest.raises(AssertionError):
        Layout.of("nv12")


@pytest.mark.parametrize("bits", [8, 10, 12])
def test_limited_range_black_and_white_are_0_and_1(bits):
    step = 1 << (bits - 8)
    black = to_rgb(*codes(16 * step, 128 * step, 128 * step), bits=bits, matrix="bt709", range_="limited")
    white = to_rgb(*codes(235 * step, 128 * step, 128 * step), bits=bits, matrix="bt709", range_="limited")
    np.testing.assert_allclose(black.ravel(), 0, atol=1e-7)
    np.testing.assert_allclose(white.ravel(), 1, atol=1e-6)


def test_full_range_divides_by_2n_minus_1():
    np.testing.assert_allclose(to_rgb(*codes(1023, 512, 512), bits=10, matrix="bt709", range_="full").ravel(), 1, atol=1e-6)
    np.testing.assert_allclose(to_rgb(*codes(512, 512, 512), bits=10, matrix="bt709", range_="full").ravel(),
                               512 / 1023, atol=1e-6)


@pytest.mark.parametrize("matrix", ["bt709", "bt2020nc"])
def test_the_matrix_inverts_equations_45_to_47(matrix):
    kr, kb = KR_KB[matrix]
    rgb = np.array([0.8, 0.3, 0.1])
    y = kr * rgb[0] + (1 - kr - kb) * rgb[1] + kb * rgb[2]
    pb, pr = 0.5 * (rgb[2] - y) / (1 - kb), 0.5 * (rgb[0] - y) / (1 - kr)
    out = to_rgb(*codes(4 * (219 * y + 16), 4 * (224 * pb + 128), 4 * (224 * pr + 128)), bits=10, matrix=matrix,
                 range_="limited")
    np.testing.assert_allclose(out.ravel(), rgb, atol=1e-5)


def test_footroom_and_headroom_are_kept():
    assert to_rgb(*codes(4, 512, 512), bits=10, matrix="bt709", range_="limited").max() < 0
    assert to_rgb(*codes(1019, 512, 512), bits=10, matrix="bt709", range_="limited").min() > 1


def test_co_sited_4_2_2_chroma_keeps_its_samples_and_averages_between():
    chroma = np.array([[100.0, 200.0, 300.0]], np.float32)
    out = upsample(chroma, Layout.of("yuv422p10le"), "left")
    assert out.tolist() == [[100, 150, 200, 250, 300, 300]]


def test_centred_chroma_is_interpolated_at_quarter_places():
    chroma = np.array([[100.0, 200.0]], np.float32)
    out = upsample(chroma, Layout.of("yuv422p10le"), "center")
    assert out.tolist() == [[100, 125, 175, 200]]


def test_4_2_0_chroma_left_sits_between_rows():
    chroma = np.array([[0.0], [100.0]], np.float32)
    out = upsample(chroma, Layout.of("yuv420p"), "left")
    assert out[:, 0].tolist() == [0, 25, 75, 100]


def test_flat_chroma_stays_flat_and_444_is_untouched():
    flat = np.full((4, 8), 321.0, np.float32)
    assert np.all(upsample(flat, Layout.of("yuv420p10le"), "topleft") == 321.0)
    full = np.arange(12, dtype=np.float32).reshape(3, 4)
    assert upsample(full, Layout.of("yuv444p10le"), "unspecified") is full


def test_a_stream_that_does_not_say_where_its_chroma_sits_is_not_guessed():
    with pytest.raises(AssertionError, match="does not say where its chroma samples sit"):
        upsample(np.zeros((2, 2), np.float32), Layout.of("yuv422p10le"), "unspecified")


def test_ffmpeg_hands_over_the_decoders_planes_unchanged(tmp_path):
    """Raw output in the stream's own pixel format is the decoded frame: its md5 is the
    framemd5 ffmpeg itself reports."""
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=25", "-frames:v", "2",
                    "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv422p10le", str(clip)], check=True)
    framemd5 = [line.rsplit(",", 1)[-1].strip() for line in subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(clip), "-f", "framemd5", "-"], capture_output=True, text=True,
        check=True).stdout.splitlines() if line and not line.startswith("#")]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-f", "rawvideo", "-pix_fmt", "yuv422p10le", "-"],
                         capture_output=True, check=True).stdout
    size = Layout.of("yuv422p10le").frame_bytes((64, 36))
    assert [hashlib.md5(raw[k * size:(k + 1) * size]).hexdigest() for k in range(2)] == framemd5
    frame = frame_rgb(raw[:size], (64, 36), pix_fmt="yuv422p10le", chroma_location="left", matrix="bt709",
                      range_="limited")
    assert frame.shape == (36, 64, 3) and frame.dtype == np.float32
