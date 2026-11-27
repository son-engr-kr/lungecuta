"""The music under the voice (07-audio.md, Ducking and A bed under the voice): `lungecuta op
duck` under many layers, the spans whose ramps meet made one, and `lungecuta op bed`, which
measures the voice and the music and sets the music's gain. The voice-over lines are the
stand-in of tests/test_voice.py, noise whose length is known."""
import json

import pytest
from click.testing import CliRunner

from lungecuta import ops, voice
from lungecuta.cli import main
from lungecuta.comp import MIX_RATE, load_comp, resolve
from lungecuta.media import Project
from lungecuta.ticks import ticks_per_frame

from test_voice import stand_in


@pytest.fixture
def lines(cut_project, monkeypatch):
    """Comp `main` with three voice-over lines, the first two 0.2 s apart, the third far on."""
    monkeypatch.setattr(voice, "synthesize", stand_in)
    voice.speak(Project.load(cut_project), [voice.Line(key=k, text="Here we go.") for k in ("vo1", "vo2", "vo3")],
                voice="af_heart")
    for key, at in (("vo1", "1s"), ("vo2", "2s"), ("vo3", "4.5s")):
        ops.place(Project.load(cut_project), "main", key, at)
    return cut_project


def comp_now(project_dir):
    project = Project.load(project_dir)
    return resolve(project, load_comp(project, "main"))


def frames_of(sound, tick):
    per = 705600000 // MIX_RATE
    return -(-sound.first * per // tick), -(-sound.end * per // tick)


def test_lines_close_together_are_one_duck(lines):
    before = comp_now(lines)
    tick = ticks_per_frame(before.rate)
    spans = {s.layer.id: frames_of(s, tick) for s in before.sounds}
    assert spans["vo2"][0] - spans["vo1"][1] < 12 < spans["vo3"][0] - spans["vo2"][1], "two lines close, one far"
    result = ops.duck(Project.load(lines), "main", "music", -12.0, "f:6", voices=True)
    first, last = (spans["vo1"][0], spans["vo2"][1]), spans["vo3"]
    assert result["changes"] == [f"music ducked -12 dB from f:{first[0]} to f:{first[1]}, f:{last[0]} to f:{last[1]}"
                                 " under vo1, vo2, vo3, ramps of f:6"]
    music = next(s for s in comp_now(lines).sounds if s.layer.id == "music")
    level = lambda frame: float(music.gain.at(frame * tick)[0])
    assert len(music.layer.gain.keys) == 8
    assert [level(f) for f in (first[0] - 6, first[0], spans["vo1"][1] + 1, first[1] - 1, first[1] + 6)] == [
        0.0, -12.0, -12.0, -12.0, 0.0], "down from the first line to the end of the second, between them too"
    assert [level(f) for f in (last[0] - 6, last[0], last[1] - 1, last[1] + 6)] == [0.0, -12.0, -12.0, 0.0]


def test_a_duck_under_layers_named_and_none_refused(lines):
    ops.duck(Project.load(lines), "main", "music", -6.0, "f:3", under=["vo1", "vo3"])
    music = next(s for s in comp_now(lines).sounds if s.layer.id == "music")
    assert len(music.layer.gain.keys) == 8
    with pytest.raises(AssertionError, match="inside the duck"):
        ops.duck(Project.load(lines), "main", "music", -6.0, "f:3", under=["vo2"])
    with pytest.raises(AssertionError, match="no other layer 'nope'"):
        ops.duck(Project.load(lines), "main", "music", -6.0, "f:3", under=["nope"])


def test_no_voices_to_duck_under_is_refused(cut_project):
    with pytest.raises(AssertionError, match="no voice-over lines"):
        ops.duck(Project.load(cut_project), "main", "music", -6.0, "f:3", voices=True)


def test_the_bed_sits_the_given_lu_under_the_voice(lines):
    result = ops.bed(Project.load(lines), "main", "music", 20.0, voices=True)
    bed = result["bed"]
    assert bed["voice_lufs"] - bed["music_lufs"] == pytest.approx(20.0, abs=0.01)
    assert bed["music_lufs"] - bed["music_lufs_before"] == pytest.approx(bed["gain_shift_db"], abs=0.01)
    layer = next(layer for layer in json.loads((lines / "comps" / "main.json").read_text())["layers"]
                 if layer["id"] == "music")
    assert layer["gain"] == bed["gain_shift_db"]
    spans = [s for s in comp_now(lines).sounds if s.layer.id.startswith("vo")]
    assert (bed["from_s"], bed["to_s"]) == (round(min(s.first for s in spans) / MIX_RATE, 3),
                                            round(max(s.end for s in spans) / MIX_RATE, 3))


def test_the_bed_moves_every_key_so_a_duck_keeps_its_depth(lines):
    ops.duck(Project.load(lines), "main", "music", -12.0, "f:6", voices=True)
    keys = lambda: [key["v"] for layer in json.loads((lines / "comps" / "main.json").read_text())["layers"]
                    if layer["id"] == "music" for key in layer["gain"]["keys"]]
    ducked = keys()
    shift = ops.bed(Project.load(lines), "main", "music", 18.0, under=["vo1", "vo2", "vo3"])["bed"]["gain_shift_db"]
    assert keys() == [round(v + shift, 2) for v in ducked]


def test_the_cli_ducks_and_beds(lines):
    run = lambda *args: CliRunner().invoke(main, [*args, "--project", str(lines)], catch_exceptions=False)
    ducked = run("op", "duck", "main", "music", "--under-voices", "--by", "-10", "--ramp", "0.1s")
    assert ducked.exit_code == 0, ducked.output
    bedded = run("op", "bed", "main", "music", "--under", "vo1", "--under", "vo2", "--below", "15")
    assert bedded.exit_code == 0, bedded.output
    bed = json.loads(bedded.output)["bed"]
    assert bed["voice_lufs"] - bed["music_lufs"] == pytest.approx(15.0, abs=0.01)
