"""A comp resolved to frames and samples: its beat grid from its music layers, each
footage layer's visible frames and the source frames they show, each audio layer's
samples, and its markers (docs/design/03-project-format.md)."""
from __future__ import annotations

import functools
import math
from collections.abc import Iterator
from dataclasses import dataclass, replace

import numpy as np
from fractions import Fraction
from pathlib import Path

from .literals import (
    Bar,
    BeatGrid,
    BeatPosition,
    BeatSpan,
    Field,
    Seconds,
    parse_literal,
    resolve_position,
    resolve_source,
    resolve_span,
)
from .media import Project, load_record, music_wav
from .keyframes import Track
from .schema.comp import (
    AudioLayer,
    CompFile,
    FootageLayer,
    GraphicsLayer,
    Keys,
    Marker,
    PrecompLayer,
    Remap,
    Speed,
    StyleTime,
    Transform,
)
from .schema.media import ColorDecided, MediaRecord, TimeDecided
from .schema.style import StyleFile
from .ticks import (
    TICKS_PER_SECOND,
    first_frame_at_or_after,
    nearest_sample,
    parse_rate,
    round_half_up,
    ticks_per_frame,
    ticks_per_sample,
)

MIX_RATE = 48000  # the engine's internal audio rate (07-audio.md)
COMPS_DIR = Path("comps")
STYLES_DIR = Path("styles")


def load_comp(project: Project, name: str) -> CompFile:
    """Comp `name` (`comps/NAME.json`), which project.json must list."""
    relative = (COMPS_DIR / f"{name}.json").as_posix()
    assert relative in project.file.comps, f"{relative} is not one of the comps project.json lists"
    path = project.directory / relative
    assert path.is_file(), f"{path} is missing"
    comp = CompFile.model_validate_json(path.read_text())
    assert comp.id == name, f"{relative} holds comp {comp.id!r}"
    return comp


def load_style(project: Project, name: str) -> StyleFile:
    """Style `name` (`styles/NAME.json`)."""
    path = project.directory / STYLES_DIR / f"{name}.json"
    assert path.is_file(), f"style {name!r}: {path} is missing"
    style = StyleFile.model_validate_json(path.read_text())
    assert style.id == name, f"styles/{name}.json holds style {style.id!r}"
    return style


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
    style_shutter: Track | None = None  # a style's shutter, which holds inside its keys

    def shutter_deg(self, ticks: int) -> float:
        """The output shutter at `ticks`: a style's inside its keys, the layer's own outside."""
        if self.style_shutter is not None and self.style_shutter.times[0] <= ticks <= self.style_shutter.times[-1]:
            return self.style_shutter.at(ticks)[0]
        return 0.0 if self.shutter is None else self.shutter.at(ticks)[0]

    @property
    def blurred(self) -> bool:
        return self.shutter is not None or self.style_shutter is not None

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


def _is_beat(text: str, field: Field) -> bool:
    return isinstance(parse_literal(text, field), BeatPosition | BeatSpan | Bar)


def _placed(layer, rate: Fraction, grid: BeatGrid | None) -> tuple[int, int, int]:
    """start, visible in and visible out of a layer, in ticks."""
    start = resolve_position(parse_literal(layer.start, Field.POSITION), rate=rate, grid=grid)
    span_in = resolve_span(parse_literal(layer.in_, Field.LAYER), anchor=start, rate=rate, grid=grid)
    span_out = resolve_span(parse_literal(layer.out, Field.LAYER), anchor=start, rate=rate, grid=grid)
    assert 0 <= span_in <= span_out, f"layer {layer.id}: in ({layer.in_}) is after out ({layer.out})"
    return start, start + span_in, start + span_out


def _music_source(layer: AudioLayer) -> int:
    literal = parse_literal(layer.source_start, Field.SOURCE)
    assert isinstance(literal, Seconds), (
        f"layer {layer.id}: audio has no frame rate, so its source_start is in seconds, not {layer.source_start}"
    )
    return literal.ticks


def _audio_record(project: Project, layer: AudioLayer) -> MediaRecord:
    record = load_record(project, layer.media)
    assert record.wav is not None, f"layer {layer.id}: {layer.media} has no sound"
    return record


def _music_record(project: Project, layer: AudioLayer) -> MediaRecord:
    record = _audio_record(project, layer)
    assert record.beats is not None, f"layer {layer.id}: {layer.media} is a sound with no beat map, not music"
    return record


SEARCH_SECONDS = 0.6  # a sound's rise is looked for in its first 0.6 s (a past project's audio builder)


@functools.lru_cache(maxsize=256)
def anchor_samples(wav: Path, kind: str, digest: str) -> int:
    """Where a sound's anchor is, in samples from its first (07-audio.md, SFX placement):
    `peak`, its loudest sample on either channel; `rise`, the steepest rise of its 2 ms
    envelope, the moving mean of the channels' mean magnitude over 96 samples (2 ms at
    48 kHz) centred on each sample, searched over its first 0.6 s, the definition a past
    project's audio builder settled on, since impact files carry a pre-swell. `digest` is
    the WAV's hash, for the cache."""
    from .music import read_wav

    signal, rate = read_wav(wav)
    if kind == "peak":
        return int(np.abs(signal).max(axis=1).argmax())
    assert kind == "rise", f"unknown anchor {kind!r}"
    window = round(0.002 * rate)
    magnitude = np.abs(signal).mean(axis=1)[:round(SEARCH_SECONDS * rate)]
    envelope = np.convolve(magnitude, np.ones(window) / window, mode="same")
    return int(np.argmax(np.diff(envelope)))


def beat_grid(project: Project, comp: CompFile, rate: Fraction) -> BeatGrid | None:
    """The comp's grid from its music layers: each contributes the beats of its map inside
    its visible range, beat k at `start + (times[k] - source_start)`, and where two
    overlap the later one's beats count (*proposed*)."""
    built = grid_with_sources(project, comp, rate)
    return None if built is None else built[0]


def grid_with_sources(
    project: Project, comp: CompFile, rate: Fraction
) -> tuple[BeatGrid, list[tuple[str, int]]] | None:
    """The comp's beat grid, and for each of its beats the media key and the index in that
    media's beat map it came from."""
    if comp.beats is None:
        return None
    layers = {layer.id: layer for layer in comp.layers}
    parts = []
    for name in comp.beats.from_layers:
        layer = layers[name]
        for field, text in ((Field.POSITION, layer.start), (Field.LAYER, layer.in_), (Field.LAYER, layer.out)):
            assert not _is_beat(text, field), (
                f"layer {name}: the beat grid depends on it, so its {text} cannot be a beat literal"
            )
        start, visible_in, visible_out = _placed(layer, rate, None)
        record = _music_record(project, layer)
        source = _music_source(layer)
        parts.append((visible_in, visible_out, start, source, record))
    parts.sort(key=lambda part: part[0])
    times, downbeats, sources = [], [], []
    for k, (visible_in, visible_out, start, source, record) in enumerate(parts):
        until = min(visible_out, parts[k + 1][0]) if k + 1 < len(parts) else visible_out
        down = set(record.beats.downbeats)
        for index, seconds in enumerate(record.beats.times):
            at = start + seconds_to_ticks(seconds) - source
            if visible_in <= at < until:
                if index in down:
                    downbeats.append(len(times))
                times.append(at)
                sources.append((record.key, index))
    assert len(times) >= 2, f"comp {comp.id}: its music layers hold {len(times)} beats; a grid needs two"
    return BeatGrid(tuple(times), tuple(downbeats)), sources


def beat_literals(comp: CompFile) -> list[tuple[str, str, Field, str | None]]:
    """Every beat and bar literal of a comp: where it is, its text, its field, and for a
    span the layer start it counts from."""
    found = [("end", comp.end, Field.POSITION, None)]
    found += [(f"marker {m.name}", m.t, Field.POSITION, None) for m in comp.markers]
    for layer in comp.layers:
        found.append((f"{layer.id}.start", layer.start, Field.POSITION, None))
        found.append((f"{layer.id}.in", layer.in_, Field.LAYER, layer.start))
        found.append((f"{layer.id}.out", layer.out, Field.LAYER, layer.start))
        found += [(f"{layer.id} marker {m.name}", m.t, Field.LAYER, layer.start) for m in layer.markers]
    return [entry for entry in found if _is_beat(entry[1], entry[2])]


def literals_past(project: Project, key: str, first_changed: int) -> list[dict[str, str]]:
    """The beat and bar literals in the project's comps that reach a beat at or after the
    first beat of media `key` that a new analysis changes (`first_changed`, an index in its
    current map): every comp beat from there on would be renumbered."""
    affected = []
    for path in project.file.comps:
        comp = load_comp(project, Path(path).stem)
        rate = parse_rate(comp.rate)
        built = grid_with_sources(project, comp, rate)
        if built is None:
            continue
        grid, sources = built
        changed = [k for k, (media, index) in enumerate(sources) if media == key and index >= first_changed]
        if not changed:
            continue
        threshold = changed[0]
        for where, text, field, start_text in beat_literals(comp):
            literal = parse_literal(text, field)
            if isinstance(literal, BeatPosition):
                reach = literal.index + literal.offset
            elif isinstance(literal, Bar):
                reach = Fraction(grid.downbeats[literal.index]) if literal.index < len(grid.downbeats) else Fraction(grid.last + 1)
            else:
                anchor = resolve_position(parse_literal(start_text, Field.POSITION), rate=rate, grid=grid)
                reach = grid.coordinate_at(anchor) + literal.count
            if reach >= threshold:
                affected.append({"comp": comp.id, "where": where, "literal": text})
    return affected


STYLE_NEUTRAL = {"speed": 1.0, "scale": 1.0, "opacity": 1.0, "rotation": 0.0, "shutter_deg": 0.0}


def _style_tracks(project: Project, layer: FootageLayer, start: int, rate: Fraction,
                  grid: BeatGrid | None) -> dict[str, Track]:
    """A style instance's tracks in comp ticks by name, its keys placed from the anchor and
    each value moved from the track's neutral (1 for speed, scale and opacity, 0 for
    rotation and the shutter) towards the style's by the intensity."""
    instance = layer.time
    style = load_style(project, instance.style)
    assert style.tracks is not None, f"layer {layer.id}: style {style.id} is a look, with no tracks to place in time"
    assert grid is not None, f"layer {layer.id}: style {style.id} is anchored on a beat, and the comp has no grid"
    for name, value in instance.overrides.items():
        assert name in style.params, f"layer {layer.id}: style {style.id} has no parameter {name!r}"
        param = style.params[name]
        assert param.min <= value <= param.max, (
            f"layer {layer.id}: {name} {value} is outside {param.min} to {param.max} for style {style.id}"
        )
    intensity = instance.overrides.get("intensity", style.params["intensity"].default
                                       if "intensity" in style.params else 1.0)
    anchor = start + resolve_span(parse_literal(instance.at, Field.LAYER), anchor=start, rate=rate, grid=grid)
    # a beat span from the start, or seconds to a beat from a start before the grid (to 10 us)
    assert grid.times[0] - TICKS_PER_SECOND // 10**5 <= anchor <= grid.times[-1], (
        f"layer {layer.id}: style {style.id}'s anchor {instance.at} is not on the beat grid"
    )

    def track(keys, name: str) -> Track | None:
        if keys is None:
            return None
        times = [anchor + resolve_span(parse_literal(key.at, Field.STYLE), anchor=anchor, rate=rate, grid=grid)
                 for key in keys]
        neutral = STYLE_NEUTRAL[name]
        values = [(neutral + intensity * (key.v - neutral),) for key in keys]
        for (value,) in values:
            fine = {"speed": value >= 0, "scale": value > 0, "opacity": 0 <= value <= 1,
                    "shutter_deg": 0 <= value <= 720}.get(name, True)
            assert fine, f"layer {layer.id}: style {style.id} at intensity {intensity} gives a {name} of {value}"
        return Track(tuple(times), tuple(values), tuple(key.ease for key in keys))

    tracks = {name: track(getattr(style.tracks, name), name) for name in STYLE_NEUTRAL}
    return {name: value for name, value in tracks.items() if value is not None}


def _footage(project: Project, layer: FootageLayer, rate: Fraction, grid: BeatGrid | None) -> Cut:
    record = load_record(project, layer.media)
    assert record.probe.video is not None, f"layer {layer.id}: {layer.media} has no video"
    assert isinstance(record.color, ColorDecided), (
        f"layer {layer.id}: {layer.media} is undecided in colour ({record.color.reason}) and cannot be placed"
    )
    assert isinstance(record.time, TimeDecided), (
        f"layer {layer.id}: {layer.media} is undecided in time ({record.time.reason}) and cannot be placed"
    )
    start, visible_in, visible_out = _placed(layer, rate, grid)
    first, end = first_frame_at_or_after(visible_in, rate), first_frame_at_or_after(visible_out, rate)
    assert first < end, f"layer {layer.id} is visible for no frame ({layer.in_} to {layer.out})"
    media_rate = parse_rate(record.time.rate)
    remap = None
    source = 0
    if isinstance(layer.time, Remap):
        times = [start + resolve_span(parse_literal(key.t, Field.LAYER), anchor=start, rate=rate, grid=grid)
                 for key in layer.time.remap]
        values = [(float(resolve_source(parse_literal(key.v, Field.SOURCE), media_rate=media_rate)),)
                  for key in layer.time.remap]
        remap = Track(tuple(times), tuple(values), tuple(key.ease for key in layer.time.remap))
    else:
        source = resolve_source(parse_literal(layer.source_start, Field.SOURCE_START), media_rate=media_rate)
    styled: dict[str, Track] = {}
    if isinstance(layer.time, StyleTime):
        styled = _style_tracks(project, layer, start, rate, grid)
    placed = None
    if layer.transform is not None or {"scale", "rotation", "opacity"} & set(styled):
        placed = replace(placement(layer.transform or Transform(), start, rate, grid), scale_by=styled.get("scale"),
                         rotation_by=styled.get("rotation"), opacity_by=styled.get("opacity"))
    shutter = None
    if layer.motion_blur is not None:
        shutter = _track(layer.motion_blur.shutter_deg, start, rate, grid, 1)
    cut = Cut(layer=layer, record=record, start=start, first=first, end=end, source_start=source,
              speed=speed_of(layer), comp_rate=rate, placement=placed, remap=remap, speed_track=styled.get("speed"),
              shutter=shutter, style_shutter=styled.get("shutter_deg"))
    return _checked(cut)


def _checked(cut: Cut) -> Cut:
    """A cut whose every shown source frame exists."""
    layer, record = cut.layer, cut.record
    assert cut.source_frames[0] >= 0, f"layer {layer.id} starts before the first frame of {layer.media}"
    last_needed = cut.source_frames[1]
    frames = record.probe.video.frames
    assert last_needed < frames, (
        f"layer {layer.id} needs source frame {last_needed} of {layer.media}, which has {frames} frames "
        f"({float(Fraction(frames) / cut.media_rate):.3f} s)"
    )
    return cut


def _span_back(literal, end: int, rate: Fraction, grid: BeatGrid | None) -> int:
    """Where a span that ends at the comp position `end` begins: beats counted back on the
    grid, frames and seconds as lengths."""
    if isinstance(literal, BeatSpan):
        assert grid is not None, f"{literal}: the comp has no beat grid"
        return grid.time_at(grid.coordinate_at(end) - literal.count)
    return end - resolve_span(literal, anchor=end, rate=rate, grid=grid)


def _transitions(comp: CompFile, cuts: list[Cut], rate: Fraction, grid: BeatGrid | None) -> list[Cut]:
    """Each transition expanded into its layers: a dissolve extends the outgoing layer and
    the incoming one into their handles and ramps the upper one's opacity across the span;
    a dip ramps the outgoing layer out before the cut and the incoming one in after it; a
    fade ramps one layer from or to black (06-editing.md#transitions)."""
    if not comp.transitions:
        return cuts
    tick = ticks_per_frame(rate)
    order = {layer.id: k for k, layer in enumerate(comp.layers)}
    state = {cut.layer.id: {"first": cut.first, "end": cut.end, "ramps": list(cut.ramps)} for cut in cuts}
    by_id = {cut.layer.id: cut for cut in cuts}
    for transition in comp.transitions:
        literal = parse_literal(transition.length, Field.LAYER)
        if transition.type in ("dissolve", "dip"):
            a, b = (by_id[name] for name in transition.layers)
            assert a.end == b.first, (
                f"a {transition.type} sits on a cut: {a.layer.id} ends on frame {a.end} and {b.layer.id} starts on "
                f"frame {b.first}"
            )
            cut = a.end * tick
            if isinstance(literal, BeatSpan):
                assert grid is not None, f"{transition.length}: the comp has no beat grid"
                middle = grid.coordinate_at(cut)
                t0, t1 = grid.time_at(middle - literal.count / 2), grid.time_at(middle + literal.count / 2)
            else:
                length = resolve_span(literal, anchor=cut, rate=rate, grid=grid)
                t0, t1 = cut - length // 2, cut + length - length // 2
            f0, f1 = first_frame_at_or_after(t0, rate), first_frame_at_or_after(t1, rate)
            assert f0 < a.end < f1, f"a {transition.type} of {transition.length} spans no frame on either side of the cut"
            if transition.type == "dissolve":
                state[a.layer.id]["end"], state[b.layer.id]["first"] = f1, f0
                if order[b.layer.id] > order[a.layer.id]:
                    state[b.layer.id]["ramps"].append((f0, f1, 0.0, 1.0))
                else:
                    state[a.layer.id]["ramps"].append((f0, f1, 1.0, 0.0))
            else:
                state[a.layer.id]["ramps"].append((f0, a.end, 1.0, 0.0))
                state[b.layer.id]["ramps"].append((b.first, f1, 0.0, 1.0))
        else:
            (name,) = transition.layers
            layer = state[name]
            if transition.type == "fade_in":
                begin = layer["first"] * tick
                f1 = first_frame_at_or_after(begin + resolve_span(literal, anchor=begin, rate=rate, grid=grid), rate)
                assert layer["first"] < f1 <= layer["end"], f"{name}'s fade in of {transition.length} does not fit it"
                layer["ramps"].append((layer["first"], f1, 0.0, 1.0))
            else:
                f0 = first_frame_at_or_after(_span_back(literal, layer["end"] * tick, rate, grid), rate)
                assert layer["first"] <= f0 < layer["end"], f"{name}'s fade out of {transition.length} does not fit it"
                layer["ramps"].append((f0, layer["end"], 1.0, 0.0))
    out = []
    for cut in cuts:
        changed = state[cut.layer.id]
        out.append(_checked(replace(cut, first=changed["first"], end=changed["end"], ramps=tuple(changed["ramps"]))))
    return sorted(out, key=lambda cut: cut.first)


def _sound(project: Project, layer: AudioLayer, rate: Fraction, grid: BeatGrid | None) -> Sound:
    record = _audio_record(project, layer)
    start, visible_in, visible_out = _placed(layer, rate, grid)
    if layer.anchor != "start":  # `start` is where the anchor lands: the sound begins that much before
        assert record.beats is None, f"layer {layer.id}: music is placed by its start, not a {layer.anchor}"
        offset = anchor_samples(music_wav(project, record), layer.anchor, record.wav.hash) * ticks_per_sample(MIX_RATE)
        start, visible_in, visible_out = start - offset, max(0, visible_in - offset), visible_out - offset
        assert visible_in < visible_out, f"layer {layer.id} plays nothing after its pre-roll is cut"
    source = _music_source(layer)
    first, end = nearest_sample(visible_in, MIX_RATE), nearest_sample(visible_out, MIX_RATE)
    at_in = source + visible_in - start
    assert at_in >= 0, f"layer {layer.id} starts before the first sample of {layer.media}"
    source_first = nearest_sample(at_in, MIX_RATE)
    from .effects import source_length

    needed = source_length(layer.effects, end - first)  # a repitch plays more or less than the layer lasts
    assert source_first + needed <= record.wav.samples, (
        f"layer {layer.id} needs {needed / MIX_RATE:.3f} s of {layer.media} from "
        f"{source_first / MIX_RATE:.3f} s, which has {record.wav.samples / MIX_RATE:.3f} s"
    )
    gain = None
    if isinstance(layer.gain, Keys) or layer.gain != 0:
        gain = _track(layer.gain, start, rate, grid, 1)
    fade_in = fade_out = 0
    if layer.fade_in is not None:
        length = resolve_span(parse_literal(layer.fade_in, Field.LAYER), anchor=visible_in, rate=rate, grid=grid)
        fade_in = nearest_sample(visible_in + length, MIX_RATE) - first
    if layer.fade_out is not None:
        literal = parse_literal(layer.fade_out, Field.LAYER)
        if isinstance(literal, BeatSpan):  # beats counted back on the grid from the out point
            assert grid is not None, f"layer {layer.id}: {layer.fade_out} needs the comp's beat grid"
            begins = grid.time_at(grid.coordinate_at(visible_out) - literal.count)
        else:
            begins = visible_out - resolve_span(literal, anchor=visible_out, rate=rate, grid=grid)
        fade_out = end - nearest_sample(begins, MIX_RATE)
    assert fade_in + fade_out <= end - first, (
        f"layer {layer.id}: its fades ({layer.fade_in}, {layer.fade_out}) are longer than the layer"
    )
    return Sound(layer=layer, record=record, first=first, end=end, source_first=source_first, gain=gain,
                 fade_in=fade_in, fade_out=fade_out)


def _nest(project: Project, layer: PrecompLayer, rate: Fraction, grid: BeatGrid | None, inside: tuple[str, ...],
          end: int) -> Nest:
    assert layer.comp not in inside, f"layer {layer.id}: comp {layer.comp} contains itself ({' > '.join(inside)})"
    inner = resolve(project, load_comp(project, layer.comp), inside)
    start, visible_in, visible_out = _placed(layer, rate, grid)
    first, last = first_frame_at_or_after(visible_in, rate), first_frame_at_or_after(visible_out, rate)
    assert first < last, f"layer {layer.id} is visible for no frame"
    assert first >= 0 and last <= end, f"layer {layer.id} runs outside the comp"
    nest = Nest(layer=layer, resolved=inner, start=start, first=first, end=last, comp_rate=rate,
                visible=(visible_in, visible_out), placement=None if layer.transform is None else placement(layer.transform, start, rate, grid))
    nest.frame(first), nest.frame(last - 1)  # both ends inside the precomp
    return nest


def resolve(project: Project, comp: CompFile, inside: tuple[str, ...] = ()) -> Resolved:
    """A comp resolved to frames; `inside` are the comps it is a precomp of, to refuse a
    comp that contains itself. A precomp's beat grid is its own, from its own music layers
    (D15: 03-project-format.md had left how it sees its parent's grid to M5)."""
    inside = (*inside, comp.id)
    rate = parse_rate(comp.rate)
    ticks_per_frame(rate)  # asserts the rate is supported
    ticks_per_sample(MIX_RATE)
    grid = beat_grid(project, comp, rate)
    end = first_frame_at_or_after(resolve_position(parse_literal(comp.end, Field.POSITION), rate=rate, grid=grid), rate)
    cuts = sorted(
        (_footage(project, layer, rate, grid) for layer in comp.layers if isinstance(layer, FootageLayer)),
        key=lambda cut: cut.first,
    )
    cuts = _transitions(comp, cuts, rate, grid)
    for cut in cuts:
        assert cut.first >= 0, f"layer {cut.layer.id} starts before the comp does"
        assert cut.end <= end, f"layer {cut.layer.id} runs past the comp's end ({comp.end})"
    sounds = [_sound(project, layer, rate, grid) for layer in comp.layers if isinstance(layer, AudioLayer)]
    nests = [_nest(project, layer, rate, grid, inside, end) for layer in comp.layers if isinstance(layer, PrecompLayer)]
    overlays = []
    last_footage = max((k for k, layer in enumerate(comp.layers) if isinstance(layer, FootageLayer | PrecompLayer)),
                       default=-1)
    for k, layer in enumerate(comp.layers):
        if isinstance(layer, GraphicsLayer):
            assert (project.directory / layer.page).is_file(), f"layer {layer.id}: {layer.page} is missing"
            start, visible_in, visible_out = _placed(layer, rate, grid)
            first, last = first_frame_at_or_after(visible_in, rate), first_frame_at_or_after(visible_out, rate)
            assert first < last, f"layer {layer.id} is visible for no frame"
            assert last <= end, f"layer {layer.id} runs past the comp's end"
            overlays.append(Overlay(layer=layer, start=start, first=first, end=last, top=k > last_footage))
    markers = [MarkerAt(marker, _marker_frame(marker.t, None, rate, grid), None) for marker in comp.markers]
    for layer in comp.layers:
        if layer.markers:
            start = resolve_position(parse_literal(layer.start, Field.POSITION), rate=rate, grid=grid)
            markers += [MarkerAt(marker, _marker_frame(marker.t, start, rate, grid), layer.id) for marker in layer.markers]
    for at in markers:
        assert 0 <= at.frame < end, f"marker {at.marker.name!r} is outside the comp"
    return Resolved(comp=comp, rate=rate, end=end, grid=grid, cuts=cuts, sounds=sounds, overlays=overlays,
                    markers=sorted(markers, key=lambda at: at.frame), nests=nests)


def _marker_frame(text: str, layer_start: int | None, rate: Fraction, grid: BeatGrid | None) -> int:
    """A marker lands on the first frame at or after its time, like a cut (*proposed*)."""
    if layer_start is None:
        ticks = resolve_position(parse_literal(text, Field.POSITION), rate=rate, grid=grid)
    else:
        ticks = layer_start + resolve_span(parse_literal(text, Field.LAYER), anchor=layer_start, rate=rate, grid=grid)
    return first_frame_at_or_after(ticks, rate)

