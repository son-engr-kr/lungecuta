"""Styles, `styles/ID.json` (docs/design/03-project-format.md#styles).

M2's part: a style holds a speed track and a scale track, each in beats (or seconds or
frames) relative to the anchor it is placed at, and an `intensity` that moves every value
from neutral (1) towards the style's. Tracks for properties the engine does not have yet
(the motion-blur shutter, M6) are validation errors, not ignored."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

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

    @model_validator(mode="after")
    def _values(self) -> Tracks:
        for key in self.speed or []:
            assert key.v >= 0, f"a speed of {key.v}: a style's speed track cannot run backwards"
        for key in self.scale or []:
            assert key.v > 0, f"a scale of {key.v}"
        assert self.speed or self.scale, "a style needs a track"
        return self


class StyleFile(_Part):
    id: StyleId
    name: str
    anchor: Literal["beat"] = Field(description="a style is placed at a beat literal, `at` on its layer")
    params: dict[Literal["intensity"], Param] = Field(default_factory=dict)
    tracks: Tracks
