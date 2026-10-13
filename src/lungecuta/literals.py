"""Time literals: how times are written in project files.

The grammar is docs/design/03-project-format.md#literal-grammar. `parse_literal` reads the
literal of one kind of field into a value that keeps the unit it was written in, and
`format_literal` writes it back in its canonical spelling. The `resolve_*` functions turn
a value into ticks against what it needs (a frame rate, a beat grid, an anchor); the
`*_literal` functions go the other way, from ticks to a value in a chosen unit, and refuse
a tick count the unit cannot express exactly.
"""
from __future__ import annotations

import bisect
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

from .ticks import TICKS_PER_SECOND, round_half_up, ticks_per_frame

# Five decimal places of a second are always exact: 705,600,000 = 2^9 * 3^2 * 5^5 * 7^2.
SECONDS_DIGITS = 5
TICKS_PER_SECONDS_STEP = TICKS_PER_SECOND // 10**SECONDS_DIGITS
assert TICKS_PER_SECONDS_STEP * 10**SECONDS_DIGITS == TICKS_PER_SECOND

# Writing a tick count as a beat literal takes the shortest decimal that resolves back to
# it. Twelve digits always find one: 1e-12 of a beat is under half a tick for any beat
# shorter than 708 s.
MAX_BEAT_DIGITS = 12

UNITS = ("s", "f", "b", "bar", "tc")


class Field(Enum):
    """What a time field means, which decides the literals it accepts. Every time field
    in the schema is declared as one of these."""

    POSITION = "a comp position"
    LAYER = "layer time"  # a span from a layer's start, at least 0
    STYLE = "style time"  # a span from a style's anchor, negative before it
    SOURCE = "source time"  # a point in a media file, from its first presented frame
    # a footage layer's source time at its layer time 0, which is before the media starts
    # when the layer's in point is later than the media's first frame there (a split's part)
    SOURCE_START = "a layer's source start"


@dataclass(frozen=True)
class Frames:
    """A frame index (a position) or a frame count (a span)."""

    count: int


@dataclass(frozen=True)
class Seconds:
    ticks: int


@dataclass(frozen=True)
class BeatPosition:
    """Beat `index` of the grid plus `offset`, a fraction of the interval it lands in."""

    index: int
    offset: Fraction


@dataclass(frozen=True)
class BeatSpan:
    """A number of beats on the grid, counted from an anchor's place on it."""

    count: Fraction


@dataclass(frozen=True)
class Bar:
    """Downbeat `index` of the grid."""

    index: int


@dataclass(frozen=True)
class Timecode:
    """Non-drop-frame timecode at the comp's rate."""

    hours: int
    minutes: int
    seconds: int
    frames: int


TimeLiteral = Frames | Seconds | BeatPosition | BeatSpan | Bar | Timecode

_DECIMAL = r"\d+(?:\.\d+)?"
_SECONDS = re.compile(r"(-?)(\d+)(?:\.(\d+))?s")
_FRAMES = re.compile(r"f:(-?\d+)((?:[+-]\d+)*)")
_FRAME_OFFSET = re.compile(r"[+-]\d+")
_BEATS = re.compile(rf"b:(-?{_DECIMAL})((?:[+-]{_DECIMAL})*)")
_BEAT_OFFSET = re.compile(rf"[+-]{_DECIMAL}")
_BAR = re.compile(r"bar:(\d+)")
_TIMECODE = re.compile(r"tc:(\d{2}):(\d{2}):(\d{2}):(\d{2})")
_DROP_FRAME = re.compile(r"tc:\d{2}:\d{2}:\d{2};\d{2}")


def parse_literal(text: str, field: Field) -> TimeLiteral:
    """Read `text` as the literal of a `field`. Anything the grammar does not define, or
    the field does not accept, is an error; nothing is rounded or guessed."""
    assert isinstance(text, str), f"a time is written as a string with a unit, not {text!r}"
    if match := _SECONDS.fullmatch(text):
        return _parse_seconds(match, text, field)
    if match := _FRAMES.fullmatch(text):
        return _parse_frames(match, text, field)
    if match := _BEATS.fullmatch(text):
        return _parse_beats(match, text, field)
    if match := _BAR.fullmatch(text):
        assert field is Field.POSITION, f"{text!r}: a bar names a position, and {field.value} takes none"
        return Bar(int(match[1]))
    assert not _DROP_FRAME.fullmatch(text), f"{text!r}: drop-frame timecode is not supported"
    match = _TIMECODE.fullmatch(text)
    assert match, (
        f"{text!r} is not a time literal (f:120, 12.5s, b:64+0.5, bar:17, tc:00:00:12:15)"
    )
    assert field is Field.POSITION, f"{text!r}: timecode names a comp position, and {field.value} takes none"
    hours, minutes, seconds, frames = (int(group) for group in match.groups())
    assert minutes < 60 and seconds < 60, f"{text!r}: minutes and seconds run from 00 to 59"
    return Timecode(hours, minutes, seconds, frames)


def _parse_seconds(match: re.Match[str], text: str, field: Field) -> Seconds:
    sign, whole, fraction = match.groups()
    fraction = fraction or ""
    assert len(fraction) <= SECONDS_DIGITS, (
        f"{text!r}: seconds take at most {SECONDS_DIGITS} decimal places, the ones that are "
        "exact in ticks; more would have to be rounded"
    )
    steps = int(whole) * 10**SECONDS_DIGITS + int(fraction.ljust(SECONDS_DIGITS, "0"))
    ticks = -steps * TICKS_PER_SECONDS_STEP if sign else steps * TICKS_PER_SECONDS_STEP
    assert ticks >= 0 or field in (Field.STYLE, Field.SOURCE_START), f"{text!r}: {field.value} cannot be negative"
    return Seconds(ticks)


def _parse_frames(match: re.Match[str], text: str, field: Field) -> Frames:
    base = int(match[1])
    count = base + sum(int(offset) for offset in _FRAME_OFFSET.findall(match[2]))
    if field not in (Field.STYLE, Field.SOURCE_START):
        assert base >= 0 and count >= 0, f"{text!r}: {field.value} cannot be negative"
    return Frames(count)


def _parse_beats(match: re.Match[str], text: str, field: Field) -> BeatPosition | BeatSpan:
    assert field not in (Field.SOURCE, Field.SOURCE_START), (
        f"{text!r}: source time cannot be written in beats, because a beat grid belongs to a comp"
    )
    base_text = match[1]
    offset = sum((Fraction(item) for item in _BEAT_OFFSET.findall(match[2])), Fraction(0))
    if field is Field.POSITION:
        assert "." not in base_text and not base_text.startswith("-"), (
            f"{text!r}: a beat position starts from a whole beat index; "
            "write the fraction as an offset (b:64+0.5)"
        )
        return BeatPosition(int(base_text), offset)
    count = Fraction(base_text) + offset
    assert count >= 0 or field is Field.STYLE, f"{text!r}: {field.value} cannot be negative"
    return BeatSpan(count)


def format_literal(literal: TimeLiteral) -> str:
    """The canonical spelling: no leading zeros, offsets reduced to one, `+0` dropped,
    a frame offset folded into its index, a beat span as one decimal count."""
    match literal:
        case Frames(count):
            return f"f:{count}"
        case Seconds(ticks):
            return f"{_decimal(Fraction(ticks, TICKS_PER_SECOND))}s"
        case BeatPosition(index, offset):
            if offset == 0:
                return f"b:{index}"
            sign = "+" if offset > 0 else "-"
            return f"b:{index}{sign}{_decimal(abs(offset))}"
        case BeatSpan(count):
            return f"b:{_decimal(count)}"
        case Bar(index):
            return f"bar:{index}"
        case Timecode(hours, minutes, seconds, frames):
            return f"tc:{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"
    raise AssertionError(f"{literal!r} is not a time literal")


def _decimal(value: Fraction) -> str:
    """A terminating fraction as the shortest decimal that is exactly it."""
    digits = 0
    while (value * 10**digits).denominator != 1:
        digits += 1
        assert digits <= 40, f"{value} is not a terminating decimal"
    scaled = abs(value * 10**digits).numerator
    whole, fraction = divmod(scaled, 10**digits)
    text = f"{whole}.{fraction:0{digits}d}" if digits else str(whole)
    return f"-{text}" if value < 0 else text


@dataclass(frozen=True)
class BeatGrid:
    """A comp's beats in comp time: `times` in ticks, strictly increasing, and the indices
    of the downbeats among them. A time between two beats is a fraction of that interval,
    rounded to the nearest tick (halves up), the one rounding a beat time goes through."""

    times: tuple[int, ...]
    downbeats: tuple[int, ...]

    def __post_init__(self) -> None:
        assert len(self.times) >= 2, "a beat grid needs at least two beats"
        assert self.times[0] >= 0, "a beat grid cannot start before the comp"
        assert all(a < b for a, b in zip(self.times, self.times[1:])), "beat times must increase"
        assert all(a < b for a, b in zip(self.downbeats, self.downbeats[1:])), "downbeats must increase"
        assert all(0 <= index < len(self.times) for index in self.downbeats), (
            "every downbeat must be one of the beats"
        )

    @property
    def last(self) -> int:
        return len(self.times) - 1

    def contains(self, coordinate: Fraction) -> bool:
        return 0 <= coordinate <= self.last

    def time_at(self, coordinate: Fraction) -> int:
        """The tick at a place on the grid, `index + fraction of the next interval`."""
        assert self.contains(coordinate), (
            f"beat {_decimal(coordinate)} is outside the beat grid (beats 0 to {self.last})"
        )
        index = math.floor(coordinate)
        if index == self.last:
            return self.times[index]
        start, end = self.times[index], self.times[index + 1]
        return round_half_up(start + (coordinate - index) * (end - start))

    def coordinate_at(self, ticks: int) -> Fraction:
        """The exact place of a tick on the grid; the inverse of `time_at` before rounding."""
        assert self.times[0] <= ticks <= self.times[-1], (
            f"{ticks} ticks is outside the beat grid ({self.times[0]} to {self.times[-1]})"
        )
        index = bisect.bisect_right(self.times, ticks) - 1
        if index == self.last:
            return Fraction(index)
        start, end = self.times[index], self.times[index + 1]
        return index + Fraction(ticks - start, end - start)

    def downbeat(self, index: int) -> int:
        assert 0 <= index < len(self.downbeats), (
            f"bar:{index} is outside the beat grid (bars 0 to {len(self.downbeats) - 1})"
        )
        return self.times[self.downbeats[index]]


def timecode_base(rate: Fraction) -> int:
    """Frames per timecode second: the rate rounded up, so 30 at 29.97 (non-drop-frame).
    The frames field has two digits, so timecode is defined up to 100 fps."""
    ticks_per_frame(rate)  # asserts the rate is supported
    base = math.ceil(rate)
    assert base <= 100, f"timecode has two frame digits and is not defined at {rate} fps"
    return base


def resolve_position(literal: TimeLiteral, *, rate: Fraction, grid: BeatGrid | None = None) -> int:
    """A comp position in ticks, at the comp's `rate` and on its beat `grid`."""
    match literal:
        case Frames(count):
            return count * ticks_per_frame(rate)
        case Seconds(ticks):
            return ticks
        case BeatPosition(index, offset):
            assert grid is not None, f"{format_literal(literal)}: the comp has no beat grid"
            return grid.time_at(index + offset)
        case Bar(index):
            assert grid is not None, f"{format_literal(literal)}: the comp has no beat grid"
            return grid.downbeat(index)
        case Timecode(hours, minutes, seconds, frames):
            base = timecode_base(rate)
            assert frames < base, f"{format_literal(literal)}: frames run from 00 to {base - 1} at {rate} fps"
            return (((hours * 60 + minutes) * 60 + seconds) * base + frames) * ticks_per_frame(rate)
    raise AssertionError(f"{format_literal(literal)} is not a comp position")


def resolve_span(
    literal: TimeLiteral, *, anchor: int, rate: Fraction, grid: BeatGrid | None = None
) -> int:
    """The length in ticks of a span that starts at the comp position `anchor`. A beat
    span counts beats on the grid from the anchor's own place on it."""
    match literal:
        case Frames(count):
            return count * ticks_per_frame(rate)
        case Seconds(ticks):
            return ticks
        case BeatSpan(count):
            assert grid is not None, f"{format_literal(literal)}: the comp has no beat grid"
            return grid.time_at(grid.coordinate_at(anchor) + count) - anchor
    raise AssertionError(f"{format_literal(literal)} is not a span")


def resolve_source(literal: TimeLiteral, *, media_rate: Fraction) -> int:
    """A source time in ticks from the media's first presented frame, `f:` at its own rate."""
    match literal:
        case Frames(count):
            return count * ticks_per_frame(media_rate)
        case Seconds(ticks):
            return ticks
    raise AssertionError(f"{format_literal(literal)} is not a source time")


def position_literal(
    ticks: int, unit: str, *, rate: Fraction, grid: BeatGrid | None = None
) -> TimeLiteral:
    """The comp position `ticks` written in `unit`; an error if the unit cannot hold it."""
    assert unit in UNITS, f"unknown unit {unit!r} (units: {', '.join(UNITS)})"
    assert ticks >= 0, f"a comp position cannot be negative ({ticks} ticks)"
    if unit == "s":
        return _seconds(ticks)
    if unit == "f":
        return Frames(_whole_frames(ticks, rate))
    if unit == "tc":
        frames = _whole_frames(ticks, rate)
        base = timecode_base(rate)
        seconds, frame = divmod(frames, base)
        minutes, second = divmod(seconds, 60)
        hours, minute = divmod(minutes, 60)
        assert hours <= 99, f"{ticks} ticks is beyond tc:99:59:59"
        return Timecode(hours, minute, second, frame)
    assert grid is not None, f"{ticks} ticks cannot be written in beats: the comp has no beat grid"
    if unit == "bar":
        time_of = {grid.times[index]: bar for bar, index in enumerate(grid.downbeats)}
        assert ticks in time_of, f"{ticks} ticks is not on a downbeat"
        return Bar(time_of[ticks])
    coordinate = grid.coordinate_at(ticks)
    index = math.floor(coordinate)
    offset = _shortest(coordinate - index, lambda d: _on_grid(grid, index + d), ticks)
    if offset == 1:
        return BeatPosition(index + 1, Fraction(0))
    return BeatPosition(index, offset)


def span_literal(
    length: int,
    unit: str,
    *,
    anchor: int,
    rate: Fraction,
    grid: BeatGrid | None = None,
    field: Field = Field.LAYER,
) -> TimeLiteral:
    """The span `length` from the comp position `anchor`, written in `unit`."""
    assert field in (Field.LAYER, Field.STYLE), f"{field.value} is not a span"
    assert unit in ("s", "f", "b"), f"a span is written in s, f or b, not {unit!r}"
    assert length >= 0 or field is Field.STYLE, f"{field.value} cannot be negative ({length} ticks)"
    if unit == "s":
        return _seconds(length)
    if unit == "f":
        return Frames(_whole_frames(length, rate))
    assert grid is not None, f"{length} ticks cannot be written in beats: the comp has no beat grid"
    start = grid.coordinate_at(anchor)
    exact = grid.coordinate_at(anchor + length) - start
    return BeatSpan(_shortest(exact, lambda d: _on_grid(grid, start + d), anchor + length))


def source_literal(ticks: int, unit: str, *, media_rate: Fraction, start: bool = False) -> TimeLiteral:
    """The source time `ticks` written in seconds or in frames at the media's rate; a
    layer's source `start` may be negative (Field.SOURCE_START)."""
    assert unit in ("s", "f"), f"source time is written in s or f, not {unit!r}"
    assert ticks >= 0 or start, f"source time cannot be negative ({ticks} ticks)"
    if unit == "s":
        return _seconds(ticks)
    return Frames(_whole_frames(ticks, media_rate))


def _seconds(ticks: int) -> Seconds:
    assert ticks % TICKS_PER_SECONDS_STEP == 0, (
        f"{ticks} ticks is not exact in {SECONDS_DIGITS} decimal places of a second"
    )
    return Seconds(ticks)


def _whole_frames(ticks: int, rate: Fraction) -> int:
    count, remainder = divmod(ticks, ticks_per_frame(rate))
    assert remainder == 0, f"{ticks} ticks is not a whole number of frames at {rate} fps"
    return count


def _on_grid(grid: BeatGrid, coordinate: Fraction) -> int | None:
    return grid.time_at(coordinate) if grid.contains(coordinate) else None


def _shortest(exact: Fraction, resolve: Callable[[Fraction], int | None], target: int) -> Fraction:
    """The decimal with the fewest digits near `exact` that `resolve` maps to `target`."""
    for digits in range(MAX_BEAT_DIGITS + 1):
        step = Fraction(1, 10**digits)
        candidate = round_half_up(exact / step) * step
        if resolve(candidate) == target:
            return candidate
    raise AssertionError(f"no beat literal of up to {MAX_BEAT_DIGITS} digits lands on {target} ticks")
