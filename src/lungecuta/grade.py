"""Clip grades (docs/design/04-color.md, Grading): a footage layer's operations, in order,
on its frame in the working space, after the input transform and before the layer is
placed: OCIO's primary grade, the ASC CDL and LUTs with declared spaces, as one list or as
a graph of serial nodes, each keyed by an optional qualifier and window and mixed over its
input (M4's start; tracked windows and parallel nodes are not written yet).

The primary grade is OCIO's GradingPrimaryTransform in its LOG style, whose controls OCIO
2.6.0 defines on code values (`GradingPrimary.cpp`, `GradingPrimaryOpCPU.cpp`, measured in
`tests/test_grade.py`): brightness adds 6.25/1023 a unit, contrast turns around the code
value 0.5 + 0.5 pivot, gamma raises code values between the black and white pivots (0 and
1) to 1/gamma, and saturation mixes towards a luma with Rec.709 weights. OCIO's default
pivot, -0.2, puts the turn at 0.4, near ACEScct's grey; lungecuta states its controls in
the working space's code values instead (`offset` added, `pivot` the turning value) and
defaults the pivot to the working space's encoding of 18 % grey, 0.336043 in DaVinci
Intermediate (Blackmagic's DI curve, `docs/research/color-sources.md`).
"""
from __future__ import annotations

import functools
import json
from pathlib import Path

import numpy as np
import PyOpenColorIO as ocio

from . import color
from .geometry import mask_alpha
from .schema.comp import Cdl, GradeGraph, Lut, Node, Primary

BRIGHTNESS_UNIT = 6.25 / 1023  # what one unit of OCIO's LOG brightness adds (GradingPrimary.cpp)
GREY = 0.18


@functools.cache
def grey(config_name: str, linear: str, working: str) -> float:
    """The working space's code value for 18 % grey in its linear space."""
    pixel = np.full((1, 1, 3), GREY, np.float32)
    color.cpu_processor(config_name, linear, working).applyRGB(pixel)
    assert np.ptp(pixel) < 1e-6, f"18 % grey in {linear} is not neutral in {working}: {pixel.ravel()}"
    return float(pixel[0, 0, 0])


def _rgbm(value: float | tuple[float, float, float], neutral: float) -> ocio.GradingRGBM:
    """A lungecuta value as OCIO's red, green, blue and master: a single number is the
    master, three are the channels with the master left neutral."""
    if isinstance(value, (int, float)):
        return ocio.GradingRGBM(neutral, neutral, neutral, float(value))
    return ocio.GradingRGBM(*(float(v) for v in value), neutral)


def _primary(op: Primary, pivot: float) -> ocio.Transform:
    values = ocio.GradingPrimary(ocio.GRADING_LOG)
    if isinstance(op.offset, (int, float)):
        values.brightness = ocio.GradingRGBM(0, 0, 0, op.offset / BRIGHTNESS_UNIT)
    else:
        values.brightness = ocio.GradingRGBM(*(v / BRIGHTNESS_UNIT for v in op.offset), 0)
    values.contrast = _rgbm(op.contrast, 1.0)
    values.gamma = _rgbm(op.gamma, 1.0)
    values.pivot = 2 * (pivot if op.pivot is None else op.pivot) - 1
    values.saturation = op.saturation
    if op.clamp is not None:
        values.clampBlack, values.clampWhite = op.clamp
    values.validate(ocio.GRADING_LOG)
    return ocio.GradingPrimaryTransform(values, ocio.GRADING_LOG, False)


def _cdl(op: Cdl) -> ocio.Transform:
    transform = ocio.CDLTransform()
    transform.setSlope(list(op.slope))
    transform.setOffset(list(op.offset))
    transform.setPower(list(op.power))
    transform.setSat(op.saturation)
    transform.setStyle(ocio.CDL_ASC if op.clamp else ocio.CDL_NO_CLAMP)
    return transform


def _lut(op: Lut, project_dir: Path, cfg: ocio.Config, working: str) -> list[ocio.Transform]:
    path = project_dir / op.path
    for space in (op.space, op.out):
        assert cfg.getColorSpace(space) is not None, f"{space!r} is not a colour space of the project's config"
    return [ocio.ColorSpaceTransform(src=working, dst=op.space),
            ocio.FileTransform(src=str(path), interpolation=ocio.INTERP_TETRAHEDRAL),
            ocio.ColorSpaceTransform(src=op.out, dst=working)]


@functools.cache
def _processor(config_name: str, linear: str, working: str, project_dir: str, ops_json: str,
               stamp: tuple) -> ocio.CPUProcessor | None:
    from pydantic import TypeAdapter

    from .schema.comp import GradeOp

    ops = TypeAdapter(list[GradeOp]).validate_json(ops_json)
    if not ops:
        return None
    cfg = color.config(config_name)
    group = ocio.GroupTransform()
    for op in ops:
        if isinstance(op, Primary):
            group.appendTransform(_primary(op, grey(config_name, linear, working)))
        elif isinstance(op, Cdl):
            group.appendTransform(_cdl(op))
        else:
            for transform in _lut(op, Path(project_dir), cfg, working):
                group.appendTransform(transform)
    return cfg.getProcessor(group).getDefaultCPUProcessor()


def processor(project, ops: list) -> ocio.CPUProcessor | None:
    """The CPU processor for a layer's grade, or None for no grade. Cached by the
    operations and by each LUT file's modification time."""
    settings = project.file.color
    for op in ops:
        if isinstance(op, Lut):
            assert (project.directory / op.path).is_file(), f"{op.path} is missing"
    stamp = tuple((op.path, (project.directory / op.path).stat().st_mtime_ns) for op in ops if isinstance(op, Lut))
    ops_json = json.dumps([op.model_dump(mode="json", by_alias=True) for op in ops], sort_keys=True)
    return _processor(settings.config, settings.linear, settings.working, str(project.directory), ops_json, stamp)


def nodes(grade) -> list[Node]:
    """A layer's grade as nodes: a list of operations is one node over the whole frame."""
    if isinstance(grade, GradeGraph):
        return list(grade.nodes)
    return [Node(ops=list(grade))] if grade else []


def look(project, name: str):
    """The grade of look `name`, a style in styles/ with a grade."""
    from .comp import load_style

    style = load_style(project, name)
    assert style.grade, f"style {name} is not a look: it has no grade"
    return style.grade


def looks(project, grade) -> list[str]:
    """The looks a grade's nodes name, in order."""
    return [node.look for node in nodes(grade) if node.look is not None]


def operations(project, grade) -> list:
    """Every operation of a grade, in order, its nodes' and their looks' included."""
    out = []
    for node in nodes(grade):
        out += operations(project, look(project, node.look)) if node.look is not None else node.ops
    return out


@functools.cache
def _hsy() -> ocio.CPUProcessor:
    """OCIO's RGB to HSY in its LOG style, the conversion its hue curves use."""
    transform = ocio.FixedFunctionTransform(ocio.FIXED_FUNCTION_RGB_TO_HSY_LOG)
    return ocio.Config.CreateRaw().getProcessor(transform).getDefaultCPUProcessor()


def _ramp(value: np.ndarray, low: float, high: float, soft: float) -> np.ndarray:
    """1 inside [low, high], falling linearly to 0 over `soft` beyond each edge
    (D15: the shape of a qualifier's softness is not taken from Resolve)."""
    if soft == 0:
        return ((value >= low) & (value <= high)).astype(np.float32)
    return np.clip(np.minimum(value - (low - soft), (high + soft) - value) / soft, 0, 1).astype(np.float32)


def qualifier_key(key, frame: np.ndarray, threads: int | None = None) -> np.ndarray:
    """A qualifier's key (HxWx1) on a working-space frame: the product of its hue,
    saturation and luma ranges. A grey has hue 0 (magenta) in OCIO's definition, so a hue
    range near 0 wants a saturation range beside it to leave greys out."""
    hsy = np.ascontiguousarray(frame.copy())
    color.apply(_hsy(), hsy, threads)
    out = np.ones(frame.shape[:2], np.float32)
    if key.hue is not None:
        offset = np.abs(hsy[..., 0] - key.hue.centre)
        distance = np.minimum(offset, 1 - offset)  # hue goes round
        out *= _ramp(distance, 0, key.hue.width / 2, key.hue.soft)
    if key.sat is not None:
        out *= _ramp(hsy[..., 1], key.sat.low, key.sat.high, key.sat.soft)
    if key.luma is not None:
        out *= _ramp(hsy[..., 2], key.luma.low, key.luma.high, key.luma.soft)
    if key.invert:
        out = 1 - out
    return out[..., None]


def apply(project, grade, frame: np.ndarray, threads: int | None = None) -> np.ndarray:
    """`frame` (working space, float32 HxWx3, the clip's frame as shown) through its
    grade: node by node, each node's operations applied, keyed by its qualifier and its
    window and mixed over its input, `input + mix * key * window * (graded - input)`. A
    look's node grades its input with the look's whole grade, as one node."""
    for node in nodes(grade):
        frame = np.ascontiguousarray(frame)
        if node.look is not None:
            out = apply(project, look(project, node.look), frame.copy(), threads)
            if not node.window and node.key is None and node.mix == 1:
                frame = out
                continue
        else:
            graded = processor(project, node.ops)
            if graded is None:
                continue
            if not node.window and node.key is None and node.mix == 1:
                color.apply(graded, frame, threads)
                continue
            out = frame.copy()
            color.apply(graded, out, threads)
        key = np.float32(node.mix)
        if node.key is not None:  # keyed on the node's input
            key = key * qualifier_key(node.key, frame, threads)
        if node.window:
            key = key * mask_alpha(node.window, frame.shape[1::-1])
        frame = np.ascontiguousarray(frame + key * (out - frame), dtype=np.float32)
    return frame
