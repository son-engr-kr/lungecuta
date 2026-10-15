"""lungecuta status: what is in a project, at a glance (src/lungecuta/status.py)."""
import json

from click.testing import CliRunner

from lungecuta.cli import main


def run(*args):
    result = CliRunner().invoke(main, list(args), catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_status_lists_media_comps_and_renders(cut_project):
    printed = run("status", "--project", str(cut_project))
    assert [group["keys"] for group in printed["media"]["footage"]] == [["C0001"]]
    assert printed["media"]["footage"][0]["usable"] and printed["media"]["footage"][0]["size"] == [128, 72]
    assert printed["media"]["footage"][0]["seconds"] == round(70 * 1001 / 30000, 3)
    assert printed["media"]["music"][0]["key"] == "track" and printed["media"]["music"][0]["licence"] is False
    assert printed["media"]["music"][0]["tempo_bpm"] == 120
    (comp,) = printed["comps"]
    assert (comp["comp"], comp["frames"], comp["beats"]) == ("main", 188, 20)
    assert comp["layers"] == {"audio": 1, "footage": 2} and comp["markers"] == ["hit", "drop"]  # in time order: hit at b:1, drop at b:4
    assert printed["renders"] == [] and printed["unseen_editor_changes"] == 0


def test_a_render_goes_stale_when_its_comp_changes(cut_project):
    rendered = run("render", "main", "--preset", "web-h264", "--project", str(cut_project))
    (render,) = run("status", "--project", str(cut_project))["renders"]
    assert render["file"] == rendered["file"] and render["current"] and render["preset"] == "web-h264"
    run("op", "move", "main", "shot2", "--by", "f:1", "--project", str(cut_project))
    (render,) = run("status", "--project", str(cut_project))["renders"]
    assert not render["current"] and render["why_not"] == ["the comp changed"]
