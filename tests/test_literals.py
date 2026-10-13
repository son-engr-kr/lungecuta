import random
from fractions import Fraction

import pytest

from lungecuta.literals import (
    TICKS_PER_SECONDS_STEP,
    BeatGrid,
    BeatPosition,
    BeatSpan,
    Field,
    Frames,
    Seconds,
    format_literal,
    parse_literal,
    position_literal,
    resolve_position,
    resolve_source,
    resolve_span,
    source_literal,
    span_literal,
)
from lungecuta.ticks import SUPPORTED_RATES, TICKS_PER_SECOND, ticks_per_frame

SECOND = TICKS_PER_SECOND
NTSC = Fraction(30000, 1001)


def uniform_grid(count: int, interval: int, first: int = 0, bar: int = 4) -> BeatGrid:
    return BeatGrid(
        times=tuple(first + k * interval for k in range(count)),
        downbeats=tuple(range(0, count, bar)),
    )


def irregular_grid(count: int, seed: int) -> BeatGrid:
    """Beats whose intervals drift, as a tracker's output does: 0.3 to 0.7 s apart."""
    rng = random.Random(seed)
    times, now = [], rng.randrange(0, SECOND)
    for _ in range(count):
        times.append(now)
        now += rng.randrange(SECOND * 3 // 10, SECOND * 7 // 10)
    return BeatGrid(times=tuple(times), downbeats=tuple(range(0, count, 4)))


# ---- canonical spelling: format(parse(x)) ----------------------------------------------

CANONICAL = [
    ("12.5s", Field.POSITION, "12.5s"),
    ("012.50s", Field.POSITION, "12.5s"),
    ("12.00000s", Field.POSITION, "12s"),
    ("0.00001s", Field.POSITION, "0.00001s"),
    ("-0s", Field.POSITION, "0s"),
    ("f:360", Field.POSITION, "f:360"),
    ("f:0120", Field.POSITION, "f:120"),
    ("f:120+3", Field.POSITION, "f:123"),
    ("f:120-3+1", Field.POSITION, "f:118"),
    ("b:64", Field.POSITION, "b:64"),
    ("b:064+0.50", Field.POSITION, "b:64+0.5"),
    ("b:64+0", Field.POSITION, "b:64"),
    ("b:64+0.5-0.25", Field.POSITION, "b:64+0.25"),
    ("b:64-0.25", Field.POSITION, "b:64-0.25"),
    ("b:64+1.25", Field.POSITION, "b:64+1.25"),
    ("bar:17", Field.POSITION, "bar:17"),
    ("tc:00:00:12:15", Field.POSITION, "tc:00:00:12:15"),
    ("b:4", Field.LAYER, "b:4"),
    ("b:4+0.5", Field.LAYER, "b:4.5"),
    ("b:4.5", Field.LAYER, "b:4.5"),
    ("f:12", Field.LAYER, "f:12"),
    ("b:-2", Field.STYLE, "b:-2"),
    ("b:-0.25", Field.STYLE, "b:-0.25"),
    ("b:-0", Field.STYLE, "b:0"),
    ("b:0.5", Field.STYLE, "b:0.5"),
    ("-1.5s", Field.STYLE, "-1.5s"),
    ("f:-3", Field.STYLE, "f:-3"),
    ("8.2s", Field.SOURCE, "8.2s"),
    ("f:705", Field.SOURCE, "f:705"),
]


@pytest.mark.parametrize(("text", "field", "canonical"), CANONICAL)
def test_format_of_parse_is_the_canonical_spelling(text, field, canonical):
    literal = parse_literal(text, field)
    assert format_literal(literal) == canonical
    assert parse_literal(canonical, field) == literal


# ---- what the grammar refuses ---------------------------------------------------------

ERRORS = [
    ("12.500001s", Field.POSITION, "at most 5 decimal places"),
    ("12.5", Field.POSITION, "is not a time literal"),
    ("12,5s", Field.POSITION, "is not a time literal"),
    (".5s", Field.POSITION, "is not a time literal"),
    ("b:64.5", Field.POSITION, "whole beat index"),
    ("b:-1", Field.POSITION, "whole beat index"),
    ("f:2.5", Field.POSITION, "is not a time literal"),
    ("f:120+0.5", Field.POSITION, "is not a time literal"),
    ("f:3-5", Field.POSITION, "cannot be negative"),
    ("f:-3", Field.POSITION, "cannot be negative"),
    ("-1s", Field.POSITION, "cannot be negative"),
    ("bar:17+1", Field.POSITION, "is not a time literal"),
    ("tc:00:00:12;15", Field.POSITION, "drop-frame"),
    ("tc:0:0:12:15", Field.POSITION, "is not a time literal"),
    ("tc:00:60:00:00", Field.POSITION, "00 to 59"),
    ("b:-0.5", Field.LAYER, "cannot be negative"),
    ("-1s", Field.LAYER, "cannot be negative"),
    ("bar:2", Field.LAYER, "names a position"),
    ("tc:00:00:01:00", Field.LAYER, "names a comp position"),
    ("b:3", Field.SOURCE, "cannot be written in beats"),
    ("bar:1", Field.SOURCE, "names a position"),
    ("tc:00:00:21:15", Field.SOURCE, "names a comp position"),
    ("-1s", Field.SOURCE, "cannot be negative"),
]


@pytest.mark.parametrize(("text", "field", "message"), ERRORS)
def test_the_grammar_refuses(text, field, message):
    with pytest.raises(AssertionError, match=message):
        parse_literal(text, field)


def test_a_time_must_be_a_string():
    with pytest.raises(AssertionError, match="string with a unit"):
        parse_literal(12.5, Field.POSITION)


# ---- frames, seconds, timecode at every rate ------------------------------------------

FRAME_SAMPLES = [0, 1, 2, 23, 29, 59, 99, 1000, 1001, 30_000, 107_892, 1_000_003]


@pytest.mark.parametrize("rate", SUPPORTED_RATES, ids=str)
def test_frame_literals_round_trip_at_every_rate(rate):
    per_frame = ticks_per_frame(rate)
    for index in FRAME_SAMPLES:
        ticks = index * per_frame
        literal = position_literal(ticks, "f", rate=rate)
        assert literal == Frames(index)
        assert resolve_position(parse_literal(format_literal(literal), Field.POSITION), rate=rate) == ticks


@pytest.mark.parametrize("rate", SUPPORTED_RATES, ids=str)
def test_timecode_round_trips_at_every_rate_up_to_100_fps(rate):
    per_frame = ticks_per_frame(rate)
    if rate > 100:
        with pytest.raises(AssertionError, match="not defined"):
            position_literal(per_frame, "tc", rate=rate)
        return
    for index in FRAME_SAMPLES:
        ticks = index * per_frame
        text = format_literal(position_literal(ticks, "tc", rate=rate))
        assert resolve_position(parse_literal(text, Field.POSITION), rate=rate) == ticks


@pytest.mark.parametrize("rate", SUPPORTED_RATES, ids=str)
def test_seconds_round_trip_where_a_frame_is_exact_in_seconds(rate):
    per_frame = ticks_per_frame(rate)
    for index in FRAME_SAMPLES:
        ticks = index * per_frame
        if ticks % TICKS_PER_SECONDS_STEP:
            with pytest.raises(AssertionError, match="not exact"):
                position_literal(ticks, "s", rate=rate)
            continue
        text = format_literal(position_literal(ticks, "s", rate=rate))
        assert resolve_position(parse_literal(text, Field.POSITION), rate=rate) == ticks


def test_seconds_round_trip_on_every_five_digit_step():
    rng = random.Random(1)
    for _ in range(2000):
        ticks = rng.randrange(0, 3600 * 10**5) * TICKS_PER_SECONDS_STEP
        text = format_literal(position_literal(ticks, "s", rate=Fraction(60)))
        assert resolve_position(parse_literal(text, Field.POSITION), rate=Fraction(60)) == ticks


def test_a_tick_count_the_unit_cannot_hold_is_refused():
    with pytest.raises(AssertionError, match="not exact"):
        position_literal(1, "s", rate=NTSC)
    with pytest.raises(AssertionError, match="whole number of frames"):
        position_literal(ticks_per_frame(NTSC) + 1, "f", rate=NTSC)


def test_timecode_counts_non_drop_frames_at_the_rounded_up_rate():
    position = parse_literal("tc:00:00:01:00", Field.POSITION)
    assert resolve_position(position, rate=NTSC) == 30 * ticks_per_frame(NTSC)
    assert resolve_position(position, rate=Fraction(24000, 1001)) == 24 * ticks_per_frame(Fraction(24000, 1001))
    with pytest.raises(AssertionError, match="frames run from 00 to 24"):
        resolve_position(parse_literal("tc:00:00:00:25", Field.POSITION), rate=Fraction(25))


# ---- beats ----------------------------------------------------------------------------


def test_beat_positions_resolve_on_the_grid():
    grid = uniform_grid(8, SECOND // 2, first=SECOND // 10)
    assert resolve_position(parse_literal("b:0", Field.POSITION), rate=NTSC, grid=grid) == SECOND // 10
    assert resolve_position(parse_literal("b:3", Field.POSITION), rate=NTSC, grid=grid) == grid.times[3]
    halfway = resolve_position(parse_literal("b:3+0.5", Field.POSITION), rate=NTSC, grid=grid)
    assert halfway == grid.times[3] + SECOND // 4
    assert resolve_position(parse_literal("bar:1", Field.POSITION), rate=NTSC, grid=grid) == grid.times[4]


def test_a_beat_offset_is_a_fraction_of_the_interval_it_lands_in():
    grid = BeatGrid(times=(0, 1000, 3000, 6000), downbeats=(0,))
    resolve = lambda text: resolve_position(parse_literal(text, Field.POSITION), rate=NTSC, grid=grid)
    assert resolve("b:1+0.5") == 2000
    assert resolve("b:1-0.25") == 750  # a quarter of the way back towards beat 0
    assert resolve("b:1-0.25") == resolve("b:0+0.75")
    assert resolve("b:1+1.5") == 4500  # lands in the interval from beat 2 to beat 3


def test_a_beat_between_ticks_rounds_to_the_nearest_tick_halves_up():
    grid = BeatGrid(times=(0, 3, 6), downbeats=(0,))
    resolve = lambda text: resolve_position(parse_literal(text, Field.POSITION), rate=NTSC, grid=grid)
    assert resolve("b:0+0.5") == 2  # 1.5 rounds up
    assert resolve("b:0+0.25") == 1  # 0.75


def test_beats_outside_the_grid_are_errors():
    grid = uniform_grid(8, SECOND // 2)
    for text in ("b:8", "b:7+0.5", "b:0-0.5", "bar:2"):
        with pytest.raises(AssertionError, match="outside the beat grid"):
            resolve_position(parse_literal(text, Field.POSITION), rate=NTSC, grid=grid)
    with pytest.raises(AssertionError, match="no beat grid"):
        resolve_position(parse_literal("b:1", Field.POSITION), rate=NTSC)


@pytest.mark.parametrize("seed", range(5))
def test_beat_positions_round_trip_from_any_tick(seed):
    grid = irregular_grid(64, seed)
    rng = random.Random(seed)
    for _ in range(300):
        ticks = rng.randrange(grid.times[0], grid.times[-1] + 1)
        literal = position_literal(ticks, "b", rate=NTSC, grid=grid)
        text = format_literal(literal)
        assert resolve_position(parse_literal(text, Field.POSITION), rate=NTSC, grid=grid) == ticks


@pytest.mark.parametrize("seed", range(5))
def test_short_beat_literals_are_written_back_as_written(seed):
    grid = irregular_grid(64, seed)
    for text in ("b:0", "b:5", "b:5+0.5", "b:12+0.25", "b:40+0.125", "b:63"):
        ticks = resolve_position(parse_literal(text, Field.POSITION), rate=NTSC, grid=grid)
        assert format_literal(position_literal(ticks, "b", rate=NTSC, grid=grid)) == text


def test_bars_are_written_only_on_downbeats():
    grid = uniform_grid(16, SECOND // 2)
    assert format_literal(position_literal(grid.times[8], "bar", rate=NTSC, grid=grid)) == "bar:2"
    with pytest.raises(AssertionError, match="not on a downbeat"):
        position_literal(grid.times[9], "bar", rate=NTSC, grid=grid)


# ---- spans: layer time and style time -------------------------------------------------


def test_a_beat_span_counts_from_the_anchors_place_on_the_grid():
    grid = uniform_grid(16, 1000)
    anchor = 2300  # 2.3 beats
    span = parse_literal("b:4", Field.LAYER)
    assert anchor + resolve_span(span, anchor=anchor, rate=NTSC, grid=grid) == 6300


def test_keys_in_beats_stay_on_beats_when_a_layer_moves():
    # The tempo changes between beats 3 and 6; a key 4 beats into the layer stays on a
    # beat wherever the layer starts.
    grid = BeatGrid(times=(0, 500, 1000, 1500, 1900, 2300, 2700, 3200, 3700), downbeats=(0, 4, 8))
    key = parse_literal("b:4", Field.LAYER)
    for start_beat in range(5):
        start = grid.times[start_beat]
        assert start + resolve_span(key, anchor=start, rate=NTSC, grid=grid) == grid.times[start_beat + 4]


def test_style_spans_reach_back_before_the_anchor():
    grid = uniform_grid(16, 1000)
    anchor = grid.times[8]
    assert resolve_span(parse_literal("b:-2", Field.STYLE), anchor=anchor, rate=NTSC, grid=grid) == -2000
    assert resolve_span(parse_literal("b:-0.25", Field.STYLE), anchor=anchor, rate=NTSC, grid=grid) == -250
    assert resolve_span(parse_literal("-0.5s", Field.STYLE), anchor=anchor, rate=NTSC) == -SECOND // 2


@pytest.mark.parametrize("seed", range(5))
def test_beat_spans_round_trip(seed):
    grid = irregular_grid(64, seed)
    rng = random.Random(seed + 100)
    for _ in range(300):
        anchor = rng.randrange(grid.times[0], grid.times[-1] + 1)
        length = rng.randrange(0, grid.times[-1] - anchor + 1)
        literal = span_literal(length, "b", anchor=anchor, rate=NTSC, grid=grid)
        assert isinstance(literal, BeatSpan)
        text = format_literal(literal)
        assert resolve_span(parse_literal(text, Field.LAYER), anchor=anchor, rate=NTSC, grid=grid) == length


def test_frame_and_second_spans_round_trip():
    per_frame = ticks_per_frame(NTSC)
    assert span_literal(12 * per_frame, "f", anchor=0, rate=NTSC) == Frames(12)
    assert span_literal(-3 * per_frame, "f", anchor=0, rate=NTSC, field=Field.STYLE) == Frames(-3)
    assert span_literal(SECOND // 2, "s", anchor=0, rate=NTSC) == Seconds(SECOND // 2)
    with pytest.raises(AssertionError, match="cannot be negative"):
        span_literal(-per_frame, "f", anchor=0, rate=NTSC)


# ---- source time ----------------------------------------------------------------------


def test_source_frames_count_at_the_medias_own_rate():
    literal = parse_literal("f:705", Field.SOURCE)
    assert resolve_source(literal, media_rate=NTSC) == 705 * ticks_per_frame(NTSC)
    assert resolve_source(literal, media_rate=Fraction(120000, 1001)) == 705 * ticks_per_frame(Fraction(120000, 1001))
    assert resolve_source(parse_literal("8.2s", Field.SOURCE), media_rate=NTSC) == 82 * SECOND // 10


@pytest.mark.parametrize("rate", SUPPORTED_RATES, ids=str)
def test_source_literals_round_trip_at_every_rate(rate):
    per_frame = ticks_per_frame(rate)
    for index in FRAME_SAMPLES:
        literal = source_literal(index * per_frame, "f", media_rate=rate)
        text = format_literal(literal)
        assert resolve_source(parse_literal(text, Field.SOURCE), media_rate=rate) == index * per_frame


def test_kinds_do_not_cross():
    with pytest.raises(AssertionError, match="not a span"):
        resolve_span(BeatPosition(1, Fraction(0)), anchor=0, rate=NTSC, grid=uniform_grid(4, 1000))
    with pytest.raises(AssertionError, match="not a comp position"):
        resolve_position(BeatSpan(Fraction(1)), rate=NTSC, grid=uniform_grid(4, 1000))
    with pytest.raises(AssertionError, match="not a source time"):
        resolve_source(BeatSpan(Fraction(1)), media_rate=NTSC)
