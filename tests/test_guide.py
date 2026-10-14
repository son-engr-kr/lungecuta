"""The guide a coding agent reads in a project (src/lungecuta/guide.py)."""
import json
import re
import tomllib
from pathlib import Path

from click.testing import CliRunner

from lungecuta.cli import main
from lungecuta.guide import END, IMPORT, INSTALL, START, guide_text, write_guide
from lungecuta.ops import OPERATIONS
from lungecuta.project import init_project


def run(*args):
    result = CliRunner().invoke(main, list(args), catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_init_writes_the_guide_for_codex_and_claude_code(tmp_path):
    directory = init_project(tmp_path / "edit.lungecuta")
    agents = (directory / "AGENTS.md").read_text()
    assert agents.startswith(START) and agents.rstrip().endswith(END) and guide_text() in agents
    assert (directory / "CLAUDE.md").read_text() == IMPORT + "\n"
    server = {"command": "lungecuta", "args": ["mcp"]}
    assert json.loads((directory / ".mcp.json").read_text()) == {"mcpServers": {"lungecuta": server}}
    assert tomllib.loads((directory / ".codex" / "config.toml").read_text()) == {"mcp_servers": {"lungecuta": server}}


def test_the_readme_installs_lungecuta_as_init_says_to():
    repository = Path(__file__).parents[1]
    assert INSTALL in (repository / "README.md").read_text()
    assert f"--python {(repository / '.python-version').read_text().strip()} " in INSTALL


def test_the_guide_names_only_commands_that_exist():
    text = guide_text()
    commands = set(re.findall(r"`lungecuta ([a-z-]+)", text))
    assert {"changes", "op", "sheet", "still", "scope", "render", "verify", "flash"} <= commands
    assert commands <= set(main.commands), commands - set(main.commands)
    listed = re.search(r"\(move, (.*?)\): the editor's", text, re.S).group(1)
    ops = {"move", *(name.strip() for name in listed.replace("\n", " ").split(","))}
    assert ops == set(main.commands["op"].commands), ops ^ set(main.commands["op"].commands)
    assert ops == set(OPERATIONS), ops ^ set(OPERATIONS)  # the editor's table and lungecuta op's are one
    for flag in ("--every", "--from", "--to"):
        assert flag in {param.opts[0] for param in main.commands["sheet"].params if param.opts}


def test_writing_the_guide_again_keeps_what_the_person_wrote(tmp_path):
    directory = init_project(tmp_path / "edit.lungecuta")
    agents = directory / "AGENTS.md"
    old = agents.read_text().replace(guide_text(), "an older guide\n")
    agents.write_text("# Our notes\n\nCut on the snare.\n\n" + old + "\nMore notes.\n")
    (directory / "CLAUDE.md").write_text("Speak Korean.\n")
    printed = run("guide", "--write", "--project", str(directory))
    assert printed["files"] == {"AGENTS.md": "updated", "CLAUDE.md": "updated", ".mcp.json": "unchanged",
                                ".codex/config.toml": "unchanged"}
    text = agents.read_text()
    assert text.startswith("# Our notes\n\nCut on the snare.\n\n") and text.endswith("\nMore notes.\n")
    assert "an older guide" not in text and text.count(START) == 1 and guide_text() in text
    assert (directory / "CLAUDE.md").read_text() == "Speak Korean.\n\n" + IMPORT + "\n"
    assert write_guide(directory)["files"] == {"AGENTS.md": "unchanged", "CLAUDE.md": "unchanged",
                                               ".mcp.json": "unchanged", ".codex/config.toml": "unchanged"}


def test_a_guide_is_added_to_an_agents_file_without_one(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Notes\n")
    write_guide(tmp_path)
    text = (tmp_path / "AGENTS.md").read_text()
    assert text.startswith("# Notes\n\n" + START) and guide_text() in text


def test_guide_prints_the_guide():
    assert run("guide")["guide"] == guide_text()


def test_commands_describes_every_command_and_operation():
    described = {c["command"]: c for c in run("commands")["commands"]}
    assert set(described) == {f"lungecuta {name}" for name in main.commands}
    op = described["lungecuta op"]
    assert {c["command"].removeprefix("lungecuta op ") for c in op["commands"]} == set(OPERATIONS)
    sheet = described["lungecuta sheet"]
    every = next(o for o in sheet["options"] if o["flags"] == ["--every"])
    assert every["required"] and "b:1" in every["help"] and sheet["arguments"][0]["name"] == "comp"
    render = described["lungecuta render"]
    preset = next(o for o in render["options"] if o["flags"] == ["--preset"])
    assert "web-hevc10" in preset["choices"]
    assert "  " not in sheet["summary"] and sheet["summary"].startswith("A contact sheet")


def test_an_mcp_config_keeps_the_other_servers_it_lists(tmp_path):
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    assert write_guide(tmp_path)["files"][".mcp.json"] == "updated"
    servers = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert set(servers) == {"other", "lungecuta"}


def test_a_codex_config_keeps_what_it_holds(tmp_path):
    (tmp_path / ".codex").mkdir()
    config = tmp_path / ".codex" / "config.toml"
    config.write_text('model = "gpt-5.5"\n\n[mcp_servers.other]\ncommand = "x"\n')
    assert write_guide(tmp_path)["files"][".codex/config.toml"] == "updated"
    read = tomllib.loads(config.read_text())
    assert read["model"] == "gpt-5.5" and set(read["mcp_servers"]) == {"other", "lungecuta"}
    assert write_guide(tmp_path)["files"][".codex/config.toml"] == "unchanged"


def test_the_help_says_what_each_grade_field_and_property_value_means():
    grade = CliRunner().invoke(main, ["op", "grade", "--help"]).output
    assert "offset (0.0): added to the working-space code values" in grade and "power ([1.0, 1.0, 1.0])" in grade
    for command in ("set", "key"):
        text = CliRunner().invoke(main, ["op", command, "--help"]).output
        assert "fractions of the comp's width and height" in text and "gain (0.0): dB" in text
