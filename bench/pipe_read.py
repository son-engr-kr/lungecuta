# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy"]
# ///
"""Throughput of the final-render input path: ffmpeg decode -> rgb48le pipe -> float32 numpy.

Usage: uv run bench/pipe_read.py CLIP FRAMES sw|hw
  sw decodes in software, hw with -hwaccel videotoolbox. The clip must be 3840x2160.
"""

import subprocess
import sys
import time

import numpy as np

src, nframes, hwaccel = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "hw"
w, h = 3840, 2160
frame_bytes = w * h * 3 * 2

cmd = ["ffmpeg", "-v", "error"]
if hwaccel:
    cmd += ["-hwaccel", "videotoolbox"]
cmd += ["-i", src, "-frames:v", str(nframes), "-pix_fmt", "rgb48le", "-f", "rawvideo", "-"]
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=frame_bytes)

t0 = time.perf_counter()
got = 0
while True:
    buf = proc.stdout.read(frame_bytes)
    if len(buf) < frame_bytes:
        break
    frame = np.frombuffer(buf, dtype=np.uint16).reshape(h, w, 3).astype(np.float32) * (1.0 / 65535.0)
    got += 1
dt = time.perf_counter() - t0
assert proc.wait() == 0, "ffmpeg failed"
assert got == nframes, f"expected {nframes} frames, got {got}"
print(f"{got} frames in {dt:.1f} s = {got / dt:.1f} fps (rgb48le -> float32)")
