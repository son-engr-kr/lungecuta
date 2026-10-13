"""Looks: styles holding a grade, used by a clip's grade as a linked node (04-color.md)."""
import json

import numpy as np
import pytest
from click.testing import CliRunner
from pydantic import TypeAdapter, ValidationError

from clips import comp_json, put_comp
from lungecuta import grade
from lungecuta.cli import main
from lungecuta.comp import load_comp, resolve
from lungecuta.compose import frame_at
from lungecuta.media import Project
from lungecuta.schema.comp import GradeGraph, GradeOp
from lungecuta.schema.style import StyleFile

GRAPH = TypeAdapter(list[GradeOp] | GradeGraph)
WARM = {"id": "warm", "name": "warm and lifted",
        "grade": {"nodes": [{"ops": [{"op": "primary", "offset": 0.05}]},
                            {"ops": [{"op": "cdl", "slope": [1.1, 1.0, 0.9]}]}]}}


def put_look(project, look=WARM):
    (project / "styles" / f"{look['id']}.json").write_text(json.dumps(look))


def run(project, *args):
    result = CliRunner().invoke(main, [*args, "--project", str(project)], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def shot1(project):
    return next(layer for layer in json.loads((project / "comps" / "main.json").read_text())["layers"]
                if layer["id"] == "shot1")


def test_a_look_node_is_the_look_s_grade_mixed_like_any_node(cut_project):
    put_look(cut_project)
    project = Project.load(cut_project)
    frame = np.random.default_rng(5).random((12, 20, 3), dtype=np.float32) * 0.8
    direct = grade.apply(project, GRAPH.validate_python(WARM["grade"]), frame.copy())
    looked = grade.apply(project, GRAPH.validate_python({"nodes": [{"look": "warm"}]}), frame.copy())
    np.testing.assert_allclose(looked, direct, atol=1e-6)
    half = grade.apply(project, GRAPH.validate_python({"nodes": [{"look": "warm", "mix": 0.5}]}), frame.copy())
    np.testing.assert_allclose(half, (frame + direct) / 2, atol=1e-5)
    assert grade.looks(project, GRAPH.validate_python({"nodes": [{"look": "warm"}]})) == ["warm"]
    assert [op.op for op in grade.operations(project, GRAPH.validate_python({"nodes": [{"look": "warm"}]}))] == \
        ["primary", "cdl"]


def test_lc_op_look_adds_one_node_and_unlook_takes_it_off(cut_project):
    put_look(cut_project)
    run(cut_project, "op", "grade", "main", "shot1", "--ops", '[{"op": "primary", "contrast": 1.1}]')
    printed = run(cut_project, "op", "look", "main", "shot1", "warm", "--mix", "0.7")
    assert printed["changes"] == ["shot1 look warm added, mix 0.7"]
    assert shot1(cut_project)["grade"] == {"nodes": [{"ops": [{"op": "primary", "contrast": 1.1}]},
                                                      {"look": "warm", "mix": 0.7}]}
    printed = run(cut_project, "op", "look", "main", "shot1", "warm")  # again: the mix changes, no second node
    assert printed["changes"] == ["shot1 look warm mix 0.7 to 1.0"] and len(shot1(cut_project)["grade"]["nodes"]) == 2
    run(cut_project, "op", "unlook", "main", "shot1", "warm")
    assert shot1(cut_project)["grade"] == {"nodes": [{"ops": [{"op": "primary", "contrast": 1.1}]}]}


def test_editing_a_look_changes_every_layer_that_uses_it(cut_project):
    put_look(cut_project)
    comp = comp_json()
    for k in (1, 2):
        comp["layers"][k]["grade"] = {"nodes": [{"look": "warm"}]}
    put_comp(cut_project, comp)
    project = Project.load(cut_project)
    resolved = resolve(project, load_comp(project, "main"))
    from lungecuta.preview import Preview

    preview = Preview(project, "main")
    before = [frame_at(project, resolved, index) for index in (30, 90)]  # shot1, then shot2
    rendered = run(cut_project, "render", "main", "--preset", "web-h264")
    manifest = json.loads(open(rendered["manifest"]).read())
    assert [name for name, _ in manifest["looks"]] == ["warm"]
    put_look(cut_project, WARM | {"grade": [{"op": "primary", "offset": -0.05}]})
    after = [frame_at(project, resolved, index) for index in (30, 90)]
    for a, b in zip(before, after):
        assert b.mean() < a.mean() - 0.01  # both layers darker: the look is linked, not copied
    assert preview.stale()
    (render,) = run(cut_project, "status")["renders"]
    assert render["why_not"] == ["look warm changed"]


def test_what_a_look_cannot_be(cut_project):
    with pytest.raises(ValidationError, match="tracks, a grade"):
        StyleFile.model_validate({"id": "empty", "name": "nothing"})
    with pytest.raises(ValidationError, match="not other looks"):
        StyleFile.model_validate({"id": "nested", "name": "a look of a look", "grade": {"nodes": [{"look": "warm"}]}})
    with pytest.raises(ValidationError, match="operations or a look"):
        GRAPH.validate_python({"nodes": [{"look": "warm", "ops": [{"op": "cdl"}]}]})
    put_look(cut_project)
    with pytest.raises(AssertionError, match="is a look"):  # placed in time, a look has no tracks
        run(cut_project, "op", "style", "main", "shot1", "warm", "--at", "b:1")
    (cut_project / "styles" / "dip.json").write_text(json.dumps(
        {"id": "dip", "name": "a dip", "tracks": {"speed": [{"at": "b:0", "v": 0.5}]}}))
    with pytest.raises(AssertionError, match="not a look"):
        run(cut_project, "op", "look", "main", "shot1", "dip")
