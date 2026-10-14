"""Creating and finding a project directory (docs/design/03-project-format.md)."""
from __future__ import annotations

from pathlib import Path

from .files import json_text, write_atomic
from .guide import write_guide
from .schema import ColorSettings, ProjectFile

PROJECT_FILE = "project.json"
ROOTS_FILE = "roots.local.json"
PROJECT_DIRS = ("media", "comps", "grades", "styles", "graphics", "luts", "cache")
GITIGNORE = f"cache/\n{ROOTS_FILE}\n"

DEFAULT_COLOR = ColorSettings(
    config="studio-config-v5.0.0_aces-v2.1_ocio-v2.6",
    working="DaVinci Intermediate WideGamut",
    linear="Linear DaVinci WideGamut",
    display="Rec.1886 Rec.709 - Display",
    view="ACES 2.0 - SDR 100 nits (Rec.709)",
)
DEFAULT_PROXY_HEIGHT = 540


def new_project() -> ProjectFile:
    return ProjectFile(
        format=1,
        color=DEFAULT_COLOR,
        media_roots=[],
        comps=[],
        proxy_height=DEFAULT_PROXY_HEIGHT,
        deliver=[],
    )


def init_project(directory: Path) -> Path:
    """Create a project in `directory`, which must not exist yet or be empty."""
    if directory.exists():
        assert directory.is_dir(), f"{directory} exists and is not a directory"
        assert not any(directory.iterdir()), f"{directory} is not empty; lungecuta init only starts new projects"
    else:
        directory.mkdir(parents=True)
    for name in PROJECT_DIRS:
        (directory / name).mkdir()
    write_atomic(directory / PROJECT_FILE, json_text(new_project().model_dump(mode="json")))
    write_atomic(directory / ROOTS_FILE, json_text({}))
    write_atomic(directory / ".gitignore", GITIGNORE)
    write_guide(directory)
    return directory.resolve()


def nearest_project(start: Path) -> Path | None:
    """The nearest directory holding `project.json`, from `start` upwards, as git finds a
    repository; None when there is none."""
    start = start.resolve()
    return next((directory for directory in (start, *start.parents) if (directory / PROJECT_FILE).is_file()), None)


def find_project(start: Path) -> Path:
    """`nearest_project`, which must exist."""
    found = nearest_project(start)
    assert found is not None, f"no {PROJECT_FILE} in {start.resolve()} or any directory above it"
    return found
