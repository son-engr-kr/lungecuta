"""What a comp resolves to: placements, cuts, sounds, overlays, markers, nests, and the
resolved comp that holds them (docs/design/03-project-format.md)."""
from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
from fractions import Fraction

from ..literals import (
    BeatGrid,
    Field,
    parse_literal,
    resolve_span,
)
from ..keyframes import Track
from ..schema.comp import (
    AudioLayer,
    CompFile,
    FootageLayer,
    GraphicsLayer,
    Keys,
    Marker,
    PrecompLayer,
    Speed,
    Transform,
)
from ..schema.media import MediaRecord
from ..ticks import (
    TICKS_PER_SECOND,
    parse_rate,
    round_half_up,
    ticks_per_frame,
    ticks_per_sample,
)
from .loading import MIX_RATE


def seconds_to_ticks(seconds: float) -> int:
    """A time in float seconds (as a beat map holds them) on the nearest tick, halves up."""
    return round_half_up(Fraction(seconds) * TICKS_PER_SECOND)


def speed_of(layer: FootageLayer) -> Fraction:
    """The layer's constant speed as the decimal it is written as (2.0, 0.5, 1.25); a
    remapped layer has none (1 is its placeholder: `Cut.remap` decides)."""
    return Fraction(repr(layer.time.speed)) if isinstance(layer.time, Speed) else Fraction(1)


def _inside(track: Track, ticks: int, outside: float) -> float:
    """A one-value track's value, or `outside` before its first key and after its last."""
    return track.at(ticks)[0] if track.times[0] <= ticks <= track.times[-1] else outside


@dataclass(frozen=True)
class Placement:
    """A layer's transform resolved to comp time: tracks for position, scale (x and y) and
    opacity, and the anchor, which does not move; and a style's scale track, a multiplier
    that is 1 outside its keys."""

    position: Track
    scale: Track
    opacity: Track
    anchor: tuple[float, float]
    scale_by: Track | None = None
    rotation: Track | None = None  # degrees; None is 0
    rotation_by: Track | None = None  # a style's degrees, added; 0 outside its keys
    opacity_by: Track | None = None  # a style's multiplier on opacity; 1 outside its keys

    def rotation_at(self, ticks: int) -> float:
        own = 0.0 if self.rotation is None else self.rotation.at(ticks)[0]
        return own + (0.0 if self.rotation_by is None else _inside(self.rotation_by, ticks, 0.0))

    def at(self, ticks: int) -> tuple[tuple[float, float], tuple[float, float], float]:
        scale = self.scale.at(ticks)
        if self.scale_by is not None:
            factor = _inside(self.scale_by, ticks, 1.0)
            scale = (scale[0] * factor, scale[1] * factor)
        opacity = self.opacity.at(ticks)[0]
        if self.opacity_by is not None:
            opacity *= _inside(self.opacity_by, ticks, 1.0)
        return self.position.at(ticks), scale, opacity


def _track(value, start: int, rate: Fraction, grid: BeatGrid | None, size: int) -> Track:
    """A property as a track: a constant is one key at the layer's start."""
    def vector(v) -> tuple[float, ...]:
        v = (v,) * size if isinstance(v, int | float) else tuple(v)
        assert len(v) == size, f"{v} has {len(v)} values for {size}"
        return tuple(float(x) for x in v)

    if not isinstance(value, Keys):
        return Track((start,), (vector(value),), (None,))
    times = [start + resolve_span(parse_literal(key.t, Field.LAYER), anchor=start, rate=rate, grid=grid)
             for key in value.keys]
    return Track(tuple(times), tuple(vector(key.v) for key in value.keys), tuple(key.ease for key in value.keys))


def placement(transform: Transform, start: int, rate: Fraction, grid: BeatGrid | None) -> Placement:
    return Placement(
        position=_track(transform.position, start, rate, grid, 2),
        scale=_track(transform.scale, start, rate, grid, 2),
        opacity=_track(transform.opacity, start, rate, grid, 1),
        anchor=transform.anchor,
        rotation=None if not isinstance(transform.rotation, Keys) and transform.rotation == 0
        else _track(transform.rotation, start, rate, grid, 1),
    )


@dataclass(frozen=True)
class Cut:
    """A footage layer resolved to frames. A comp frame shows the source at the layer's
    source time there: by default the frame whose interval holds it (*proposed*),
    with `retime` the nearest frame or a blend of the two either side."""

    layer: FootageLayer
    record: MediaRecord
    start: int  # ticks, the comp position of layer time 0
    first: int  # comp frame index of the first visible frame
    end: int  # comp frame index after the last visible frame
    source_start: int  # ticks, the source time at layer time 0 (unused when remapped)
    speed: Fraction
    comp_rate: Fraction
    placement: Placement | None = None
    remap: Track | None = None  # source time in ticks against comp time in ticks
    speed_track: Track | None = None  # a style's speed against comp time, 1 outside its keys
    ramps: tuple[tuple[int, int, float, float], ...] = ()  # transitions: opacity from a0 to a1 over frames [f0, f1)
    shutter: Track | None = None  # motion blur: the output shutter in degrees against comp ticks; None is none
    cut_at: int | None = None  # the frame its cut is on, when a dissolve has it begin before (else `first`)
    style_shutter: Track | None = None  # a style's shutter, which holds inside its keys

    def shutter_deg(self, ticks: int) -> float:
        """The output shutter at `ticks`: a style's inside its keys, the layer's own outside."""
        if self.style_shutter is not None and self.style_shutter.times[0] <= ticks <= self.style_shutter.times[-1]:
            return self.style_shutter.at(ticks)[0]
        return 0.0 if self.shutter is None else self.shutter.at(ticks)[0]

    @property
    def blurred(self) -> bool:
        return self.shutter is not None or self.style_shutter is not None

    @property
    def cut_frame(self) -> int:
        """The frame the cut to this layer is on: its first, or under a dissolve the frame
        the dissolve is centred on, where the edit is."""
        return self.first if self.cut_at is None else self.cut_at

    def opacity_at(self, frame: int) -> float:
        """The opacity transitions give comp frame `frame`, linear over each ramp's frames
        (D15), 1 outside them."""
        factor = 1.0
        for f0, f1, a0, a1 in self.ramps:
            if f0 <= frame < f1:
                factor *= a0 + (a1 - a0) * (frame - f0) / (f1 - f0)
        return factor

    @property
    def media_rate(self) -> Fraction:
        return parse_rate(self.record.time.rate)

    @property
    def retime(self) -> str | None:
        return self.layer.retime

    def source_ticks(self, frame: int) -> Fraction:
        """The exact source time at comp frame `frame` (to the tick when remapped)."""
        at = frame * ticks_per_frame(self.comp_rate)
        if self.remap is not None:
            return Fraction(round(self.remap.at(at)[0]))
        if self.speed_track is not None:
            # a frame before the layer's start (a dissolve's handle) counts back from it
            integral = self.speed_track.integral(self.start, at, outside=1.0) if at >= self.start \
                else -self.speed_track.integral(at, self.start, outside=1.0)
            return Fraction(round(self.source_start + integral))
        return self.source_start + self.speed * (at - self.start)

    def samples(self, frame: int) -> list[tuple[int, float]]:
        """The source frames comp frame `frame` shows, with their weights."""
        position = self.source_ticks(frame) / ticks_per_frame(self.media_rate)
        if self.retime == "nearest":
            return [(round_half_up(position), 1.0)]
        below = math.floor(position)
        share = float(position - below)
        if self.retime == "blend" and share > 0:
            return [(below, 1 - share), (below + 1, share)]
        return [(below, 1.0)]

    def source_frame(self, frame: int) -> int:
        """The source frame comp frame `frame` shows, the first of a blend's two."""
        return self.samples(frame)[0][0]

    def all_samples(self) -> list[int]:
        return [index for frame in range(self.first, self.end) for index, _ in self.samples(frame)]

    @property
    def source_frames(self) -> tuple[int, int]:
        """The first and the last source frame shown (in source order)."""
        every = self.all_samples()
        return min(every), max(every)

    @property
    def forwards(self) -> bool:
        """Whether the source frames shown never go back, so one pass decodes them."""
        every = self.all_samples()
        return all(a <= b for a, b in zip(every, every[1:]))


@dataclass(frozen=True)
class Sound:
    """An audio layer resolved to samples at the mix rate: comp samples [first, end), and
    the source sample at `first`. Audio lands on the nearest sample (*proposed*)."""

    layer: AudioLayer
    record: MediaRecord
    first: int
    end: int
    source_first: int
    gain: Track | None = None  # dB against comp ticks; None is 0 dB
    fade_in: int = 0  # samples from `first` rising from silence
    fade_out: int = 0  # samples before `end` falling to silence

    def envelope(self) -> np.ndarray:
        """The amplitude on each of the layer's samples: its gain and its fades (linear in
        amplitude, D15)."""
        count = self.end - self.first
        out = np.ones(count, dtype=np.float64)
        if self.gain is not None:
            ticks = (self.first + np.arange(count, dtype=np.int64)) * ticks_per_sample(MIX_RATE)
            out *= 10 ** (self.gain.many(ticks) / 20)
        if self.fade_in:
            out[:self.fade_in] *= np.arange(self.fade_in) / self.fade_in
        if self.fade_out:
            out[count - self.fade_out:] *= np.arange(self.fade_out, 0, -1) / self.fade_out
        return out


@dataclass(frozen=True)
class Overlay:
    """A graphics layer resolved to frames: drawn on comp frames [first, end) at layer time
    `frame time - start`."""

    layer: GraphicsLayer
    start: int  # ticks
    first: int
    end: int
    top: bool = True  # in the top run of the stack, drawn after the output view; else under footage

    def layer_seconds(self, frame: int, rate: Fraction) -> float:
        return float(Fraction(frame * ticks_per_frame(rate) - self.start, TICKS_PER_SECOND))


@dataclass(frozen=True)
class MarkerAt:
    marker: Marker
    frame: int  # the comp frame it lands on: the first at or after its time
    layer: str | None


@dataclass(frozen=True)
class Nest:
    """A precomp layer resolved: the precomp, resolved on its own, shown on comp frames
    [first, end), comp frame f showing the precomp's frame whose interval holds layer time
    `f - start` (the rule source time follows, *proposed*)."""

    layer: PrecompLayer
    resolved: Resolved
    start: int  # ticks
    first: int
    end: int
    comp_rate: Fraction
    visible: tuple[int, int]  # ticks: its visible in and out points in the comp, where its sound plays
    placement: Placement | None = None

    def frame(self, index: int) -> int:
        """The precomp's frame shown on comp frame `index`."""
        layer_time = index * ticks_per_frame(self.comp_rate) - self.start
        inner = math.floor(Fraction(layer_time, ticks_per_frame(self.resolved.rate)))
        assert 0 <= inner < self.resolved.end, (
            f"layer {self.layer.id} shows {self.layer.comp} frame {inner}, outside it (0 to {self.resolved.end - 1})"
        )
        return inner


@dataclass(frozen=True)
class Resolved:
    """A comp resolved to frames; `cuts` in time order (by first frame)."""

    comp: CompFile
    rate: Fraction
    end: int  # comp frames
    grid: BeatGrid | None
    cuts: list[Cut]
    sounds: list[Sound]
    overlays: list[Overlay]
    markers: list[MarkerAt]
    nests: list[Nest] = ()

    def nests_at(self, index: int) -> list[Nest]:
        return [nest for nest in self.nests if nest.first <= index < nest.end]

    def every_cut(self) -> Iterator[Cut]:
        """Its footage layers and its precomps', all the way down."""
        yield from self.cuts
        for nest in self.nests:
            yield from nest.resolved.every_cut()

    def every_sound(self) -> Iterator[Sound]:
        """Its audio layers and its precomps', all the way down."""
        yield from self.sounds
        for nest in self.nests:
            yield from nest.resolved.every_sound()

    def precomps(self) -> set[str]:
        """The ids of every comp it places, all the way down."""
        return {nest.layer.comp for nest in self.nests} | {c for nest in self.nests for c in nest.resolved.precomps()}

    def stack(self, index: int) -> list[Cut]:
        """The footage layers visible on comp frame `index`, bottom first."""
        order = {layer.id: k for k, layer in enumerate(self.comp.layers)}
        return sorted((cut for cut in self.cuts if cut.first <= index < cut.end), key=lambda cut: order[cut.layer.id])

    def one_track(self) -> None:
        """Footage layers one after another, none over another: what an FCPXML spine holds."""
        for before, after in zip(self.cuts, self.cuts[1:]):
            assert before.end <= after.first, (
                f"layers {before.layer.id} and {after.layer.id} overlap; the FCPXML export writes one picture track"
            )
