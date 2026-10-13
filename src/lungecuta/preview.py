"""Preview frames for the editor (docs/design/01-architecture.md, Preview and final): the
same chain as a render, run on each clip's proxy at a preview size, sent to the browser
as sRGB-encoded 8-bit RGBA.

Proxies are read by PyAV with each container kept open, which decodes them identically
to the ffmpeg CLI and seeks in about 5 ms (`bench/pyav_check.py`). The preview size is the
comp's size divided by the smallest whole number that brings it to the project's
`proxy_height` or under, so both sides stay whole (a 1080x1920 comp previews at
270x480, a 3840x2160 one at 960x540). Graphics layers are not drawn here: the editor
draws them live over these frames while they are being edited.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import PyOpenColorIO as ocio

from . import color, grade
from .comp import Cut, Nest, Resolved, load_comp, resolve
from .decode import to_rgb
from .files import content_hash, content_hash_text
from .geometry import mask_alpha, place
from .media import Project
from .schema.comp import StyleTime
from .compose import composite_alpha, shown
from .ticks import ticks_per_frame

VIEWER_DISPLAY = "sRGB - Display"  # what Chrome expects, encoding the same light (01-architecture.md)
CACHE_BYTES = 2 << 30  # a memory budget for rendered preview frames, not a measured figure


def preview_divisor(size: tuple[int, int], proxy_height: int) -> int:
    width, height = size
    for k in range(1, height + 1):
        if height % k == 0 and width % k == 0 and height // k <= proxy_height:
            return k
    raise AssertionError(f"no whole preview size for {width}x{height}")


class ProxyReader:
    """One proxy file kept open, read frame by frame by index (PyAV)."""

    def __init__(self, path: Path, rate: Fraction):
        assert path.is_file(), f"{path} is missing; import the clip again to rebuild its proxy"
        self.container = av.open(str(path))
        self.stream = self.container.streams.video[0]
        self.rate = rate
        self.first = self.stream.start_time or 0
        self.next_index: int | None = None
        self.frames = None

    def planes(self, index: int) -> np.ndarray:
        """The frame's 4:4:4 planes, the decoder's own codes, as float32 3xHxW."""
        if self.next_index != index:
            target = self.first + round(Fraction(index) / self.rate / self.stream.time_base)
            self.container.seek(target, stream=self.stream, backward=True, any_frame=False)
            self.frames = (f for f in self.container.decode(self.stream) if f.pts is not None and f.pts >= target)
        frame = next(self.frames)
        assert frame.format.name == "yuv444p10le", f"a proxy decodes as {frame.format.name}"
        out = np.empty((3, frame.height, frame.width), np.float32)
        for k, plane in enumerate(frame.planes):
            rows = np.frombuffer(plane, np.uint16).reshape(plane.height, plane.line_size // 2)
            out[k] = rows[:, :plane.width]
        self.next_index = index + 1
        return out

    def close(self) -> None:
        self.container.close()


class Preview:
    """A comp's preview frames, cached by the comp's version (its file's hash and the
    files it depends on) and the frame index."""

    def __init__(self, project: Project, name: str, cache_bytes: int = CACHE_BYTES):
        self.project, self.name = project, name
        self.lock = threading.RLock()  # PyAV's readers are not thread-safe; `frame` holds it across `display`
        self.readers: dict[str, ProxyReader] = {}
        self.cache: OrderedDict[tuple[str, int], bytes] = OrderedDict()
        self.cache_bytes, self.cached = cache_bytes, 0
        self.reload()

    def reload(self) -> None:
        """Read the comp again (after an edit); frames of other versions stay cached under
        their own hash and age out."""
        self.project = Project.load(self.project.directory)
        self.comp = load_comp(self.project, self.name)
        self.hash = content_hash(self.project.directory / "comps" / f"{self.name}.json")
        self.resolved: Resolved = resolve(self.project, self.comp)
        self.inputs = self._inputs()
        self.version = content_hash_text(self.hash + repr(self.inputs))
        divisor = preview_divisor(tuple(self.comp.size), self.project.file.proxy_height)
        self.divisor = divisor
        self.size = (self.comp.size[0] // divisor, self.comp.size[1] // divisor)
        settings = self.project.file.color
        cfg = color.config(settings.config)
        self.view = color.view_processor(settings.config, settings.working, settings.display, settings.view)
        self.to_viewer = cfg.getProcessor(
            ocio.ColorSpaceTransform(src=settings.display, dst=VIEWER_DISPLAY)).getDefaultCPUProcessor()

    def _inputs(self) -> tuple:
        """The files besides the comp that decide its frames, by size and modification time:
        the comps it places as precomps, the styles its layers and theirs place (linked, so
        editing one changes every placement) and the LUTs their grades read."""
        paths = {f"comps/{name}.json" for name in self.resolved.precomps()}
        for cut in self.resolved.every_cut():
            if isinstance(cut.layer.time, StyleTime):
                paths.add(f"styles/{cut.layer.time.style}.json")
            paths.update(f"styles/{name}.json" for name in grade.looks(self.project, cut.layer.grade))
            paths.update(op.path for op in grade.operations(self.project, cut.layer.grade) if op.op == "lut")
        stats = []
        for relative in sorted(paths):
            stat = (self.project.directory / relative).stat()
            stats.append((relative, stat.st_size, stat.st_mtime_ns))
        return tuple(stats)

    def stale(self) -> bool:
        """Whether the comp or a file it depends on changed since it was read."""
        return content_hash(self.project.directory / "comps" / f"{self.name}.json") != self.hash \
            or self._inputs() != self.inputs

    def _reader(self, cut: Cut) -> ProxyReader:
        key = cut.record.key
        if key not in self.readers:
            assert cut.record.proxy is not None, f"{key} has no proxy"
            self.readers[key] = ProxyReader(self.project.directory / cut.record.proxy.path, cut.media_rate)
        return self.readers[key]

    def _layer(self, cut: Cut, index: int, canvas: tuple[int, int] | None = None) -> tuple[np.ndarray, np.ndarray | float]:
        """A footage layer at the preview's scale on `canvas` (the comp's preview size by
        default; a precomp's when it is inside one)."""
        canvas = canvas or self.size
        settings = self.project.file.color
        record = cut.record
        into = color.cpu_processor(settings.config, record.color.space, settings.working)
        working = []
        for source, weight in cut.samples(index):
            planes = self._reader(cut).planes(source)
            decoded = to_rgb(*planes, bits=10, matrix=record.color.matrix, range_=record.color.range)
            color.apply(into, decoded)
            working.append((decoded, weight))
        frame = shown(self.project, cut, working)
        # The proxy is a smaller copy of the original: its pixels stand for (original /
        # proxy) original pixels, and the preview canvas is the comp divided by `divisor`.
        video = record.probe.video
        density = video.size[1] / record.proxy.size[1]
        if cut.placement is None:
            position, scale, opacity, anchor = (0.5, 0.5), (1.0, 1.0), 1.0, (0.5, 0.5)
        else:
            position, scale, opacity = cut.placement.at(index * ticks_per_frame(cut.comp_rate))
            anchor = cut.placement.anchor
        opacity *= cut.opacity_at(index)
        factor = density / self.divisor
        scale = (scale[0] * factor, scale[1] * factor)
        rotation = 0.0 if cut.placement is None else cut.placement.rotation_at(index * ticks_per_frame(cut.comp_rate))
        if frame.shape[1::-1] == canvas and scale == (1.0, 1.0) and position == (0.5, 0.5) and opacity == 1.0 \
                and rotation == 0.0 and not cut.layer.masks:
            return frame, 1.0
        mask = mask_alpha(cut.layer.masks, frame.shape[1::-1]) if cut.layer.masks else None
        placed, alpha = place(frame, canvas, position=position, anchor=anchor, scale=scale, rotation=rotation,
                              mask=mask)
        return placed * np.float32(opacity), alpha * np.float32(opacity)

    def _working(self, resolved: Resolved, index: int, canvas: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
        """A comp's frame at the preview's scale on `canvas`, premultiplied, with coverage:
        its footage and precomps in stack order (its graphics are not drawn here)."""
        cuts = {cut.layer.id: cut for cut in resolved.stack(index)}
        nests = {nest.layer.id: nest for nest in resolved.nests_at(index)}
        layers = []
        for layer in resolved.comp.layers:
            if layer.id in cuts:
                layers.append(self._layer(cuts[layer.id], index, canvas))
            elif layer.id in nests:
                layers.append(self._nest(nests[layer.id], index, canvas))
        return composite_alpha(layers, canvas)

    def _nest(self, nest: Nest, index: int, canvas: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
        """A precomp layer at the preview's scale: the precomp drawn on a canvas its size
        over the preview's divisor, placed as the render places it."""
        width, height = nest.resolved.comp.size
        inner = (max(1, round(width / self.divisor)), max(1, round(height / self.divisor)))
        premultiplied, cover = self._working(nest.resolved, nest.frame(index), inner)
        straight = np.ascontiguousarray(np.divide(premultiplied, cover, out=np.zeros_like(premultiplied),
                                                  where=cover > 0))
        mask = cover if not nest.layer.masks else cover * mask_alpha(nest.layer.masks, inner)
        if nest.placement is None:
            position, scale, opacity, anchor, rotation = (0.5, 0.5), (1.0, 1.0), 1.0, (0.5, 0.5), 0.0
        else:
            ticks = index * ticks_per_frame(nest.comp_rate)
            position, scale, opacity = nest.placement.at(ticks)
            anchor, rotation = nest.placement.anchor, nest.placement.rotation_at(ticks)
        # the inner canvas's pixels stand for (precomp / inner) precomp pixels, `divisor` of which make a preview pixel
        factor = (width / inner[0] / self.divisor, height / inner[1] / self.divisor)
        placed, alpha = place(straight, canvas, position=position, anchor=anchor,
                              scale=(scale[0] * factor[0], scale[1] * factor[1]), rotation=rotation, mask=mask)
        return placed * np.float32(opacity), alpha * np.float32(opacity)

    def display(self, index: int) -> np.ndarray:
        """Comp frame `index` at the preview size in the project's display encoding, what
        a render's footage would hold there (graphics are drawn by the editor live)."""
        assert 0 <= index < self.resolved.end, f"frame {index} is outside the comp (0 to {self.resolved.end - 1})"
        with self.lock:
            frame = self._working(self.resolved, index, self.size)[0]
            color.apply(self.view, frame)
            return frame

    def frame(self, index: int) -> bytes:
        """Comp frame `index` as RGBA bytes at the preview size, sRGB-encoded for the viewer."""
        assert 0 <= index < self.resolved.end, f"frame {index} is outside the comp (0 to {self.resolved.end - 1})"
        with self.lock:
            key = (self.version, index)
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            frame = self.display(index)
            color.apply(self.to_viewer, frame)
            rgba = np.empty(frame.shape[:2] + (4,), np.uint8)
            rgba[..., :3] = np.floor(np.clip(frame, 0, 1) * np.float32(255) + np.float32(0.5))
            rgba[..., 3] = 255
            data = rgba.tobytes()
            self.cache[key] = data
            self.cached += len(data)
            while self.cached > self.cache_bytes:
                _, old = self.cache.popitem(last=False)
                self.cached -= len(old)
            return data

    def close(self) -> None:
        for reader in self.readers.values():
            reader.close()
