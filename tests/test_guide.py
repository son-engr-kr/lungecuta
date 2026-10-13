"""The guide a coding agent reads in a project (src/lungecuta/guide.py)."""
import json
import re

from click.testing import CliRunner

from lungecuta.cli import main
from lungecuta.guide import END, IMPORT, START, guide_text, write_guide
from lungecuta.ops import OPERATIONS
from lungecuta.project import init_project


def run(*args):
    result = CliRunner().invoke(main, list(args), catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_lc_init_writes_the_guide_for_codex_and_claude_code(tmp_path):
    directory = init_project(tmp_path / "edit.lc")
    agents = (directory / "AGENTS.md").read_text()
    assert agents.startswith(START) and agents.rstrip().endswith(END) and guide_text() in agents
    assert (directory / "CLAUDE.md").read_text() == IMPORT + "\n"


def test_the_guide_names_only_commands_that_exist():
    text = guide_text()
    commands = set(re.findall(r"`lc ([a-z-]+)", text))
    assert {"changes", "op", "sheet", "still", "scope", "render", "verify", "flash"} <= commands
    assert commands <= set(main.commands), commands - set(main.commands)
    listed = re.search(r"\(move, (.*?)\): the editor's", text, re.S).group(1)
    ops = {"move", *(name.strip() for name in listed.replace("\n", " ").split(","))}
    assert ops == set(main.commands["op"].commands), ops ^ set(main.commands["op"].commands)
    assert ops == set(OPERATIONS), ops ^ set(OPERATIONS)  # the editor's table and lc op's are one
    for flag in ("--every", "--from", "--to"):
        assert flag in {param.opts[0] for param in main.commands["sheet"].params if param.opts}


def test_writing_the_guide_again_keeps_what_the_person_wrote(tmp_path):
    directory = init_project(tmp_path / "edit.lc")
    agents = directory / "AGENTS.md"
    old = agents.read_text().replace(guide_text(), "an older guide\n")
    agents.write_text("# Our notes\n\nCut on the snare.\n\n" + old + "\nMore notes.\n")
    (directory / "CLAUDE.md").write_text("Speak Korean.\n")
    printed = run("guide", "--write", "--project", str(directory))
    assert printed["files"] == {"AGENTS.md": "updated", "CLAUDE.md": "updated"}
    text = agents.read_text()
    assert text.startswith("# Our notes\n\nCut on the snare.\n\n") and text.endswith("\nMore notes.\n")
    assert "an older guide" not in text and text.count(START) == 1 and guide_text() in text
    assert (directory / "CLAUDE.md").read_text() == "Speak Korean.\n\n" + IMPORT + "\n"
    assert write_guide(directory)["files"] == {"AGENTS.md": "unchanged", "CLAUDE.md": "unchanged"}


def test_a_guide_is_added_to_an_agents_file_without_one(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Notes\n")
    write_guide(tmp_path)
    text = (tmp_path / "AGENTS.md").read_text()
    assert text.startswith("# Notes\n\n" + START) and guide_text() in text


def test_lc_guide_prints_the_guide():
    assert run("guide")["guide"] == guide_text()
