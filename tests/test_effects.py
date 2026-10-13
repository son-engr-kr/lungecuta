"""An audio layer's effects and pan (src/lungecuta/effects.py, 07-audio.md, Effects)."""
import numpy as np
import pytest
from click.testing import CliRunner
from scipy.signal import butter, sosfilt

from clips import put_comp
from lungecuta.cli import main
from lungecuta.comp import load_comp, resolve
from lungecuta.effects import apply, pan_gains, source_length
from lungecuta.media import Project
from lungecuta.mixdown import mix
from lungecuta.schema.comp import AudioLayer

RATE = 48000


def layer(**changes):
    return AudioLayer.model_validate({"id": "a", "type": "audio", "media": "m", "start": "0s", "out": "1s",
                                      "source_start": "0s", **changes})


def tone(hz, seconds=1.0):
    t = np.arange(round(seconds * RATE)) / RATE
    x = np.sin(2 * np.pi * hz * t).astype(np.float32)
    return np.stack([x, x], 1)


def peak_hz(signal):
    spectrum = np.abs(np.fft.rfft(signal[:, 0] * np.hanning(signal.shape[0])))
    return np.fft.rfftfreq(signal.shape[0], 1 / RATE)[spectrum.argmax()]


def test_filters_are_butterworths_run_forwards():
    piece = tone(50) + tone(2000) * 0.5
    effects = layer(effects=[{"type": "highpass", "hz": 500, "order": 4}]).effects
    sos = butter(4, 500, btype="highpass", fs=RATE, output="sos")
    np.testing.assert_allclose(apply(effects, piece, RATE, RATE), sosfilt(sos, piece.astype(np.float64), axis=0),
                               atol=1e-6)
    band = layer(effects=[{"type": "band", "low": 150, "high": 7000}]).effects
    low = butter(2, 150, btype="highpass", fs=RATE, output="sos")
    high = butter(2, 7000, btype="lowpass", fs=RATE, output="sos")
    np.testing.assert_allclose(apply(band, piece, RATE, RATE),
                               sosfilt(high, sosfilt(low, piece.astype(np.float64), axis=0), axis=0), atol=1e-6)
    with pytest.raises(AssertionError, match="Nyquist"):
        apply(layer(effects=[{"type": "lowpass", "hz": 30000}]).effects, piece, RATE, RATE)


def test_reverse_and_repitch_play_the_source_backwards_and_faster():
    click = np.zeros((RATE, 2), np.float32)
    click[100] = 1.0
    backwards = apply(layer(effects=[{"type": "reverse"}]).effects, click, RATE, RATE)
    assert np.abs(backwards).argmax(axis=0)[0] == RATE - 1 - 100
    octave = layer(effects=[{"type": "repitch", "semitones": 12}]).effects
    assert source_length(octave, RATE) == 2 * RATE + 1  # an octave up plays two seconds of source a second
    up = apply(octave, tone(220, 2.1), RATE, RATE)
    assert up.shape == (RATE, 2) and peak_hz(up) == pytest.approx(440, abs=2)
    slower = layer(effects=[{"type": "repitch", "percent": 97}]).effects
    assert peak_hz(apply(slower, tone(1000), RATE, RATE // 2)) == pytest.approx(970, abs=3)
    with pytest.raises(ValueError, match="semitones or in percent"):
        layer(effects=[{"type": "repitch", "semitones": 1, "percent": 101}])


def test_pan_is_constant_power_with_the_centre_at_unity():
    np.testing.assert_allclose(pan_gains(0), [1, 1])
    np.testing.assert_allclose(pan_gains(1), [0, np.sqrt(2)], atol=1e-7)
    left, right = pan_gains(-0.4)
    assert left ** 2 + right ** 2 == pytest.approx(2)


def test_the_mix_carries_a_layer_s_effects_and_pan(cut_project):
    # the track alone, no beat grid hanging on it: its 220 Hz tone an octave up, hard right
    comp = {"id": "main", "size": [128, 72], "rate": "30000/1001", "end": "5s",
            "layers": [{"id": "fx", "type": "audio", "media": "track", "start": "0s", "out": "4s", "source_start": "0s",
                        "effects": [{"type": "repitch", "semitones": 12}], "pan": 1.0}]}
    put_comp(cut_project, comp)
    project = Project.load(cut_project)
    mixed = mix(project, resolve(project, load_comp(project, "main")))
    assert np.abs(mixed[:, 0]).max() == 0 and np.abs(mixed[:, 1]).max() > 0  # hard right
    assert peak_hz(mixed[RATE:2 * RATE, 1:]) == pytest.approx(440, abs=2)  # the track's 220 Hz tone, an octave up
    comp["layers"][0] |= {"out": "6s"}  # 12 s of a 10 s track
    put_comp(cut_project, comp)
    with pytest.raises(AssertionError, match="which has 10.000 s"):
        resolve(project, load_comp(project, "main"))
    comp["layers"][0] |= {"out": "4s"}
    put_comp(cut_project, comp)
    refused = CliRunner().invoke(main, ["fcpxml", "main", "--project", str(cut_project)])
    assert "effects or a pan" in str(refused.exception)
