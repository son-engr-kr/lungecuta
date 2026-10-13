import numpy as np
import pytest

from lungecuta import audio

RATE = 48000


def tone(hz: float, seconds: float, db: float, phase: float = 0.0) -> np.ndarray:
    t = np.arange(round(seconds * RATE)) / RATE
    wave = 10 ** (db / 20) * np.sin(2 * np.pi * hz * t + phase)
    return np.stack([wave, wave], axis=1)


def level_db(signal: np.ndarray) -> float:
    middle = signal[len(signal) // 4: 3 * len(signal) // 4]
    return 20 * np.log10(np.sqrt(np.mean(middle ** 2)) * np.sqrt(2))


def test_the_high_pass_removes_rumble_and_keeps_the_music():
    assert level_db(audio.high_pass(tone(1000, 2, -6), RATE)) == pytest.approx(-6, abs=0.01)
    # a second-order Butterworth at 28 Hz: 12 dB per octave below the corner, -3 dB at it
    assert level_db(audio.high_pass(tone(28, 4, 0), RATE)) == pytest.approx(-3.01, abs=0.05)
    assert level_db(audio.high_pass(tone(7, 8, 0), RATE)) < -23


def test_loudness_follows_bs1770_for_a_sine():
    # ITU-R BS.1770: a 997 Hz sine at 0 dBFS in one channel reads -3.01 LKFS; in both, 0.
    assert audio.loudness(tone(997, 5, -20), RATE) == pytest.approx(-20.0, abs=0.05)
    assert audio.loudness(np.zeros((RATE * 5, 2)), RATE) == float("-inf")


def test_true_peak_finds_the_peak_between_samples():
    # a sine at a quarter of the rate, sampled 45 degrees off its crests: every sample is
    # at -3.01 dB of a peak that falls between them
    signal = tone(RATE / 4, 1, -1, phase=np.pi / 4)
    assert 20 * np.log10(np.abs(signal).max()) == pytest.approx(-4.01, abs=0.01)
    assert audio.true_peak_db(signal, RATE) == pytest.approx(-1.0, abs=0.1)


def test_the_gain_is_down_before_the_peak_and_recovers_with_the_release():
    need = np.ones(RATE)
    need[24000] = 0.5
    gain = audio._gain(need, RATE)
    assert (gain <= need + 1e-12).all()
    window = round(audio.LOOKAHEAD_S * RATE)
    assert gain[24000 - window - 1] == pytest.approx(1.0)  # untouched before the look-ahead
    assert gain[24000 - window // 2] < 1.0  # already moving
    # one release time constant after the peak, the cut has recovered to 1/e of its depth
    later = 24000 + round(audio.RELEASE_S * RATE)
    assert 1 - gain[later] == pytest.approx(0.5 * np.exp(-1), rel=0.01)


def test_the_limiter_holds_the_ceiling_between_samples():
    rng = np.random.default_rng(1)
    signal = rng.normal(0, 0.5, (RATE * 3, 2))
    out, cut = audio.limit(signal, RATE, -1.0)
    assert audio.true_peak_db(out, RATE) <= -1.0
    assert cut < -6


def test_the_limiter_leaves_a_signal_under_the_ceiling_alone():
    signal = tone(440, 1, -6)
    out, cut = audio.limit(signal, RATE, -1.0)
    assert np.array_equal(out, signal) and cut == 0.0


def test_master_normalizes_then_limits():
    quiet = tone(997, 6, -30)
    out, report = audio.master(quiet, RATE, target_lufs=-14.0, ceiling_dbtp=-1.0)
    assert report["loudness_after"] == pytest.approx(-14.0, abs=0.01)
    assert report["limiter_deepest_db"] == 0.0 and report["true_peak_after"] <= -1.0
    assert out.dtype == np.float32

    rng = np.random.default_rng(2)
    noisy = rng.normal(0, 0.05, (RATE * 6, 2))
    noisy[RATE * 3: RATE * 3 + 200] *= 15  # a spike far above the rest
    out, report = audio.master(noisy, RATE, target_lufs=-14.0, ceiling_dbtp=-1.0)
    assert report["true_peak_after"] <= -1.0 and report["limiter_deepest_db"] < -3
    assert report["loudness_after"] == pytest.approx(-14.0, abs=0.01)


def test_master_leaves_silence_silent():
    out, report = audio.master(np.zeros((RATE, 2), np.float32), RATE, target_lufs=-14.0, ceiling_dbtp=-1.0)
    assert report == {"silent": True} and not out.any()
