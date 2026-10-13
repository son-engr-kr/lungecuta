"""Frame geometry: the orientation a clip is shown in, and exact area downscaling."""
from __future__ import annotations

import math
from fractions import Fraction

import numpy as np
from scipy import sparse


def turns(rotation: int) -> int:
    """Quarter turns counterclockwise for a rotation tag: FFmpeg's display matrix rotates
    counterclockwise (`av_display_rotation_get`), and ffprobe prints that angle. Checked
    against ffmpeg's autorotate at 0, 90, 180 and -90 (tests/test_still.py) and on the HLG
    sample (a media test)."""
    assert rotation % 90 == 0, f"a rotation of {rotation} degrees is not a quarter turn"
    return (rotation // 90) % 4


def oriented(image: np.ndarray, rotation: int) -> np.ndarray:
    """An HxWx3 frame in the coded orientation turned the way the clip is shown."""
    k = turns(rotation)
    return np.ascontiguousarray(np.rot90(image, k)) if k else image


def shown_size(coded: tuple[int, int], rotation: int) -> tuple[int, int]:
    width, height = coded
    return (height, width) if turns(rotation) % 2 else (width, height)


def scaled_size(size: tuple[int, int], height: int) -> tuple[int, int]:
    """`size` at `height` lines with its shape kept; a width that is not whole is an error,
    not a rounding."""
    width, own_height = size
    scaled = Fraction(width * height, own_height)
    assert scaled.denominator == 1, (
        f"a {width}x{own_height} frame at {height} lines is {float(scaled):.2f} wide, not a whole number"
    )
    return int(scaled), height


def area_resize(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Average every output pixel over the input area it covers (exact box filter)."""
    height, width, channels = image.shape
    out_width, out_height = size
    rows = _area_weights(height, out_height)
    columns = _area_weights(width, out_width)
    tall = (rows @ image.reshape(height, width * channels)).reshape(out_height, width, channels)
    flat = tall.transpose(0, 2, 1).reshape(out_height * channels, width) @ columns.T
    return np.ascontiguousarray(flat.reshape(out_height, channels, out_width).transpose(0, 2, 1), dtype=np.float32)


def _area_weights(size_in: int, size_out: int) -> np.ndarray:
    assert size_out <= size_in, "area_resize only shrinks"
    weights = np.zeros((size_out, size_in), np.float32)
    scale = Fraction(size_in, size_out)
    for i in range(size_out):
        start, end = i * scale, (i + 1) * scale
        for j in range(int(start), -(-end.numerator // end.denominator)):
            overlap = min(end, j + 1) - max(start, Fraction(j))
            weights[i, j] = float(overlap / scale)
    return weights


def place_weights(size_in: int, size_out: int, scale: float, offset: float) -> tuple[sparse.csr_array, np.ndarray]:
    """One axis of a layer placed in a comp: output pixel i covers [i, i + 1), which is
    source [(i - offset) / scale, (i + 1 - offset) / scale). Shrinking (scale at most 1)
    averages the source over that interval exactly; enlarging interpolates linearly at the
    pixel's centre. Returns the weights (size_out x size_in, sparse: each output pixel takes
    a few source pixels), premultiplied by the share of each output pixel the source
    covers, and that share, which becomes the layer's alpha."""
    assert scale > 0, f"a scale of {scale} is not positive"
    edges = (np.arange(size_out + 1, dtype=np.float64) - offset) / scale
    low, high = edges[:-1], edges[1:]
    if scale <= 1:
        # the source cells an output pixel's interval can meet: from the one holding its
        # start, at most ceil(1 / scale) + 1 of them
        cells = np.floor(low)[:, None] + np.arange(math.ceil(1 / scale) + 2)[None, :]
        overlap = np.clip(np.minimum(high[:, None], cells + 1) - np.maximum(low[:, None], cells), 0, None)
        weights = overlap / (high - low)[:, None]
        rows = np.broadcast_to(np.arange(size_out)[:, None], cells.shape)
        keep = (cells >= 0) & (cells < size_in) & (overlap > 0)
        matrix = sparse.csr_array((weights[keep], (rows[keep], cells[keep].astype(np.int64))),
                                  shape=(size_out, size_in))
    else:
        centre = (low + high) / 2 - 0.5
        inside = (centre >= -0.5) & (centre <= size_in - 0.5)
        below = np.floor(centre)  # the edge pixel holds beyond the first and last centres
        left = np.clip(below, 0, size_in - 1).astype(np.int64)
        right = np.clip(below + 1, 0, size_in - 1).astype(np.int64)
        share = centre - below
        rows = np.nonzero(inside)[0]
        matrix = sparse.csr_array((np.concatenate([1 - share[rows], share[rows]]),
                                   (np.concatenate([rows, rows]), np.concatenate([left[rows], right[rows]]))),
                                  shape=(size_out, size_in))
    matrix.sum_duplicates()  # at an edge both neighbours are the edge pixel: its weights add
    covered = np.clip(np.minimum(high, size_in) - np.maximum(low, 0), 0, None) / (high - low)
    covered = np.clip(covered, 0, 1)
    if scale > 1:
        matrix.data *= np.repeat(covered, np.diff(matrix.indptr))
    return matrix.astype(np.float32), covered.astype(np.float32)


def place(frame: np.ndarray, comp: tuple[int, int], *, position: tuple[float, float], anchor: tuple[float, float],
          scale: tuple[float, float], rotation: float = 0.0,
          mask: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """A layer's frame (HxWx3) placed in a comp of `comp` (width, height): returns the
    comp-sized frame, premultiplied by its alpha, and the alpha (HxWx1), 0 where the layer
    does not reach. `rotation` is in degrees, clockwise on screen about the anchor, as in
    After Effects. `mask` (HxWx1, in the layer's own pixels) is the layer's own alpha,
    resampled with its colour."""
    if mask is not None:
        frame = np.concatenate([frame * mask, mask], axis=2)
    if rotation % 360 != 0:
        out, alpha = _rotated(frame, comp, position=position, anchor=anchor, scale=scale, rotation=rotation)
    else:
        out, alpha = _straight(frame, comp, position=position, anchor=anchor, scale=scale)
    if mask is None:
        return out, alpha
    # the mask came through as a fourth channel, already scaled by the geometric coverage
    return np.ascontiguousarray(out[..., :3]), np.ascontiguousarray(out[..., 3:])


def _straight(frame: np.ndarray, comp: tuple[int, int], *, position: tuple[float, float],
              anchor: tuple[float, float], scale: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    height, width, channels = frame.shape
    comp_w, comp_h = comp
    columns, cover_x = place_weights(width, comp_w, scale[0], position[0] * comp_w - anchor[0] * width * scale[0])
    rows, cover_y = place_weights(height, comp_h, scale[1], position[1] * comp_h - anchor[1] * height * scale[1])
    tall = (rows @ frame.reshape(height, width * channels)).reshape(comp_h, width, channels)
    wide = np.ascontiguousarray(tall.transpose(1, 0, 2)).reshape(width, comp_h * channels)
    out = (columns @ wide).reshape(comp_w, comp_h, channels).transpose(1, 0, 2)
    alpha = (cover_y[:, None] * cover_x[None, :])[..., None]
    return np.ascontiguousarray(out, dtype=np.float32), alpha.astype(np.float32)


def _rotated(frame: np.ndarray, comp: tuple[int, int], *, position: tuple[float, float], anchor: tuple[float, float],
             scale: tuple[float, float], rotation: float) -> tuple[np.ndarray, np.ndarray]:
    """A rotated placement: the frame scaled first with `place_weights` (exact averaging
    when it shrinks), then sampled bilinearly, premultiplied colour and coverage alike, at
    each comp pixel centre mapped back through the rotation about the anchor (D15:
    bilinear, so a rotation softens by up to half a pixel)."""
    from scipy.ndimage import map_coordinates

    height, width, channels = frame.shape
    scaled_w, scaled_h = max(1, math.ceil(width * scale[0])), max(1, math.ceil(height * scale[1]))
    scaled, cover = _straight(frame, (scaled_w, scaled_h), position=(0, 0), anchor=(0, 0), scale=scale)
    layers = np.concatenate([scaled, cover], axis=2)
    comp_w, comp_h = comp
    turn = math.radians(rotation)
    cos, sin = math.cos(turn), math.sin(turn)
    ys, xs = np.mgrid[0:comp_h, 0:comp_w].astype(np.float64)
    dx, dy = xs + 0.5 - position[0] * comp_w, ys + 0.5 - position[1] * comp_h
    # back through the rotation: clockwise on screen (y down) forward, so R(-turn) here
    local_x = cos * dx + sin * dy + anchor[0] * width * scale[0] - 0.5
    local_y = -sin * dx + cos * dy + anchor[1] * height * scale[1] - 0.5
    out = np.empty((comp_h, comp_w, channels + 1), np.float32)
    # `grid-constant` interpolates towards 0 beyond the last pixel centre, so the layer's edge
    # falls on its pixel boundary and a turn keeps its area of coverage
    for channel in range(channels + 1):
        out[..., channel] = map_coordinates(layers[..., channel], [local_y, local_x], order=1, mode="grid-constant",
                                            cval=0.0)
    return np.ascontiguousarray(out[..., :channels]), np.ascontiguousarray(out[..., channels:])


def mask_alpha(masks: list, size: tuple[int, int]) -> np.ndarray:
    """A layer's masks as its own alpha (HxWx1) at `size` (width, height), in order: each
    shape (`rectangle`, `ellipse`) at its centre and size in fractions of the layer's frame
    as shown, its edge ramped linearly over `feather` (a fraction of the layer's height,
    centred on the edge; at least one pixel, so an edge is never aliased), inverted if
    asked, and combined with what came before: `add` (a + b - ab), `subtract` (a (1 - b)),
    `intersect` (a b), starting from nothing (D15: the ramp's shape and the modes'
    formulas follow After Effects' names, not a measurement of it)."""
    width, height = size
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float64)
    xs, ys = xs + 0.5, ys + 0.5
    total = np.zeros((height, width))
    for mask in masks:
        cx, cy = mask.centre[0] * width, mask.centre[1] * height
        rx, ry = mask.size[0] * width / 2, mask.size[1] * height / 2
        dx, dy = xs - cx, ys - cy
        if mask.shape == "rectangle":
            qx, qy = np.abs(dx) - rx, np.abs(dy) - ry
            distance = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0)
        else:
            k = np.sqrt((dx / rx) ** 2 + (dy / ry) ** 2)
            gradient = np.sqrt((dx / rx ** 2) ** 2 + (dy / ry ** 2) ** 2)
            with np.errstate(invalid="ignore", divide="ignore"):
                distance = np.where(k > 0, (k - 1) * k / np.maximum(gradient, 1e-12), -min(rx, ry))
        feather = max(mask.feather * height, 1.0)
        alpha = np.clip(0.5 - distance / feather, 0, 1)
        if mask.invert:
            alpha = 1 - alpha
        if mask.mode == "add":
            total = total + alpha - total * alpha
        elif mask.mode == "subtract":
            total = total * (1 - alpha)
        else:
            total = total * alpha
    return total[..., None].astype(np.float32)
