import json
import subprocess

from click.testing import CliRunner

from clips import TITLE, comp_json, put_comp
from lungecuta.cli import main


def run(project, *args):
    result = CliRunner().invoke(main, [*args, "--project", str(project)])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_lint_warns_on_a_short_title_a_repeated_shot_and_unlicensed_music(cut_project):
    (cut_project / "graphics" / "title.html").write_text(TITLE)
    comp = comp_json()
    comp["layers"].append({"id": "title", "type": "graphics", "page": "graphics/title.html", "space": "display",
                           "start": "b:2", "out": "f:15"})
    comp["layers"][2]["source_start"] = "f:20"  # shot2 shows source frames shot1 shows too
    put_comp(cut_project, comp)
    report = run(cut_project, "lint", "main")
    rules = {w["rule"]: w for w in report["warnings"]}
    assert set(rules) == {"graphics_min_seconds", "repeated_source", "music_licence"}
    assert "0.50 s" in rules["graphics_min_seconds"]["message"]
    assert rules["repeated_source"]["layers"] == ["shot1", "shot2"]
    assert report["not_checked"]

    run(cut_project, "licence", "track", "--name", "CC0", "--source", "https://example.org/track", "--checked",
        "2026-10-13")
    settings = json.loads((cut_project / "project.json").read_text())
    settings["lint"] = {"graphics_min_seconds": None, "repeated_source": False}
    (cut_project / "project.json").write_text(json.dumps(settings))
    assert run(cut_project, "lint", "main")["warnings"] == []


def test_diff_reports_edits_in_editing_terms(cut_project, tmp_path):
    before = tmp_path / "before.json"
    before.write_text((cut_project / "comps" / "main.json").read_text())
    run(cut_project, "op", "move", "main", "shot2", "--by", "b:1")
    run(cut_project, "op", "trim", "main", "shot1", "--out", "b:-0.5")
    run(cut_project, "op", "set", "main", "shot1", "scale", "--value", "1.2")
    report = run(cut_project, "diff", "main", str(before), "working")
    assert "shot2 start moved b:4 to b:5" in report["changes"]
    assert "shot1 out point moved b:4 to b:3.5 (from start)" in report["changes"]
    assert any(change.startswith("shot1 scale changed 1.0 to 1.2") for change in report["changes"])


def test_diff_reads_git_revisions(cut_project):
    git = ["git", "-C", str(cut_project)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "comps/main.json"], check=True)
    subprocess.run([*git, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "first"], check=True)
    run(cut_project, "op", "ripple-delete", "main", "shot1")
    changes = run(cut_project, "diff", "main")["changes"]
    assert "shot1 removed (was footage C0001)" in changes and "shot2 start moved b:4 to b:0" in changes
