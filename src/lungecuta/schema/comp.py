"""Comps, `comps/ID.json` (docs/design/03-project-format.md#compositions-and-layers).

Phase 0 reads the part of the comp format that a cut for Resolve needs: footage and
audio layers with After Effects timing (D11), constant speed, a beat grid from music
layers, and comp and layer markers. Every other field of the format is an error here
rather than something silently ignored; M0 extends these models."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .media import MediaKey, Rate

LayerId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
CompId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]


def _time(kind: str, description: str):
    """A time literal field, declared as the kind of time it is (03-project-format.md)."""
    return Field(description=description, json_schema_extra={"x-time": kind})


class _Part(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Marker(_Part):
    """A note at a time: in comp time on a comp, in layer time on a layer (06-editing.md#markers)."""

    t: str = _time("position or layer", "a comp position on a comp, a span from the layer's start on a layer")
    name: str
    note: str | None = None


class Speed(_Part):
    """Constant speed from `source_start`, shorthand for a straight remap line."""

    speed: float = Field(gt=0, description="source seconds per layer second; 1 plays at 100 %")


class Key(_Part):
    """A keyframe: at layer time `t`, value `v`; `ease` shapes the segment arriving at it
    (`in`, `out`, `in_out`, `hold`; none is linear), src/lungecuta/keyframes.py."""

    t: str = _time("layer", "a span from the layer's start")
    v: float | tuple[float, float]
    ease: Literal["in", "out", "in_out", "hold"] | None = None


class Keys(_Part):
    keys: list[Key] = Field(min_length=1)


class Mask(_Part):
    """A shape that cuts the layer, in its own frame as shown (fractions of its width and
    height), as After Effects' masks are in layer space."""

    shape: Literal["rectangle", "ellipse"]
    centre: tuple[float, float] = (0.5, 0.5)
    size: tuple[float, float] = Field(description="width and height, fractions of the layer's")
    feather: float = Field(default=0.0, ge=0, description="the edge's ramp, a fraction of the layer's height")
    invert: bool = False
    mode: Literal["add", "subtract", "intersect"] = "add"

    @model_validator(mode="after")
    def _size(self) -> Mask:
        assert self.size[0] > 0 and self.size[1] > 0, f"a mask of size {self.size}"
        return self


class Transform(_Part):
    """Where a layer's frame lands in the comp (*proposed* units): `position` of the layer's
    `anchor` as fractions of the comp's width and height, `anchor` as fractions of the
    layer's frame as shown, `scale` relative to the layer's own pixels (one number, or x
    and y), `opacity` from 0 to 1. A missing transform puts the layer at its own size in the
    middle of the comp, as After Effects does."""

    position: tuple[float, float] | Keys = (0.5, 0.5)
    anchor: tuple[float, float] = (0.5, 0.5)
    scale: float | tuple[float, float] | Keys = 1.0
    opacity: float | Keys = 1.0
    rotation: float | Keys = Field(default=0.0, description="degrees, clockwise on screen about the anchor, "
                                   "as in After Effects")


class StyleTime(_Part):
    """A style instance (03-project-format.md#styles): the style's tracks placed at `at`
    in layer time, speed 1 from `source_start` outside its speed track."""

    style: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    at: str = _time("layer", "a span from the layer's start: where the style's anchor lands")
    overrides: dict[Literal["intensity"], float] = Field(default_factory=dict)


class RemapKey(_Part):
    """A time-remap key: at layer time `t`, source time `v` (seconds or f: at the media's
    rate); between keys the source time follows the keys' easing, as any property does."""

    t: str = _time("layer", "a span from the layer's start")
    v: str = _time("source", "the source time shown at t")
    ease: Literal["in", "out", "in_out", "hold"] | None = None


class Remap(_Part):
    """Source time against layer time, After Effects' time remap (03-project-format.md#time-remap)."""

    remap: list[RemapKey] = Field(min_length=2)


RGB = tuple[float, float, float]


class Primary(_Part):
    """OCIO's GradingPrimaryTransform in its LOG style, on the working space's code values
    (04-color.md, Operations come from OpenColorIO)."""

    op: Literal["primary"]
    offset: float | RGB = Field(default=0.0, description="added to the working-space code values (OCIO's LOG "
                                "brightness, of which one unit adds 6.25/1023)")
    contrast: float | RGB = Field(default=1.0, description="a multiplier on the distance from the pivot")
    pivot: float | None = Field(default=None, description="the working-space code value contrast turns around; "
                                "absent, the working space's 18 % grey (0.336043 in DaVinci Intermediate)")
    gamma: float | RGB = Field(default=1.0, description="OCIO's LOG gamma: code values from 0 to 1 raised to "
                               "1/gamma, so above 1 lifts the middle")
    saturation: float = Field(default=1.0, ge=0, description="OCIO's, around a luma with Rec.709 weights")
    clamp: tuple[float, float] | None = Field(default=None, description="black and white clip, in code values")


class Cdl(_Part):
    """An ASC CDL by OCIO's CDLTransform, on the working space's code values."""

    op: Literal["cdl"]
    slope: RGB = (1.0, 1.0, 1.0)
    offset: RGB = (0.0, 0.0, 0.0)
    power: RGB = (1.0, 1.0, 1.0)
    saturation: float = Field(default=1.0, ge=0, description="the CDL's, with Rec.709 weights by its definition")
    clamp: bool = Field(default=True, description="ASC v1.2 clamps to 0 to 1; false is OCIO's no-clamp style")


class Lut(_Part):
    """A LUT file under `luts/`, with the spaces it expects and produces declared, since a
    `.cube` declares neither."""

    op: Literal["lut"]
    path: Annotated[str, StringConstraints(pattern=r"^luts/[A-Za-z0-9._-]+\.(cube|3dl|csp|spi1d|spi3d|clf|ctf)$")]
    space: str = Field(description="the OCIO colour space the LUT's input is in")
    out: str = Field(description="the OCIO colour space of what it returns")


GradeOp = Annotated[Primary | Cdl | Lut, Field(discriminator="op")]


class HueRange(_Part):
    centre: float = Field(ge=0, lt=1, description="OCIO's hue: magenta 0, red 1/6, green 1/2, blue 5/6")
    width: float = Field(gt=0, le=1, description="the whole range, in hue")
    soft: float = Field(default=0.0, ge=0, description="beyond each edge, the key falls to 0 over this much")


class LevelRange(_Part):
    low: float
    high: float
    soft: float = Field(default=0.0, ge=0, description="beyond each edge, the key falls to 0 over this much")

    @model_validator(mode="after")
    def _order(self) -> LevelRange:
        assert self.low <= self.high, f"a range from {self.low} to {self.high}"
        return self


class Qualifier(_Part):
    """An HSL key on a node's input, in OCIO's LOG-style HSY (the hue, saturation and luma
    `GradingHueCurveTransform` uses, so a curve and a key agree on what a colour is)."""

    hue: HueRange | None = None
    sat: LevelRange | None = Field(default=None, description="OCIO's LOG saturation: 4 times the sum of each "
                                   "channel's distance from luma; a pure red in DI codes reads about 4.85")
    luma: LevelRange | None = Field(default=None, description="Rec.709-weighted luma of the working-space codes")
    invert: bool = False

    @model_validator(mode="after")
    def _some(self) -> Qualifier:
        assert self.hue or self.sat or self.luma, "a qualifier keys on hue, saturation or luma"
        return self


class Node(_Part):
    """A node of a clip's grade (04-color.md, Node graph): its operations in order, an
    optional window that keys them (shapes as a mask's, in the clip's frame as shown), and
    how much of the result is mixed over the node's input."""

    ops: list[GradeOp] = Field(min_length=1)
    window: list[Mask] = Field(default_factory=list, description="where the node applies; none is everywhere")
    key: Qualifier | None = Field(default=None, description="which colours the node applies to, with the window")
    mix: float = Field(default=1.0, ge=0, le=1)


class GradeGraph(_Part):
    nodes: list[Node] = Field(min_length=1, description="serial: each node grades the one before's output")


class _MediaLayer(_Part):
    id: LayerId
    media: MediaKey
    start: str = _time("position", "the comp position of the layer's own time 0")
    in_: str = Field(
        default="0s", alias="in", description="a span from start: where the layer becomes visible",
        json_schema_extra={"x-time": "layer"},
    )
    out: str = _time("layer", "a span from start: where the layer ends")
    markers: list[Marker] = Field(default_factory=list)


class FootageLayer(_MediaLayer):
    type: Literal["footage"]
    source_start: str | None = Field(default=None, description="the source time at layer time 0; "
                                     "none when `time` gives remap keys", json_schema_extra={"x-time": "source"})
    time: Speed | Remap | StyleTime | None = None
    retime: Literal["nearest", "blend"] | None = Field(
        default=None, description="a source time between frames: absent shows the frame that holds it, "
        "`nearest` the closest one, `blend` the two neighbours mixed in linear light")
    transform: Transform | None = None
    grade: list[GradeOp] | GradeGraph = Field(default_factory=list, description="operations in order, in the "
                                              "working space, after the input transform and before the layer is "
                                              "placed; or a graph of serial nodes")
    masks: list[Mask] = Field(default_factory=list, description="in order; the layer is seen only where they "
                              "leave it, none showing the whole layer")

    @model_validator(mode="after")
    def _source(self) -> FootageLayer:
        remapped = isinstance(self.time, Remap)
        assert remapped != (self.source_start is not None), (
            f"layer {self.id}: a layer has a source_start or remap keys, one of the two"
        )
        return self


class AudioLayer(_MediaLayer):
    type: Literal["audio"]
    source_start: str = _time("source", "the source time at layer time 0, in seconds")
    gain: float | Keys = Field(default=0.0, description="dB, a constant or keys in layer time")
    fade_in: str | None = Field(default=None, description="a span from the visible in point over which the layer "
                                "rises from silence (linear in amplitude)", json_schema_extra={"x-time": "layer"})
    fade_out: str | None = Field(default=None, description="a span before the visible out point over which it "
                                 "falls to silence (linear in amplitude)", json_schema_extra={"x-time": "layer"})

    @model_validator(mode="after")
    def _gain(self) -> AudioLayer:
        if isinstance(self.gain, Keys):
            for key in self.gain.keys:
                assert isinstance(key.v, (int, float)), f"layer {self.id}: a gain key holds one number, not {key.v}"
        return self


class GraphicsLayer(_Part):
    """A web page drawn over the comp (05-motion-graphics.md): Phase 0's contract, a page in
    `graphics/` that sets `window.ready` and draws layer time t in `window.renderAt(t)`. Its
    `params` reach the page as the JSON in its `params` query parameter. Only `display`
    space for now: its codes are the deliverable's codes, composited after the output
    view as a top overlay (01-architecture.md)."""

    id: LayerId
    type: Literal["graphics"]
    page: Annotated[str, StringConstraints(pattern=r"^graphics/[A-Za-z0-9._-]+\.html$")]
    space: Literal["display"]
    start: str = _time("position", "the comp position of the layer's own time 0")
    in_: str = Field(default="0s", alias="in", description="a span from start: where the layer becomes visible",
                     json_schema_extra={"x-time": "layer"})
    out: str = _time("layer", "a span from start: where the layer ends")
    params: dict[str, Any] = Field(default_factory=dict)
    markers: list[Marker] = Field(default_factory=list)


class PrecompLayer(_Part):
    """Another comp placed as a layer (03-project-format.md, layer types): its layer time is
    the precomp's own time, as in After Effects; transparent where the precomp has nothing,
    placed and masked like footage."""

    id: LayerId
    type: Literal["comp"]
    comp: CompId
    start: str = _time("position", "the comp position of the layer's own time 0, the precomp's 0s")
    in_: str = Field(default="0s", alias="in", description="a span from start: where the layer becomes visible",
                     json_schema_extra={"x-time": "layer"})
    out: str = _time("layer", "a span from start: where the layer ends")
    transform: Transform | None = None
    masks: list[Mask] = Field(default_factory=list)
    markers: list[Marker] = Field(default_factory=list)


class BeatsFrom(_Part):
    from_layers: list[LayerId] = Field(min_length=1)


class Transition(_Part):
    """A transition on a cut point, Resolve style, expanded into opacity on its layers when
    the comp is resolved (06-editing.md#transitions): `dissolve` from the first layer to
    the second, centred on the cut between them, each extended into its handles; `dip`
    through black, the first fading out before the cut and the second in after it;
    `fade_in` and `fade_out` on one layer, from and to black."""

    type: Literal["dissolve", "dip", "fade_in", "fade_out"]
    layers: list[LayerId] = Field(min_length=1, max_length=2,
                                  description="the outgoing and incoming footage layers; one for a fade")
    length: str = _time("layer", "how long, centred on the cut for a dissolve and a dip")
    space: Literal["working"] = Field(default="working", description="where it blends: the working space "
                                      "(DaVinci Intermediate, Resolve's behaviour); `linear` is not written yet")

    @model_validator(mode="after")
    def _count(self) -> Transition:
        two = self.type in ("dissolve", "dip")
        assert len(self.layers) == (2 if two else 1), f"a {self.type} names {'two layers' if two else 'one layer'}"
        return self


class CompFile(_Part):
    """`comps/ID.json`: a comp's size, rate, end, beat grid and layers, in stack order."""

    id: CompId
    size: tuple[int, int]
    rate: Rate
    end: str = _time("position", "where the comp ends; every comp starts at 0s")
    beats: BeatsFrom | None = None
    markers: list[Marker] = Field(default_factory=list)
    layers: list[Annotated[FootageLayer | AudioLayer | GraphicsLayer | PrecompLayer, Field(discriminator="type")]] = Field(
        description="in stack order: a later layer is drawn over an earlier one")
    transitions: list[Transition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references(self) -> CompFile:
        ids = [layer.id for layer in self.layers]
        assert len(set(ids)) == len(ids), f"comp {self.id}: layer ids are not unique"
        footage = {layer.id for layer in self.layers if layer.type == "footage"}
        for transition in self.transitions:
            for name in transition.layers:
                assert name in footage, f"comp {self.id}: a {transition.type} is on footage layers, and {name!r} is not one"
        assert all(side > 0 for side in self.size), f"comp {self.id}: size {self.size} is not positive"
        if self.beats is not None:
            audio = {layer.id for layer in self.layers if layer.type == "audio"}
            for name in self.beats.from_layers:
                assert name in audio, f"comp {self.id}: beats come from audio layers, and {name!r} is not one"
        return self
