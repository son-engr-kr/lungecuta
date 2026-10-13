# /// script
# requires-python = ">=3.12"
# dependencies = ["av==19.0.1", "numpy"]
# ///
"""Does PyAV's bundled FFmpeg decode exactly what the ffmpeg CLI decodes, and what does a
seek cost with a decoder that stays open?

Usage: uv run bench/pyav_check.py FILE [FILE...]

For each file: the framemd5 of every frame from the ffmpeg CLI (in the stream's own pixel
format, coded orientation) against the md5 of the same frame's planes from PyAV, decoded
sequentially; then 40 random frames reached by seeking an open container to the keyframe
at or before the frame and decoding forward to it, each checked against the CLI's md5 for
that index. A frame index's time is index / rate from the stream's start.
"""
import hashlib
import subprocess
import sys
import time
from fractions import Fraction

import av
import numpy as np

SEEKS = 40


def planes_md5(frame: av.VideoFrame, bytes_per_sample: int) -> str:
    """The frame's planes without line padding, as ffmpeg's framemd5 hashes rawvideo."""
    digest = hashlib.md5()
    for plane in frame.planes:
        rows = np.frombuffer(plane, np.uint8).reshape(plane.height, plane.line_size)
        digest.update(rows[:, :plane.width * bytes_per_sample].tobytes())
    return digest.hexdigest()


def check(path: str) -> None:
    lines = subprocess.run(["ffmpeg", "-v", "error", "-noautorotate", "-i", path, "-map", "0:v:0", "-f", "framemd5", "-"],
                           capture_output=True, text=True, check=True).stdout.splitlines()
    cli = [line.rsplit(",", 1)[-1].strip() for line in lines if line and not line.startswith("#")]
    with av.open(path) as container:
        stream = container.streams.video[0]
        name = stream.codec_context.pix_fmt
        width = 2 if "10" in name or "12" in name or "16" in name else 1
        start = time.perf_counter()
        ours = [planes_md5(frame, width) for frame in container.decode(stream)]
        rate = len(ours) / (time.perf_counter() - start)
    same = sum(a == b for a, b in zip(ours, cli))
    print(f"{path.rsplit('/', 1)[-1]}: {stream.codec_context.name} {name}, PyAV {av.__version__}; "
          f"{len(ours)} frames from PyAV, {len(cli)} from the CLI, {same} identical; sequential {rate:.0f} fps")
    assert len(ours) == len(cli) == same, "PyAV and the CLI disagree"
    with av.open(path) as container:
        stream = container.streams.video[0]
        frame_rate = Fraction(stream.average_rate)
        first = stream.start_time or 0
        times = []
        for index in np.random.default_rng(0).integers(0, len(cli), SEEKS):
            target = first + round(Fraction(int(index)) / frame_rate / stream.time_base)
            start = time.perf_counter()
            container.seek(target, stream=stream, backward=True, any_frame=False)
            frame = next(f for f in container.decode(stream) if f.pts is not None and f.pts >= target)
            times.append((time.perf_counter() - start) * 1000)
            assert planes_md5(frame, width) == cli[index], f"a seek to frame {index} landed elsewhere"
    print(f"  {SEEKS} seeks with the container open, each landing on its frame: median {np.median(times):.1f} ms, "
          f"p95 {np.percentile(times, 95):.1f} ms, max {max(times):.1f} ms")


if __name__ == "__main__":
    for path in sys.argv[1:]:
        check(path)
