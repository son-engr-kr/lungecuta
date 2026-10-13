"""An audio layer's effects (docs/design/07-audio.md, Effects), in order on its sound, and
its pan: the parts of that table whose parameters say everything there is to say.

- `highpass`, `lowpass`, `band`: Butterworth filters of the given order and cutoffs (the
  band a high-pass and a low-pass in turn), run forwards from the layer's first sample as
  a console's filter is, the convention the master chain's high-pass took (D15).
- `reverse`: the samples backwards.
- `repitch`: played faster or slower by a factor (2 to the semitones over 12, or a
  percent), pitch moving with speed, resampled by soxr; it plays the factor's worth of
  source for every sample it gives.
- `pan`: constant power, each channel `sqrt(1 -/+ pan)`, so the centre leaves both at
  0 dB and a hard pan gives the one channel 3 dB, the law a past project's audio builder
  used.

Ducking, the gate, soft clip and the reverb send wait for their definitions (an envelope
follower's attack, a clip curve, the impulse response), which the table does not give.
"""
from __future__ import annotations

import math

import numpy as np
import soxr

from .schema.comp import Band, Pass, Repitch, Reverse


def factor(effects: list) -> float:
    """How many source samples the effects play for every sample of the layer."""
    return math.prod(effect.factor for effect in effects if isinstance(effect, Repitch))


def source_length(effects: list, count: int) -> int:
    """The source samples a layer of `count` samples needs, a sample to spare for resampling."""
    rate = factor(effects)
    return count if rate == 1 else math.ceil(count * rate) + 1


def _butter(signal: np.ndarray, order: int, hz, kind: str, rate: int) -> np.ndarray:
    from scipy.signal import butter, sosfilt

    nyquist = rate / 2
    for value in np.atleast_1d(hz):
        assert value < nyquist, f"a cutoff of {value} Hz is at or above the Nyquist frequency, {nyquist} Hz"
    sos = butter(order, hz, btype=kind, fs=rate, output="sos")
    return sosfilt(sos, signal, axis=0)


def apply(effects: list, piece: np.ndarray, rate: int, count: int) -> np.ndarray:
    """The source piece (samples by channels, `source_length` of it) through the effects in
    order, `count` samples of it coming out."""
    signal = piece.astype(np.float64)
    for effect in effects:
        if isinstance(effect, Pass):
            signal = _butter(signal, effect.order, effect.hz, effect.type, rate)
        elif isinstance(effect, Band):
            signal = _butter(_butter(signal, effect.order, effect.low, "highpass", rate),
                             effect.order, effect.high, "lowpass", rate)
        elif isinstance(effect, Reverse):
            signal = signal[::-1]
        else:
            assert isinstance(effect, Repitch)
            signal = soxr.resample(np.ascontiguousarray(signal), rate * effect.factor, rate)
    assert signal.shape[0] >= count, f"the effects gave {signal.shape[0]} samples for {count}"
    return signal[:count].astype(np.float32)


def pan_gains(pan: float) -> np.ndarray:
    """Left and right gains for a pan from -1 (left) to 1 (right)."""
    return np.array([math.sqrt(1 - pan), math.sqrt(1 + pan)], np.float32)
