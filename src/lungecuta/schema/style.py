"""Styles, `styles/ID.json` (docs/design/03-project-format.md#styles).

A style holds tracks of speed, scale, rotation, opacity and the motion blur shutter, each in
beats (or seconds or frames) relative to the anchor it is placed at, and an `intensity`
that moves every value from its neutral (1 for speed, scale and opacity, 0 for rotation
and the shutter) towards the style's. A track for a property the engine does not have is
a validation error, not ignored.

A look is a style with a `grade` (operations, or serial nodes of them), which a clip's
grade uses as a node; it may have tracks too, and a style needs tracks or a grade."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .comp import GradeGraph, GradeOp

StyleId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]


class _Part(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class StyleKey(_Part):
    """A key at a time relative to the style's anchor (negative before it)."""

    at: str = Field(description="a span from the anchor, negative before it",
                    json_schema_extra={"x-time": "style"})
    v: float
    ease: Literal["in", "out", "in_out", "hold"] | None = None


class Param(_Part):
    default: float
    min: float
    max: float

    @model_validator(mode="after")
    def _range(self) -> Param:
        assert self.min <= self.default <= self.max, f"default {self.default} is outside {self.min} to {self.max}"
        return self


class Tracks(_Part):
    speed: list[StyleKey] | None = Field(default=None, min_length=1,
                                         description="source seconds per layer second; 1 outside the track")
    scale: list[StyleKey] | None = Field(default=None, min_length=1,
                                         description="a multiplier on the layer's scale; 1 outside the track")
    rotation: list[StyleKey] | None = Field(default=None, min_length=1,
                                            description="degrees added to the layer's rotation; 0 outside the track")
    opacity: list[StyleKey] | None = Field(default=None, min_length=1,
                                           description="a multiplier on the layer's opacity, 0 to 1; 1 outside the track")
    shutter_deg: list[StyleKey] | None = Field(
        default=None, min_length=1, description="the motion blur shutter in degrees inside the track, the layer's own "
        "outside it (06-editing.md, Motion blur)")

    @model_validator(mode="after")
    def _values(self) -> Tracks:
        for key in self.speed or []:
            assert key.v >= 0, f"a speed of {key.v}: a style's speed track cannot run backwards"
        for key in self.scale or []:
            assert key.v > 0, f"a scale of {key.v}"
        for key in self.opacity or []:
            assert 0 <= key.v <= 1, f"an opacity of {key.v}"
        for key in self.shutter_deg or []:
            assert 0 <= key.v <= 720, f"a shutter of {key.v} degrees"
        assert self.speed or self.scale or self.rotation or self.opacity or self.shutter_deg, (
            "a style's tracks need a track"
        )
        return self


class StyleFile(_Part):
    id: StyleId
    name: str
    anchor: Literal["beat"] = Field(default="beat", description="a style is placed at a beat literal, `at` on its layer")
    params: dict[Literal["intensity"], Param] = Field(default_factory=dict)
    tracks: Tracks | None = None
    grade: list[GradeOp] | GradeGraph | None = Field(
        default=None, description="a look: the grade a clip's node applies when it names this style")

    @model_validator(mode="after")
    def _something(self) -> StyleFile:
        assert self.tracks is not None or self.grade, "a style needs tracks, a grade (a look), or both"
        if isinstance(self.grade, GradeGraph):
            assert all(node.look is None for node in self.grade.nodes), (
                f"look {self.id}: its nodes hold operations, not other looks"
            )
        return self
