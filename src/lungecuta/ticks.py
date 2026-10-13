"""The tick, the one unit of time inside lungecuta, and the grids times land on.

There are 705,600,000 ticks in a second (the "flick"). That count divides exactly into a
frame at every supported rate and into a sample at every supported audio rate, so
mixed-rate timelines never accumulate rounding (docs/design/03-project-format.md#the-unit).
Rounding happens only where a time has to land on a frame or a sample, by the rules in
docs/design/03-project-format.md#from-time-to-frames-and-samples.
"""
from __future__ import annotations

import math
import re
from fractions import Fraction

TICKS_PER_SECOND = 705_600_000

# The supported frame rates: a source at any other rate is an error at import.
SUPPORTED_RATES: tuple[Fraction, ...] = (
    Fraction(24000, 1001),
    Fraction(30000, 1001),
    Fraction(60000, 1001),
    Fraction(120000, 1001),
    Fraction(24),
    Fraction(25),
    Fraction(30),
    Fraction(48),
    Fraction(50),
    Fraction(60),
    Fraction(100),
    Fraction(120),
    Fraction(240),
)

AUDIO_RATES: tuple[int, ...] = (44_100, 48_000, 96_000)

_RATE = re.compile(r"([1-9]\d*)(?:/([1-9]\d*))?")


def parse_rate(text: str) -> Fraction:
    """A frame rate as written in files and by ffprobe: `30000/1001`, `60/1` or `60`."""
    match = _RATE.fullmatch(text)
    assert match, f"frame rate {text!r} is not a rational like 30000/1001 or an integer"
    rate = Fraction(int(match[1]), int(match[2] or 1))
    assert_supported_rate(rate)
    return rate


def format_rate(rate: Fraction) -> str:
    """The canonical spelling: `30000/1001`, or `60` for an integer rate."""
    assert_supported_rate(rate)
    return str(rate.numerator) if rate.denominator == 1 else f"{rate.numerator}/{rate.denominator}"


def assert_supported_rate(rate: Fraction) -> None:
    supported = ", ".join(str(r) for r in SUPPORTED_RATES)
    assert rate in SUPPORTED_RATES, f"frame rate {rate} is not supported (supported: {supported})"


def ticks_per_frame(rate: Fraction) -> int:
    assert_supported_rate(rate)
    per_frame = TICKS_PER_SECOND / rate
    assert per_frame.denominator == 1, f"{rate} fps does not divide the tick"
    return int(per_frame)


def ticks_per_sample(sample_rate: int) -> int:
    assert sample_rate in AUDIO_RATES, (
        f"audio rate {sample_rate} Hz is not supported (supported: {AUDIO_RATES})"
    )
    per_sample, remainder = divmod(TICKS_PER_SECOND, sample_rate)
    assert remainder == 0, f"{sample_rate} Hz does not divide the tick"
    return per_sample


def round_half_up(value: Fraction) -> int:
    """The nearest integer, halves rounding up (towards positive infinity)."""
    return math.floor(value + Fraction(1, 2))


def first_frame_at_or_after(ticks: int, rate: Fraction) -> int:
    """The frame a cut at `ticks` lands on: the first frame boundary at or after it."""
    assert ticks >= 0, f"a cut cannot be before the start of the comp ({ticks} ticks)"
    return -(-ticks // ticks_per_frame(rate))


def nearest_sample(ticks: int, sample_rate: int) -> int:
    """The sample an audio time lands on: the nearest one, halves rounding up."""
    assert ticks >= 0, f"an audio time cannot be before the start of the comp ({ticks} ticks)"
    return round_half_up(Fraction(ticks, ticks_per_sample(sample_rate)))
