"""Captions: `lungecuta op captions` and lungecuta's captions page, on the comp in
tests/clips.py (128x72 at 29.97 fps)."""
import dataclasses
import json

import numpy as np
import pytest
from click.testing import CliRunner

from lungecuta import ops
from lungecuta.cli import main
from lungecuta.comp import load_comp, resolve
from lungecuta.compose import frame_at
from lungecuta.media import Project

LOOK = """#captions { justify-content: center; align-items: center; }
.line { font: 700 24px sans-serif; color: #fff; }
.word { opacity: 0; } .word.said { opacity: 1; }
.fading { opacity: min(1, calc(var(--in) / 1)); }"""

LINES = [
    {"at": "1s", "to": "2s", "text": "Hi"},
    {"at": "2.5s", "to": "3.5s", "words": [{"at": "2.5s", "text": "one"}, {"at": "3s", "text": "two"}]},
    {"at": "4s", "to": "5s", "text": "Hi", "class": "fading"},
]


def frame_index(seconds: float) -> int:
    return round(seconds * 30000 / 1001)


def drawn(project, resolved, seconds):
    """How many pixels the captions change on the frame at `seconds`, and how much light they add."""
    index = frame_index(seconds)
    with_captions = frame_at(project, resolved, index)
    beneath = frame_at(project, dataclasses.replace(resolved, overlays=[]), index)
    changed = np.abs(with_captions - beneath).max(axis=2) > 1e-3
    return int(changed.sum()), float((with_captions - beneath).clip(0).sum())


def test_captions_go_on_as_one_layer_of_lungecuta_s_page(cut_project):
    result = ops.captions(Project.load(cut_project), "main", LINES, LOOK)
    assert result["changes"] == ["graphics/captions.html placed at 1s as captions, 4s long, with css, lines, 3 lines"]
    layer = json.loads((cut_project / "comps" / "main.json").read_text())["layers"][-1]
    assert (layer["type"], layer["start"]) == ("graphics", "1s")
    assert layer["params"]["lines"] == [
        {"from": 0.0, "to": 1.0, "text": "Hi"},
        {"from": 1.5, "to": 2.5, "words": [{"at": 1.5, "text": "one"}, {"at": 2.0, "text": "two"}]},
        {"from": 3.0, "to": 4.0, "text": "Hi", "class": "fading"}]
    assert (cut_project / "graphics" / "captions.html").is_file()
    (cut_project / "graphics" / "captions.html").write_text("<!-- the project's own -->")
    ops.captions(Project.load(cut_project), "main", LINES[:1], LOOK)
    assert (cut_project / "graphics" / "captions.html").read_text() == "<!-- the project's own -->", "kept"


def test_each_line_shows_from_its_time_to_its_end(cut_project):
    ops.captions(Project.load(cut_project), "main", LINES, LOOK)
    project = Project.load(cut_project)
    resolved = resolve(project, load_comp(project, "main"))
    assert drawn(project, resolved, 1.5)[0] > 50  # "Hi"
    assert drawn(project, resolved, 2.2) == (0, 0.0)  # between the lines
    one, _ = drawn(project, resolved, 2.7)  # only "one" said
    both, _ = drawn(project, resolved, 3.2)
    assert one > 50 and both > one * 1.5
    # a line animated by its --in, from t: at 4.5 s it has been on for half a second (0.5045 on
    # the frame), so its glyphs are white at that opacity over what is beneath
    full = frame_at(project, resolved, frame_index(1.5))
    inside = np.all(full == 1.0, axis=2)  # pixels the opaque "Hi" covers entirely
    assert inside.sum() > 20
    index = frame_index(4.5)
    faded = frame_at(project, resolved, index)[inside]
    beneath = frame_at(project, dataclasses.replace(resolved, overlays=[]), index)[inside]
    opacity = (faded - beneath) / (1 - beneath)
    assert float(np.median(opacity)) == pytest.approx(index * 1001 / 30000 - 4.0, abs=0.01)


def test_captions_are_refused_without_a_look_or_with_a_line_that_ends_before_it_starts(cut_project):
    project = Project.load(cut_project)
    with pytest.raises(AssertionError, match="captions need their look"):
        ops.captions(project, "main", LINES, " ")
    with pytest.raises(AssertionError, match="line 0 ends at or before it starts"):
        ops.captions(project, "main", [{"at": "2s", "to": "1s", "text": "x"}], LOOK)
    with pytest.raises(AssertionError, match="a line has at, to, and text or words"):
        ops.captions(project, "main", [{"at": "1s", "to": "2s", "text": "x", "words": []}], LOOK)
    result = CliRunner().invoke(main, ["op", "captions", "main", "--lines", json.dumps(LINES[:1]), "--css", LOOK,
                                       "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
