"""The Phase 1 spike's preview engine: a throwaway, to measure the preview loop
(docs/design/10-roadmap.md, Phase 1). Not part of the lungecuta package.

A frame of a clip's 540-line proxy, read by PyAV with the container kept open (identical
to the ffmpeg CLI on the samples, bench/pyav_check.py), converted by H.273 as lungecuta does,
through the clip's input transform, an OCIO GradingPrimaryTransform (LOG style, dynamic, on
DaVinci Intermediate), a 50 % blend of a second layer, the project's output view, and
from the Rec.1886 display encoding to sRGB encoding, the encoding the viewer sends to
Chrome (01-architecture.md, Playback). Every stage is timed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import PyOpenColorIO as ocio

from lungecuta import color
from lungecuta.decode import read_rgb, to_rgb
from lungecuta.media import Project, load_record
from lungecuta.schema.media import ColorDecided
from lungecuta.ticks import parse_rate

VIEWER_DISPLAY = "sRGB - Display"


@dataclass
class Timings:
    stages: dict[str, float] = field(default_factory=dict)

    def run(self, name, call):
        start = time.perf_counter()
        value = call()
        self.stages[name] = self.stages.get(name, 0.0) + (time.perf_counter() - start) * 1000
        return value


class Proxy:
    """A proxy file kept open, read frame by frame by index."""

    def __init__(self, path: Path, rate: Fraction, bits: int):
        self.container = av.open(str(path))
        self.stream = self.container.streams.video[0]
        self.rate, self.bits = rate, bits
        self.first = self.stream.start_time or 0
        self.next_index = None

    def planes(self, index: int) -> np.ndarray:
        """Planes 3xHxW, the decoder's own 10-bit codes (4:4:4)."""
        if self.next_index != index:
            target = self.first + round(Fraction(index) / self.rate / self.stream.time_base)
            self.container.seek(target, stream=self.stream, backward=True, any_frame=False)
            frames = (f for f in self.container.decode(self.stream) if f.pts is not None and f.pts >= target)
        else:
            frames = self.container.decode(self.stream)
        frame = next(frames)
        assert frame.format.name == "yuv444p10le", frame.format.name
        out = np.empty((3, frame.height, frame.width), np.uint16)
        for k, plane in enumerate(frame.planes):
            rows = np.frombuffer(plane, np.uint16).reshape(plane.height, plane.line_size // 2)
            out[k] = rows[:, :plane.width]
        self.next_index = index + 1
        return out


class Engine:
    def __init__(self, project_dir: Path, key: str, threads: int | None = None):
        project = Project.load(project_dir)
        record = load_record(project, key)
        assert isinstance(record.color, ColorDecided) and record.proxy is not None
        self.decision = record.color
        self.size = tuple(record.proxy.size)
        self.proxy_path = project.directory / record.proxy.path
        self.proxy = Proxy(self.proxy_path, parse_rate(record.time.rate), 10)
        self.frames = record.probe.video.frames
        settings = project.file.color
        self.threads = threads
        self.into = color.cpu_processor(settings.config, self.decision.space, settings.working)
        self.view = color.view_processor(settings.config, settings.working, settings.display, settings.view)
        config = color.config(settings.config)
        grade = ocio.GradingPrimaryTransform(ocio.GRADING_LOG, dynamic=True)
        self.grade = config.getProcessor(grade).getDefaultCPUProcessor()
        self.grade_values = self.grade.getDynamicProperty(ocio.DYNAMIC_PROPERTY_GRADING_PRIMARY)
        # The project's display is Rec.1886; the viewer is sent sRGB-encoded values of the same light.
        self.to_viewer = config.getProcessor(
            ocio.ColorSpaceTransform(src=settings.display, dst=VIEWER_DISPLAY)).getDefaultCPUProcessor()
        self.working_cache: dict[int, np.ndarray] = {}

    def working(self, index: int, timings: Timings, decoder: str = "pyav") -> np.ndarray:
        """Frame `index` through the input transform, cached for a parked frame. `decoder`
        "cli" reads it the way lungecuta still does, one ffmpeg process per frame."""
        if index not in self.working_cache:
            if decoder == "cli":
                frame = timings.run("decode", lambda: read_rgb(
                    self.proxy_path, index, rate=self.proxy.rate, size=self.size, pix_fmt="yuv444p10le",
                    matrix=self.decision.matrix, range_=self.decision.range))  # 4:4:4: no chroma siting
            else:
                planes = timings.run("decode", lambda: self.proxy.planes(index))
                frame = timings.run("to R'G'B'", lambda: to_rgb(*planes.astype(np.float32), bits=10,
                                                                matrix=self.decision.matrix, range_=self.decision.range))
            timings.run("input transform", lambda: color.apply(self.into, frame, self.threads))
            self.working_cache[index] = frame
        return self.working_cache[index]

    def render(self, index: int, second: int, exposure: float, saturation: float, *, cached: bool,
               decoder: str = "pyav") -> tuple[bytes, dict]:
        """RGBA 8-bit sRGB-encoded bytes of the comp frame, and the stage timings in ms."""
        timings = Timings()
        if not cached:
            self.working_cache.clear()
        top = self.working(index, timings, decoder).copy()
        below = self.working(second, timings, decoder)
        values = ocio.GradingPrimary(ocio.GRADING_LOG)
        values.exposure = ocio.GradingRGBM(0, 0, 0, exposure)
        values.saturation = saturation
        self.grade_values.setGradingPrimary(values)
        timings.run("grade", lambda: color.apply(self.grade, top, self.threads))
        frame = timings.run("composite", lambda: np.ascontiguousarray(0.5 * top + 0.5 * below, dtype=np.float32))
        timings.run("output view", lambda: color.apply(self.view, frame, self.threads))
        timings.run("to sRGB", lambda: color.apply(self.to_viewer, frame, self.threads))

        def pack():
            rgba = np.empty(frame.shape[:2] + (4,), np.uint8)
            rgba[..., :3] = np.floor(np.clip(frame, 0, 1) * np.float32(255) + np.float32(0.5))
            rgba[..., 3] = 255
            return rgba.tobytes()

        data = timings.run("8-bit RGBA", pack)
        return data, timings.stages
