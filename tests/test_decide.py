"""`lungecuta decide`: a person's decision of an undecided clip's colour space
(docs/design/02-media.md, Colour-space detection)."""
import json

import numpy as np
import pytest
from click.testing import CliRunner

from clips import make_clip
from lungecuta.cli import main
from lungecuta.decode import read_rgb
from lungecuta.files import content_hash
from lungecuta.media import Project, decide, import_file, load_record, set_root
from lungecuta.project import init_project
from lungecuta.still import render
from lungecuta.ticks import parse_rate


@pytest.fixture
def imported(tmp_path):
    """A project with two clips imported undecided: no sidecar and no colour tags; one
    states limited range, the other no range."""
    project = init_project(tmp_path / "edit.lungecuta")
    settings = json.loads((project / "project.json").read_text())
    settings["proxy_height"] = 36
    (project / "project.json").write_text(json.dumps(settings))
    footage = tmp_path / "card"
    footage.mkdir()
    set_root(project, "card", footage)
    loaded = Project.load(project)
    for name, limited in (("screen", True), ("bare", False)):
        record = import_file(loaded, make_clip(footage / f"{name}.mov", limited=limited, frames=5), root="card")
        assert record.color.status == "undecided"
    return Project.load(project)


def test_a_screen_recording_decided_as_display_comes_out_as_it_was_recorded(imported):
    before = load_record(imported, "screen")
    assert before.proxy is not None and before.proxy.parity.codes is None
    record = decide(imported, "screen", "display", note="a screen recording of the app", matrix="bt709")
    assert (record.color.status, record.color.space, record.color.matrix, record.color.range) == (
        "decided", "display", "bt709", "limited")
    evidence = record.color.evidence
    assert evidence["source"] == "person" and evidence["note"] == "a screen recording of the app"
    assert (evidence["matrix_from"], evidence["range_from"]) == ("given", "stream tag")
    assert evidence["undecided"]["reason"] == before.color.reason
    assert record.proxy.matrix == "bt709" and record.proxy.parity.codes is not None and record.proxy.parity.di is not None
    assert load_record(imported, "screen") == record
    # through the inverse output view and the view again: the codes it was recorded with,
    # within the round trip measured in docs/design/01-architecture.md, as a display shows
    # them. The test pattern's saturated edges decode, through 4:2:2 chroma, to values outside
    # [0, 1], which no display shows: they come out at the display's edge.
    video = record.probe.video
    shown = render(imported, record, 2)
    recorded = read_rgb(imported.root_path("card") / "screen.mov", 2, rate=parse_rate(video.rate), size=video.size,
                        pix_fmt=video.pix_fmt, matrix="bt709", range_="limited",
                        chroma_location=video.chroma_location)
    inside = ((recorded >= 0) & (recorded <= 1)).all(axis=-1)
    error = np.abs(shown - np.clip(recorded, 0, 1)) * 1023
    assert inside.any() and np.median(error) < 0.15 and error.max() < 12
    assert error[inside].max() < 12


def test_a_stream_that_states_no_range_needs_it_given_and_then_gets_its_proxy(imported):
    assert load_record(imported, "bare").proxy is None  # no range, so no proxy was made at import
    with pytest.raises(AssertionError, match="does not state its range"):
        decide(imported, "bare", "Camera Rec.709", note="the phone's standard video", matrix="bt709")
    record = decide(imported, "bare", "Camera Rec.709", note="the phone's standard video", matrix="bt709",
                    range_="full")
    assert record.color.range == "full" and record.color.evidence["range_from"] == "given"
    result = CliRunner().invoke(main, ["proxy", "bare", "--project", str(imported.directory)])
    assert result.exit_code == 0, result.output
    proxy = load_record(imported, "bare").proxy
    assert proxy.range == "full" and proxy.parity.codes is not None


def test_decide_refuses_a_guess_it_cannot_check_and_writes_nothing(imported):
    path = imported.directory / "media" / "screen.json"
    before = content_hash(path)
    with pytest.raises(AssertionError, match="neither a colour space of the project's config nor 'display'"):
        decide(imported, "screen", "Rec.709", note="looks like it", matrix="bt709")
    with pytest.raises(AssertionError, match="--note"):
        decide(imported, "screen", "display", note="  ", matrix="bt709")
    with pytest.raises(AssertionError, match="does not state a matrix"):
        decide(imported, "screen", "display", note="a screen recording")
    assert content_hash(path) == before
    decide(imported, "screen", "display", note="a screen recording", matrix="bt709")
    with pytest.raises(AssertionError, match="decided already"):
        decide(imported, "screen", "S-Log3 S-Gamut3.Cine", note="no, log", matrix="bt709")


def test_the_command_prints_the_decision_with_its_evidence(imported):
    result = CliRunner().invoke(main, ["decide", "screen", "--colour", "display", "--note", "a screen recording",
                                       "--matrix", "bt709", "--project", str(imported.directory)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert printed["key"] == "screen" and printed["color"] == "decided" and printed["space"] == "display"
    assert printed["evidence"]["source"] == "person" and printed["proxy_parity"] is not None
