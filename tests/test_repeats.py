"""Clips that repeat their images, and shots that should start on a new one (src/lungecuta/repeats.py)."""
import json

import numpy as np
from click.testing import CliRunner

from lungecuta.cli import main
from lungecuta.repeats import cadence
from lungecuta.tools import run

from clips import comp_json, put_comp, write_record


def held_clip(path, frames=60, held=5):
    """The test pattern holding each image for `held` frames at 29.97p, as a long shutter does."""
    run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=128x72:rate={30000 // held}/1001",
         "-vf", "fps=30000/1001", "-frames:v", str(frames), "-c:v", "libx264", "-qp", "0", "-g", "1",
         "-pix_fmt", "yuv422p10le", str(path)])
    return path


def test_the_period_is_found_only_where_images_repeat():
    rng = np.random.default_rng(0)
    held = np.where(np.arange(60) % 5 == 4, rng.uniform(1, 9, 60), rng.uniform(0, 0.2, 60))
    separation, period, phase = cadence(held)
    assert separation > 1 and (period, phase) == (5, 4)
    still_scene = rng.uniform(0.2, 1.5, 300)  # an ordinary clip that barely moves: no period stands out
    assert cadence(still_scene)[0] < 1


def test_a_shot_that_starts_into_an_image_is_linted(cut_project):
    write_record(cut_project, held_clip(cut_project / "footage" / "H0001.mov"))
    run_ = lambda *args: json.loads(CliRunner().invoke(main, [*args, "--project", str(cut_project)],
                                                       catch_exceptions=False).output)
    found = run_("repeats", "H0001")["clips"][0]
    assert found["repeats"] and found["every"] == 5 and found["new"][:3] == [5, 10, 15]
    comp = comp_json()
    comp["layers"] += [{"id": "late", "type": "footage", "media": "H0001", "start": "b:8", "out": "f:20",
                        "source_start": "f:7"}]
    put_comp(cut_project, comp)
    warned = [w for w in run_("lint", "main")["warnings"] if w["rule"] == "new_image"]
    assert len(warned) == 1 and "start it on a new image: f:10 or f:5" in warned[0]["message"]
    comp["layers"][-1]["source_start"] = "f:10"
    put_comp(cut_project, comp)
    assert not [w for w in run_("lint", "main")["warnings"] if w["rule"] == "new_image"]
