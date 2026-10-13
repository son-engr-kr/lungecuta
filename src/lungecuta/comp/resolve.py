"""A comp resolved to frames and samples: each footage layer's visible frames and the
source frames they show, its styles and transitions, each audio layer's samples, its
precomps and markers (docs/design/03-project-format.md)."""
from __future__ import annotations

import functools
from dataclasses import replace

import numpy as np
from fractions import Fraction
from pathlib import Path

from ..literals import (
    BeatGrid,
    BeatSpan,
    Field,
    parse_literal,
    resolve_position,
    resolve_source,
    resolve_span,
)
from ..media import Project, load_record, music_wav
from ..keyframes import Track
from ..schema.comp import (
    AudioLayer,
    CompFile,
    FootageLayer,
    GraphicsLayer,
    Keys,
    PrecompLayer,
    Remap,
    StyleTime,
    Transform,
)
from ..schema.media import ColorDecided, TimeDecided
from ..ticks import (
    TICKS_PER_SECOND,
    first_frame_at_or_after,
    nearest_sample,
    parse_rate,
    ticks_per_frame,
    ticks_per_sample,
)
from .grid import _audio_record, _music_source, _placed, beat_grid
from .loading import MIX_RATE, load_comp, load_style
from .parts import Cut, MarkerAt, Nest, Overlay, Resolved, Sound, _track, placement, speed_of


SEARCH_SECONDS = 0.6  # a sound's rise is looked for in its first 0.6 s (a past project's audio builder)


@functools.lru_cache(maxsize=256)
def anchor_samples(wav: Path, kind: str, digest: str) -> int:
    """Where a sound's anchor is, in samples from its first (07-audio.md, SFX placement):
    `peak`, its loudest sample on either channel; `rise`, the steepest rise of its 2 ms
    envelope, the moving mean of the channels' mean magnitude over 96 samples (2 ms at
    48 kHz) centred on each sample, searched over its first 0.6 s, the definition a past
    project's audio builder settled on, since impact files carry a pre-swell. `digest` is
    the WAV's hash, for the cache."""
    from ..music import read_wav

    signal, rate = read_wav(wav)
    if kind == "peak":
        return int(np.abs(signal).max(axis=1).argmax())
    assert kind == "rise", f"unknown anchor {kind!r}"
    window = round(0.002 * rate)
    magnitude = np.abs(signal).mean(axis=1)[:round(SEARCH_SECONDS * rate)]
    envelope = np.convolve(magnitude, np.ones(window) / window, mode="same")
    return int(np.argmax(np.diff(envelope)))


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
    state = {cut.layer.id: {"first": cut.first, "end": cut.end, "ramps": list(cut.ramps), "cut_at": None}
             for cut in cuts}
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
                state[b.layer.id]["cut_at"] = a.end
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
        out.append(_checked(replace(cut, first=changed["first"], end=changed["end"], ramps=tuple(changed["ramps"]),
                                    cut_at=changed["cut_at"])))
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
    from ..effects import source_length

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
