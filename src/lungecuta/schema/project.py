"""`project.json` and `roots.local.json` (docs/design/03-project-format.md#projectjson)."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, StringConstraints, field_validator

# The root that is the project directory itself; it is implicit and never listed.
PROJECT_ROOT = "project"

RootName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*$")]
CompPath = Annotated[str, StringConstraints(pattern=r"^comps/[A-Za-z0-9._-]+\.json$")]


class ColorSettings(BaseModel):
    """The OpenColorIO config and the colour spaces a project renders through."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    config: str
    working: str
    linear: str
    display: str
    view: str


class LintSettings(BaseModel):
    """`lungecuta lint`'s thresholds (09-agent-interface.md, Lint), each from the past projects'
    review rules; `null` or `false` turns a rule off."""

    model_config = ConfigDict(extra="forbid")

    graphics_min_seconds: float | None = Field(default=0.6, gt=0, description="a graphics layer is on screen at "
                                               "least this long (text cards are readable)")
    repeated_source: bool = Field(default=True, description="no source frame is shown by two layers (montage "
                                  "shots do not repeat)")
    music_licence: bool = Field(default=True, description="every music file and sound in a comp carries a licence "
                                "record")
    sfx_vary: bool = Field(default=True, description="the same sound is not on two consecutive sound layers")
    no_black: bool = Field(default=True, description="no comp frame has nothing on screen, a gap between cuts or a "
                           "frame before the first, as when a cut on the first beat lands a frame in")


class ProjectFile(BaseModel):
    """`project.json`: settings, colour management, media roots and the comp index."""

    model_config = ConfigDict(extra="forbid")

    format: Literal[1]
    color: ColorSettings
    media_roots: list[RootName] = Field(
        description="names of the media roots the project uses; roots.local.json places them"
    )
    comps: list[CompPath]
    proxy_height: int = Field(gt=0, description="proxy height in lines of the coded frame")
    deliver: list[str]
    lint: LintSettings = Field(default_factory=LintSettings)

    @field_validator("media_roots")
    @classmethod
    def _roots_are_named_once(cls, roots: list[str]) -> list[str]:
        assert PROJECT_ROOT not in roots, (
            f"{PROJECT_ROOT!r} is the project directory itself and is never listed as a root"
        )
        assert len(set(roots)) == len(roots), f"media roots are listed twice: {roots}"
        return roots


class RootsFile(RootModel[dict[RootName, str]]):
    """`roots.local.json`: where this machine keeps each media root. Never committed."""

    @field_validator("root")
    @classmethod
    def _paths_are_absolute(cls, roots: dict[str, str]) -> dict[str, str]:
        assert PROJECT_ROOT not in roots, f"{PROJECT_ROOT!r} is the project directory itself"
        for name, path in roots.items():
            assert PurePosixPath(path).is_absolute(), f"root {name!r} must be an absolute path, not {path!r}"
        return roots
