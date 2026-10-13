"""The master chain (docs/design/07-audio.md, Layers, buses, master): a high-pass at 28 Hz,
the gain that brings the mix to the preset's integrated loudness, then a true-peak limiter
last, so normalizing cannot move the peaks the limiter set.

- The high-pass is a second-order Butterworth (D15: 07-audio.md gives the corner,
  not the order), applied forwards like a console's filter.
- Loudness is ITU-R BS.1770 integrated loudness by pyloudnorm, which agrees with ffmpeg's
  `ebur128` on the music sample (-14.36 against -14.3 LUFS, `bench/RESULTS.md`).
- True peak is the largest sample after 4x oversampling by soxr, which agrees with
  `ebur128`'s true-peak meter on the same file (-1.89 against -1.9 dBTP).
- The limiter looks ahead 5 ms and releases with a 110 ms time constant (07-audio.md:
  5 ms look-ahead, 100 to 120 ms release; 110 is the middle). The gain a sample needs is
  the ceiling over its true peak; the gain applied is the smallest needed over the next
  5 ms, averaged over 5 ms (so it is already down when the peak arrives), and recovering
  from each dip exponentially. The result is measured again, and limited again against the
  overshoot if gain changes made new inter-sample peaks.
"""
from __future__ import annotations

from typing import Any

import numpy as np

HIGH_PASS_HZ = 28.0  # 07-audio.md
LOOKAHEAD_S = 0.005  # 07-audio.md
RELEASE_S = 0.110  # within 07-audio.md's 100 to 120 ms
OVERSAMPLE = 4


def high_pass(signal: np.ndarray, rate: int) -> np.ndarray:
    from scipy.signal import butter, sosfilt

    sos = butter(2, HIGH_PASS_HZ, btype="highpass", fs=rate, output="sos")
    return sosfilt(sos, signal.astype(np.float64), axis=0)


def loudness(signal: np.ndarray, rate: int) -> float:
    """BS.1770 integrated loudness in LUFS; -inf for silence."""
    import pyloudnorm

    return float(pyloudnorm.Meter(rate).integrated_loudness(signal.astype(np.float64)))


def peaks(signal: np.ndarray, rate: int) -> np.ndarray:
    """Each sample's true peak over its channels: the largest magnitude of the 4x
    oversampled signal within that sample's time."""
    import soxr

    up = np.abs(soxr.resample(signal.astype(np.float64), rate, rate * OVERSAMPLE)).max(axis=1)
    count = signal.shape[0]
    up = np.pad(up, (0, max(0, count * OVERSAMPLE - up.size)))[:count * OVERSAMPLE]
    return np.maximum(up.reshape(count, OVERSAMPLE).max(axis=1), np.abs(signal).max(axis=1))


def true_peak_db(signal: np.ndarray, rate: int) -> float:
    top = float(peaks(signal, rate).max()) if signal.size else 0.0
    return float(20 * np.log10(top)) if top > 0 else float("-inf")


def _gain(need: np.ndarray, rate: int) -> np.ndarray:
    """From the gain each sample needs to the gain applied: the least needed over the next
    5 ms, averaged over 5 ms, recovering from each dip with the release's time constant.
    The recovery solves d[t] = max(d_in[t], a d[t - 1]) for the dip d = 1 - gain exactly,
    as a running maximum in the log domain."""
    from scipy.ndimage import minimum_filter1d, uniform_filter1d

    window = max(1, round(LOOKAHEAD_S * rate))
    # minimum over [t, t + window): a filter centred `window // 2` later
    ahead = minimum_filter1d(need, size=window, origin=-(window // 2), mode="nearest")
    smooth = uniform_filter1d(ahead, size=window, origin=(window - 1) // 2, mode="nearest")
    smooth = np.minimum(smooth, ahead)
    dip = np.clip(1 - smooth, 0, 1)
    log_a = -1 / (RELEASE_S * rate)
    index = np.arange(dip.size, dtype=np.float64)
    with np.errstate(divide="ignore"):
        key = np.log(dip) - index * log_a
    recovered = np.exp(np.maximum.accumulate(key) + index * log_a)
    return 1 - np.maximum(recovered, dip)


PASSES = 8


def limit(signal: np.ndarray, rate: int, ceiling_db: float) -> tuple[np.ndarray, float]:
    """The signal with its true peak at or under `ceiling_db`; also the deepest gain cut in dB.

    A gain that changes from sample to sample changes the inter-sample peaks too, so one
    pass leaves overshoots of a few thousandths of a dB. Each further pass aims under the
    ceiling by the overshoot the previous pass measured."""
    target = 10 ** (ceiling_db / 20)
    signal = signal.astype(np.float64)
    out, gain, aim = signal, np.ones(signal.shape[0]), target
    for k in range(PASSES):
        level = peaks(out, rate)
        top = float(level.max()) if level.size else 0.0
        if top <= target:
            return out, float(20 * np.log10(float(gain.min()) if gain.size else 1.0))
        if k:
            aim *= target / top
        need = np.minimum(1.0, aim / np.maximum(level, np.finfo(np.float64).tiny))
        gain = gain * _gain(need, rate)
        out = signal * gain[:, None]
    raise AssertionError(f"the limiter did not reach {ceiling_db} dBTP in {PASSES} passes (top {20 * np.log10(top):.4f})")


LOUDNESS_STEP = 0.01  # LU: the precision the loudness is reported at
GAIN_PASSES = 8


def master(mix: np.ndarray, rate: int, *, target_lufs: float, ceiling_dbtp: float) -> tuple[np.ndarray, dict[str, Any]]:
    """The mix through the master chain, and what each stage did.

    The normalization gain is applied before the limiter, and the limiter lowers the
    loudness of a mix whose peaks it cuts; so the result is measured again and the gain
    before the limiter raised by what is missing, until the loudness is at the target to
    the reported precision (D15: 07-audio.md fixes the order, not this). A mix that
    gains nothing from more gain cannot reach the target under the ceiling: an error."""
    filtered = high_pass(mix, rate)
    measured = loudness(filtered, rate)
    if not np.isfinite(measured):  # silence has no loudness to normalize
        return filtered.astype(np.float32), {"silent": True}
    gain_db, previous = target_lufs - measured, None
    for _ in range(GAIN_PASSES):
        limited, cut_db = limit(filtered * 10 ** (gain_db / 20), rate, ceiling_dbtp)
        after = loudness(limited, rate)
        if abs(after - target_lufs) <= LOUDNESS_STEP:
            break
        # the next gain by the secant through the last two passes: under limiting, each
        # dB of gain adds less than a dB of loudness
        slope = 1.0
        if previous is not None:
            assert after > previous[1] + LOUDNESS_STEP, (
                f"raising the gain to {gain_db:.2f} dB left the loudness at {after:.2f} LUFS: {target_lufs} LUFS "
                f"cannot be reached under {ceiling_dbtp} dBTP")
            slope = (after - previous[1]) / (gain_db - previous[0])
        previous = (gain_db, after)
        gain_db += (target_lufs - after) / slope
    else:
        raise AssertionError(f"the master did not reach {target_lufs} LUFS in {GAIN_PASSES} passes (at {after:.2f})")
    report = {
        "loudness_before": round(measured, 2), "gain_db": round(gain_db, 2), "limiter_deepest_db": round(cut_db, 2),
        "loudness_after": round(after, 2), "true_peak_after": round(true_peak_db(limited, rate), 2),
        "target_lufs": target_lufs, "ceiling_dbtp": round(ceiling_dbtp, 3),
    }
    return limited.astype(np.float32), report
