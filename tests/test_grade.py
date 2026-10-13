import json

import numpy as np
import PyOpenColorIO as ocio
import pytest
from click.testing import CliRunner
from pydantic import TypeAdapter, ValidationError

from clips import comp_json, put_comp
from lungecuta import grade
from lungecuta.cli import main
from lungecuta.media import Project
from lungecuta.schema.comp import GradeGraph, GradeOp

OPS = TypeAdapter(list[GradeOp])


def ramp() -> np.ndarray:
    x = np.linspace(0, 1, 1001, dtype=np.float32)
    return np.ascontiguousarray(np.stack([x, x * 0.8, x * 0.6], axis=1)[None])


def ocio_log(**values) -> np.ndarray:
    primary = ocio.GradingPrimary(ocio.GRADING_LOG)
    for name, value in values.items():
        setattr(primary, name, value)
    transform = ocio.GradingPrimaryTransform(primary, ocio.GRADING_LOG, False)
    out = ramp()
    ocio.Config.CreateRaw().getProcessor(transform).getDefaultCPUProcessor().applyRGB(out)
    return out


def test_ocio_log_controls_are_what_grade_py_says():
    # the facts the module's units rest on, measured on OCIO itself
    x = ramp()[0, :, 0]
    fixed = x[np.argmin(np.abs(ocio_log(contrast=ocio.GradingRGBM(1, 1, 1, 1.5))[0, :, 0] - x))]
    assert fixed == pytest.approx(0.4, abs=1e-3)  # default pivot -0.2: 0.5 + 0.5 * -0.2
    added = ocio_log(brightness=ocio.GradingRGBM(0, 0, 0, 1))[0, :, 0] - x
    assert np.allclose(added, 6.25 / 1023, atol=1e-6)
    lifted = ocio_log(gamma=ocio.GradingRGBM(1, 1, 1, 2))[0, 250, 0]
    assert lifted == pytest.approx(0.25 ** 0.5, abs=1e-5)
    grey = ocio_log(saturation=0.0)[0, 1000]
    assert np.allclose(grey, 0.2126 * 1 + 0.7152 * 0.8 + 0.0722 * 0.6, atol=1e-5)


def test_the_default_pivot_is_davinci_intermediate_grey(cut_project):
    settings = Project.load(cut_project).file.color
    assert grade.grey(settings.config, settings.linear, settings.working) == pytest.approx(0.336043, abs=2e-6)


def test_a_primary_grade_in_working_space_code_values(cut_project):
    project = Project.load(cut_project)
    frame = ramp()
    out = grade.apply(project, OPS.validate_python([{"op": "primary", "offset": 0.05}]), frame.copy())
    assert np.allclose(out - frame, 0.05, atol=1e-5)
    out = grade.apply(project, OPS.validate_python([{"op": "primary", "contrast": 1.4}]), frame.copy())
    turned = frame[0, :, 0][np.argmin(np.abs(out[0, :, 0] - frame[0, :, 0]))]
    assert turned == pytest.approx(0.336, abs=1e-3)
    out = grade.apply(project, OPS.validate_python([{"op": "primary", "contrast": 2, "pivot": 0.5}]), frame.copy())
    assert np.allclose(out[0, :, 0], (frame[0, :, 0] - 0.5) * 2 + 0.5, atol=1e-5)
    out = grade.apply(project, OPS.validate_python([{"op": "primary", "offset": [0.01, 0, -0.01]}]), frame.copy())
    assert np.allclose(out - frame, [0.01, 0, -0.01], atol=1e-5)
    out = grade.apply(project, OPS.validate_python([{"op": "primary", "clamp": [0.1, 0.9]}]), frame.copy())
    assert out.min() == pytest.approx(0.1) and out.max() == pytest.approx(0.9)


def test_a_cdl_follows_its_formula(cut_project):
    project = Project.load(cut_project)
    frame = ramp()
    op = {"op": "cdl", "slope": [1.2, 1.0, 0.9], "offset": [0.02, 0.0, -0.01], "power": [1.1, 1.0, 0.95]}
    out = grade.apply(project, OPS.validate_python([op]), frame.copy())
    expected = np.clip(frame * np.array(op["slope"]) + np.array(op["offset"]), 0, 1) ** np.array(op["power"])
    assert np.allclose(out, expected, atol=2e-5)


def test_a_lut_runs_between_its_declared_spaces(cut_project):
    project = Project.load(cut_project)
    (cut_project / "luts").mkdir(exist_ok=True)
    (cut_project / "luts" / "half.cube").write_text("LUT_1D_SIZE 2\n0 0 0\n0.5 0.5 0.5\n")
    working = project.file.color.working
    op = {"op": "lut", "path": "luts/half.cube", "space": working, "out": working}
    out = grade.apply(project, OPS.validate_python([op]), ramp())
    assert np.allclose(out, ramp() * 0.5, atol=1e-5)


def test_grade_ops_are_checked():
    with pytest.raises(ValidationError):
        OPS.validate_python([{"op": "primary", "lift": 0.1}])  # not a field: an error, not ignored
    with pytest.raises(ValidationError):
        OPS.validate_python([{"op": "lut", "path": "../outside.cube", "space": "a", "out": "b"}])
    with pytest.raises(ValidationError):
        OPS.validate_python([{"op": "curves"}])


def test_a_graded_layer_renders_brighter_and_validate_builds_grades(cut_project):
    from lungecuta.comp import load_comp, resolve
    from lungecuta.render import frame_at

    project = Project.load(cut_project)
    before = frame_at(project, resolve(project, load_comp(project, "main")), 20)
    comp = comp_json()
    comp["layers"][1]["grade"] = [{"op": "primary", "offset": 0.05}]
    put_comp(cut_project, comp)
    project = Project.load(cut_project)
    after = frame_at(project, resolve(project, load_comp(project, "main")), 20)
    assert after.mean() > before.mean() + 0.02
    assert json.loads(CliRunner().invoke(main, ["validate", "--project", str(cut_project)]).output)["valid"]

    comp["layers"][1]["grade"] = [{"op": "lut", "path": "luts/missing.cube", "space": "ACEScct", "out": "ACEScct"}]
    put_comp(cut_project, comp)
    result = CliRunner().invoke(main, ["validate", "--project", str(cut_project)])
    assert result.exit_code != 0 and "luts/missing.cube is missing" in str(result.exception)


def test_fcpxml_refuses_a_graded_layer(cut_project):
    comp = comp_json()
    comp["layers"][1]["grade"] = [{"op": "primary", "contrast": 1.1}]
    put_comp(cut_project, comp)
    result = CliRunner().invoke(main, ["fcpxml", "main", "--project", str(cut_project)])
    assert result.exit_code != 0 and "FCPXML cannot carry" in str(result.exception)


GRAPH = TypeAdapter(list[GradeOp] | GradeGraph)


def test_nodes_run_in_order_keyed_by_their_window_and_mixed(cut_project):
    project = Project.load(cut_project)
    frame = np.full((20, 40, 3), 0.4, np.float32)
    lift = {"op": "primary", "offset": 0.1}
    serial = GRAPH.validate_python({"nodes": [{"ops": [lift]}, {"ops": [{"op": "primary", "contrast": 2, "pivot": 0.5}]}]})
    out = grade.apply(project, serial, frame.copy())
    np.testing.assert_allclose(out, (0.5 - 0.5) * 2 + 0.5, atol=1e-5)  # lifted to 0.5 first, then turned around it
    windowed = GRAPH.validate_python({"nodes": [{"ops": [lift], "window": [
        {"shape": "rectangle", "centre": [0.25, 0.5], "size": [0.5, 1.0]}]}]})
    out = grade.apply(project, windowed, frame.copy())
    np.testing.assert_allclose(out[:, :20], 0.5, atol=1e-5)  # inside the window
    np.testing.assert_allclose(out[:, 20:], 0.4, atol=1e-6)  # outside it, untouched
    half = GRAPH.validate_python({"nodes": [{"ops": [lift], "mix": 0.5}]})
    np.testing.assert_allclose(grade.apply(project, half, frame.copy()), 0.45, atol=1e-5)
    assert [op.op for op in grade.operations(serial)] == ["primary", "primary"]


def test_lc_op_grade_takes_a_node_graph(cut_project):
    graph = {"nodes": [{"ops": [{"op": "primary", "saturation": 0.0}], "window": [
        {"shape": "ellipse", "size": [0.5, 0.5], "feather": 0.1, "invert": True}], "mix": 0.8}]}
    result = CliRunner().invoke(main, ["op", "grade", "main", "shot1", "--ops", json.dumps(graph),
                                       "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    assert "1 nodes" in json.loads(result.output)["changes"][0]
    assert json.loads(CliRunner().invoke(main, ["validate", "--project", str(cut_project)]).output)["valid"]


def test_ocio_hsy_is_what_the_qualifier_says():
    px = np.array([[[1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [0.5, 0.5, 0.5]]], np.float32)
    grade._hsy().applyRGB(px)
    np.testing.assert_allclose(px[0, :, 0], [1 / 6, 1 / 2, 5 / 6, 0, 0], atol=1e-6)
    np.testing.assert_allclose(px[0, 0, 1], 4 * (1 - 0.2126 + 0.2126 + 0.2126), atol=1e-5)


def test_a_qualifier_keys_a_node_on_hue_with_greys_left_out(cut_project):
    project = Project.load(cut_project)
    frame = np.array([[[0.8, 0.2, 0.2], [0.2, 0.8, 0.2], [0.5, 0.5, 0.5], [0.8, 0.5, 0.2]]], np.float32)
    reds = {"nodes": [{"ops": [{"op": "primary", "offset": 0.1}],
                       "key": {"hue": {"centre": 1 / 6, "width": 0.1, "soft": 0.05}, "sat": {"low": 0.5, "high": 100}}}]}
    out = grade.apply(project, GRAPH.validate_python(reds), frame.copy())
    np.testing.assert_allclose(out[0, 0] - frame[0, 0], 0.1, atol=1e-5)  # red, keyed
    np.testing.assert_allclose(out[0, 1], frame[0, 1], atol=1e-6)  # green, not
    np.testing.assert_allclose(out[0, 2], frame[0, 2], atol=1e-6)  # grey: hue 0 but no saturation
    edge = out[0, 3] - frame[0, 3]  # an orange in the softness: partly keyed
    assert edge[0] == pytest.approx(0.1 / 3, abs=1e-4)  # hue 0.25: 0.033 beyond the range, a third of the way back
    inverted = GRAPH.validate_python({"nodes": [dict(reds["nodes"][0], key=dict(reds["nodes"][0]["key"], invert=True))]})
    out = grade.apply(project, inverted, frame.copy())
    np.testing.assert_allclose(out[0, 0], frame[0, 0], atol=1e-6)
