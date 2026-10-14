"""A footage layer's own sound (07-audio.md, Location sound): it plays with its picture,
cuts on the layer's frames and goes where the layer goes."""
import json

import numpy as np
import pytest
from click.testing import CliRunner

from lungecuta import ops
from lungecuta.cli import main
from lungecuta.comp import MIX_RATE, load_comp, resolve
from lungecuta.media import Project, footage_wav, load_record
from lungecuta.mixdown import mix
from lungecuta.music import read_wav

from clips import CLICK, clip_with_sound, put_comp, write_record


def project_with(cut_project, **layer):
    """cut_project with C0002, a clip with sound, as layer `talk` alone in comp `talk`."""
    write_record(cut_project, clip_with_sound(cut_project / "footage" / "C0002.mov"))
    talk = {"id": "talk", "type": "footage", "media": "C0002", "start": "0s", "out": "1.5s", "source_start": "0.5s",
            "sound": {}} | layer
    put_comp(cut_project, {"id": "talk", "size": [128, 72], "rate": "30000/1001", "end": "3s", "layers": [talk]})
    return cut_project


def mixed(project_dir, name="talk"):
    project = Project.load(project_dir)
    return mix(project, resolve(project, load_comp(project, name)))


def test_the_clip_s_sound_plays_with_its_picture(cut_project):
    signal = mixed(project_with(cut_project))
    # the click at 1.0 s of the clip, from 0.5 s into it, at 0 s of the comp: 0.5 s
    assert int(np.abs(signal).max(axis=1).argmax()) == round((CLICK - 0.5) * MIX_RATE)
    assert np.abs(signal).max() == pytest.approx(0.5, abs=1e-6)


def test_the_sound_cuts_on_the_layer_s_frames(cut_project):
    project = project_with(cut_project, start="0.25s", out="1.5s")  # 0.25 s is between frames 7 and 8
    resolved = resolve(Project.load(project), load_comp(Project.load(project), "talk"))
    sound, cut = resolved.sounds[0], resolved.cuts[0]
    assert sound.footage and sound.first == round(cut.first * 1001 / 30000 * MIX_RATE)
    assert sound.end == round(cut.end * 1001 / 30000 * MIX_RATE)
    click = int(np.abs(mixed(project)).max(axis=1).argmax())
    assert abs(click - round((0.25 + CLICK - 0.5) * MIX_RATE)) <= 1  # in step with its picture, to the sample


def test_a_sound_that_starts_after_the_picture_is_led_in_with_silence(cut_project):
    write_record(cut_project, clip_with_sound(cut_project / "footage" / "C0003.mov", lead=0.1))
    signal, rate = read_wav(footage_wav(Project.load(cut_project), load_record(Project.load(cut_project), "C0003")))
    assert rate == MIX_RATE and int(np.abs(signal).max(axis=1).argmax()) == round((0.1 + CLICK) * MIX_RATE)


def test_the_sound_goes_with_a_split_and_comes_off(cut_project):
    project = project_with(cut_project)
    ops.split(Project.load(project), "talk", "talk", "0.25s")
    layers = json.loads((project / "comps" / "talk.json").read_text())["layers"]
    assert [layer.get("sound") for layer in layers] == [{}, {}]
    assert int(np.abs(mixed(project)).max(axis=1).argmax()) == round((CLICK - 0.5) * MIX_RATE)
    result = ops.sound(Project.load(project), "talk", "talk.2", on=False)
    assert result["changes"] == ["talk.2 plays no sound of its own"]
    assert np.abs(mixed(project)[round(0.3 * MIX_RATE):]).max() == 0


def test_op_sound_sets_only_what_it_is_given(cut_project):
    project = project_with(cut_project)
    result = CliRunner().invoke(main, ["op", "sound", "talk", "talk", "--gain", "-6", "--fade-in", "0.1s",
                                       "--project", str(project)], catch_exceptions=False)
    assert json.loads(result.output)["changes"] == ["talk plays its own sound (gain -6.0 dB, fade in 0.1s)"]
    ops.sound(Project.load(project), "talk", "talk", fade_in="none")
    assert json.loads((project / "comps" / "talk.json").read_text())["layers"][0]["sound"] == {"gain": -6.0}
    assert np.abs(mixed(project)).max() == pytest.approx(0.5 * 10 ** (-6 / 20), abs=1e-6)


def test_a_sound_is_refused_where_it_cannot_play(cut_project):
    with pytest.raises(AssertionError, match="C0001 has no sound of its own"):
        ops.sound(Project.load(cut_project), "main", "shot1")
    project = project_with(cut_project, time={"speed": 0.5})
    with pytest.raises(AssertionError, match="its sound plays only at 100 %"):
        resolve(Project.load(project), load_comp(Project.load(project), "talk"))


def test_lint_and_the_render_take_a_clip_s_sound(cut_project):
    write_record(cut_project, clip_with_sound(cut_project / "footage" / "C0002.mov"))
    comp = json.loads((cut_project / "comps" / "main.json").read_text())
    comp["layers"].append({"id": "talk", "type": "footage", "media": "C0002", "start": "b:1", "out": "1.5s",
                           "source_start": "0.5s", "sound": {}})
    put_comp(cut_project, comp)
    lint = CliRunner().invoke(main, ["lint", "main", "--project", str(cut_project)], catch_exceptions=False)
    assert not [w for w in json.loads(lint.output)["warnings"]
                if w["rule"] in ("music_licence", "sfx_vary") and "talk" in w["layers"]]
    render = CliRunner().invoke(main, ["render", "main", "--preset", "web-h264", "--project", str(cut_project)],
                                catch_exceptions=False)
    assert render.exit_code == 0, render.output
    manifest = json.loads((cut_project / "cache" / "lungecuta" / "render" / "main.web-h264.mp4.manifest.json").read_text())
    assert sorted(key for key, _ in manifest["media"]) == ["C0001", "C0002", "track"]


def test_the_music_ducks_under_a_shot_s_own_sound(cut_project):
    from lungecuta.ticks import ticks_per_frame

    write_record(cut_project, clip_with_sound(cut_project / "footage" / "C0002.mov"))
    comp = json.loads((cut_project / "comps" / "main.json").read_text())
    comp["layers"].append({"id": "talk", "type": "footage", "media": "C0002", "start": "b:4", "out": "1s",
                           "source_start": "0.5s", "sound": {}})
    put_comp(cut_project, comp)
    result = ops.duck(Project.load(cut_project), "main", "music", -12.0, "f:6", under="talk")
    project = Project.load(cut_project)
    resolved = resolve(project, load_comp(project, "main"))
    talk = next(cut for cut in resolved.cuts if cut.layer.id == "talk")
    assert result["changes"] == [f"music ducked -12 dB from f:{talk.first} to f:{talk.end} under talk, ramps of f:6"]
    music = next(s for s in resolved.sounds if s.layer.id == "music")
    tick = ticks_per_frame(resolved.rate)
    level = lambda frame: float(music.gain.at(frame * tick)[0])
    assert [level(f) for f in (talk.first - 6, talk.first, talk.end - 1, talk.end + 6)] == [0.0, -12.0, -12.0, 0.0]
    assert level(talk.first - 3) == pytest.approx(-6.0) and level(talk.end + 30) == 0.0
    with pytest.raises(AssertionError, match="gain keys at .* inside the duck"):
        ops.duck(Project.load(cut_project), "main", "music", -6.0, "f:6", start=f"f:{talk.first}", end=f"f:{talk.end}")
