"""Picture effects on the finished frame (05-motion-graphics.md, Picture effects): each
effect on frames whose answer is known, the datamosh's motion and its order, and the
effects inside render, verify and a draft on the comp in tests/clips.py."""
import json

import numpy as np
import pytest
from click.testing import CliRunner

from clips import comp_json, put_comp
from lungecuta import fx
from lungecuta.cli import main
from lungecuta.comp import load_comp, resolve
from lungecuta.media import Project
from lungecuta.schema.comp import CompFile, Grain, Halation, RgbSplit, Vignette

GREY = np.full((72, 128, 3), 0.5, np.float32)


def test_grain_is_its_amount_at_mid_grey_and_the_same_for_a_frame():
    effect = Grain(type="grain", amount=0.04, size=0, edges=0.5, seed=3)
    a, b = fx.grain(effect, GREY.copy(), 7, 1.0), fx.grain(effect, GREY.copy(), 7, 1.0)
    assert np.array_equal(a, b) and not np.array_equal(a, fx.grain(effect, GREY.copy(), 8, 1.0))
    assert (a - 0.5).std() == pytest.approx(0.04, rel=1e-5) and np.allclose(a[..., 0], a[..., 2])
    black = fx.grain(effect, np.zeros_like(GREY), 7, 1.0)
    assert black.std() == pytest.approx(0.02, rel=1e-5), "edges: half the amount at black"
    assert (fx.grain(effect, GREY.copy(), 7, 0.5) - 0.5).std() == pytest.approx(0.02, rel=1e-5), "the envelope scales it"


def test_vignette_leaves_the_centre_and_darkens_the_corners():
    out = fx.vignette(Vignette(type="vignette", amount=0.3, inner=0.4, power=1.0), GREY.copy(), 0, 1.0)
    assert out[36, 64, 0] == pytest.approx(0.5) and out[0, 0, 0] < out[10, 20, 0] < 0.5
    assert out[0, 0, 0] == pytest.approx(0.5 * (1 - 0.3 * (np.hypot(63.5 / 64, 35.5 / 36) / np.sqrt(2) - 0.4) / 0.6), rel=1e-5)


def test_halation_glows_in_its_colour_around_what_is_bright():
    frame = np.zeros((72, 128, 3), np.float32)
    frame[30:42, 58:70] = 1.0
    out = fx.halation(Halation(type="halation", threshold=0.8, knee=0.2, width=0.05, core=0.0,
                               colour=(1.0, 0.5, 0.25), strength=0.5), frame, 0, 1.0)
    near, far = out[28, 64], out[2, 2]
    assert near[0] > near[1] > near[2] > 0 and near[1] == pytest.approx(near[0] * 0.5, rel=1e-5)
    assert far.max() < 1e-3 and np.all(out[36, 64] == 1.0)


def test_rgb_split_moves_red_right_and_blue_left():
    frame = np.zeros((72, 128, 3), np.float32)
    frame[:, 64:] = 1.0
    out = fx.rgb_split(RgbSplit(type="rgb_split", shift=4 / 128, seed=0), frame, 0, 1.0)
    row = out[10]
    assert row[64:68, 0].max() == 0 and row[68, 0] == 1, "red starts 4 px later"
    assert row[60, 2] == 1 and row[59, 2] == 0, "blue 4 px earlier"
    assert np.array_equal(row[:, 1], frame[10, :, 1])
    sliced = RgbSplit(type="rgb_split", shift=0, seed=1, slices={"count": 3, "height": [0.1, 0.2], "offset": 0.1})
    assert not np.array_equal(fx.rgb_split(sliced, frame, 0, 1.0), frame)
    assert np.array_equal(fx.rgb_split(sliced, frame, 0, 0.0), frame), "no bands at no strength"


def test_block_motion_finds_a_shift_and_the_warp_follows_it():
    rng = np.random.default_rng(0)
    previous = np.repeat(rng.random((64, 96, 1), dtype=np.float32), 3, axis=2)
    current = np.roll(previous, (2, 4), axis=(0, 1))  # current(x) = previous(x - (2, 4))
    vectors = fx.block_motion(current, previous, block=8, search=6)
    assert np.all(vectors[16:48, 16:80] == (-2, -4))
    warped = fx.warp(previous, vectors)
    assert np.allclose(warped[16:48, 16:80], current[16:48, 16:80])


def with_effects(project_dir, effects):
    put_comp(project_dir, comp_json(effects=effects))
    project = Project.load(project_dir)
    return project, resolve(project, load_comp(project, "main"))


MOSH = {"type": "datamosh", "at": ["b:4"], "length": "f:5", "motion": 1.0, "block": 0.25, "search": 0.1, "ease": 2.0}


def test_a_moshed_frame_alone_is_the_frame_made_in_order(cut_project):
    project, resolved = with_effects(cut_project, [MOSH, {"type": "grain", "amount": 0.02, "size": 0.01,
                                                         "edges": 0.5, "seed": 1}])
    plan = fx.Plan(resolved)
    (cut,) = plan.cuts
    assert (cut.first, cut.frames) == (68, 5) and plan.needs([70]) == [67, 68, 69, 70]
    rng = np.random.default_rng(2)
    composites = {i: rng.random((36, 64, 3), dtype=np.float32) for i in range(66, 75)}
    ordered = fx.InOrder(resolved)
    made = {i: ordered.take(i, composites[i]) if not ordered.alone(i) else plan.apply_local(i, composites[i])
            for i in range(66, 75)}
    for i in range(66, 75):
        assert np.array_equal(made[i], fx.finished(resolved, i, composites.__getitem__)), i
    assert np.array_equal(fx.finished(resolved, 68, composites.__getitem__),
                          plan.apply_local(68, composites[67])), "the cut's first frame is the frame before it"


def test_a_datamosh_comes_first_and_inside_the_comp():
    comp = comp_json(effects=[{"type": "vignette", "amount": 0.2, "inner": 0.5, "power": 1}, MOSH])
    with pytest.raises(ValueError, match="comes before the other effects"):
        CompFile.model_validate(comp)


def test_render_verify_and_draft_take_the_effects(cut_project):
    effects = [MOSH, {"type": "rgb_split", "shift": 0.01, "seed": 4,
                      "beats": {"on": "beats", "floor": 0.3, "decay": "f:3"}},
               {"type": "vignette", "amount": 0.3, "inner": 0.3, "power": 1.5}]
    with_effects(cut_project, effects)
    run = lambda *args: CliRunner().invoke(main, [*args, "--project", str(cut_project)])
    rendered = run("render", "main", "--preset", "web-h264")
    assert rendered.exit_code == 0, rendered.output
    checked = run("verify", json.loads(rendered.output)["file"], "--comp", "main", "--preset", "web-h264")
    assert checked.exit_code == 0 and json.loads(checked.output)["ok"], checked.output
    draft = run("render", "main", "--preset", "draft-h264")
    assert draft.exit_code == 0, draft.output
    still = run("still", "main", "--at", "f:70")
    assert still.exit_code == 0, still.output
    assert run("fcpxml", "main").exit_code != 0
    assert run("render", "main", "--preset", "prores-4444-alpha").exit_code != 0
