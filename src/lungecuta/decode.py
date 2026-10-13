"""Decoding: the per-clip decoder check, and single frames as float RGB
(docs/design/02-media.md#decode)."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np

from .schema.media import DecodeRecord, DecoderCheck
from .tools import run, tool

CHECK_FRAMES = 300
# Frames are kept in VideoToolbox surfaces and downloaded in this format, so a silent
# software fallback fails the check instead of being compared with itself.
VIDEOTOOLBOX_DOWNLOAD = {"yuv422p10le": "p210le", "yuv420p10le": "p010le", "yuv420p": "nv12", "yuv422p": "nv16"}
SWS_FLAGS = "accurate_rnd+full_chroma_int+full_chroma_inp"


def _decoding(args: list[str], log) -> subprocess.Popen:
    """ffmpeg decoding to raw frames on its stdout, its messages written to `log` (a file,
    so a decoder that reports an error on every frame cannot fill a pipe and stall)."""
    return subprocess.Popen([tool("ffmpeg"), "-v", "error", "-noautorotate", *args], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=log)


def _reported(log) -> str | None:
    """The first message in `log`, without the decoder's address, which differs every run."""
    if os.fstat(log.fileno()).st_size == 0:
        return None
    log.seek(0)
    first = log.read().decode(errors="replace").strip().splitlines()[0]
    return re.sub(r"^\[(\w+) @ 0x[0-9a-f]+\] ", r"\1: ", first)


def decoder_check(path: Path, *, pix_fmt: str, size: tuple[int, int], frames: int) -> DecodeRecord:
    """Decode the first 300 frames (or all) in software and with VideoToolbox at once and
    compare them frame by frame, byte for byte, in the clip's own pixel format; hardware is
    used only when every frame is identical. The check stops at the first frame that differs
    or that VideoToolbox reports an error on, since one is enough to rule it out."""
    count = min(CHECK_FRAMES, frames)

    def record(decoder: str, result: str, compared: int, detail: str | None) -> DecodeRecord:
        check = DecoderCheck(method="bytes", frames=compared, against="videotoolbox", result=result, detail=detail)
        return DecodeRecord(decoder=decoder, checked=check)

    download = VIDEOTOOLBOX_DOWNLOAD.get(pix_fmt)
    if download is None:
        return record("software", "unchecked", 0, f"no VideoToolbox download format known for {pix_fmt}")
    frame_bytes = Layout.of(pix_fmt).frame_bytes(size)
    output = ["-map", "0:v:0", "-frames:v", str(count), "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"]
    with tempfile.TemporaryFile() as software_log, tempfile.TemporaryFile() as hardware_log:
        with _decoding(["-i", str(path), *output], software_log) as software, _decoding([
            "-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld", "-i", str(path),
            "-vf", f"hwdownload,format={download}", *output,
        ], hardware_log) as hardware:
            stopped = None
            for index in range(count):
                reference = software.stdout.read(frame_bytes)
                assert len(reference) == frame_bytes, (
                    f"software decode of {path.name}: {index} of {count} frames arrived; {_reported(software_log)}"
                )
                candidate = hardware.stdout.read(frame_bytes)
                if candidate != reference or _reported(hardware_log):
                    stopped = index, len(candidate) < frame_bytes
                    # both are ended before their pipes close, or a decoder still alive
                    # would report the closed pipe as its error
                    for process in (software, hardware):
                        process.kill()
                        process.wait()
                    break
            else:
                extra = software.stdout.read(), hardware.stdout.read()
        if stopped:
            index, short = stopped
            # an error VideoToolbox reported explains a difference, so it is what is recorded
            message = _reported(hardware_log)
            if message or short:
                return record("software", "failed", index + 1, f"videotoolbox at frame {index}: "
                              + (message or "no frame arrived"))
            return record("software", "different", index + 1, f"frame {index} differs from the software decode")
        assert software.returncode == 0 and not extra[0] and not _reported(software_log), (
            f"software decode of {path.name} failed: {_reported(software_log)}"
        )
        message = _reported(hardware_log) or (f"exit code {hardware.returncode}" if hardware.returncode else None)
        if message or extra[1]:
            return record("software", "failed", count, f"videotoolbox after frame {count - 1}: "
                          + (message or f"more than {count} frames"))
    return record("videotoolbox", "identical", count, None)


def seek_seconds(index: int, rate: Fraction) -> str:
    """ffmpeg's input seek returns the first frame at or after the time, so half a frame
    before frame N lands on N (checked against a sequential decode; a media test)."""
    seconds = max(Fraction(0), (index - Fraction(1, 2)) / rate)
    return f"{float(seconds):.6f}"


# BT.709 and BT.2020 non-constant luminance, ITU-T H.273 V4 Table 4 (MatrixCoefficients 1
# and 9); the media record names them as ffprobe does.
KR_KB = {"bt709": (0.2126, 0.0722), "bt2020nc": (0.2627, 0.0593)}
_PLANAR_YUV = re.compile(r"yuv(a?)(444|422|420)p(\d+)?(le)?")
_SUBSAMPLING = {"444": (1, 1), "422": (2, 1), "420": (2, 2)}
# Where the first chroma sample sits, in luma samples right of and below the first luma
# sample: ITU-T H.273 V4 Table 8 (Chroma420SampleLocType 0 to 5), under the names ffprobe
# gives the six types. A 4:2:2 stream uses only the horizontal offset.
CHROMA_OFFSET = {"left": (0.0, 0.5), "center": (0.5, 0.5), "topleft": (0.0, 0.0), "top": (0.5, 0.0),
                 "bottomleft": (0.0, 1.0), "bottom": (0.5, 1.0)}


@dataclass(frozen=True)
class Layout:
    """A planar Y'CbCr pixel format as a decoder gives it."""

    bits: int
    sub_x: int
    sub_y: int
    alpha: bool

    @classmethod
    def of(cls, pix_fmt: str) -> Layout:
        match = _PLANAR_YUV.fullmatch(pix_fmt)
        assert match, f"{pix_fmt} is not a planar Y'CbCr format lc reads"
        sub_x, sub_y = _SUBSAMPLING[match[2]]
        return cls(int(match[3]) if match[3] else 8, sub_x, sub_y, bool(match[1]))

    def frame_bytes(self, size: tuple[int, int]) -> int:
        width, height = size
        assert width % self.sub_x == 0 and height % self.sub_y == 0, f"{width}x{height} does not divide the chroma"
        samples = width * height * (1 + int(self.alpha)) + 2 * (width // self.sub_x) * (height // self.sub_y)
        return samples * (1 if self.bits == 8 else 2)


def bit_depth(pix_fmt: str) -> int:
    return Layout.of(pix_fmt).bits


def planes(data: bytes, size: tuple[int, int], layout: Layout) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Y, Cb and Cr of one raw frame in the decoder's own format, as float32 codes."""
    width, height = size
    dtype = np.dtype(np.uint8) if layout.bits == 8 else np.dtype("<u2")
    values = np.frombuffer(data, dtype)
    chroma_w, chroma_h = width // layout.sub_x, height // layout.sub_y
    y = values[:width * height].reshape(height, width)
    cb = values[width * height:width * height + chroma_w * chroma_h].reshape(chroma_h, chroma_w)
    cr = values[width * height + chroma_w * chroma_h:width * height + 2 * chroma_w * chroma_h].reshape(chroma_h, chroma_w)
    return y.astype(np.float32), cb.astype(np.float32), cr.astype(np.float32)


def linear_resample(values: np.ndarray, factor: int, offset: float, axis: int) -> np.ndarray:
    """`values` brought to `factor` times as many samples along `axis` by linear
    interpolation, each sample k sitting at k * factor + offset in the output's units;
    outside the first and last sample the edge value holds."""
    if factor == 1:
        return values
    count = values.shape[axis]
    unclipped = (np.arange(count * factor, dtype=np.float64) - offset) / factor
    place = np.clip(unclipped, 0, count - 1)
    low = np.floor(place).astype(np.int64)
    high = np.minimum(low + 1, count - 1)
    weight = (place - low).astype(np.float32)
    shape = list(values.shape)
    shape[axis] = count * factor
    out = np.empty(shape, np.result_type(values.dtype, np.float32))

    def at(index) -> tuple:
        return (slice(None),) * axis + (index,)

    def along(w: np.ndarray) -> np.ndarray:
        return w.reshape([-1 if d == axis else 1 for d in range(values.ndim)])

    # Each output sample is low * (1 - w) + high * w. Within one phase (every factor-th
    # output sample) low and high step on by one sample, so away from the clipped edges
    # they are slices; only the edges are gathered. The same products as gathering all.
    for phase in range(factor):
        lo, hi, w = low[phase::factor], high[phase::factor], weight[phase::factor]
        target = out[at(slice(phase, None, factor))]
        inside = np.nonzero((unclipped[phase::factor] == place[phase::factor]) & (hi == lo + 1))[0]
        a, b = (int(inside[0]), int(inside[-1]) + 1) if inside.size else (0, 0)
        first = int(lo[a]) if b else 0
        assert inside.size == b - a and np.array_equal(lo[a:b], np.arange(first, first + b - a)), (
            "the samples of a phase do not step on by one"
        )
        if b:
            target[at(slice(a, b))] = (values[at(slice(first, first + b - a))] * along(1 - w[a:b])
                                       + values[at(slice(first + 1, first + 1 + b - a))] * along(w[a:b]))
        edges = np.r_[0:a, b:lo.size]
        target[at(edges)] = (np.take(values, lo[edges], axis=axis) * along(1 - w[edges])
                             + np.take(values, hi[edges], axis=axis) * along(w[edges]))
    return out


def upsample(chroma: np.ndarray, layout: Layout, location: str) -> np.ndarray:
    """A chroma plane at every pixel: linear interpolation between the samples, placed
    where the stream says they sit (*proposed*: the filter is a choice; linear keeps flat
    areas exact and adds no overshoot)."""
    if layout.sub_x == layout.sub_y == 1:
        return chroma
    assert location in CHROMA_OFFSET, (
        f"the stream does not say where its chroma samples sit (chroma_location {location!r}); state it"
    )
    offset_x, offset_y = CHROMA_OFFSET[location]
    wide = linear_resample(chroma, layout.sub_x, offset_x, axis=1)
    return np.ascontiguousarray(linear_resample(wide, layout.sub_y, offset_y, axis=0), dtype=np.float32)


def to_rgb(y: np.ndarray, cb: np.ndarray, cr: np.ndarray, *, bits: int, matrix: str, range_: str) -> np.ndarray:
    """Y'CbCr codes at every pixel (float, `bits`-bit code units) as float32 R'G'B', by
    ITU-T H.273 V4: limited range is equations 30 to 32 (Y = 2^(n-8) (219 E'Y + 16),
    Cb = 2^(n-8) (224 E'PB + 128)), full range equations 36 to 38 (Y = (2^n - 1) E'Y,
    Cb = (2^n - 1) E'PB + 2^(n-1)), and the matrix equations 45 to 47. Values below 0 or
    above 1 (footroom and headroom) are kept, not clipped."""
    assert range_ in ("full", "limited"), f"unknown range {range_!r}"
    kr, kb = KR_KB[matrix]
    if range_ == "limited":
        step = np.float32(1 << (bits - 8))
        e_y = (y / step - 16) / 219
        pb = (cb / step - 128) / 224
        pr = (cr / step - 128) / 224
    else:
        top, half = np.float32((1 << bits) - 1), np.float32(1 << (bits - 1))
        e_y = y / top
        pb = (cb - half) / top
        pr = (cr - half) / top
    r = e_y + np.float32(2 * (1 - kr)) * pr
    b = e_y + np.float32(2 * (1 - kb)) * pb
    g = (e_y - np.float32(kr) * r - np.float32(kb) * b) / np.float32(1 - kr - kb)
    return np.ascontiguousarray(np.stack([r, g, b], axis=-1), dtype=np.float32)


def frame_rgb(data: bytes, size: tuple[int, int], *, pix_fmt: str, chroma_location: str, matrix: str,
              range_: str) -> np.ndarray:
    """One raw frame in the decoder's own format as float32 R'G'B'."""
    layout = Layout.of(pix_fmt)
    y, cb, cr = planes(data, size, layout)
    return to_rgb(y, upsample(cb, layout, chroma_location), upsample(cr, layout, chroma_location),
                  bits=layout.bits, matrix=matrix, range_=range_)


def read_raw(path: Path, index: int, *, rate: Fraction, size: tuple[int, int], pix_fmt: str) -> bytes:
    """Frame `index` raw in the decoder's own format, by a seek."""
    out = run([
        "ffmpeg", "-v", "error", "-noautorotate", "-ss", seek_seconds(index, rate), "-i", str(path),
        "-map", "0:v:0", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-",
    ]).stdout
    expected = Layout.of(pix_fmt).frame_bytes(size)
    assert len(out) == expected, f"expected one {size[0]}x{size[1]} {pix_fmt} frame from {path.name}, got {len(out)} bytes"
    return out


def read_rgb(
    path: Path, index: int, *, rate: Fraction, size: tuple[int, int], pix_fmt: str, matrix: str, range_: str,
    chroma_location: str = "unspecified",
) -> np.ndarray:
    """Frame `index` as float32 R'G'B', the file's own encoded values (0 to 1 is black to
    nominal white), in the coded orientation (rotation is applied in the render's geometry
    stage, never here). ffmpeg hands over the decoder's own planes (`pix_fmt`, no
    conversion); chroma upsampling, range and matrix are lc's, as stated."""
    out = read_raw(path, index, rate=rate, size=size, pix_fmt=pix_fmt)
    return frame_rgb(out, size, pix_fmt=pix_fmt, chroma_location=chroma_location, matrix=matrix, range_=range_)


def raw_frames(path: Path, *, pix_fmt: str, size: tuple[int, int], count: int, step: int = 1) -> Iterator[bytes]:
    """Frames 0, step, 2 step, ... (`count` of them) from one decode from the start, raw in
    the decoder's own format; the count that arrives is checked."""
    assert step >= 1 and count >= 1, f"step {step} and count {count} must be at least 1"
    frame_bytes = Layout.of(pix_fmt).frame_bytes(size)
    select = [] if step == 1 else ["-vf", f"select=not(mod(n\\,{step}))", "-fps_mode", "passthrough"]
    args = [
        tool("ffmpeg"), "-v", "error", "-noautorotate", "-i", str(path), "-map", "0:v:0", *select,
        "-frames:v", str(count), "-f", "rawvideo", "-pix_fmt", pix_fmt, "-",
    ]
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        for arrived in range(count):
            data = process.stdout.read(frame_bytes)
            assert len(data) == frame_bytes, f"{path.name}: {arrived} of {count} frames arrived from ffmpeg"
            yield data
        rest = process.stdout.read()
        error = process.stderr.read().decode(errors="replace").strip()
    assert process.returncode == 0 and not error, f"ffmpeg failed reading {path.name}: {error}"
    assert not rest, f"{path.name}: ffmpeg sent more than {count} frames"


def read_every(
    path: Path, step: int, count: int, *, size: tuple[int, int], pix_fmt: str, matrix: str, range_: str,
    chroma_location: str = "unspecified",
) -> Iterator[np.ndarray]:
    """Frames 0, step, 2 step, ... (`count` of them) from one decode from the start, each as
    `read_rgb` gives it. At every 30th frame of the S-Log3 sample this is about four times
    faster than a seek per frame (bench/RESULTS.md)."""
    for data in raw_frames(path, pix_fmt=pix_fmt, size=size, count=count, step=step):
        yield frame_rgb(data, size, pix_fmt=pix_fmt, chroma_location=chroma_location, matrix=matrix, range_=range_)
