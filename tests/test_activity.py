"""Every command run in a project is logged (src/lungecuta/activity.py)."""
import json

from click.testing import CliRunner

from lungecuta import activity
from lungecuta.cli import main
from lungecuta.mcp_server import call, tools


def test_each_command_in_a_project_is_logged_with_its_time_and_outcome(cut_project):
    runner = CliRunner()
    runner.invoke(main, ["status", "--project", str(cut_project)], catch_exceptions=False)
    runner.invoke(main, ["op", "move", "main", "shot2", "--by", "b:1", "--project", str(cut_project)],
                  catch_exceptions=False)
    failed = runner.invoke(main, ["op", "move", "main", "nothing", "--by", "b:1", "--project", str(cut_project)])
    assert failed.exit_code != 0
    runner.invoke(main, ["doctor"])  # no project: not logged anywhere
    logged = activity.commands(cut_project)
    assert [(e["command"], e["ok"], e["via"]) for e in logged] == [
        ("status", True, "cli"), ("op move", True, "cli"), ("op move", False, "cli")]
    assert logged[1]["params"]["comp"] == "main" and logged[1]["params"]["by"] == "b:1"
    assert all(e["seconds"] >= 0 and e["time"][:4].isdigit() for e in logged)


def test_a_command_through_mcp_is_logged_as_such(cut_project, monkeypatch):
    monkeypatch.setattr(activity, "via", "mcp")
    words, command = tools(main)["validate"]
    result = call(main, words, command, {}, cut_project)
    assert not result.is_error
    assert [(e["command"], e["via"]) for e in activity.commands(cut_project)] == [("validate", "mcp")]
    assert json.loads(result.content[0].text)


def test_stages_count_the_time_before_each_thing_to_its_stage(cut_project):
    from lungecuta import journal
    from lungecuta.media import Project
    from lungecuta.stages import stages

    log = cut_project / activity.LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    entries = [  # minute by minute: material, then a cut, a grade, a long break, a render
        ("2026-10-14T10:00:00-0400", 60.0, "import", {"files": ["a.mp4"]}),
        ("2026-10-14T10:05:00-0400", 1.0, "sheet", {"key": "a"}),
        ("2026-10-14T10:09:00-0400", 1.0, "op place", {"comp": "main"}),
        ("2026-10-14T10:12:00-0400", 1.0, "op grade", {"comp": "main"}),
        ("2026-10-14T11:00:00-0400", 120.0, "render", {"comp": "main"}),
    ]
    log.write_text("".join(json.dumps({"time": t, "seconds": s, "command": c, "via": "cli", "ok": True, "params": p}) + "\n"
                           for t, s, c, p in entries))
    journal.record(cut_project, "comps/main.json", "{}", "{}", ["shot2 moved"], op="move")
    journal_path = cut_project / journal.JOURNAL
    entry = json.loads(journal_path.read_text())
    journal_path.write_text(json.dumps(entry | {"time": "2026-10-14T10:10:00-0400"}) + "\n")
    report = stages(Project.load(cut_project), comp="main")
    # import ran 1 min; 4 min more to the sheet (material 5); 4 min to the place and 1 to the
    # editor's move (cutting 5); 2 min to the grade (grading 2); a 48 min break; the render ran 2 min
    assert report["minutes"] == {"material": 5.0, "cutting": 5.0, "grading": 2.0, "graphics": 0.0, "audio": 0.0,
                                 "review": 0.0, "rendering": 2.0}
    assert report["total_minutes"] == 14.0
    assert report["counts"]["cutting"] == {"agent": 1, "editor": 1}
    assert [s["minutes"] for s in report["sessions"]] == [12.0, 2.0]
    assert report["renders"] == [{"at": report["renders"][0]["at"], "seconds": 120.0, "ok": True}]
    assert report["material"]["shots"] == 2 and report["material"]["comp"] == "main"
