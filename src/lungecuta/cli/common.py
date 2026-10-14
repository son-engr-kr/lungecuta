"""What every lungecuta command shares: JSON output, finding the project, the common
options, and the `lungecuta` group itself."""
from __future__ import annotations

import inspect
import json
import sys
import time
from pathlib import Path
from typing import Any

import click
from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from .. import activity
from ..media import Project
from ..project import find_project


def print_json(data: Any) -> None:
    """Numbers and paths go to stdout as one JSON object, so an agent can parse them."""
    click.echo(json.dumps(data, ensure_ascii=False))


RAN_IN = "lungecuta.ran_in"  # context meta: the project a command runs in, the command and its parameters


def project_of(directory: Path | None) -> Project:
    """`--project DIR`, or else the nearest project.json above the current directory. The
    command asking is logged in that project when it ends (`activity.py`)."""
    project = Project.load(find_project(directory or Path.cwd()))
    context = click.get_current_context(silent=True)
    if context is not None:
        names, at = [], context
        while at.parent is not None:
            names.append(at.info_name)
            at = at.parent
        context.meta[RAN_IN] = {"directory": project.directory, "command": " ".join(reversed(names)),
                                "params": context.params}
    return project


project_option = click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The project directory; by default the nearest one holding project.json.",
)


threads_option = click.option(
    "--threads", type=click.IntRange(min=1), help="OpenColorIO threads; by default the machine's core count."
)


@click.group()
@click.pass_context
def main(context: click.Context) -> None:
    """lungecuta: edit short, music-driven pieces shot in log, with an AI agent."""
    started = time.time()

    def log() -> None:
        ran = context.meta.get(RAN_IN)
        if ran is None:  # a command outside any project: setup, doctor, init
            return
        failure = sys.exc_info()[1]  # the command's exception, while the context closes
        ok = failure is None or (isinstance(failure, click.exceptions.Exit) and failure.exit_code == 0)
        activity.record(ran["directory"], ran["command"], ran["params"], started, ok)

    context.call_on_close(log)


def one_of(comp: str | None, key: str | None) -> None:
    assert (comp is None) != (key is None), "name a comp, or a clip with --media, not both and not neither"


def parse_size(text: str) -> tuple[int, int]:
    width, _, height = text.partition("x")
    assert width.isdigit() and height.isdigit(), f"a size is WIDTHxHEIGHT, such as 1920x1080, not {text!r}"
    return int(width), int(height)


size_option = click.option("--size", required=True, help="WIDTHxHEIGHT in pixels, such as 1920x1080.")


rate_option = click.option("--rate", required=True, help="Frame rate as ffprobe writes it: 30000/1001, 25, 60.")


def fields_help(title: str, model: type[BaseModel], only: tuple[str, ...] | None = None) -> str:
    """A model's fields as a block of help text: each field's name, its default and its
    description, taken from the schema itself so that the help cannot disagree with what
    the files take. `\\b` keeps click from rewrapping the lines."""
    doc = " ".join(inspect.cleandoc(model.__doc__ or "").split())
    lines = ["\b", f"{title}: {doc}" if doc else f"{title}:"]
    for name, field in model.model_fields.items():
        if name == "op" or (only is not None and name not in only):
            continue
        default = "required" if field.default is PydanticUndefined else json.dumps(field.default)
        lines.append(f"  {name} ({default})" + (f": {field.description}" if field.description else ""))
    return "\n".join(lines)
