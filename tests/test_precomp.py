"""Precomps: a comp placed as a layer of another (03-project-format.md, layer types)."""
import json

import numpy as np
import pytest
from click.testing import CliRunner

from clips import TITLE, comp_json, put_comp
from lungecuta.cli import main
from lungecuta.comp import load_comp, resolve
from lungecuta.media import Project
from lungecuta.compose import frame_at


def inner(rate="30000/1001", **changes):
    """A precomp holding shot1's clip at its own size for three seconds."""
    comp = {"id": "inner", "size": [128, 72], "rate": rate, "end": "3s",
            "layers": [{"id": "clip", "type": "footage", "media": "C0001", "start": "0s", "out": "2s",
                        "source_start": "f:0"}]}
    comp.update(changes)
    return comp


def with_precomp(project, layer_changes=None, inner_comp=None):
    put_comp(project, inner_comp or inner())
    comp = comp_json()
    comp["layers"][1] = {"id": "shot1", "type": "comp", "comp": "inner", "start": "b:0", "out": "b:4",
                         **(layer_changes or {})}
    put_comp(project, comp)
    loaded = Project.load(project)
    return loaded, resolve(loaded, load_comp(loaded, "main"))


def test_a_precomp_shows_its_own_frames_and_places_like_footage(cut_project):
    project, resolved = with_precomp(cut_project, {"transform": {"scale": 0.5}})
    (nest,) = resolved.nests
    assert (nest.first, nest.end) == (8, 68)
    assert nest.frame(8) == 0 and nest.frame(67) == 59  # layer time from b:0, frame by frame at the same rate
    framed = frame_at(project, resolved, 30)
    # the same clip placed directly at half size, from the same source frame
    direct = comp_json()
    direct["layers"][1] |= {"source_start": "f:0", "transform": {"scale": 0.5}}
    put_comp(cut_project, direct)
    project = Project.load(cut_project)
    expected = frame_at(project, resolve(project, load_comp(project, "main")), 30)
    np.testing.assert_allclose(framed, expected, atol=1e-5)


def test_a_precomp_at_another_rate_shows_the_frame_that_holds_its_time(cut_project):
    _, resolved = with_precomp(cut_project, inner_comp=inner(rate="25"))
    (nest,) = resolved.nests
    # comp frame 30 is 1.001 s; 0.25 s after the layer's start is 0.751 s, frame 18 at 25 fps
    assert nest.frame(30) == 18


def sounding(**changes):
    """The precomp with music of its own: the track's first two seconds from its time 0."""
    comp = inner(**changes)
    comp["layers"].append({"id": "music", "type": "audio", "media": "track", "start": "0s", "out": "2s",
                           "source_start": "0s"})
    return comp


def test_a_precomps_music_plays_where_its_layer_does(cut_project):
    from lungecuta.mixdown import mix

    project, quiet = with_precomp(cut_project)
    without = mix(project, quiet)
    for changes, first, inner_first in (({}, 12000, 0), ({"in": "b:1"}, 36000, 24000)):
        project, resolved = with_precomp(cut_project, changes, inner_comp=sounding())
        heard = mix(project, resolved) - without
        precomp = mix(project, resolve(project, load_comp(project, "inner")))
        # the layer starts at b:0 (0.25 s, sample 12000) and is out at b:4 (2.25 s, sample 108000); a trimmed
        # in point at b:1 (0.75 s) starts it there, half a second into the precomp
        np.testing.assert_allclose(heard[first:108000], precomp[inner_first:96000], atol=1e-6)
        assert np.abs(heard[:first]).max() == 0 and np.abs(heard[108000:]).max() == 0
        assert np.abs(heard).max() > 0.01


def test_a_render_records_its_precomps_and_goes_stale_with_them(cut_project):
    from lungecuta.preview import Preview

    project, _ = with_precomp(cut_project, inner_comp=sounding())
    preview = Preview(project, "main")
    args = ["--project", str(cut_project)]
    rendered = json.loads(CliRunner().invoke(main, ["render", "main", "--preset", "web-h264", *args]).output)
    manifest = json.loads(open(rendered["manifest"]).read())
    assert [name for name, _ in manifest["precomps"]] == ["inner"]
    assert {key for key, _ in manifest["media"]} == {"C0001", "track"}
    status = json.loads(CliRunner().invoke(main, ["status", *args]).output)
    assert status["renders"][0]["current"]
    put_comp(cut_project, sounding(end="2.5s"))
    status = json.loads(CliRunner().invoke(main, ["status", *args]).output)
    assert status["renders"][0]["why_not"] == ["precomp inner changed"]
    assert preview.stale()  # the editor's preview reads the precomp too


def test_precomps_that_contain_themselves_are_refused(cut_project):
    looped = inner()
    looped["layers"].append({"id": "again", "type": "comp", "comp": "inner", "start": "0s", "out": "1s"})
    with pytest.raises(AssertionError, match="contains itself"):
        with_precomp(cut_project, inner_comp=looped)
    put_comp(cut_project, inner())
    with pytest.raises(AssertionError, match="outside it"):
        with_precomp(cut_project, {"out": "b:7"})  # 3.5 s of a 3 s precomp


def test_graphics_inside_a_precomp_come_in_through_the_inverse_view(cut_project):
    (cut_project / "graphics" / "title.html").write_text(TITLE)
    card = inner()
    card["layers"] = [{"id": "title", "type": "graphics", "page": "graphics/title.html", "space": "display",
                       "start": "0s", "out": "3s", "params": {"colour": "rgb(200, 40, 120)"}}]
    project, resolved = with_precomp(cut_project, inner_comp=card)
    frame = frame_at(project, resolved, 30)
    np.testing.assert_allclose(frame[10, 10] * 255, [200, 40, 120], atol=1.0)
    assert frame[60, 120].max() < 1e-6  # where the card has nothing the precomp is transparent: black beneath


def test_the_preview_and_a_render_draw_a_precomp(cut_project):
    from lungecuta.preview import Preview

    project, _ = with_precomp(cut_project, {"transform": {"scale": 0.5}})
    preview = Preview(project, "main")
    shown = preview.display(30)
    assert shown.shape == (36, 64, 3)
    assert shown[18, 32].max() > 0.01 and shown[2, 2].max() < 1e-6  # the clip in the middle, black around it
    args = ["--project", str(cut_project)]
    rendered = CliRunner().invoke(main, ["render", "main", "--preset", "master-prores-hq", *args])
    assert rendered.exit_code == 0, rendered.output
    checked = CliRunner().invoke(main, ["verify", json.loads(rendered.output)["file"], "--comp", "main",
                                        "--preset", "master-prores-hq", *args])
    assert checked.exit_code == 0, checked.output
    refused = CliRunner().invoke(main, ["fcpxml", "main", *args])
    assert "precomp layers are not written to FCPXML yet" in str(refused.exception)


def test_the_editor_lists_a_precomp_layer(cut_project):
    from fastapi.testclient import TestClient

    from lungecuta.server import create_app

    with_precomp(cut_project)
    with TestClient(create_app(cut_project)) as client:
        layers = {layer["id"]: layer for layer in client.get("/api/comp/main").json()["layers"]}
    assert (layers["shot1"]["type"], layers["shot1"]["media"], layers["shot1"]["first"]) == ("comp", "inner", 8)
