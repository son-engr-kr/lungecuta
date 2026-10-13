"""Proxies: 10-bit all-intra in the original's range, each with its own decode record and
a parity test against the original (docs/design/02-media.md#proxies). The format is
provisional (`PROXY_PROFILE` in media.py)."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np

from . import color
from .decode import CHROMA_OFFSET, Layout, linear_resample, planes, raw_frames, read_rgb
from .geometry import _area_weights, area_resize
from .schema.media import ParityNumbers
from .ticks import format_rate
from .tools import run, tool


@dataclass(frozen=True)
class ProxyProfile:
    suffix: str
    codec: tuple[str, ...]
    pix_fmt: str  # what is encoded
    decoded_pix_fmt: str  # what ffprobe reports back (ProRes 4444 is 12-bit inside)
    carries_range: bool  # whether the file's range tag survives (ProRes always reads tv)

    @property
    def chroma_location(self) -> str:
        """Where a 4:2:2 proxy's chroma sits: `build_proxy` decimates it co-sited with the
        even pixels ("left"), since ProRes and FFV1 carry no siting of their own."""
        return "left" if Layout.of(self.pix_fmt).sub_x > 1 else "unspecified"


PROFILES = {
    "prores-proxy": ProxyProfile(".mov", ("-c:v", "prores_ks", "-profile:v", "0"), "yuv422p10le", "yuv422p10le", False),
    "prores-lt": ProxyProfile(".mov", ("-c:v", "prores_ks", "-profile:v", "1"), "yuv422p10le", "yuv422p10le", False),
    "prores-hq": ProxyProfile(".mov", ("-c:v", "prores_ks", "-profile:v", "3"), "yuv422p10le", "yuv422p10le", False),
    "prores-4444": ProxyProfile(".mov", ("-c:v", "prores_ks", "-profile:v", "4"), "yuv444p10le", "yuv444p12le", False),
    # Lossless and all-intra (-g 1), so any frame is one seek.
    "ffv1-422": ProxyProfile(".mkv", ("-c:v", "ffv1", "-level", "3", "-g", "1", "-slices", "16"), "yuv422p10le", "yuv422p10le", True),
    "ffv1-444": ProxyProfile(".mkv", ("-c:v", "ffv1", "-level", "3", "-g", "1", "-slices", "16"), "yuv444p10le", "yuv444p10le", True),
}
PARITY_FRAMES = 11
# The provisional gate (bench/RESULTS.md, "Proxy parity"): in the file's own R'G'B' codes,
# p99 at most 2 and no channel biased by more than half a code. *proposed*
PARITY_P99 = 2.0
PARITY_BIAS = 0.5
_FFMPEG_RANGE = {"full": "pc", "limited": "tv"}


def proxy_size(coded: tuple[int, int], height: int) -> tuple[int, int]:
    """The proxy keeps the coded frame's shape at `height` lines. A width that is not a
    whole even number is an error, not a rounding."""
    width, coded_height = coded
    scaled = Fraction(width * height, coded_height)
    assert scaled.denominator == 1 and scaled % 2 == 0, (
        f"a {width}x{coded_height} frame at {height} lines is {float(scaled):.2f} wide, "
        "not a whole even number; choose another proxy_height"
    )
    return int(scaled), height


def downscale_operators(layout: Layout, chroma_location: str, coded: tuple[int, int], size: tuple[int, int]):
    """Row and column matrices that take each plane of a decoded frame to `size`: an exact
    area average of the frame's pixels, for chroma after bringing it to every pixel by
    `decode.upsample`'s interpolation (the operators compose)."""
    width, height = coded
    out_w, out_h = size
    rows, columns = _area_weights(height, out_h), _area_weights(width, out_w)
    rows_c, columns_c = rows, columns
    if layout.sub_x > 1 or layout.sub_y > 1:
        assert chroma_location in CHROMA_OFFSET, (
            f"the source does not say where its chroma samples sit (chroma_location {chroma_location!r})"
        )
        offset_x, offset_y = CHROMA_OFFSET[chroma_location]
        if layout.sub_y > 1:
            rows_c = rows @ linear_resample(np.eye(height // layout.sub_y, dtype=np.float32), layout.sub_y, offset_y, 0)
        if layout.sub_x > 1:
            columns_c = columns @ linear_resample(np.eye(width // layout.sub_x, dtype=np.float32), layout.sub_x, offset_x, 0)
    return (rows, columns), (np.ascontiguousarray(rows_c, np.float32), np.ascontiguousarray(columns_c, np.float32))


def recode(y: np.ndarray, cb: np.ndarray, cr: np.ndarray, *, bits: int, range_: str, to_bits: int,
           to_range: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Y'CbCr codes at one bit depth and range as codes at another, exactly by ITU-T H.273
    (through E'Y and E'PB, E'PR), unrounded."""
    if (bits, range_) == (to_bits, to_range):
        return y, cb, cr

    def normalised(value: np.ndarray, chroma: bool, n: int, rng: str) -> np.ndarray:
        if rng == "limited":
            step = 1 << (n - 8)
            return (value / step - 128) / 224 if chroma else (value / step - 16) / 219
        top = (1 << n) - 1
        return (value - (1 << (n - 1))) / top if chroma else value / top

    def encoded(value: np.ndarray, chroma: bool, n: int, rng: str) -> np.ndarray:
        if rng == "limited":
            step = 1 << (n - 8)
            return step * (224 * value + 128) if chroma else step * (219 * value + 16)
        top = (1 << n) - 1
        return top * value + (1 << (n - 1)) if chroma else top * value

    return tuple(encoded(normalised(v, c, bits, range_), c, to_bits, to_range)
                 for v, c in ((y, False), (cb, True), (cr, True)))


def cosited_half(plane: np.ndarray) -> np.ndarray:
    """A chroma plane at half the width, each sample on an even pixel: a [1, 2, 1] / 4
    average around it (*proposed* for the 4:2:2 candidates; edges repeat their pixel)."""
    padded = np.pad(plane, ((0, 0), (1, 1)), mode="edge")
    return (padded[:, 0:-2:2] + 2 * padded[:, 1:-1:2] + padded[:, 2::2]) / 4


def build_proxy(
    source: Path,
    target: Path,
    *,
    size: tuple[int, int],
    coded_size: tuple[int, int],
    source_pix_fmt: str,
    source_chroma_location: str,
    source_range: str,
    profile: str,
    proxy_range: str,
    frames: int,
    rate: Fraction,
) -> None:
    """Write the proxy from lc's own decode, so it decodes to the downscaled original up to
    rounding: the decoder's planes, chroma brought to every pixel as `read_rgb` does, each
    plane area-averaged to `size` in code units (an affine Y'CbCr to R'G'B' conversion
    commutes with an average), the range changed by H.273 only if `proxy_range` differs,
    rounded to 10-bit codes, and encoded by `profile`. Then check that every frame arrived."""
    chosen = PROFILES[profile]
    assert target.suffix == chosen.suffix, f"a {profile} proxy is a {chosen.suffix} file, not {target.name}"
    assert not target.exists(), f"{target} exists already"
    partial = target.with_name(f".{target.stem}.partial{target.suffix}")
    layout = Layout.of(source_pix_fmt)
    (rows, columns), (rows_c, columns_c) = downscale_operators(layout, source_chroma_location, coded_size, size)
    width, height = size
    encoded = Layout.of(chosen.pix_fmt)
    assert encoded.bits == 10 and encoded.sub_y == 1, f"{chosen.pix_fmt} is not a 10-bit 4:4:4 or 4:2:2 format"
    encoder = subprocess.Popen([
        tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", chosen.pix_fmt, "-s", f"{width}x{height}",
        "-framerate", format_rate(rate), "-color_range", _FFMPEG_RANGE[proxy_range], "-i", "-",
        *chosen.codec, "-pix_fmt", chosen.pix_fmt, "-color_range", _FFMPEG_RANGE[proxy_range], str(partial),
    ], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    top = np.float32(1023)
    for data in raw_frames(source, pix_fmt=source_pix_fmt, size=coded_size, count=frames):
        y, cb, cr = planes(data, coded_size, layout)
        small = (rows @ y @ columns.T, rows_c @ cb @ columns_c.T, rows_c @ cr @ columns_c.T)
        small = recode(*small, bits=layout.bits, range_=source_range, to_bits=10, to_range=proxy_range)
        if encoded.sub_x > 1:
            small = (small[0], *(cosited_half(plane) for plane in small[1:]))
        codes = [np.clip(np.floor(plane + np.float32(0.5)), 0, top).astype("<u2") for plane in small]
        encoder.stdin.write(b"".join(plane.tobytes() for plane in codes))
    encoder.stdin.close()
    error = encoder.stderr.read().decode(errors="replace").strip()
    assert encoder.wait() == 0 and not error, f"ffmpeg failed encoding {target.name}: {error}"
    stream = json.loads(run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
        "-show_entries", "stream=nb_read_packets,color_range,pix_fmt,width,height", "-of", "json", str(partial),
    ]).stdout)["streams"][0]
    assert int(stream["nb_read_packets"]) == frames, f"the proxy has {stream['nb_read_packets']} frames for {frames}"
    assert stream["pix_fmt"] == chosen.decoded_pix_fmt, f"the proxy came out as {stream}"
    if chosen.carries_range:
        assert stream["color_range"] == _FFMPEG_RANGE[proxy_range], f"the proxy came out as {stream}"
    assert (stream["width"], stream["height"]) == size, f"the proxy came out {stream['width']}x{stream['height']}"
    partial.rename(target)


def parity_frames(frames: int) -> list[int]:
    """First, last, and nine evenly spaced between."""
    return sorted({k * (frames - 1) // (PARITY_FRAMES - 1) for k in range(PARITY_FRAMES)})


def parity(
    original: Path,
    proxy: Path,
    *,
    indices: list[int],
    rate: Fraction,
    coded_size: tuple[int, int],
    size: tuple[int, int],
    source_pix_fmt: str,
    source_chroma_location: str,
    proxy_pix_fmt: str,
    matrix: str,
    proxy_chroma_location: str = "unspecified",
    source_range: str,
    proxy_range: str,
    config: str,
    space: str | None,
) -> ParityNumbers:
    """The parity test on the frames `indices`: in DaVinci Intermediate when `space`
    names the clip's colour space, in the file's own R'G'B' codes when it is None."""
    processor = color.cpu_processor(config, space, color.DAVINCI_INTERMEDIATE) if space else None
    differences, blocks = [], []
    for index in indices:
        reference = read_rgb(original, index, rate=rate, size=coded_size, pix_fmt=source_pix_fmt, matrix=matrix,
                             range_=source_range, chroma_location=source_chroma_location)
        candidate = read_rgb(proxy, index, rate=rate, size=size, pix_fmt=proxy_pix_fmt, matrix=matrix,
                             range_=proxy_range, chroma_location=proxy_chroma_location)
        if processor is not None:
            color.apply(processor, reference)
            color.apply(processor, candidate)
        signed = (candidate - area_resize(reference, size)) * 1023
        differences.append(signed.reshape(-1, 3))
        blocks.append(np.abs(area_resize(np.ascontiguousarray(signed), (size[0] // 4, size[1] // 4))).ravel())
    every = np.concatenate(differences)
    absolute = np.abs(every)
    return ParityNumbers(
        p99=round(float(np.percentile(absolute, 99)), 4),
        max=round(float(absolute.max()), 4),
        bias=tuple(round(float(b), 4) for b in every.mean(axis=0)),
        p99_blocks=round(float(np.percentile(np.concatenate(blocks), 99)), 4),
    )


def passes(numbers: ParityNumbers) -> bool:
    return numbers.p99 <= PARITY_P99 and max(abs(b) for b in numbers.bias) <= PARITY_BIAS
