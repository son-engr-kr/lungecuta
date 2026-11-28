"""Project housekeeping (02-media.md, Ingest): lungecuta's own renders imported as decided
display-referred video, known by their manifests, and clips left out of the edit with
`lungecuta exclude`, on the comp in tests/clips.py."""
import json
import shutil

import pytest
from click.testing import CliRunner

from lungecuta import ops
from lungecuta.cli import main
from lungecuta.media import Project, load_record, own_render


def run(project, *args, ok=True):
    result = CliRunner().invoke(main, [*args, "--project", str(project)])
    assert (result.exit_code == 0) == ok, result.output
    return json.loads(result.output) if ok else result


def test_an_own_render_imports_decided_display(cut_project):
    file = run(cut_project, "render", "main", "--preset", "web-h264", "--height", "36")["file"]
    imported = run(cut_project, "import", file, "--root", "project", "--key", "v1")["imported"][0]
    record = load_record(Project.load(cut_project), "v1")
    assert (record.color.status, record.color.space, record.color.matrix, record.color.range) == (
        "decided", "display", "bt709", "limited")
    assert record.color.evidence["source"] == "lungecuta-render" and record.color.evidence["comp"] == "main"
    assert imported["key"] == "v1"
    ops.place(Project.load(cut_project), "main", "v1", "b:8", length="b:1")  # usable as it is
    copy = cut_project / "copy.mp4"
    shutil.copy(file, copy)  # no manifest beside it: undecided, as any file with only its tags
    run(cut_project, "import", str(copy), "--root", "project")
    assert load_record(Project.load(cut_project), "copy").color.status == "undecided"


def test_an_own_render_for_another_view_is_left_undecided(cut_project):
    file = run(cut_project, "render", "main", "--preset", "web-h264", "--height", "36")["file"]
    manifest = json.loads(open(file + ".manifest.json").read())
    manifest["color"]["view"] = "another view"
    open(file + ".manifest.json", "w").write(json.dumps(manifest))
    project = Project.load(cut_project)
    from lungecuta.files import content_hash
    from lungecuta.probe import probe
    from pathlib import Path

    found = own_render(project, Path(file), content_hash(Path(file)), probe(Path(file)).video)
    assert found.status == "undecided" and "another view" in found.reason
    assert own_render(project, Path(file), "sha256:" + "0" * 64, probe(Path(file)).video) is None, "another version"


def test_an_excluded_clip_is_kept_but_not_placed(cut_project):
    printed = run(cut_project, "exclude", "C0001", "--note", "soft focus")
    assert printed["excluded"][0]["excluded"]["note"] == "soft focus"
    status = run(cut_project, "status")["media"]
    assert [c["key"] for c in status["excluded"]] == ["C0001"] and not status["footage"]
    with pytest.raises(AssertionError, match="left out of the edit"):
        ops.place(Project.load(cut_project), "main", "C0001", "b:8", length="b:1")
    warnings = [w for w in run(cut_project, "lint", "main")["warnings"] if w["rule"] == "excluded"]
    assert sorted(layer for w in warnings for layer in w["layers"]) == ["shot1", "shot2"]
    assert run(cut_project, "exclude", "C0001", "--note", "again", ok=False).exit_code != 0
    run(cut_project, "exclude", "C0001", "--undo")
    assert load_record(Project.load(cut_project), "C0001").excluded is None
    ops.place(Project.load(cut_project), "main", "C0001", "b:8", length="b:1")
