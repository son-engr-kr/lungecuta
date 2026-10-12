# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy"]
# ///
"""What YCbCr matrix does ffmpeg assume for a clip with no colour tags?

Usage: uv run bench/matrix_check.py CLIP [SECONDS]

Decodes one frame to rgb48 with ffmpeg's default and with each matrix forced, and prints
the largest difference between the default and each. On an untagged clip the default is
whichever forced matrix it matches exactly.
"""
import subprocess
import sys

import numpy as np

src = sys.argv[1]
at = sys.argv[2] if len(sys.argv) > 2 else "10"
out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                      "stream=width,height,color_space,color_range", "-of", "default=nw=1", src],
                     capture_output=True, text=True, check=True).stdout
probe = dict(line.split("=", 1) for line in out.strip().splitlines())
w, h = int(probe["width"]), int(probe["height"])
print(f"{src}: {w}x{h}, color_space={probe['color_space']}, color_range={probe['color_range']}")


def frame(matrix: str | None) -> np.ndarray:
    vf = "scale=out_range=full" if matrix is None else f"scale=in_color_matrix={matrix}:out_range=full"
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", at, "-i", src, "-frames:v", "1",
                          "-vf", f"{vf},format=rgb48le", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    assert len(raw) == w * h * 6, f"expected one {w}x{h} rgb48 frame, got {len(raw)} bytes"
    return np.frombuffer(raw, np.uint16).reshape(h, w, 3).astype(np.float64) / 65535


default = frame(None)
for m in ("bt601", "bt709", "bt2020"):
    d = np.abs(default - frame(m)).max()
    print(f"default vs {m:6s}: max |diff| = {d:.5f} of full scale ({d * 1023:.1f} ten-bit codes)")
