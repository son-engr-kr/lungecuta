import numpy as np
import pytest

from lungecuta import color
from lungecuta.proxy import parity_frames, passes, proxy_size
from lungecuta.schema.media import ParityNumbers
from lungecuta.tools import run

CONFIG = "studio-config-v5.0.0_aces-v2.1_ocio-v2.6"


def test_the_proxy_keeps_the_coded_frames_shape():
    assert proxy_size((3840, 2160), 540) == (960, 540)
    assert proxy_size((1920, 1080), 540) == (960, 540)
    assert proxy_size((3840, 2160), 1080) == (1920, 1080)
    with pytest.raises(AssertionError, match="not a whole even number"):
        proxy_size((2940, 1912), 540)


def test_parity_uses_first_last_and_nine_between():
    frames = parity_frames(705)
    assert len(frames) == 11 and frames[0] == 0 and frames[-1] == 704
    assert parity_frames(210) == [0, 20, 41, 62, 83, 104, 125, 146, 167, 188, 209]
    assert parity_frames(3) == [0, 1, 2]


def test_ocio_on_row_bands_equals_ocio_on_the_whole_frame():
    processor = color.cpu_processor(CONFIG, "S-Log3 S-Gamut3.Cine", color.DAVINCI_INTERMEDIATE)
    image = np.random.default_rng(1).random((37, 53, 3), dtype=np.float32)
    whole, banded = image.copy(), image.copy()
    processor.applyRGB(whole)
    color.apply(processor, banded, threads=4)
    assert np.array_equal(whole, banded)
    assert not np.array_equal(banded, image)


def test_a_colour_space_outside_the_config_is_an_error():
    with pytest.raises(AssertionError, match="is not a colour space"):
        color.cpu_processor(CONFIG, "S-Log2 S-Gamut", color.DAVINCI_INTERMEDIATE)


def test_the_gate_checks_spread_and_bias():
    def numbers(p99, bias):
        return ParityNumbers(p99=p99, max=10.0, bias=bias, p99_blocks=0.5)

    # The proxies measured on the samples (bench/RESULTS.md, "Proxy parity").
    assert passes(numbers(1.66, (0.34, 0.19, 0.37)))
    assert not passes(numbers(2.1, (0.0, 0.0, 0.0)))
    # A range converted on the way shows as a bias before it shows in the p99.
    assert not passes(numbers(1.92, (-0.48, 0.73, -0.67)))


def test_range_recoding_is_exact_both_ways():
    from lungecuta.proxy import recode

    y, cb, cr = (np.array([64.0, 502.0, 940.0]), np.array([64.0, 512.0, 960.0]), np.array([512.0, 512.0, 512.0]))
    full = recode(y, cb, cr, bits=10, range_="limited", to_bits=10, to_range="full")
    np.testing.assert_allclose(full[0], [0, 511.5, 1023], atol=1e-9)
    np.testing.assert_allclose(full[1], [512 - 1023 * 0.5, 512, 512 + 1023 * 0.5], atol=1e-9)
    back = recode(*full, bits=10, range_="full", to_bits=10, to_range="limited")
    for a, b in zip(back, (y, cb, cr)):
        np.testing.assert_allclose(a, b, atol=1e-9)
    eight = recode(np.array([16.0, 235.0]), np.array([128.0]), np.array([240.0]), bits=8, range_="limited", to_bits=10,
                   to_range="limited")
    np.testing.assert_allclose(eight[0], [64, 940]) and np.testing.assert_allclose(eight[2], [960])


def test_a_proxy_built_from_lcs_decode_decodes_to_the_downscaled_original(tmp_path):
    from fractions import Fraction

    from lungecuta.decode import Layout
    from lungecuta.proxy import build_proxy, downscale_operators, parity

    clip = tmp_path / "C0001.mp4"
    run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=256x144:rate=25", "-frames:v", "4", "-c:v",
         "libx264", "-qp", "0", "-g", "1", "-pix_fmt", "yuv422p10le", "-chroma_sample_location", "left", str(clip)])
    proxy = tmp_path / "C0001.mkv"
    common = dict(size=(64, 36), coded_size=(256, 144), source_pix_fmt="yuv422p10le", source_chroma_location="left",
                  source_range="limited", frames=4, rate=Fraction(25))
    build_proxy(clip, proxy, profile="ffv1-444", proxy_range="limited", **common)
    numbers, = parity(clip, proxy, indices=[0, 3], rate=Fraction(25), coded_size=(256, 144), size=(64, 36),
                      source_pix_fmt="yuv422p10le", source_chroma_location="left", proxy_pix_fmt="yuv444p10le",
                      matrix="bt709", source_range="limited", proxy_range="limited", config=CONFIG, spaces=[None])
    # Only the rounding of each plane to 10-bit codes separates them. Half a code of Y and of
    # Cr moves R by at most 0.5 * 1023/876 + 2 (1 - KR) * 0.5 * 1023/896, and B by the same
    # with KB: 1.48 and 1.64 codes in limited range (H.273 equations 30 to 32 and 45 to 47).
    bound = 0.5 * 1023 / 876 + 2 * (1 - 0.0722) * 0.5 * 1023 / 896
    assert numbers.max <= bound + 1e-3 and numbers.p99 < 1.2 and max(abs(b) for b in numbers.bias) < 0.05
    (_, _), (rows_c, columns_c) = downscale_operators(Layout.of("yuv422p10le"), "left", (256, 144), (64, 36))
    np.testing.assert_allclose(columns_c.sum(axis=1), 1, atol=1e-6)  # the composed operator still averages
    assert rows_c.shape == (36, 144) and columns_c.shape == (64, 128)
