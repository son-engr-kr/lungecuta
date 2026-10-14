"""lungecuta commands that set up and describe a project: init, guide, status, import and the media around it, beats, licence, validate, schema, changes, new-comp."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import click

from .. import journal, ops
from ..beats import reanalyse
from ..detect import detect as detect_clip
from ..files import json_text, write_atomic
from ..media import import_files, relink as relink_root, set_root, summary
from ..probe import probe
from ..guide import PUT_ON_PATH, SERVER
from ..project import find_project, init_project
from ..runtime import on_path
from ..schema import SCHEMAS, schema_text
from ..validate import validate as validate_project
from .common import main, parse_size, print_json, project_of, project_option


@main.command()
@click.argument("directory", type=click.Path(file_okay=False, path_type=Path))
def init(directory: Path) -> None:
    """Create a new project in DIRECTORY, which must be empty or not exist yet.

    Writes project.json with the colour defaults, roots.local.json (empty) and a
    .gitignore for it and cache/, the empty media/, comps/, grades/, styles/,
    graphics/, luts/ and cache/ directories, and the guide a coding agent reads
    (AGENTS.md for Codex, CLAUDE.md importing it for Claude Code; see lungecuta guide) with
    the .mcp.json and .codex/config.toml that start lungecuta mcp. The .lungecuta suffix is a
    convention only. When `lungecuta` is not on the PATH, which both need, the output says so
    and how to put it there.
    """
    printed = {"project": str(init_project(directory))}
    if not on_path(SERVER["command"]):
        printed["mcp"] = (f"{SERVER['command']} is not on the PATH outside this virtual environment, so an agent "
                          f"started elsewhere cannot start the project's MCP server: {PUT_ON_PATH}")
    print_json(printed)


@main.command()
@click.option("--write", is_flag=True, help="Write the guide into the project instead of printing it.")
@project_option
def guide(write: bool, project_dir: Path | None) -> None:
    """The guide a coding agent reads in a project: its files, time literals, the steps of
    every edit and the commands that check them. --write puts it into the project's
    AGENTS.md (read by Codex) between its markers, keeping the rest of the file, and makes
    CLAUDE.md import AGENTS.md (read by Claude Code); lungecuta init already does this."""
    from ..guide import guide_text, write_guide

    if write:
        print_json(write_guide(find_project(project_dir or Path.cwd())))
    else:
        print_json({"guide": guide_text()})


@main.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_context
def detect(context: click.Context, file: Path) -> None:
    """The colour space and time of FILE, with every piece of evidence, without importing.

    Exits with status 1 when either is undecided.
    """
    detection = detect_clip(file, probe(file))
    print_json({
        "file": file.name,
        "time": detection.time.model_dump(mode="json"),
        "color": detection.color.model_dump(mode="json"),
    })
    if not detection.decided:
        context.exit(1)


@main.command()
@click.argument("name")
@click.argument("place", type=click.Path(exists=True, file_okay=False, path_type=Path))
@project_option
def root(name: str, place: Path, project_dir: Path | None) -> None:
    """List media root NAME in project.json and place it at PLACE on this machine
    (roots.local.json, never committed)."""
    project = project_of(project_dir)
    set_root(project.directory, name, place)
    print_json({"root": name, "place": str(place.resolve())})


@main.command()
@click.option("--root", "name", required=True, help="The media root whose files have moved.")
@click.argument("place", type=click.Path(exists=True, file_okay=False, path_type=Path))
@project_option
def relink(name: str, place: Path, project_dir: Path | None) -> None:
    """Point media root NAME at PLACE, where its files are now.

    Each record's file is found under PLACE by its size and then its hash, never by name
    alone. Every file of the root must be found, once each; a record whose file now sits
    at another path under PLACE gets that path. Nothing is written otherwise.
    """
    print_json(relink_root(project_of(project_dir), name, place))


@main.command("import")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--root", "root_name", required=True, help="The media root the files lie under.")
@click.option("--key", help="The media key, for a single file whose stem is taken already.")
@click.option("--sound", is_flag=True, help="Files of sound alone are sounds (effects, voices): their WAV, no beat map.")
@project_option
def import_(files: tuple[Path, ...], root_name: str, key: str | None, sound: bool, project_dir: Path | None) -> None:
    """Import FILES as media records under media/: probe, rates and timecode, colour
    decision with evidence, decoder check, proxy with its parity test; music decoded to
    a 48 kHz WAV with its beat map, or with --sound without one."""
    assert key is None or len(files) == 1, "--key names one file"
    records = import_files(project_of(project_dir), list(files), root=root_name, key=key, sound=sound)
    print_json({"imported": [summary(record) for record in records]})


@main.command()
@click.argument("key")
@click.option(
    "--replace",
    is_flag=True,
    help="Apply the new map even though beat or bar indices shift: the person's decision, after reading "
    "what a run without it lists.",
)
@project_option
@click.pass_context
def beats(context: click.Context, key: str, replace: bool, project_dir: Path | None) -> None:
    """Run the beat analysis of music KEY again on its WAV.

    Each new beat is matched to the nearest current one within half the median beat
    interval. The new times are written only if every current beat has exactly one
    match and the downbeats stay on the same indices, so every beat and bar literal keeps
    its musical beat; manual beats keep their times. Otherwise nothing is written, the
    inserted and removed beats are listed, and the command exits with status 1.
    """
    result = reanalyse(project_of(project_dir), key, replace=replace)
    print_json(result)
    if not result["applied"]:
        context.exit(1)


@main.command()
@project_option
def validate(project_dir: Path | None) -> None:
    """Check the project: project.json, roots.local.json and every media record against
    their schemas, every original found and unchanged since import (and every music WAV),
    and every comp resolved to frames. The first problem stops it with its message.

    Printed as JSON: the media count, the keys still undecided in colour or time (which no
    comp can place), and each comp's frames, cuts, sounds and beats.
    """
    print_json(validate_project(project_of(project_dir)))


@main.command("licence")
@click.argument("key")
@click.option("--name", required=True, help="The licence as its source states it (CC0, a stock licence and its number).")
@click.option("--source", required=True, help="The page where it was checked.")
@click.option("--checked", required=True, help="The date it was checked, YYYY-MM-DD.")
@click.option("--note", help="Anything else the next person should know.")
@project_option
def licence_command(key: str, name: str, source: str, checked: str, note: str | None, project_dir: Path | None) -> None:
    """Record under what terms media KEY may be used, as a person checked it on its source."""
    from ..media import load_record
    from ..schema.media import Licence

    project = project_of(project_dir)
    record = load_record(project, key)
    record.licence = Licence(name=name, source=source, checked=checked, note=note)
    path = project.directory / "media" / f"{key}.json"
    write_atomic(path, json_text(record.model_dump(mode="json")))
    print_json({"media": key, "licence": record.licence.model_dump()})


@main.command("new-comp")
@click.argument("name")
@click.option("--size", required=True, help="WIDTHxHEIGHT, such as 3840x2160 or 1080x1920.")
@click.option("--rate", required=True, help="Frame rate as ffprobe writes it: 30000/1001, 25, 60.")
@click.option("--end", required=True, help="Where the comp ends, a position: 30s, f:900, or with music b:64.")
@click.option("--music", help="A music record to put at 0s and take the beat grid from.")
@project_option
def new_comp(name: str, size: str, rate: str, end: str, music: str | None, project_dir: Path | None) -> None:
    """Create comp NAME (comps/NAME.json) and list it in project.json."""
    print_json(ops.new_comp(project_of(project_dir), name, size=parse_size(size), rate=rate, end=end, music=music))


@main.command()
@project_option
def status(project_dir: Path | None) -> None:
    """What is in the project, for an agent arriving: media (footage with its colour space
    and whether it is usable, music with its tempo and whether its licence is recorded),
    comps (size, rate, length, layers by type, markers, transitions), how many editor
    changes lungecuta changes has not shown yet, and each render in cache/lungecuta/render/ with whether
    its comp and media are still the ones it was made from. Changes nothing."""
    from ..status import status as project_status

    print_json(project_status(project_of(project_dir)))


@main.command()
@click.option("--peek", is_flag=True, help="Show the changes without moving the agent's place on.")
@project_option
def changes(peek: bool, project_dir: Path | None) -> None:
    """What the editor wrote since the agent last asked, from the journal of UI writes:
    each entry's file, kind (edit, undo, redo) and changes in editing terms. Run it before
    editing a file by hand, so as not to overwrite what the person just did."""
    print_json(journal.changes(project_of(project_dir).directory, peek=peek))


@main.command()
@click.option("--write", is_flag=True, help="Regenerate the schema files instead of printing them.")
@click.option(
    "--dir",
    "directory",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("schema"),
    show_default=True,
    help="Where the schema files are written.",
)
def schema(write: bool, directory: Path) -> None:
    """The JSON Schema of every project file, generated from the engine's models."""
    if not write:
        print_json({name: json.loads(schema_text(model)) for name, model in SCHEMAS.items()})
        return
    directory.mkdir(exist_ok=True)
    written = []
    for name, model in SCHEMAS.items():
        write_atomic(directory / name, schema_text(model))
        written.append(str(directory / name))
    print_json({"written": written})


def _described(command: click.Command, path: str) -> dict[str, Any]:
    """A command as data: its usage, the first paragraph of its help, its arguments and
    options with their types, defaults and whether they are required."""
    first = inspect.cleandoc(command.help or "").split("\n\n")[0]
    out: dict[str, Any] = {"command": path, "summary": " ".join(first.split())}
    arguments, options = [], []
    for param in command.params:
        entry = {"name": param.name, "type": param.type.name, "required": param.required}
        if isinstance(param, click.Option):
            if param.name == "help":
                continue
            entry |= {"flags": list(param.opts), "flag": param.is_flag, "multiple": param.multiple,
                      "help": param.help or ""}
            if isinstance(param.default, (str, int, float)) and not param.is_flag:  # click marks no default otherwise
                entry["default"] = param.default
            if isinstance(param.type, click.Choice):
                entry["choices"] = list(param.type.choices)
            options.append(entry)
        else:
            arguments.append(entry | {"many": param.nargs == -1})
    out |= {"arguments": arguments, "options": options}
    if isinstance(command, click.Group):
        out["commands"] = [_described(sub, f"{path} {name}") for name, sub in sorted(command.commands.items())]
    return out


@main.command()
def commands() -> None:
    """Every lungecuta command as data, for an agent to read instead of the help text: each with
    its summary, its arguments and its options (flags, types, defaults, choices, whether
    required), the op group's operations among them."""
    print_json({"commands": [_described(command, f"lungecuta {name}")
                             for name, command in sorted(main.commands.items())]})


@main.command()
@project_option
def mcp(project_dir: Path | None) -> None:
    """Serve every lungecuta command as a tool over the Model Context Protocol on stdio, for Claude
    Code, Codex or any MCP client: each tool's input schema is its command's arguments and
    options, a call returns what the command prints, and the pictures it writes come back
    as images. Tools default to this project (--project, or the nearest project.json above
    the current directory, if there is one); the project guide is the server's
    instructions. A project's .mcp.json and .codex/config.toml (lungecuta init writes them)
    start it for Claude Code and Codex."""
    from ..mcp_server import run
    from ..project import nearest_project

    run(main, project_dir.resolve() if project_dir else nearest_project(Path.cwd()))
