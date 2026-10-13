"""Comp and style files loaded and checked, and the comp engine's constants."""
from __future__ import annotations


from pathlib import Path

from ..media import Project
from ..schema.comp import (
    CompFile,
)
from ..schema.style import StyleFile


MIX_RATE = 48000  # the engine's internal audio rate (07-audio.md)

COMPS_DIR = Path("comps")

STYLES_DIR = Path("styles")

def load_comp(project: Project, name: str) -> CompFile:
    """Comp `name` (`comps/NAME.json`), which project.json must list."""
    relative = (COMPS_DIR / f"{name}.json").as_posix()
    assert relative in project.file.comps, f"{relative} is not one of the comps project.json lists"
    path = project.directory / relative
    assert path.is_file(), f"{path} is missing"
    comp = CompFile.model_validate_json(path.read_text())
    assert comp.id == name, f"{relative} holds comp {comp.id!r}"
    return comp

def load_style(project: Project, name: str) -> StyleFile:
    """Style `name` (`styles/NAME.json`)."""
    path = project.directory / STYLES_DIR / f"{name}.json"
    assert path.is_file(), f"style {name!r}: {path} is missing"
    style = StyleFile.model_validate_json(path.read_text())
    assert style.id == name, f"styles/{name}.json holds style {style.id!r}"
    return style
