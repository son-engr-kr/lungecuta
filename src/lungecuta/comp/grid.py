"""A comp's beat grid, from its music layers, and the beat literals a re-analysis would
move (docs/design/03-project-format.md#beat-maps)."""
from __future__ import annotations
from .loading import load_comp
from .parts import seconds_to_ticks


from fractions import Fraction
from pathlib import Path

from ..literals import (
    Bar,
    BeatGrid,
    BeatPosition,
    BeatSpan,
    Field,
    Seconds,
    parse_literal,
    resolve_position,
    resolve_span,
)
from ..media import Project, load_record
from ..schema.comp import (
    AudioLayer,
    CompFile,
)
from ..schema.media import MediaRecord
from ..ticks import (
    parse_rate,
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
