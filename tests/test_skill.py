"""The lungecuta skill, which Claude Code and Codex read before a project exists
(src/lungecuta/skill.py)."""
import re

from lungecuta.cli import main
from lungecuta.skill import PLACES, current, install_skill, skill_files, skill_text


def frontmatter(text):
    head, body = text.removeprefix("---\n").split("\n---\n", 1)
    fields = dict(line.split(": ", 1) for line in head.splitlines())
    return fields, body


def test_the_skill_is_an_agent_skill_both_agents_read():
    fields, body = frontmatter(skill_text())
    assert set(fields) == {"name", "description"}
    # agentskills.io/specification: a name of lowercase letters, digits and single hyphens,
    # matching its directory; a description of at most 1024 characters
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fields["name"]) and len(fields["name"]) <= 64
    assert all(place.name == fields["name"] for place in PLACES.values())
    assert 0 < len(fields["description"]) <= 1024 and ": " not in fields["description"]  # a plain YAML scalar
    assert len(body.splitlines()) < 500


def test_the_skill_names_only_commands_and_options_that_exist():
    text = skill_text()
    blocks = re.findall(r"^```\n(.*?)^```$", text, re.M | re.S)
    lines = [line for block in blocks for line in block.splitlines()]
    assert lines and all(line.startswith("lungecuta ") for line in lines)
    commands = set(re.findall(r"`lungecuta ([a-z-]+)", text)) | {line.split()[1] for line in lines}
    assert {"doctor", "setup", "status", "init", "root", "import", "new-comp", "serve", "changes"} <= commands
    assert commands <= set(main.commands), commands - set(main.commands)
    for line in lines + re.findall(r"`(lungecuta [a-z][^`]*)`", text):
        name, *words = line.split()[1:]
        options = {opt for param in main.commands[name].params for opt in param.opts if opt.startswith("--")}
        assert {w for w in words if w.startswith("--")} <= options, (line, options)


def test_installing_writes_both_agents_files_once(tmp_path):
    files = skill_files(tmp_path)
    assert current(tmp_path) == {"Claude Code": False, "Codex": False}
    assert set(install_skill(tmp_path).values()) == {"created"}
    assert files["Claude Code"] == tmp_path / ".claude/skills/lungecuta/SKILL.md"
    assert files["Codex"] == tmp_path / ".agents/skills/lungecuta/SKILL.md"
    assert all(file.read_text() == skill_text() for file in files.values())
    assert current(tmp_path) == {"Claude Code": True, "Codex": True}
    assert set(install_skill(tmp_path).values()) == {"unchanged"}
    files["Codex"].write_text("an older skill\n")
    assert current(tmp_path) == {"Claude Code": True, "Codex": False}
    assert install_skill(tmp_path)[str(files["Codex"])] == "updated" and files["Codex"].read_text() == skill_text()
