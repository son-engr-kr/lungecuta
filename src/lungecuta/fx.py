"""Picture effects on the finished frame (05-motion-graphics.md, Picture effects): a comp's
`effects`, run inside `render`, `still`, `sheet`, `scope`, `verify` and the editor's
preview on display-encoded R'G'B', so a stylised version is one render rather than a
round trip through an outside script.

Every size is a fraction of the comp's height or width, so a preview and a render show
the same effect at their own sizes (01-architecture.md, Preview and final). Every number
that shapes a look is the comp's to give; none has a default. Noise and bands are drawn
from the effect's `seed` and the frame's index, so a frame comes out the same in every
command that shows it.

A datamosh moves pictures from one frame to the next, so its frames depend on the frames
before them: `Plan.needs` names the frames a set of frames needs, `Mosh` carries a cut's
picture forward frame by frame, and `finished` makes a frame alone by running the cut from
its start. The other effects act on one frame each, in the comp's order, after it.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .comp import Resolved
from .literals import Field, parse_literal, resolve_position, resolve_span
from .schema.comp import BeatEnvelope, Datamosh, Grain, Halation, RgbSplit, Vignette
from .ticks import first_frame_at_or_after, ticks_per_frame

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)  # BT.709 luma coefficients, ITU-R BT.709-6 item 3.2
MATCH_BLOCK = 8  # pixels a motion block spans where its match is searched (the frame is scaled to it), *proposed*


@dataclass(frozen=True)
class Cut:
    """A datamosh's cut: frames [first, first + frames) carry frame first - 1 forward."""

    effect: Datamosh
    first: int
    frames: int


class Plan:
    """A comp's effects resolved to frames."""

    def __init__(self, resolved: Resolved):
        self.resolved = resolved
        rate, grid = resolved.rate, resolved.grid
        self.tick = ticks_per_frame(rate)
        frame = lambda text: first_frame_at_or_after(
            resolve_position(parse_literal(text, Field.POSITION), rate=rate, grid=grid), rate)
        self.local: list[tuple[object, int, int]] = []
        self.cuts: list[Cut] = []
        for effect in resolved.comp.effects:
            if isinstance(effect, Datamosh):
                assert effect.from_ is None and effect.to is None and effect.beats is None, (
                    "a datamosh is placed by its cuts (at), with no from, to or beats")
                for at in effect.at:
                    first = frame(at)
                    length = resolve_span(parse_literal(effect.length, Field.LAYER), anchor=first * self.tick,
                                          rate=rate, grid=grid)
                    end = first_frame_at_or_after(first * self.tick + length, rate)
                    assert 1 <= first and end - first >= 2 and end <= resolved.end, (
                        f"a datamosh at {at} (frame {first}) for {effect.length} needs a frame before it and two "
                        f"after, inside the comp's {resolved.end} frames")
                    self.cuts.append(Cut(effect, first, end - first))
                continue
            first = 0 if effect.from_ is None else frame(effect.from_)
            end = resolved.end if effect.to is None else frame(effect.to)
            assert first < end, f"a {effect.type} from {effect.from_} to {effect.to} covers no frame"
            if effect.beats is not None:
                assert grid is not None, f"a {effect.type} on the {effect.beats.on} needs the comp's beat grid"
            self.local.append((effect, first, end))
        self.cuts.sort(key=lambda cut: cut.first)
        for before, after in zip(self.cuts, self.cuts[1:]):
            assert before.first + before.frames <= after.first, (
                f"two datamoshes overlap at frames {after.first} to {before.first + before.frames - 1}")

    def cut_at(self, index: int) -> Cut | None:
        return next((cut for cut in self.cuts if cut.first <= index < cut.first + cut.frames), None)

    def seeds(self) -> dict[int, Cut]:
        """The frame each datamosh carries forward, by its index."""
        return {cut.first - 1: cut for cut in self.cuts}

    def needs(self, indices: Iterable[int]) -> list[int]:
        """`indices` and every frame they are made from: a moshed frame needs the frames
        of its cut before it, and the frame before the cut."""
        wanted = set(indices)
        for index in list(wanted):
            cut = self.cut_at(index)
            if cut is not None:
                wanted |= set(range(cut.first - 1, index))
        return sorted(wanted)

    def envelope(self, envelope: BeatEnvelope | None, index: int) -> float:
        if envelope is None:
            return 1.0
        grid = self.resolved.grid
        times = grid.times if envelope.on == "beats" else [grid.times[k] for k in grid.downbeats]
        ticks = index * self.tick
        before = [t for t in times if t <= ticks]
        if not before:
            return envelope.floor
        beat = before[-1]
        decay = resolve_span(parse_literal(envelope.decay, Field.LAYER), anchor=beat, rate=self.resolved.rate, grid=grid)
        assert decay > 0, f"a decay of {envelope.decay}"
        return envelope.floor + (1 - envelope.floor) * math.exp(-(ticks - beat) / decay)

    def apply_local(self, index: int, frame: np.ndarray) -> np.ndarray:
        """The one-frame effects active on frame `index`, in order, on a display-encoded frame
        of any size."""
        for effect, first, end in self.local:
            if first <= index < end:
                frame = LOCAL[effect.type](effect, frame, index, self.envelope(effect.beats, index))
        return frame


def finished(resolved: Resolved, index: int, composite_of: Callable[[int], np.ndarray]) -> np.ndarray:
    """Comp frame `index` with its effects, from `composite_of(i)`, the finished composite of
    frame i (`compose.finish`): a moshed frame from its cut's frames run from the start."""
    plan = Plan(resolved)
    cut = plan.cut_at(index)
    if cut is None:
        frame = composite_of(index)
    else:
        run = Mosh(cut, composite_of(cut.first - 1))
        for i in range(cut.first, index + 1):
            frame = run.next(composite_of(i))
    return plan.apply_local(index, frame)


class Mosh:
    """A datamosh's cut carried forward: `next(composite)` takes the cut's frames in order
    and returns each moshed. The picture of the frame before the cut is moved by the
    motion of each frame against the one before it; the incoming composite is mixed in
    by (j / n) ** ease."""

    def __init__(self, cut: Cut, before: np.ndarray):
        self.cut, self.picture, self.previous, self.j = cut, before.copy(), before, 0

    def next(self, composite: np.ndarray) -> np.ndarray:
        effect, height = self.cut.effect, composite.shape[0]
        assert self.j < self.cut.frames, "the datamosh's cut is over"
        if self.j > 0:
            vectors = block_motion(composite, self.previous, effect.block * height, effect.search * height)
            self.picture = warp(self.picture, vectors * np.float32(effect.motion))
        weight = np.float32((self.j / self.cut.frames) ** effect.ease)
        self.previous, self.j = composite, self.j + 1
        return np.ascontiguousarray(self.picture * (1 - weight) + composite * weight, dtype=np.float32)


def block_motion(current: np.ndarray, previous: np.ndarray, block: float, search: float) -> np.ndarray:
    """Where each pixel of `current` came from in `previous`, per block, as a codec's motion
    vectors say: (dy, dx) in pixels, HxWx2. Blocks of `block` pixels are matched by the
    least sum of absolute luma differences within `search` pixels, on frames scaled so a
    block spans MATCH_BLOCK pixels; ties go to the shorter vector."""
    from .geometry import area_resize

    height, width = current.shape[:2]
    scale = max(1.0, block / MATCH_BLOCK)
    size = (max(1, round(width / scale)), max(1, round(height / scale)))
    luma = lambda frame: (frame @ LUMA)[..., None]
    a, b = luma(current), luma(previous)
    if size != (width, height):
        a, b = area_resize(np.ascontiguousarray(a), size), area_resize(np.ascontiguousarray(b), size)
    a, b = a[..., 0], b[..., 0]
    side = MATCH_BLOCK if scale > 1 else max(1, round(block))
    reach = max(1, math.ceil(search / scale))
    rows, columns = a.shape[0] // side, a.shape[1] // side
    assert rows and columns, f"a block of {block:.1f} pixels is larger than the {width}x{height} frame"
    a = a[:rows * side, :columns * side]
    padded = np.pad(b, reach, mode="edge")
    best = np.full((rows, columns), np.inf, np.float32)
    found = np.zeros((rows, columns, 2), np.float32)
    offsets = sorted(((dy, dx) for dy in range(-reach, reach + 1) for dx in range(-reach, reach + 1)),
                     key=lambda d: (d[0] ** 2 + d[1] ** 2, d))
    for dy, dx in offsets:
        moved = padded[reach + dy:reach + dy + rows * side, reach + dx:reach + dx + columns * side]
        cost = np.abs(a - moved).reshape(rows, side, columns, side).sum(axis=(1, 3))
        better = cost < best
        best[better] = cost[better]
        found[better] = (dy, dx)
    # each pixel takes its block's vector, the edge left over by whole blocks its nearest block's
    ys = np.minimum((np.arange(height) * size[1] / height / side).astype(int), rows - 1)
    xs = np.minimum((np.arange(width) * size[0] / width / side).astype(int), columns - 1)
    return found[ys][:, xs] * np.float32(height / size[1])


def warp(picture: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    """`picture` sampled at each pixel plus its vector, bilinear, the edge held."""
    from scipy.ndimage import map_coordinates

    height, width = picture.shape[:2]
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    coords = [ys + vectors[..., 0], xs + vectors[..., 1]]
    return np.stack([map_coordinates(picture[..., c], coords, order=1, mode="nearest")
                     for c in range(picture.shape[2])], axis=-1).astype(np.float32)


def _gaussian(image: np.ndarray, sigma: float) -> np.ndarray:
    from scipy.ndimage import gaussian_filter

    return gaussian_filter(image, sigma, mode="nearest") if sigma > 0 else image


def grain(effect: Grain, frame: np.ndarray, index: int, strength: float) -> np.ndarray:
    height, width = frame.shape[:2]
    noise = np.random.default_rng([effect.seed, index]).standard_normal((height, width), dtype=np.float32)
    noise = _gaussian(noise, effect.size * height)
    noise /= noise.std()
    lum = np.clip(frame @ LUMA, 0, 1)
    amount = effect.amount * strength * (effect.edges + (1 - effect.edges) * 4 * lum * (1 - lum))
    return frame + (noise * amount.astype(np.float32))[..., None]


@lru_cache(maxsize=8)
def _falloff(width: int, height: int, inner: float, power: float) -> np.ndarray:
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float64)
    r = np.sqrt(((xs + 0.5 - width / 2) / (width / 2)) ** 2 + ((ys + 0.5 - height / 2) / (height / 2)) ** 2) / math.sqrt(2)
    return (np.clip((r - inner) / (1 - inner), 0, 1) ** power).astype(np.float32)


def vignette(effect: Vignette, frame: np.ndarray, index: int, strength: float) -> np.ndarray:
    height, width = frame.shape[:2]
    return frame * (1 - np.float32(effect.amount * strength) * _falloff(width, height, effect.inner, effect.power))[..., None]


def halation(effect: Halation, frame: np.ndarray, index: int, strength: float) -> np.ndarray:
    bright = np.clip((frame @ LUMA - effect.threshold) / effect.knee, 0, 1)
    glow = np.clip(_gaussian(bright, effect.width * frame.shape[0]) - bright * effect.core, 0, 1)
    halo = glow[..., None] * (np.array(effect.colour, np.float32) * np.float32(effect.strength * strength))
    return 1 - (1 - frame) * (1 - halo)


def shift_x(image: np.ndarray, dx: float) -> np.ndarray:
    """`image` moved `dx` pixels right (linear between pixels), its edge held."""
    width = image.shape[1]
    xs = np.arange(width, dtype=np.float64) - dx
    left = np.floor(xs)
    t = (xs - left).astype(np.float32)
    a = np.clip(left, 0, width - 1).astype(int)
    b = np.clip(left + 1, 0, width - 1).astype(int)
    t = t.reshape((1, width) + (1,) * (image.ndim - 2))
    return image[:, a] * (1 - t) + image[:, b] * t


def rgb_split(effect: RgbSplit, frame: np.ndarray, index: int, strength: float) -> np.ndarray:
    height, width = frame.shape[:2]
    dx = effect.shift * width * strength
    split = frame.copy()
    split[..., 0] = shift_x(frame[..., 0], dx)
    split[..., 2] = shift_x(frame[..., 2], -dx)
    slices = effect.slices
    if slices is not None:
        rng = np.random.default_rng([effect.seed, index])
        for _ in range(round(slices.count * strength)):
            tall = max(1, round(rng.uniform(*slices.height) * height))
            top = int(rng.integers(0, height - tall + 1))
            split[top:top + tall] = shift_x(split[top:top + tall], rng.normal(0, slices.offset * width * strength))
    return np.ascontiguousarray(split, dtype=np.float32)


LOCAL = {"grain": grain, "vignette": vignette, "halation": halation, "rgb_split": rgb_split}


class InOrder:
    """A comp's effects on its frames taken in order, as a render takes them: a frame no
    datamosh involves is finished alone (`alone`), anywhere; the others go through `take`
    in order, which carries each cut's picture forward. The same frames as `finished`."""

    def __init__(self, resolved: Resolved):
        self.plan = Plan(resolved)
        self.seeds = self.plan.seeds()
        self.runs: dict[int, Mosh] = {}

    def alone(self, index: int) -> bool:
        return index not in self.seeds and self.plan.cut_at(index) is None

    def take(self, index: int, composite: np.ndarray) -> np.ndarray:
        cut = self.plan.cut_at(index)
        frame = composite if cut is None else self.runs[cut.first].next(composite)
        if cut is not None and index == cut.first + cut.frames - 1:
            del self.runs[cut.first]
        if index in self.seeds:
            self.runs[self.seeds[index].first] = Mosh(self.seeds[index], composite)
        return self.plan.apply_local(index, frame)
