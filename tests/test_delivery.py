"""The search for the master's ceiling under which a lossy encode's true peak holds the
preset's ceiling (`lungecuta.mixdown.under_ceiling`), on the ladder measured on a real
comp's AAC encode (bench/aac_peak_ladder.py, bench/RESULTS.md)."""
import pytest

from lungecuta.mixdown import PEAK_STEP, under_ceiling

# ceiling -> the encoded true peak, measured: flat from -1.0 to -1.06, up at -1.078
LADDER = {-1.0: -0.932, -1.02: -0.932, -1.04: -0.932, -1.06: -0.932, -1.078: -0.913, -1.1: -1.02, -1.12: -1.062,
          -1.15: -1.171, -1.2: -1.241, -1.25: -1.3, -1.3: -1.313, -1.4: -1.37, -1.5: -1.351}


def measured(asked: list[float]):
    def peak_at(ceiling: float) -> float:
        asked.append(ceiling)
        return LADDER[min(LADDER, key=lambda c: abs(c - ceiling))]  # the nearest rung measured
    return peak_at


def test_the_ceiling_only_falls_and_ends_under_the_limit_where_the_peak_did_not_follow():
    asked = []
    ceiling, peak = under_ceiling(measured(asked), -1.0)
    assert peak <= -1.0 and ceiling == asked[-1]
    assert all(b < a - PEAK_STEP for a, b in zip(asked, asked[1:])), asked
    assert asked[:2] == [-1.0, pytest.approx(-1.078)]  # the pass the secant failed on, then one more


def test_a_peak_held_just_over_the_limit_still_ends():
    asked = []
    ceiling, peak = under_ceiling(lambda c: asked.append(c) or (-0.999 if c > -1.05 else -1.01), -1.0)
    # 0.001 over the limit lowers the ceiling 0.011 a pass, so a flat 0.05 dB wide takes five passes
    assert peak == -1.01 and len(asked) == 6


def test_a_peak_that_never_falls_is_an_error():
    with pytest.raises(AssertionError, match="stayed over -1.0 dBTP after 3 passes"):
        under_ceiling(lambda c: -0.5, -1.0, passes=3)
