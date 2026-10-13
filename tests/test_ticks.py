from fractions import Fraction

import pytest

from lungecuta.ticks import (
    AUDIO_RATES,
    SUPPORTED_RATES,
    TICKS_PER_SECOND,
    first_frame_at_or_after,
    format_rate,
    nearest_sample,
    parse_rate,
    ticks_per_frame,
    ticks_per_sample,
)

# The table in docs/design/03-project-format.md#the-unit.
DOCUMENTED_TICKS_PER_FRAME = {
    Fraction(24000, 1001): 29_429_400,
    Fraction(30000, 1001): 23_543_520,
    Fraction(60000, 1001): 11_771_760,
    Fraction(120000, 1001): 5_885_880,
}
DOCUMENTED_TICKS_PER_SAMPLE = {44_100: 16_000, 48_000: 14_700, 96_000: 7_350}


def test_the_tick_divides_every_supported_rate():
    for rate in SUPPORTED_RATES:
        assert ticks_per_frame(rate) * rate == TICKS_PER_SECOND
    for rate, ticks in DOCUMENTED_TICKS_PER_FRAME.items():
        assert ticks_per_frame(rate) == ticks
    for rate in AUDIO_RATES:
        assert ticks_per_sample(rate) == DOCUMENTED_TICKS_PER_SAMPLE[rate]


def test_the_tick_factors_as_documented():
    assert TICKS_PER_SECOND == 2**9 * 3**2 * 5**5 * 7**2


def test_a_signed_64_bit_count_lasts_414_years():
    years = (2**63 - 1) // TICKS_PER_SECOND // int(365.25 * 86400)
    assert years == 414


@pytest.mark.parametrize("text", ["30000/1001", "60/1", "60", "24000/1001", "25"])
def test_rates_parse_and_format(text):
    rate = parse_rate(text)
    assert rate in SUPPORTED_RATES
    assert parse_rate(format_rate(rate)) == rate


def test_rates_have_one_spelling():
    assert format_rate(parse_rate("60/1")) == "60"
    assert format_rate(parse_rate("30000/1001")) == "30000/1001"


@pytest.mark.parametrize("text", ["2997/100", "15", "29.97", "0", "30000/0", "-30"])
def test_unsupported_or_malformed_rates_are_errors(text):
    with pytest.raises(AssertionError):
        parse_rate(text)


def test_unsupported_audio_rate_is_an_error():
    with pytest.raises(AssertionError, match="not supported"):
        ticks_per_sample(22_050)


def test_a_cut_lands_on_the_first_frame_at_or_after_it():
    rate = Fraction(30000, 1001)
    per_frame = ticks_per_frame(rate)
    assert first_frame_at_or_after(0, rate) == 0
    assert first_frame_at_or_after(per_frame, rate) == 1
    assert first_frame_at_or_after(per_frame + 1, rate) == 2
    assert first_frame_at_or_after(per_frame - 1, rate) == 1


def test_a_beat_between_frames_cuts_on_the_next_frame():
    # 128 BPM at 60 fps: a beat is 28.125 frames, so beat 1 cuts on frame 29.
    beat = TICKS_PER_SECOND * 60 // 128
    assert Fraction(beat, ticks_per_frame(Fraction(60))) == Fraction(225, 8)
    assert first_frame_at_or_after(beat, Fraction(60)) == 29


def test_audio_lands_on_the_nearest_sample():
    # At 29.97p and 48 kHz a frame is 1601.6 samples, so frame 1's boundary is sample 1602.
    per_frame = ticks_per_frame(Fraction(30000, 1001))
    assert Fraction(per_frame, ticks_per_sample(48_000)) == Fraction(8008, 5)
    assert nearest_sample(per_frame, 48_000) == 1602
    assert nearest_sample(2 * per_frame, 48_000) == 3203  # 3203.2


def test_an_audio_time_half_a_sample_in_rounds_up():
    half = ticks_per_sample(48_000) // 2
    assert nearest_sample(half - 1, 48_000) == 0
    assert nearest_sample(half, 48_000) == 1


def test_negative_times_do_not_land_anywhere():
    with pytest.raises(AssertionError):
        first_frame_at_or_after(-1, Fraction(60))
    with pytest.raises(AssertionError):
        nearest_sample(-1, 48_000)
