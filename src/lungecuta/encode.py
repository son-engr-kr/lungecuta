"""Delivery encoding (docs/design/08-deliver.md): display-encoded R'G'B' to Y'CbCr by
ITU-T H.273, chroma decimated where the file says it sits, 8-bit output dithered, the
planes handed to ffmpeg as they are, and all four colour tags set on the frames.

Presets name everything that decides what the file is, the master chain's loudness target
and true-peak ceiling included. The quality settings are the past deliverables' starting
points (x264 CRF 16 to 19, AAC 192 to 256 kbps, 08-deliver.md), not measured choices yet:
*provisional* until the PSNR measurement the design calls for.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .decode import KR_KB, Layout

TAGS = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"


HEVC_QUALITY = "70"  # hevc_videotoolbox -q:v, from bench/hevc_quality.py (bench/RESULTS.md)
# Formats an encoder takes as a Y plane then one of Cb and Cr interleaved, in 16-bit words
# with the code in the top bits, and the planar format whose codes they carry.
SEMI_PLANAR = {"p010le": "yuv420p10le"}


@dataclass(frozen=True)
class Preset:
    name: str
    suffix: str
    video: tuple[str, ...]
    pix_fmt: str  # the planes handed to the encoder, which encodes them as they are
    chroma_location: str  # where the decimated chroma sits, written into the stream where it can be
    dither: bool
    audio: tuple[str, ...]
    expect_codec: str
    expect_profile: str
    loudness: float = -14.0  # integrated LUFS, music promos (07-audio.md)
    true_peak: float = -1.0  # dBTP ceiling (07-audio.md; EBU R 128-2023, m)
    alpha: bool = False  # the comp's coverage written as straight alpha, transparent where nothing is
    decodes_as: str | None = None  # the pixel format the file decodes as, when not `pix_fmt`


PRESETS = {
    "master-prores-hq": Preset(
        "master-prores-hq", ".mov", ("-c:v", "prores_ks", "-profile:v", "3"), "yuv422p10le", "left", False,
        ("-c:a", "pcm_s24le"), "prores", "HQ",
    ),
    "web-h264": Preset(
        "web-h264", ".mp4",
        ("-c:v", "libx264", "-crf", "18", "-profile:v", "high", "-chroma_sample_location", "left",
         "-movflags", "+faststart"),
        "yuv420p", "left", True, ("-c:a", "aac_at", "-b:a", "256k"), "h264", "High",
    ),
    # HEVC Main 10 through VideoToolbox (08-deliver.md), which takes 4:2:0 10-bit as P010.
    # `-q:v` is Apple's constant-quality scale, undocumented in its meaning: the setting is
    # the lowest that keeps web-h264's PSNR and grain on the check comp (bench/RESULTS.md,
    # *proposed*). No dither: ten bits do not band where eight do. HEVC's default chroma
    # siting, with no VUI saying otherwise, is the co-sited left the planes are decimated to.
    "web-hevc10": Preset(
        "web-hevc10", ".mp4",
        ("-c:v", "hevc_videotoolbox", "-profile:v", "main10", "-q:v", HEVC_QUALITY, "-tag:v", "hvc1",
         "-movflags", "+faststart"),
        "p010le", "left", False, ("-c:a", "aac_at", "-b:a", "256k"), "hevc", "Main 10", decodes_as="yuv420p10le",
    ),
    # For graphics handed to another tool (08-deliver.md): 4:4:4, so no chroma is decimated
    # and the siting is moot; straight alpha, as `lungecuta graphic` writes ProRes 4444 (whether
    # Resolve reads it as straight is one of the Phase 0 checks); no audio.
    "prores-4444-alpha": Preset(
        "prores-4444-alpha", ".mov", ("-c:v", "prores_ks", "-profile:v", "4"), "yuva444p10le", "left", False,
        (), "prores", "4444", alpha=True, decodes_as="yuva444p12le",  # ffmpeg's ProRes decoder gives 12 bits
    ),
}


def ycbcr(rgb: np.ndarray, *, bits: int, matrix: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """R'G'B' in [0, 1] (HxWx3) as limited-range Y'CbCr codes, unrounded: ITU-T H.273 V4
    equations 45 to 47 and 30 to 32, the inverse of `decode.to_rgb`."""
    kr, kb = KR_KB[matrix]
    r, g, b = (rgb[..., k] for k in range(3))
    e_y = np.float32(kr) * r + np.float32(1 - kr - kb) * g + np.float32(kb) * b
    pb = np.float32(0.5 / (1 - kb)) * (b - e_y)
    pr = np.float32(0.5 / (1 - kr)) * (r - e_y)
    step = np.float32(1 << (bits - 8))
    return step * (219 * e_y + 16), step * (224 * pb + 128), step * (224 * pr + 128)


def _pairs_cosited(plane: np.ndarray, axis: int) -> np.ndarray:
    """Half the samples along `axis`, each on an even sample: [1, 2, 1] / 4 around it, the
    edge sample repeated (*proposed*, as for the 4:2:2 proxy candidates)."""
    moved = np.moveaxis(plane, axis, -1)
    padded = np.concatenate([moved[..., :1], moved, moved[..., -1:]], axis=-1)
    out = (padded[..., 0:-2:2] + 2 * padded[..., 1:-1:2] + padded[..., 2::2]) / 4
    return np.moveaxis(out, -1, axis)


def _pairs_between(plane: np.ndarray, axis: int) -> np.ndarray:
    """Half the samples along `axis`, each halfway between two: their mean."""
    moved = np.moveaxis(plane, axis, -1)
    return np.moveaxis((moved[..., 0::2] + moved[..., 1::2]) / 2, -1, axis)


def decimate(plane: np.ndarray, layout: Layout, location: str) -> np.ndarray:
    """A full-resolution chroma plane at the layout's resolution, the samples placed where
    `location` (H.273 Table 8 by ffprobe's name) says they sit; supported: horizontally
    co-sited (`left`) for 4:2:2 and 4:2:0, vertically between rows (`left`) for 4:2:0."""
    assert location == "left", f"chroma decimation for {location!r} is not written yet"
    out = plane
    if layout.sub_x == 2:
        out = _pairs_cosited(out, axis=1)
    if layout.sub_y == 2:
        out = _pairs_between(out, axis=0)
    return out


def quantize(plane: np.ndarray, bits: int, rng: np.random.Generator | None) -> np.ndarray:
    """Codes rounded half up to integers, after triangular dither of one code either way
    when `rng` is given (zero mean, so it adds no bias; *proposed*)."""
    if rng is not None:
        plane = plane + (rng.random(plane.shape, dtype=np.float32) + rng.random(plane.shape, dtype=np.float32) - 1)
    codes = np.clip(np.floor(plane + np.float32(0.5)), 0, (1 << bits) - 1)
    return codes.astype(np.uint8 if bits == 8 else "<u2")


def frame_bytes(rgb: np.ndarray, preset: Preset, *, seed: int) -> tuple[bytes, float]:
    """One display-encoded frame as the encoder's raw planes, and the share of its pixels
    that had a channel outside [0, 1] and were clipped into it (reported, not hidden)."""
    layout = Layout.of(SEMI_PLANAR.get(preset.pix_fmt, preset.pix_fmt))
    assert layout.alpha == preset.alpha and rgb.shape[2] == 3 + int(preset.alpha), (
        f"{preset.name} takes {'RGBA' if preset.alpha else 'RGB'} frames"
    )
    alpha = rgb[..., 3] if preset.alpha else None
    rgb = rgb[..., :3]
    outside = float(((rgb < 0) | (rgb > 1)).any(axis=-1).mean())
    rgb = np.clip(rgb, 0, 1)
    y, cb, cr = ycbcr(rgb, bits=layout.bits, matrix="bt709")
    if layout.sub_x > 1 or layout.sub_y > 1:
        cb, cr = decimate(cb, layout, preset.chroma_location), decimate(cr, layout, preset.chroma_location)
    rng = np.random.default_rng(seed) if preset.dither else None
    planes = [y, cb, cr]
    if alpha is not None:  # alpha is full range: 0 to the largest code
        planes.append(alpha * np.float32((1 << layout.bits) - 1))
    codes = [quantize(plane, layout.bits, rng) for plane in planes]
    if preset.pix_fmt in SEMI_PLANAR:
        y, cb, cr = (plane << (16 - layout.bits) for plane in codes)
        return y.tobytes() + np.stack([cb, cr], axis=-1).tobytes(), outside
    return b"".join(plane.tobytes() for plane in codes), outside
