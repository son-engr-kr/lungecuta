"""`lungecuta still --media`: one frame of an imported clip at final quality, through its input
transform and the project's output view (docs/design/09-agent-interface.md)."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import PyOpenColorIO as ocio

from . import color
from .decode import read_rgb
from .files import write_atomic
from .geometry import area_resize, oriented, scaled_size, shown_size
from .images import png
from .literals import Field, parse_literal, resolve_source
from .media import Project, load_record, original
from .schema.media import ColorDecided, MediaRecord, VideoProbe
from .ticks import parse_rate, ticks_per_frame

STILL_DIR = Path("cache") / "lungecuta" / "still"
OUTPUT_CODES = 1023  # printed numbers are 10-bit output codes (09-agent-interface.md)
PERCENTILES = (1, 50, 99)


def source_frame(text: str, *, rate: Fraction, frames: int) -> int:
    """The frame a source-time literal names: `f:N`, or the frame whose interval holds a
    time in seconds, the rule comps use for a source time between frames (*proposed*)."""
    ticks = resolve_source(parse_literal(text, Field.SOURCE), media_rate=rate)
    index = ticks // ticks_per_frame(rate)
    assert 0 <= index < frames, f"{text} is frame {index}, and the clip has frames 0 to {frames - 1}"
    return index


@dataclass(frozen=True)
class Clip:
    """A clip whose colour space is decided, ready to be looked at: its original, checked
    unchanged since import, and its colour chain."""

    key: str
    path: Path
    video: VideoProbe
    decision: ColorDecided
    into: ocio.CPUProcessor
    view: ocio.CPUProcessor

    @property
    def rate(self) -> Fraction:
        return parse_rate(self.video.rate)

    @property
    def shown(self) -> tuple[int, int]:
        """The frame's size as it is shown, after its rotation."""
        return shown_size(self.video.size, self.video.rotation)

    def read(self, index: int) -> np.ndarray:
        return read_rgb(self.path, index, rate=self.rate, size=self.video.size, pix_fmt=self.video.pix_fmt,
                        matrix=self.decision.matrix, range_=self.decision.range,
                        chroma_location=self.video.chroma_location)

    def show(self, coded: np.ndarray, size: tuple[int, int] | None = None, threads: int | None = None) -> np.ndarray:
        """A decoded frame (float R'G'B' in the coded orientation, consumed) as display-encoded
        R'G'B', unclipped: through the input transform into the working space, turned the way
        the clip is shown, area-downscaled there to `size` (the working space is where comps
        composite, 04-color.md), then through the project's display and view."""
        color.apply(self.into, coded, threads)
        frame = oriented(coded, self.video.rotation)
        if size is not None and size != self.shown:
            frame = area_resize(frame, size)
        color.apply(self.view, frame, threads)
        assert np.isfinite(frame).all(), f"{self.key}: the colour pipeline produced non-finite values"
        return frame


def open_clip(project: Project, record: MediaRecord) -> Clip:
    decision = record.color
    assert isinstance(decision, ColorDecided), (
        f"{record.key}: the colour space is undecided ({decision.reason}), so it has no input transform"
    )
    video = record.probe.video
    assert video is not None, f"{record.key} has no video stream"
    settings = project.file.color
    return Clip(
        key=record.key,
        path=original(project, record),
        video=video,
        decision=decision,
        into=color.cpu_processor(settings.config, decision.space, settings.working),
        view=color.view_processor(settings.config, settings.working, settings.display, settings.view),
    )


def render(
    project: Project, record: MediaRecord, index: int, *, height: int | None = None, threads: int | None = None
) -> np.ndarray:
    """Frame `index` of a clip as display-encoded R'G'B' at `height` lines, or at its own size."""
    clip = open_clip(project, record)
    size = scaled_size(clip.shown, height) if height is not None else None
    return clip.show(clip.read(index), size, threads)


def code_numbers(frame: np.ndarray) -> dict[str, dict[str, float]]:
    """Per channel: minimum, 1st, 50th and 99th percentiles and maximum, in 10-bit output
    codes of the float frame (below 0 and above 1023 where the frame is out of range)."""
    numbers = {}
    for name, channel in zip("RGB", np.moveaxis(frame, -1, 0)):
        low, middle, high = np.percentile(channel, PERCENTILES)
        values = {"min": channel.min(), "p1": low, "p50": middle, "p99": high, "max": channel.max()}
        numbers[name] = {key: round(float(value) * OUTPUT_CODES, 4) for key, value in values.items()}
    return numbers


def still(
    project: Project, key: str, at: str, *, height: int | None = None, threads: int | None = None,
    out: Path | None = None,
) -> dict[str, Any]:
    """Write the PNG of one frame of media `key` and return the numbers `lungecuta still` prints."""
    record = load_record(project, key)
    if record.image is not None:
        return photo_still(project, record, height=height, out=out)
    video = record.probe.video
    assert video is not None, f"{key} has no video stream"
    index = source_frame(at, rate=parse_rate(video.rate), frames=video.frames)
    frame = render(project, record, index, height=height, threads=threads)
    size = (frame.shape[1], frame.shape[0])
    if out is None:
        out = project.directory / STILL_DIR / f"{key}_f{index}_{size[0]}x{size[1]}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, png(frame))
    settings = project.file.color
    return {
        "png": str(out),
        "media": key,
        "frame": index,
        "size": list(size),
        "rotation": video.rotation,
        "space": record.color.space,
        "display": settings.display,
        "view": settings.view,
        "codes": code_numbers(frame),
    }


def photo_still(project: Project, record: MediaRecord, *, height: int | None = None, out: Path | None = None) -> dict[str, Any]:
    """A photo as its page shows it: turned upright by its EXIF orientation and converted
    from its ICC profile to sRGB (an untagged file taken as sRGB), at `height` lines or its
    own size. Pillow does what Chromium does here, so the codes are close to the page's
    but not checked to be identical."""
    import io

    from PIL import Image, ImageCms, ImageOps

    from .images import png8

    with Image.open(original(project, record)) as image:
        upright = ImageOps.exif_transpose(image).convert("RGB")
        icc = image.info.get("icc_profile")
    if icc:
        srgb = ImageCms.createProfile("sRGB")
        upright = ImageCms.profileToProfile(upright, ImageCms.ImageCmsProfile(io.BytesIO(icc)), srgb, outputMode="RGB")
    if height is not None and height != upright.height:
        upright = upright.resize((round(upright.width * height / upright.height), height), Image.Resampling.BOX)
    pixels = np.asarray(upright)
    if out is None:
        out = project.directory / STILL_DIR / f"{record.key}_{pixels.shape[1]}x{pixels.shape[0]}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, png8(pixels))
    return {"png": str(out), "media": record.key, "photo": record.image.format, "size": [pixels.shape[1], pixels.shape[0]],
            "icc": record.image.icc, "codes": code_numbers(pixels.astype(np.float32) / 255)}
