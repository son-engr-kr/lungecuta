"""Which proxy can pass the parity test? Parity against the original, per codec and range.

Usage: uv run python bench/proxy_parity.py WORKDIR CLIP [CLIP...]
  (inside the project, so that the lungecuta package is importable)

For each clip and each candidate (ProRes Proxy, LT, 422 HQ and 4444; lossless FFV1 4:2:2
and 4:4:4; limited range, and full range for 4444 and FFV1 4:4:4) at 540 lines, builds the
proxy the way `lungecuta import` does and measures the parity test of
docs/design/02-media.md#proxies on the same eleven frames: the original decoded with its
own matrix and range and area-downscaled, against the proxy decoded with its own range.
Differences are in 10-bit DaVinci Intermediate codes when the clip's colour space is
decided, and always in the file's own R'G'B' codes, so a clip with no decided space (the
HLG sample) is still measured. Reported: p99, max, mean signed bias per channel, and p99
after averaging 4x4 blocks (level rather than noise). Also build speed, size, sequential
decode speed and the time of one seek including process start. The test passes at
p99 <= 2 DI codes.
"""
import sys
import time
from fractions import Fraction
from pathlib import Path

from lungecuta.decode import seek_seconds
from lungecuta.detect import RANGES, detect
from lungecuta.probe import probe
from lungecuta.proxy import PROFILES, build_proxy, parity, parity_frames, proxy_size
from lungecuta.schema.media import ColorDecided, ParityNumbers
from lungecuta.ticks import parse_rate
from lungecuta.tools import run

CONFIG = "studio-config-v5.0.0_aces-v2.1_ocio-v2.6"
CANDIDATES = [
    ("prores-proxy", "limited"),
    ("prores-lt", "limited"),
    ("prores-hq", "limited"),
    ("prores-4444", "limited"),
    ("prores-4444", "full"),
    ("ffv1-422", "limited"),
    ("ffv1-444", "limited"),
    ("ffv1-444", "full"),
]
SONY_MATRIX = {"rec709": "bt709", "rec2020ncl": "bt2020nc"}


def show(label: str, result: ParityNumbers) -> str:
    bias = " ".join(f"{b:+.2f}" for b in result.bias)
    return f"{label} p99 {result.p99:6.3f}  max {result.max:7.2f}  bias [{bias}]  blocks p99 {result.p99_blocks:.3f}"


work = Path(sys.argv[1])
work.mkdir(parents=True, exist_ok=True)
for clip in map(Path, sys.argv[2:]):
    probed = probe(clip)
    video = probed.video
    decision = detect(clip, probed).color
    # The R'G'B' comparison needs a matrix even for an undecided clip; it is read from the
    # sidecar here, for the measurement only.
    decided = isinstance(decision, ColorDecided)
    matrix = decision.matrix if decided else SONY_MATRIX[decision.evidence["CodingEquations"]]
    source_range = RANGES[video.tags.color_range]
    rate: Fraction = parse_rate(video.rate)
    size = proxy_size(video.size, 540)
    indices = parity_frames(video.frames)
    space = decision.space if decided else None
    print(f"{clip.name}: {video.size[0]}x{video.size[1]} {video.pix_fmt} {video.rate}, {video.frames} frames, "
          f"range {source_range}, matrix {matrix}, colour {space or 'undecided'}; frames {indices}")
    for profile, proxy_range in CANDIDATES:
        target = work / f"{clip.stem}.{profile}.{proxy_range}{PROFILES[profile].suffix}"
        target.unlink(missing_ok=True)
        t0 = time.perf_counter()
        build_proxy(clip, target, size=size, coded_size=video.size, source_pix_fmt=video.pix_fmt,
                    source_chroma_location=video.chroma_location, source_range=source_range, profile=profile,
                    proxy_range=proxy_range, frames=video.frames, rate=rate)
        build = video.frames / (time.perf_counter() - t0)
        t0 = time.perf_counter()
        run(["ffmpeg", "-v", "error", "-i", str(target), "-f", "null", "-"])
        decode = video.frames / (time.perf_counter() - t0)
        t0 = time.perf_counter()
        for index in indices:
            run(["ffmpeg", "-v", "error", "-ss", seek_seconds(index, rate), "-i", str(target),
                 "-frames:v", "1", "-f", "null", "-"])
        seek_ms = (time.perf_counter() - t0) / len(indices) * 1000
        common = dict(indices=indices, rate=rate, coded_size=video.size, size=size, matrix=matrix,
                      source_pix_fmt=video.pix_fmt, source_chroma_location=video.chroma_location,
                      proxy_pix_fmt=PROFILES[profile].decoded_pix_fmt,
                      proxy_chroma_location=PROFILES[profile].chroma_location,
                      source_range=source_range, proxy_range=proxy_range, config=CONFIG)
        print(f"  {profile:12s} {proxy_range:7s} build {build:5.1f} fps, {target.stat().st_size / 1e6:6.1f} MB, "
              f"decode {decode:6.1f} fps, seek {seek_ms:4.0f} ms")
        numbers = parity(clip, target, spaces=[None, space] if space else [None], **common)
        print("    " + show("R'G'B':", numbers[0]))
        if space:
            print("    " + show("DI:    ", numbers[1]) + ("  -> pass" if numbers[1].p99 <= 2 else "  -> FAIL"))
