"""DJI D-Log2 / D-Gamut2 and D-Log / D-Gamut as lungecuta applies them, checked against DJI's
own transforms, and where DJI clips put black and their clip (docs/design/04-color.md).

Usage: uv run python bench/dji_check.py [--dctl DLog2.dctl] [--dlog-dctl DLog.dctl] [CLIP ...]

  --dctl       DJI's DLog2_DGamut2_to_ACES_AP0_cat02.dctl: its constants are compared with
               src/lungecuta/camera_spaces.py, digit for digit
  --dlog-dctl  DJI's DLog_DGamut_to_ACES_AP0_cat02_v1.dctl (the Pocket 4P's D-Log workflow):
               its formula is compared with OCIO's `D-Log D-Gamut`
  CLIP         DJI clips: the luma histogram's floor and top over every keyframe, native
               ten-bit codes, and what DJI's curve makes of them read as limited or full range
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

import numpy as np
import PyOpenColorIO as ocio

from lungecuta import camera_spaces as cs
from lungecuta import color
from lungecuta.project import DEFAULT_COLOR
from lungecuta.tools import tool

CONFIG = DEFAULT_COLOR.config
cfg = color.config(CONFIG)


def matrix_of(space: str) -> np.ndarray:
    group = cfg.getColorSpace(space).getTransform(ocio.COLORSPACE_DIR_TO_REFERENCE)
    matrices = [t for t in group if isinstance(t, ocio.MatrixTransform)]
    assert len(matrices) == 1
    return np.array(matrices[0].getMatrix()).reshape(4, 4)[:3, :3]


def apply(source: str, target: str, values: np.ndarray) -> np.ndarray:
    image = np.repeat(values.astype(np.float32)[:, None], 3, axis=1).copy()
    color.cpu_processor(CONFIG, source, target).applyRGB(image)
    return image


def curve() -> None:
    print("## D-Log2 curve (DJI's constants)")
    e = 1e-12
    for name, at in (("toe | log", cs.B2), ("log | top", cs.B1)):
        left, right = cs.dlog2_to_linear(at - e), cs.dlog2_to_linear(at + e)
        print(f"  {name} at x = {at:.6f} (code {at * 1023:.3f}): {float(left):.12f} | {float(right):.12f}")
    slope_log = cs.K1 / (cs.L2 * np.log(2))  # dx/dlin of the log piece at the toe's break
    print(f"  toe slope K2 {cs.K2:.6f}, log piece's slope at the break {slope_log:.6f}")
    print(f"  18 % grey at x * 1023 = {float(cs.linear_to_dlog2(0.18)) * 1023:.12f}")
    print(f"  black (linear 0) at x * 1023 = {float(cs.linear_to_dlog2(0.0)) * 1023:.12f}")
    print(f"  x = 1 decodes to {float(cs.dlog2_to_linear(1.0)):.6f} = H")


def matrices(dctl: Path | None) -> None:
    print("## Gamut matrices")
    dgamut = ((0.71, 0.31), (0.21, 0.88), (0.09, -0.08), (0.3127, 0.3290))
    dwg = ((0.8000, 0.3130), (0.1682, 0.9877), (0.0790, -0.1155), (0.3127, 0.3290))
    print(f"  rgb_to_rgb(D-Gamut, AP0) against OCIO's Linear D-Gamut: "
          f"{np.abs(cs.rgb_to_rgb(dgamut, cs.AP0) - matrix_of('Linear D-Gamut')).max():.2e}")
    print(f"  rgb_to_rgb(DaVinci WG, AP0) against OCIO's Linear DaVinci WideGamut: "
          f"{np.abs(cs.rgb_to_rgb(dwg, cs.AP0) - matrix_of('Linear DaVinci WideGamut')).max():.2e}")
    m = cs.rgb_to_rgb(cs.D_GAMUT2, cs.AP0)
    direct = np.linalg.inv(cs.rgb_to_xyz(dwg)) @ cs.rgb_to_xyz(cs.D_GAMUT2)
    print(f"  D-Gamut2 -> DWG through AP0 (CAT02 both ways) against D65 to D65 with no adaptation: "
          f"{np.abs(np.linalg.inv(matrix_of('Linear DaVinci WideGamut')) @ m - direct).max():.2e}")
    print("  D-Gamut2 -> AP0:", np.array2string(m, precision=6, suppress_small=True).replace("\n", ""))
    print("  D-Gamut2 primaries in linear DWG (columns R G B):",
          np.array2string(direct, precision=4, suppress_small=True).replace("\n", ""))
    if dctl is not None:
        text = dctl.read_text()
        rows = {}
        for name in ("dgamut2_to_xyz", "d65_to_d60_cat02", "xyz_to_acesAP0"):
            body = re.search(rf"#define {name} make_mat3\((.*)\)\s*$", text, re.M).group(1)
            rows[name] = np.array([float(v) for v in re.findall(r"-?\d+\.\d+", body)]).reshape(3, 3)
        product = rows["xyz_to_acesAP0"] @ rows["d65_to_d60_cat02"] @ rows["dgamut2_to_xyz"]
        print(f"  against the DCTL's four-decimal matrices, multiplied: {np.abs(m - product).max():.2e}")


def constants(dctl: Path) -> None:
    print(f"## {dctl.name}: constants against camera_spaces.py")
    text = dctl.read_text()
    found = dict(re.findall(r"float (\w+) = (-?[\d.]+)f;", text))
    ours = {"H": cs.H, "a": cs.A, "k1": cs.K1, "b1": cs.B1, "k2": cs.K2, "b2": cs.B2, "in_limit1": cs.L1,
            "in_limit2": cs.L2, "in_limit_rev_1": cs.B1, "in_limit_rev_2": cs.B2}
    for name, value in ours.items():
        print(f"  {name}: DCTL {found[name]}  ours {value!r}  {'same' if float(found[name]) == value else 'DIFFERENT'}")


def error(got: np.ndarray, exact: np.ndarray) -> tuple[float, float]:
    """The largest error relative to the value where it is 0.001 or more, and the largest
    absolute error where it is less (around the toe's zero, a relative error says nothing)."""
    big = np.abs(exact) >= 1e-3
    return (float((np.abs(got - exact) / np.abs(exact))[big].max()),
            float(np.abs(got - exact)[~big].max()) if (~big).any() else 0.0)


def ocio_accuracy() -> None:
    print("## The D-Log2 colour space in OCIO against DJI's formula (both directions in one process)")
    x = np.linspace(-1.2, 2.2, 340001)
    lin = apply(cs.DLOG2, cs.LINEAR_DGAMUT2, x)[:, 0]
    exact = cs.dlog2_to_linear(x)
    for lo, hi in ((0.0, 1.0), (-1.2, 2.2)):
        inside = (x >= lo) & (x <= hi)
        rel, small = error(lin[inside], exact[inside])
        print(f"  x in [{lo}, {hi}] to linear: largest error {rel:.2e} of the value, {small:.2e} absolute "
              "where |linear| < 0.001")
    usable = x <= float(cs.linear_to_dlog2(65504.0))
    back = apply(cs.LINEAR_DGAMUT2, cs.DLOG2, exact[usable].astype(np.float32))[:, 0]
    print(f"  linear back to x, linear up to half's 65504 (x <= {float(cs.linear_to_dlog2(65504.0)):.3f}): "
          f"largest error {np.abs(back - x[usable]).max() * 1023:.2e} ten-bit codes")
    # into the working space: the LUT path against the exact curve then OCIO's linear path
    working = DEFAULT_COLOR.working
    codes = np.arange(0, 1024) / 1023.0
    via_lut = apply(cs.DLOG2, working, codes)
    via_exact = apply(cs.LINEAR_DGAMUT2, working, cs.dlog2_to_linear(codes))
    print(f"  grey ramp code 0 to 1023 into {working}: largest difference "
          f"{np.abs(via_lut - via_exact).max() * 1023:.2e} ten-bit codes")


def cache_ids() -> None:
    print("## OCIO's processor cache and in-memory 1D LUTs")

    def lut(values: np.ndarray) -> ocio.Lut1DTransform:
        t = ocio.Lut1DTransform()
        t.setLength(values.size)
        t.setData(np.repeat(values.astype(np.float32), 3))
        return t

    ramp = np.linspace(0.0, 1.0, 4096)
    a, b = lut(ramp), lut(ramp ** 2.2)  # two LUTs, the same length and extremes
    print(f"  description of a: {a}")
    print(f"  description of b: {b}")
    probe = np.array([[0.5, 0.5, 0.5]], np.float32)
    raw = ocio.Config.CreateRaw()
    pa, pb = raw.getProcessor(a), raw.getProcessor(b)
    out = probe.copy()
    pb.getDefaultCPUProcessor().applyRGB(out)
    print(f"  one config, a then b: b's processor is a's {pa is pb or pa.getCacheID() == pb.getCacheID()}; "
          f"b(0.5) gives {out[0, 0]:.4f} (b is 0.5^2.2 = {0.5 ** 2.2:.4f})")
    fresh = ocio.Config.CreateRaw()
    fresh.setProcessorCacheFlags(ocio.PROCESSOR_CACHE_OFF)
    out = probe.copy()
    fresh.getProcessor(b).getDefaultCPUProcessor().applyRGB(out)
    print(f"  the cache off: b(0.5) gives {out[0, 0]:.4f}")
    ours = (cs.half_domain_lut(cs.dlog2_to_linear), cs.half_domain_lut(cs.linear_to_dlog2))
    print(f"  camera_spaces' two LUTs: {ours[0]} | {ours[1]}")


def dlog_v1(dctl: Path | None) -> None:
    print("## D-Log: OCIO's `D-Log D-Gamut` against DJI's formula (white paper, and the Pocket 4P DCTL)")
    x = np.linspace(0.0, 1.0, 100001)
    paper = np.where(x <= 0.14, (x - 0.0929) / 6.025, (10 ** (3.89616 * x - 2.27752) - 0.0108) / 0.9892)
    lin = apply("D-Log D-Gamut", "Linear D-Gamut", x)[:, 0]
    rel, small = error(lin, paper)
    print(f"  x in [0, 1] to linear: largest difference {rel:.2e} of the value, {small:.2e} absolute where "
          f"|linear| < 0.001; absolute in the toe (x <= 0.14): {np.abs(lin - paper)[x <= 0.14].max():.2e}")
    working = DEFAULT_COLOR.working
    codes = np.arange(0, 1024) / 1023.0
    paper_codes = np.where(codes <= 0.14, (codes - 0.0929) / 6.025, (10 ** (3.89616 * codes - 2.27752) - 0.0108) / 0.9892)
    a = apply("D-Log D-Gamut", working, codes)
    b = apply("Linear D-Gamut", working, paper_codes)
    print(f"  grey ramp into {working}: largest difference {np.abs(a - b).max() * 1023:.3f} ten-bit codes")
    if dctl is not None:
        text = dctl.read_text()
        assert "(in - 0.0929f) / 6.025f" in text and "_exp10f(3.89616 * in - 2.27752f) - 0.0108f) / 0.9892f" in text
        body = re.search(r"#define dgamut_to_xyz make_mat3\((.*)\)\s*$", text, re.M).group(1)
        xyz = np.array([float(v) for v in re.findall(r"-?\d+\.\d+", body)]).reshape(3, 3)
        dgamut = ((0.71, 0.31), (0.21, 0.88), (0.09, -0.08), (0.3127, 0.3290))
        print(f"  {dctl.name}: the white paper's formula; its dgamut_to_xyz against D-Gamut's primaries: "
              f"{np.abs(xyz - cs.rgb_to_xyz(dgamut)).max():.2e}")


def keyframe_luma(path: Path) -> np.ndarray:
    """The luma histogram of every keyframe, native ten-bit codes."""
    probe = subprocess.run([tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,pix_fmt", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, check=True).stdout.split()[0].split(",")
    width, height, pix_fmt = int(probe[0]), int(probe[1]), probe[2]
    assert pix_fmt == "yuv420p10le", pix_fmt
    proc = subprocess.Popen([tool("ffmpeg"), "-v", "error", "-skip_frame", "nokey", "-i", str(path), "-map", "0:v:0",
                             "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"],
                            stdout=subprocess.PIPE)
    size = width * height * 3 // 2 * 2
    hist = np.zeros(1024, np.int64)
    frames = 0
    while chunk := proc.stdout.read(size):
        assert len(chunk) == size
        hist += np.bincount(np.frombuffer(chunk, np.uint16)[:width * height], minlength=1024)
        frames += 1
    assert proc.wait() == 0 and frames
    return hist


def clips(paths: list[Path]) -> None:
    print("## Clips: luma over every keyframe (ten-bit codes)")
    from lungecuta import dji

    for path in paths:
        found = dji.read(path)
        hist = keyframe_luma(path)
        cdf = np.cumsum(hist) / hist.sum()
        low, high = int(np.nonzero(hist)[0][0]), int(np.nonzero(hist)[0][-1])
        p001, p1, p50, p9999 = (int(np.searchsorted(cdf, q)) for q in (0.0001, 0.01, 0.5, 0.9999))
        mode = low + int(np.argmax(hist[low:low + 150]))
        print(f"  {found.tags.get('ColorGammaSxS')} {found.tags.get('LensType')}: min {low}, p0.01 {p001}, p1 {p1}, "
              f"median {p50}, p99.99 {p9999}, max {high}; most frequent code within 150 of the min {mode} "
              f"({hist[mode] / hist.sum() * 100:.1f} % of pixels)")
    print("  D-Log2 black (linear 0) read as limited range: code "
          f"{64 + float(cs.linear_to_dlog2(0.0)) * 876:.1f}; as full range: {float(cs.linear_to_dlog2(0.0)) * 1023:.1f}")
    print("  D-Log2 18 % grey: limited "
          f"{64 + float(cs.linear_to_dlog2(0.18)) * 876:.1f}; full {float(cs.linear_to_dlog2(0.18)) * 1023:.1f}")
    for code in (885, 900):
        lim, full = cs.dlog2_to_linear((code - 64) / 876), cs.dlog2_to_linear(code / 1023)
        print(f"  D-Log2 code {code}: limited {float(lim):.1f} ({np.log2(lim / 0.18):+.2f} stops over grey), "
              f"full {float(full):.1f} ({np.log2(full / 0.18):+.2f})")
    for code in (810,):
        paper = lambda v: (10 ** (3.89616 * v - 2.27752) - 0.0108) / 0.9892  # noqa: E731
        lim, full = paper((code - 64) / 876), paper(code / 1023)
        print(f"  D-Log code {code}: limited {lim:.2f} ({np.log2(lim / 0.18):+.2f} stops over grey), "
              f"full {full:.2f} ({np.log2(full / 0.18):+.2f}); black limited {64 + 0.0929 * 876:.1f}, full "
              f"{0.0929 * 1023:.1f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dctl", type=Path)
    parser.add_argument("--dlog-dctl", type=Path)
    parser.add_argument("clips", nargs="*", type=Path)
    args = parser.parse_args()
    print(f"OpenColorIO {ocio.__version__}, config {CONFIG}")
    curve()
    if args.dctl:
        constants(args.dctl)
    matrices(args.dctl)
    ocio_accuracy()
    cache_ids()
    dlog_v1(args.dlog_dctl)
    if args.clips:
        clips(args.clips)


if __name__ == "__main__":
    main()
