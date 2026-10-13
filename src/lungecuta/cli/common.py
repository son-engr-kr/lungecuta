"""What every lc command shares: JSON output, finding the project, the common options, and the `lc` group itself."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from ..media import Project
from ..project import find_project


def print_json(data: Any) -> None:
    """Numbers and paths go to stdout as one JSON object, so an agent can parse them."""
    click.echo(json.dumps(data, ensure_ascii=False))


def project_of(directory: Path | None) -> Project:
    """`--project DIR`, or else the nearest project.json above the current directory."""
    return Project.load(find_project(directory or Path.cwd()))


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
def main() -> None:
    """lungecuta: edit short, music-driven pieces shot in log, with an AI agent."""


def one_of(comp: str | None, key: str | None) -> None:
    assert (comp is None) != (key is None), "name a comp, or a clip with --media, not both and not neither"


def parse_size(text: str) -> tuple[int, int]:
    width, _, height = text.partition("x")
    assert width.isdigit() and height.isdigit(), f"a size is WIDTHxHEIGHT, such as 1920x1080, not {text!r}"
    return int(width), int(height)


size_option = click.option("--size", required=True, help="WIDTHxHEIGHT in pixels, such as 1920x1080.")


rate_option = click.option("--rate", required=True, help="Frame rate as ffprobe writes it: 30000/1001, 25, 60.")
