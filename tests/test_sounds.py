"""Sounds: audio without a beat map (effects, voices), placed by an anchor (07-audio.md, SFX
placement), and the review rules about them."""
import json

import numpy as np
import pytest
from click.testing import CliRunner

from clips import comp_json, put_comp
from lungecuta.cli import main
from lungecuta.comp import anchor_samples, load_comp, resolve
from lungecuta.media import Project, music_wav
from lungecuta.music import wav_bytes
from lungecuta.mixdown import mix

RATE = 48000
SWELL, HIT = 0.15, 0.20  # s: a pre-swell rising from 0.05 s, then the impact itself


def impact() -> np.ndarray:
    """A swell that rises slowly, then a hit: the steepest rise is the hit, not the swell's start."""
    t = np.arange(round(0.6 * RATE)) / RATE
    swell = np.clip((t - 0.05) / SWELL, 0, 1) * 0.15 * np.sin(2 * np.pi * 90 * t) * (t < HIT)
    after = np.clip(t - HIT, 0, None)
    hit = (t >= HIT) * 0.9 * np.exp(-after / 0.05) * np.sin(2 * np.pi * 60 * after)
    signal = (swell + hit).astype(np.float32)
    return np.stack([signal, signal], 1)


def run(project, *args, ok=True):
    result = CliRunner().invoke(main, [*args, "--project", str(project)], catch_exceptions=not ok)
    if ok:
        assert result.exit_code == 0, result.output
        return json.loads(result.output)
    return result


@pytest.fixture
def sounding(cut_project):
    (cut_project / "footage" / "hit.wav").write_bytes(wav_bytes(impact(), RATE))
    printed = run(cut_project, "import", str(cut_project / "footage" / "hit.wav"), "--root", "project", "--sound")
    assert printed["imported"][0]["sound"] is True
    return cut_project


def with_sounds(project, *layers, **changes):
    comp = comp_json(**changes)
    comp["layers"] += list(layers)
    put_comp(project, comp)
    loaded = Project.load(project)
    return loaded, resolve(loaded, load_comp(loaded, "main"))


def hit_layer(layer_id="hit", start="b:4", anchor="rise"):
    return {"id": layer_id, "type": "audio", "media": "hit", "start": start, "out": "0.6s", "source_start": "0s",
            "anchor": anchor}


def test_a_sound_imports_without_a_beat_map(sounding):
    record = json.loads((sounding / "media" / "hit.json").read_text())
    assert record["beats"] is None and record["wav"]["samples"] == round(0.6 * RATE)
    sounds = run(sounding, "status")["media"]["sounds"]
    assert [s["key"] for s in sounds] == ["hit"] and sounds[0]["licence"] is False


def test_an_impact_lands_on_its_beat_by_its_rise(sounding):
    project, resolved = with_sounds(sounding, hit_layer())
    record = next(s.record for s in resolved.sounds if s.layer.id == "hit")
    rise = anchor_samples(music_wav(project, record), "rise", record.wav.hash)
    assert abs(rise / RATE - HIT) < 0.003  # the hit, 2 ms envelope or so, not the swell 0.15 s before it
    _, quiet = with_sounds(sounding)
    heard = mix(project, resolved) - mix(project, quiet)
    beat = round(2.25 * RATE)  # b:4
    loudest = int(np.abs(heard).max(axis=1)[beat - 4800:beat + 4800].argmax()) + beat - 4800
    assert 0 <= (loudest - beat) / RATE < 0.02  # the impact's peak just after the beat it was placed on
    assert np.abs(heard[beat - round(0.1 * RATE):beat - round(0.01 * RATE)]).max() > 0.01  # the swell plays before


def test_a_peak_anchor_and_pre_roll_cut_at_the_comp_s_start(sounding):
    project, resolved = with_sounds(sounding, hit_layer(start="0s", anchor="peak"))
    sound = next(s for s in resolved.sounds if s.layer.id == "hit")
    peak = anchor_samples(music_wav(project, sound.record), "peak", sound.record.wav.hash)
    assert sound.first == 0 and sound.source_first == peak  # what came before the peak is cut, not moved later


def test_music_is_placed_by_its_start_and_a_sound_gives_no_beats(sounding):
    music = comp_json()["layers"][0] | {"anchor": "rise"}
    with pytest.raises(AssertionError, match="music is placed by its start"):
        with_sounds(sounding, layers=[music, *comp_json()["layers"][1:]])
    with pytest.raises(AssertionError, match="a sound with no beat map, not music"):
        with_sounds(sounding, hit_layer(start="1s", anchor="start"), beats={"from_layers": ["hit"]},
                    end="2s", markers=[], layers=[comp_json()["layers"][1] | {"start": "0s", "out": "1s"}])


def test_lint_finds_the_same_sound_twice_in_a_row(sounding):
    with_sounds(sounding, hit_layer("hit1", "b:4"), hit_layer("hit2", "b:8"))
    rules = [w["rule"] for w in run(sounding, "lint", "main")["warnings"]]
    assert rules.count("sfx_vary") == 1 and "music_licence" in rules
    (sounding / "footage" / "other.wav").write_bytes(wav_bytes(impact() * 0.5, RATE))
    run(sounding, "import", str(sounding / "footage" / "other.wav"), "--root", "project", "--sound")
    with_sounds(sounding, hit_layer("hit1", "b:4"), {**hit_layer("hit2", "b:8"), "media": "other"})
    assert "sfx_vary" not in [w["rule"] for w in run(sounding, "lint", "main")["warnings"]]
