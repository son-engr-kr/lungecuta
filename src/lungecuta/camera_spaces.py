"""Camera colour spaces lungecuta adds to the OpenColorIO config, for cameras whose maker
publishes a definition the built-in config does not carry yet (docs/design/04-color.md,
Input camera spaces).

DJI D-Log2 / D-Gamut2 (Osmo Pocket 4P): DJI has published no white paper; its definition is
DJI's own ACES input transform, `DLog2_DGamut2_to_ACES_AP0_cat02.dctl` and
`Dlog2_DGamut2_to_ACES_AP0_cat02.ctl` (ACEStransformID `IDT.DJI.DLog2_DGamut2.a1.v1`), in
`aces_workflow-dlog-2.zip` on DJI's CDN, listed on the Osmo Pocket 4P download pages of
DJI's Chinese and Hong Kong sites on 2026-06-30 (docs/research/color-sources.md, 4b). The
constants below are DJI's, digit for digit; the gamut matrix is built from DJI's primaries
by the CTL's method (`calculate_rgb_to_rgb_matrix` with CAT02), which reproduces the
built-in config's own D-Gamut matrix to 1e-15 (bench/dji_check.py).

The curve has three pieces (a linear toe, a log segment up to 18 % grey, and an exponential
of another slope above it), which no single OCIO transform expresses, so it enters OCIO as
two 1D LUTs over the half-float domain, one each way, interpolated: within 4.2e-6 of
DJI's formula relative to the value on the code range (bench/dji_check.py).
"""
from __future__ import annotations

import numpy as np
import PyOpenColorIO as ocio

# DJI's DCTL and CTL, `dlog2_to_lin`: the signal x at or above B1 is H (2^(A x) - 1) / (2^A - 1);
# from B2 to B1 it is 0.18 * 2^((x - B1) / K1); below B2 the line (x - B2) / K2 + L2.
H = 475.0
A = 16.285770761945304
K1 = 0.059439938321493
B1 = 0.304985337243402  # = 312/1023 to 2e-16: 18 % grey at ten-bit code 312 of the signal
K2 = 2.960935245492250
B2 = 0.148314799066323
L1 = 0.18
L2 = 0.028961695254132

# CIE xy of D-Gamut2's primaries and white, from DJI's CTL (`DGamut2_PRI`).
D_GAMUT2 = ((0.7347, 0.2653), (0.1600, 0.8400), (0.0900, -0.0800), (0.3127, 0.3290))
# ACES AP0 and its white (SMPTE ST 2065-1), the config's reference space.
AP0 = ((0.7347, 0.2653), (0.0000, 1.0000), (0.0001, -0.0770), (0.32168, 0.33767))
# CIECAM02's cone response matrix (CAT02), as the ACES CTL library's CONE_RESP_MAT_CAT02
# (which stores it transposed, for row vectors).
CAT02 = np.array([[0.7328, 0.4296, -0.1624], [-0.7036, 1.6975, 0.0061], [0.0030, 0.0136, 0.9834]])

REFERENCE = "ACES2065-1"
DLOG2 = "D-Log2 D-Gamut2"
LINEAR_DGAMUT2 = "Linear D-Gamut2"


def dlog2_to_linear(x: np.ndarray) -> np.ndarray:
    """DJI's D-Log2 decoding, the signal (0 to 1, as Resolve gives a clip to a DCTL) to
    scene-linear D-Gamut2, in float64."""
    x = np.asarray(x, dtype=np.float64)
    with np.errstate(over="ignore"):
        top = H / (2.0 ** A - 1.0) * (2.0 ** (A * x) - 1.0)
        middle = 2.0 ** ((x - B1) / K1 + np.log2(L1))
    return np.where(x >= B1, top, np.where(x >= B2, middle, (x - B2) / K2 + L2))


def linear_to_dlog2(y: np.ndarray) -> np.ndarray:
    """The inverse of `dlog2_to_linear`, each piece solved for x, in float64."""
    y = np.asarray(y, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        top = np.log2(y * (2.0 ** A - 1.0) / H + 1.0) / A
        middle = K1 * (np.log2(y) - np.log2(L1)) + B1
    return np.where(y >= L1, top, np.where(y >= L2, middle, (y - L2) * K2 + B2))


def _xyz(xy: tuple[float, float]) -> np.ndarray:
    x, y = xy
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def rgb_to_xyz(primaries) -> np.ndarray:
    """The RGB to XYZ matrix (column vectors) of primaries and white given as CIE xy, the
    white at Y = 1 (SMPTE RP 177)."""
    red, green, blue, white = primaries
    columns = np.stack([_xyz(red), _xyz(green), _xyz(blue)], axis=1)
    return columns * np.linalg.solve(columns, _xyz(white))


def rgb_to_rgb(source, target, cone: np.ndarray = CAT02) -> np.ndarray:
    """From one RGB space to another (column vectors), the whites adapted by von Kries in
    the `cone` space: the ACES CTL library's `calculate_rgb_to_rgb_matrix`."""
    s, t = cone @ _xyz(source[3]), cone @ _xyz(target[3])
    adapt = np.linalg.inv(cone) @ np.diag(t / s) @ cone
    return np.linalg.inv(rgb_to_xyz(target)) @ adapt @ rgb_to_xyz(source)


# Every value of the half-float domain, the LUTs' input.
_HALVES = np.arange(1 << 16, dtype=np.uint16).view(np.float16)
_FINITE = np.isfinite(_HALVES)
_FLOAT32_MAX = float(np.finfo(np.float32).max)


def half_domain_lut(function) -> ocio.Lut1DTransform:
    """`function` sampled at every half float, as an OCIO 1D LUT over the half domain,
    which OCIO interpolates between neighbouring halves; its input therefore ends at
    half's largest value, 65504 (x = 1.44 of D-Log2's signal, 18.5 stops over grey).

    OCIO 2.6.0 keys its processor cache on a transform's description, which for an in-memory
    1D LUT gives its length and the smallest and largest entry, not the data: two such LUTs
    alike in those are applied as one (bench/dji_check.py). lungecuta asks for these LUTs'
    processors only by colour space names, which the key holds; and the entries are kept
    finite (a NaN makes both extremes NaN): the infinities take the entry of the largest
    half of their sign, a value past float32's range float32's largest, the NaN codes 0. No
    decoded value comes near them: a ten-bit Y'CbCr code decodes to R'G'B' between -1.2 and 2.2."""
    values = np.zeros(_HALVES.size, dtype=np.float64)
    with np.errstate(over="ignore"):
        values[_FINITE] = np.clip(function(_HALVES[_FINITE].astype(np.float64)), -_FLOAT32_MAX, _FLOAT32_MAX)
    edge = float(np.finfo(np.float16).max)
    values[np.isposinf(_HALVES)] = values[_HALVES == edge][0]
    values[np.isneginf(_HALVES)] = values[_HALVES == -edge][0]
    assert np.isfinite(values).all(), "a LUT entry is not finite"
    lut = ocio.Lut1DTransform()
    lut.setLength(values.size)
    lut.setInputHalfDomain(True)
    lut.setOutputRawHalfs(False)
    lut.setData(np.repeat(values.astype(np.float32), 3))
    return lut


def _matrix(m: np.ndarray) -> ocio.MatrixTransform:
    m44 = np.eye(4)
    m44[:3, :3] = m
    return ocio.MatrixTransform(matrix=[float(v) for v in m44.ravel()])


def add_spaces(config: ocio.Config) -> None:
    """Add the colour spaces above to `config`, whose reference must be ACES2065-1. A space
    the config has already, under any of its names, is an error: the config's own
    definition would then have to be compared with DJI's before either is used."""
    reference = config.getColorSpace(REFERENCE)
    assert reference is not None and reference.getTransform(ocio.COLORSPACE_DIR_TO_REFERENCE) is None, (
        f"the config's reference is not {REFERENCE}")
    for name in (DLOG2, LINEAR_DGAMUT2):
        assert config.getColorSpace(name) is None, f"the config defines {name!r} itself; compare it with DJI's"
    to_ap0 = rgb_to_rgb(D_GAMUT2, AP0)

    linear = ocio.ColorSpace(ocio.REFERENCE_SPACE_SCENE)
    linear.setName(LINEAR_DGAMUT2)
    linear.setFamily("Input/DJI")
    linear.setEncoding("scene-linear")
    linear.setDescription("Convert Linear DJI D-Gamut2 to ACES2065-1 (added by lungecuta from DJI's "
                          "DLog2_DGamut2_to_ACES_AP0_cat02 CTL)")
    linear.setTransform(_matrix(to_ap0), ocio.COLORSPACE_DIR_TO_REFERENCE)
    config.addColorSpace(linear)

    log = ocio.ColorSpace(ocio.REFERENCE_SPACE_SCENE)
    log.setName(DLOG2)
    log.setFamily("Input/DJI")
    log.setEncoding("log")
    log.setDescription("Convert DJI D-Log2 - D-Gamut2 to ACES2065-1 (added by lungecuta from DJI's "
                       "DLog2_DGamut2_to_ACES_AP0_cat02 DCTL and CTL)")
    log.setTransform(ocio.GroupTransform([half_domain_lut(dlog2_to_linear), _matrix(to_ap0)]),
                     ocio.COLORSPACE_DIR_TO_REFERENCE)
    log.setTransform(ocio.GroupTransform([_matrix(np.linalg.inv(to_ap0)), half_domain_lut(linear_to_dlog2)]),
                     ocio.COLORSPACE_DIR_FROM_REFERENCE)
    config.addColorSpace(log)
