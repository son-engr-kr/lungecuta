"""ffmpeg's own Y'CbCr to R'G'B' (swscale, to rgb48le) against ITU-T H.273's equations.

Usage: uv run python bench/swscale_range.py
  (inside the project, so that the lungecuta package is importable)

For flat frames of known codes (black, mid grey, nominal white and the six colour bars at
100 %, in 8 and 10 bits, limited and full range, BT.709), converts with the scale filter
and the flags lc used before (`accurate_rnd+full_chroma_int+full_chroma_inp`, explicit
`in_color_matrix` and `in_range`) and with lc's `frame_rgb`, which follows H.273 V4
equations 30 to 38 and 45 to 47, and prints the difference in 10-bit R'G'B' codes
(value x 1023). The check: lc's conversion gives exactly 0 and 1 at black and white.
"""
import subprocess

import numpy as np

from lungecuta.decode import KR_KB, frame_rgb

W, H = 8, 2
SWS = "accurate_rnd+full_chroma_int+full_chroma_inp"


def codes_for(rgb, bits, range_, matrix="bt709"):
    kr, kb = KR_KB[matrix]
    r, g, b = rgb
    y = kr * r + (1 - kr - kb) * g + kb * b
    pb, pr = 0.5 * (b - y) / (1 - kb), 0.5 * (r - y) / (1 - kr)
    if range_ == "limited":
        step = 1 << (bits - 8)
        return [round(step * (219 * y + 16)), round(step * (224 * pb + 128)), round(step * (224 * pr + 128))]
    top, half = (1 << bits) - 1, 1 << (bits - 1)
    # Clip1C of equations 37 and 38: full-range red needs Cr = 2^n - 1 + 0.5, one past the top.
    return [min(top, max(0, round(v))) for v in (top * y, top * pb + half, top * pr + half)]


def frame(codes, bits):
    dtype = np.uint8 if bits == 8 else np.uint16
    return b"".join(np.full((H, W), c, dtype).tobytes() for c in codes)


def swscale(codes, bits, range_):
    fmt = "yuv444p" if bits == 8 else f"yuv444p{bits}le"
    raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", fmt, "-s", f"{W}x{H}", "-i", "-",
                          "-vf", f"scale=in_color_matrix=bt709:in_range={range_}:flags={SWS},format=rgb48le",
                          "-f", "rawvideo", "-"], input=frame(codes, bits), capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint16)[:3] / 65535


def ours(codes, bits, range_):
    fmt = "yuv444p" if bits == 8 else f"yuv444p{bits}le"
    return frame_rgb(frame(codes, bits), (W, H), pix_fmt=fmt, chroma_location="unspecified", matrix="bt709",
                     range_=range_)[0, 0].astype(np.float64)


def main() -> None:
    patches = {"black": (0, 0, 0), "grey 0.5": (0.5, 0.5, 0.5), "white": (1, 1, 1), "red": (1, 0, 0),
               "green": (0, 1, 0), "blue": (0, 0, 1), "yellow": (1, 1, 0), "cyan": (0, 1, 1), "magenta": (1, 0, 1)}
    for bits in (8, 10):
        for range_ in ("limited", "full"):
            print(f"{bits}-bit {range_}: R'G'B' x 1023, swscale and lc (H.273), for codes made from the patch")
            for name, rgb in patches.items():
                codes = codes_for(rgb, bits, range_)
                a, b = swscale(codes, bits, range_) * 1023, ours(codes, bits, range_) * 1023
                print(f"  {name:9s} codes {codes}  swscale {np.round(a, 2)}  lc {np.round(b, 2)}  "
                      f"swscale - lc {np.round(a - b, 2)}")
                if name == "black":
                    assert np.allclose(b, 0, atol=1e-3), "lc's black is not 0"
                if name == "white":
                    assert np.allclose(b, 1023, atol=1e-2), "lc's white is not 1023"


if __name__ == "__main__":
    main()
