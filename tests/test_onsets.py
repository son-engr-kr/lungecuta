"""SuperFlux onsets (src/lungecuta/onsets.py) against a drum track whose hits are known."""
import numpy as np
import pytest

from clips import drum_track
from lungecuta.onsets import FPS, RATE, filterbank, mu, onsets, peaks, superflux, spectrogram

WINDOW = 0.025  # s: the paper's evaluation window, a detection within 25 ms of a hit is correct


def test_the_filterbank_and_the_difference_frame_are_the_papers():
    bank = filterbank()
    assert bank.shape == (1024, 139)  # the paper says 138; built as its reference implementation builds it
    assert np.allclose(bank.max(axis=0), 1.0) and (bank >= 0).all()
    assert mu(RATE / FPS) == 2


def test_every_hit_of_a_drum_track_is_found_and_nothing_else():
    signal, beats, _ = drum_track(rate=48000, bars=4)
    hits = sorted({round(t + half * 0.5, 6) for t in beats for half in (0.0, 0.5)})  # kicks and hats, every eighth
    # each bar's bass note is cut off at the next downbeat, a click where the last one ends
    hits.append(beats[-1] + 0.5)
    found = onsets(signal, 48000)
    missed = [hit for hit in hits if min(abs(np.array(found) - hit)) > WINDOW]
    extra = [t for t in found if min(abs(np.array(hits) - t)) > WINDOW]
    assert not missed and not extra, (missed, extra)
    # Measured, not corrected: centred frames see an attack in their later half, so each
    # onset comes one or two frames (5 or 10 ms) before its hit, well inside the 25 ms window.
    errors = [min(found, key=lambda t: abs(t - hit)) - hit for hit in hits]
    assert all(-2 / FPS - 1e-9 <= e <= 1e-9 for e in errors), errors


def test_silence_has_no_onsets_and_peaks_follow_the_paper():
    assert onsets(np.zeros((48000, 2), np.float32), 48000) == []
    function = np.zeros(400)
    function[[50, 53, 200]] = [5.0, 4.0, 5.0]  # 53 is within 30 ms of 50, and not the maximum near it
    assert peaks(function) == [50 / FPS, 200 / FPS]
    function[54] = 6.0  # the maximum from 30 ms before to 30 ms after is 54 now: 50 is not a peak
    assert peaks(function) == [54 / FPS, 200 / FPS]
    assert superflux(spectrogram(np.zeros(4410, np.float32))).max() == pytest.approx(0)
