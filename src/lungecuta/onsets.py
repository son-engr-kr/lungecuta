"""Onsets by SuperFlux (docs/design/07-audio.md, Analysis and checks): S. Boeck and
G. Widmer, "Maximum filter vibrato suppression for onset detection", Proc. DAFx-13,
Maynooth, 2013 (dafx.de paper archive, read 2026-10-13), with the details the paper
leaves out taken from its authors' BSD-licensed reference implementation
(github.com/CPJKU/SuperFlux, `SuperFlux.py` at 0a85f89, read 2026-10-13).

From the paper (section 2): frames of N = 2048 samples under a Hann window at 200 frames
a second and 44.1 kHz, so the mix is resampled to 44.1 kHz (soxr) and every number is
the paper's; the difference taken to the frame mu back, mu = max(1, floor((N/2 -
min{n : w(n) > r}) / h + 0.5)) with r = 0.5, which is 2; triangular filters a quarter
tone apart from 27.5 Hz to 16 kHz, not normalized; log10(filtered magnitude + 1); a
maximum over each band and its two neighbours of the frame mu back; the half-wave
rectified differences summed (its equation 6). Peak picking (section 2.3, offline): a
frame is an onset where it is the maximum from 30 ms before to 30 ms after, at least the
mean from 100 ms before to 70 ms after plus the threshold delta, and more than 30 ms after
the previous onset.

From the reference implementation, where the paper says nothing: frames centred on
their time (offline), seeking `int(frame * hop - N / 2)` with zeros outside the signal;
the filter edges placed from A4 = 440 Hz by quarter tones, rounded to FFT bins and kept
once each; channels averaged; the frequency maximum reflecting at the edges, the moving
maximum and mean padded with zeros; times in milliseconds rounded to frames. And delta:
the paper tunes it per dataset for the best F-measure and gives no value, so this uses
the implementation's default, 1.1, which was tuned with that program's own peak-picking
windows rather than the paper's (unverified for this combination; `tests/test_onsets.py`
checks it finds every hit of a synthetic drum track and nothing else). Two differences
from the implementation, both following the paper: onsets within 30 ms are combined
(the program divides its 0.03 s by 1000 again, so it never combines), and the gap is
measured from the last onset kept. Built this way the filterbank has 139 bands where the
paper says 138, one band at an edge, not explained.
"""
from __future__ import annotations

import math

import numpy as np
import soxr
from scipy.ndimage import maximum_filter1d

RATE = 44100  # the paper's sample rate
FRAME = 2048  # N, samples
FPS = 200  # frames a second
RATIO = 0.5  # r: the window height that sets mu
BANDS_PER_OCTAVE = 24  # quarter tones
FMIN, FMAX = 27.5, 16000.0  # Hz
A4 = 440.0  # where the reference implementation starts its filter edges
NEIGHBOURS = 3  # the maximum over m - 1, m, m + 1
PRE_MAX, POST_MAX = 0.030, 0.030  # s
PRE_AVG, POST_AVG = 0.100, 0.070  # s
COMBINE = 0.030  # s
THRESHOLD = 1.1  # delta: the reference implementation's default (the paper gives none)


def filterbank(rate: int = RATE, frame: int = FRAME) -> np.ndarray:
    """Triangular filters (FFT bins by bands), a quarter tone apart, each 1 at its centre."""
    bins = frame // 2
    edges = [A4]
    step = 2.0 ** (1.0 / BANDS_PER_OCTAVE)
    while edges[-1] <= min(FMAX, rate / 2):  # one edge past the top, the last filter's right corner
        edges.append(edges[-1] * step)
    low = A4
    while low >= FMIN:  # and one under the bottom
        low /= step
        edges.append(low)
    width = (rate / 2.0) / bins
    placed = [int(b) for b in np.unique(np.round(np.asarray(sorted(edges)) / width).astype(int)) if b < bins]
    assert len(placed) >= 5, "too few distinct filter edges"
    bank = np.zeros((bins, len(placed) - 2), np.float32)
    for band, (start, middle, stop) in enumerate(zip(placed, placed[1:], placed[2:])):
        bank[start:middle, band] = np.linspace(0, 1, middle - start, endpoint=False)
        bank[middle:stop, band] = np.linspace(1, 0, stop - middle, endpoint=False)
    return bank


def mu(hop: float, frame: int = FRAME) -> int:
    """The paper's equation 2."""
    window = np.hanning(frame)
    first = int(np.argmax(window > RATIO))
    return max(1, math.floor((frame / 2 - first) / hop + 0.5))


def spectrogram(signal: np.ndarray, rate: int = RATE) -> np.ndarray:
    """log10 of the filtered magnitude spectrum plus 1, frames by bands, of a mono signal."""
    hop = rate / FPS
    count = math.ceil(signal.size / hop)
    window = np.hanning(FRAME).astype(np.float32)
    bank = filterbank(rate)
    padded = np.concatenate([np.zeros(FRAME, np.float32), signal.astype(np.float32), np.zeros(FRAME, np.float32)])
    out = np.empty((count, bank.shape[1]), np.float32)
    block = 1024
    for first in range(0, count, block):
        frames = np.arange(first, min(first + block, count))
        starts = (frames * hop - FRAME / 2).astype(np.int64) + FRAME  # int() of the seek, into the padding
        chunk = padded[starts[:, None] + np.arange(FRAME)[None, :]] * window
        magnitude = np.abs(np.fft.rfft(chunk, axis=1))[:, :FRAME // 2]
        out[frames] = magnitude @ bank
    return np.log10(out + 1)


def superflux(spec: np.ndarray, rate: int = RATE) -> np.ndarray:
    """The detection function, one value a frame (the paper's equation 6)."""
    back = mu(rate / FPS)
    widened = maximum_filter1d(spec, size=NEIGHBOURS, axis=1, mode="reflect")
    difference = np.zeros_like(spec)
    difference[back:] = spec[back:] - widened[:-back]
    return np.maximum(difference, 0).sum(axis=1)


def _frames(seconds: float) -> int:
    return int(round(FPS * seconds))


def peaks(function: np.ndarray, threshold: float = THRESHOLD) -> list[float]:
    """Onset times in seconds from a detection function (the paper's section 2.3, offline)."""
    pre_max, post_max, pre_avg, post_avg = (_frames(t) for t in (PRE_MAX, POST_MAX, PRE_AVG, POST_AVG))
    combine = _frames(COMBINE)
    count = function.size
    padded = np.concatenate([np.zeros(pre_max), function, np.zeros(post_max)])
    window_max = np.lib.stride_tricks.sliding_window_view(padded, pre_max + post_max + 1).max(axis=1)
    summed = np.concatenate([[0.0], np.cumsum(np.concatenate([np.zeros(pre_avg), function, np.zeros(post_avg)]))])
    length = pre_avg + post_avg + 1
    window_mean = (summed[length:length + count] - summed[:count]) / length
    candidates = np.flatnonzero((function == window_max) & (function >= window_mean + threshold))
    kept: list[int] = []
    for frame in candidates:
        if not kept or frame - kept[-1] > combine:
            kept.append(int(frame))
    return [frame / FPS for frame in kept]


def onsets(signal: np.ndarray, rate: int, threshold: float = THRESHOLD) -> list[float]:
    """Onset times in seconds of a signal (samples by channels, or mono), its channels
    averaged and resampled to the paper's 44.1 kHz."""
    mono = signal.mean(axis=1) if signal.ndim == 2 else signal
    if rate != RATE:
        mono = soxr.resample(mono.astype(np.float64), rate, RATE)
    return peaks(superflux(spectrogram(np.asarray(mono, np.float32))), threshold)
