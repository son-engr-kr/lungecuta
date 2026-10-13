"""How a comp frame is made, for renders, checks, stills of a comp and the preview: each
footage layer's source frames decoded, brought into the working space, blended, oriented
and graded (`shown`), placed by its transform and cut by its masks (`in_comp`); precomps
made the same way and placed as footage is; graphics under footage through the inverse
output view; the stack composited in the working space, then the output view, then the
top graphics on display-encoded values (`finish`; docs/design/01-architecture.md,
03-project-format.md). Each comp frame shows the source frame whose interval holds its
source time (D12, *proposed*).
"""
from __future__ import annotations

import math
import subprocess
from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager

import numpy as np

from . import color, grade, graphics
from .comp import Cut, Nest, Overlay, Placement, Resolved
from .decode import Layout, frame_rgb, read_raw, seek_seconds
from .geometry import mask_alpha, oriented, place
from .media import Project
from .parallel import in_order
from .ticks import ticks_per_frame
from .tools import tool

IDENTITY = ((0.5, 0.5), (1.0, 1.0), 1.0, (0.5, 0.5), 0.0)  # position, scale, opacity, anchor, rotation


def _cut_frames(project: Project, cut: Cut) -> Iterator[list[tuple[bytes, float]]]:
    """For each comp frame of a cut, in order, the raw source frames it shows (in the
    decoder's own format) with their weights. When the source never goes back, one pass
    decodes it from the first frame needed, keeping the frames still to be shown; a layer
    that plays backwards is read by a seek per frame."""
    from .media import original

    video = cut.record.probe.video
    path = original(project, cut.record)
    if not cut.forwards:
        for frame in range(cut.first, cut.end):
            yield [(read_raw(path, index, rate=cut.media_rate, size=video.size, pix_fmt=video.pix_fmt), weight)
                   for index, weight in cut.samples(frame)]
        return
    first, last = cut.source_frames
    frame_size = Layout.of(video.pix_fmt).frame_bytes(video.size)
    args = [tool("ffmpeg"), "-v", "error", "-noautorotate", "-ss", seek_seconds(first, cut.media_rate),
            "-i", str(path), "-map", "0:v:0", "-frames:v", str(last - first + 1),
            "-f", "rawvideo", "-pix_fmt", video.pix_fmt, "-"]
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        held: dict[int, bytes] = {}
        decoded = first - 1
        for frame in range(cut.first, cut.end):
            wanted = cut.samples(frame)
            while decoded < wanted[-1][0]:
                data = process.stdout.read(frame_size)
                assert len(data) == frame_size, f"layer {cut.layer.id}: frame {decoded + 1} did not arrive"
                decoded += 1
                held[decoded] = data
            yield [(held[index], weight) for index, weight in wanted]
            for index in [k for k in held if k < wanted[0][0]]:
                del held[index]
        rest = process.stdout.read()
        error = process.stderr.read().decode(errors="replace").strip()
    assert process.returncode == 0 and not error, f"ffmpeg failed reading {cut.record.key}: {error}"
    assert not rest, f"layer {cut.layer.id}: ffmpeg sent more frames than asked for"


def blended(project: Project, frames: list[tuple[np.ndarray, float]]) -> np.ndarray:
    """Working-space frames mixed by weight in linear light, where frame interpolation
    averages (04-color.md); one frame comes back as it is."""
    if len(frames) == 1:
        return frames[0][0]
    settings = project.file.color
    to_linear = color.cpu_processor(settings.config, settings.working, settings.linear)
    back = color.cpu_processor(settings.config, settings.linear, settings.working)
    total = None
    for frame, weight in frames:
        linear = np.ascontiguousarray(frame)
        color.apply(to_linear, linear)
        total = linear * np.float32(weight) if total is None else total + linear * np.float32(weight)
    total = np.ascontiguousarray(total, dtype=np.float32)
    color.apply(back, total)
    return total


def shown(project: Project, cut: Cut, working: list[tuple[np.ndarray, float]], threads: int | None = None) -> np.ndarray:
    """A layer's frame as shown, from its source frames in the working space with their
    weights: blended, rotated as the file says, through the layer's grade. Renders, checks
    and the preview all come through here."""
    # oriented first, so a grade's windows sit in the frame as shown (the operations are
    # per pixel and do not care)
    shown_frame = np.ascontiguousarray(oriented(blended(project, working), cut.record.probe.video.rotation))
    return grade.apply(project, cut.layer.grade, shown_frame, threads)


def _all_overlays(resolved: Resolved, prefix: str = "") -> list[tuple[str, Overlay, tuple[int, int]]]:
    """Every graphics layer of a comp and of its precomps, by its path of layer ids from the
    comp down (`title`, `card/title`), with the size of the comp it is drawn in."""
    out = [(prefix + overlay.layer.id, overlay, tuple(resolved.comp.size)) for overlay in resolved.overlays]
    for nest in resolved.nests:
        out += _all_overlays(nest.resolved, f"{prefix}{nest.layer.id}/")
    return out


@contextmanager
def overlay_pages(project: Project, resolved: Resolved) -> Iterator[dict]:
    """A Chromium page for each graphics layer, precomps' included, open for the whole
    render, by path."""
    with ExitStack() as stack:
        yield {path: (overlay, stack.enter_context(graphics.opened(project.directory / overlay.layer.page, size,
                                                                    overlay.layer.params)))
               for path, overlay, size in _all_overlays(resolved)}


def _inline(project: Project, png: bytes, comp: tuple[int, int], threads: int | None) -> tuple[np.ndarray, np.ndarray]:
    """A captured page under footage, brought into the working space through the inverse
    of the output view (01-architecture.md, Where display-referred layers enter), as a
    premultiplied layer and its alpha."""
    settings = project.file.color
    rgb, alpha = graphics.straight(png, comp)
    color.apply(color.inverse_view_processor(settings.config, settings.working, settings.display, settings.view),
                rgb, threads)
    return rgb * alpha, alpha


def finish(project: Project, resolved: Resolved, index: int, footage: dict[str, tuple], captures: dict[str, bytes],
           threads: int | None = None, alpha: bool = False) -> np.ndarray:
    """Comp frame `index` from its footage layers (placed, premultiplied, in the working
    space) and its graphics captures: everything below the top run of graphics composited
    in stack order in the working space, graphics under footage through the inverse view;
    then the output view; then the top graphics over it on display-encoded values."""
    settings = project.file.color
    comp = tuple(resolved.comp.size)
    overlays = {overlay.layer.id: overlay for overlay in resolved.overlays}
    nests = {nest.layer.id: nest for nest in resolved.nests_at(index)}
    layers = []
    for layer in resolved.comp.layers:
        if layer.id in footage:
            layers.append(footage[layer.id])
        elif layer.id in nests:
            layers.append(_nested(project, nests[layer.id], index, comp, captures, threads, f"{layer.id}/"))
        elif layer.id in captures and not overlays[layer.id].top:
            layers.append(_inline(project, captures[layer.id], comp, threads))
    view = color.view_processor(settings.config, settings.working, settings.display, settings.view)
    if not alpha:
        frame = composite(layers, comp)
        color.apply(view, frame, threads)
        for overlay in resolved.overlays:
            if overlay.top and overlay.layer.id in captures:
                frame = graphics.over(frame, captures[overlay.layer.id])
        return frame
    # With alpha: the output view on straight colour (a colour transform of premultiplied
    # values is wrong wherever the coverage is partial), and the top graphics composited
    # over it straight, as image tools composite two straight layers.
    premultiplied, cover = composite_alpha(layers, comp)
    frame = np.ascontiguousarray(np.divide(premultiplied, cover, out=np.zeros_like(premultiplied), where=cover > 0))
    color.apply(view, frame, threads)
    for overlay in resolved.overlays:
        if overlay.top and overlay.layer.id in captures:
            colour, page_alpha = graphics.straight(captures[overlay.layer.id], comp)
            total = page_alpha + cover * (1 - page_alpha)
            mixed = colour * page_alpha + frame * cover * (1 - page_alpha)
            frame = np.divide(mixed, total, out=np.zeros_like(mixed), where=total > 0)
            cover = total
    return np.ascontiguousarray(np.concatenate([frame, cover], axis=2), dtype=np.float32)


def layer_frame(project: Project, cut: Cut, index: int, threads: int | None = None) -> np.ndarray:
    """A footage layer's frame as shown on comp frame `index`, in the working space, before
    it is placed: its source frames read one at a time (no streaming decode), through the
    input transform, and `shown`."""
    from .still import open_clip

    clip = open_clip(project, cut.record)
    working = []
    for source, weight in cut.samples(index):
        decoded = clip.read(source)
        color.apply(clip.into, decoded, threads)
        working.append((decoded, weight))
    return shown(project, cut, working, threads)


def footage_at(project: Project, resolved: Resolved, index: int, threads: int | None = None) -> dict[str, tuple]:
    """Comp frame `index`'s footage layers, placed: what checks, stills of a comp and
    precomps use."""
    size = tuple(resolved.comp.size)
    return {cut.layer.id: in_comp(project, cut, index, layer_frame(project, cut, index, threads), size)
            for cut in resolved.stack(index)}


def working_frame(project: Project, resolved: Resolved, index: int, captures: dict[str, bytes],
                  threads: int | None = None, prefix: str = "") -> tuple[np.ndarray, np.ndarray]:
    """A precomp's frame `index` in the working space, premultiplied, with its coverage:
    its footage, its own precomps, and its graphics, which all enter through the inverse
    view, since inside a precomp they are never the top of the final stack (D15)."""
    size = tuple(resolved.comp.size)
    footage = footage_at(project, resolved, index, threads)
    nests = {nest.layer.id: nest for nest in resolved.nests_at(index)}
    layers = []
    for layer in resolved.comp.layers:
        if layer.id in footage:
            layers.append(footage[layer.id])
        elif layer.id in nests:
            layers.append(_nested(project, nests[layer.id], index, size, captures, threads, f"{prefix}{layer.id}/"))
        elif prefix + layer.id in captures:
            layers.append(_inline(project, captures[prefix + layer.id], size, threads))
    return composite_alpha(layers, size)


def _nested(project: Project, nest: Nest, index: int, comp: tuple[int, int], captures: dict[str, bytes],
            threads: int | None, prefix: str) -> tuple[np.ndarray, np.ndarray]:
    """A precomp layer on comp frame `index`: the precomp's frame, transparent where it has
    nothing, placed and masked as footage is."""
    premultiplied, cover = working_frame(project, nest.resolved, nest.frame(index), captures, threads, prefix)
    straight = np.ascontiguousarray(np.divide(premultiplied, cover, out=np.zeros_like(premultiplied), where=cover > 0))
    mask = cover if not nest.layer.masks else cover * mask_alpha(nest.layer.masks, tuple(nest.resolved.comp.size))
    position, scale, opacity, anchor, rotation = transform_at(nest.placement, nest.comp_rate, index)
    placed, alpha = place(straight, comp, position=position, anchor=anchor, scale=scale, rotation=rotation, mask=mask)
    return placed * np.float32(opacity), alpha * np.float32(opacity)


def frames(project: Project, resolved: Resolved, threads: int | None = None) -> Iterator[np.ndarray]:
    """Every frame of the comp as display-encoded float R'G'B', in order, one at a time."""
    with overlay_pages(project, resolved) as pages:
        for index, raws in enumerate(raw_layers(project, resolved)):
            yield compose(project, resolved, index, raws, captured(resolved, index, pages), threads)


def transform_at(placement: Placement | None, rate, index: int) -> tuple:
    """A layer's position, scale, opacity, anchor and rotation on comp frame `index`; a
    layer without a transform sits at its own size in the middle, as in After Effects."""
    if placement is None:
        return IDENTITY
    ticks = index * ticks_per_frame(rate)
    position, scale, opacity = placement.at(ticks)
    return position, scale, opacity, placement.anchor, placement.rotation_at(ticks)


def transform_ticks(placement: Placement, ticks: int) -> tuple:
    position, scale, opacity = placement.at(ticks)
    return position, scale, opacity, placement.anchor, placement.rotation_at(ticks)


def _corners(placement: Placement, ticks: int, layer: tuple[int, int], canvas: tuple[int, int],
             factor: float) -> np.ndarray:
    """The layer frame's four corners on the canvas at `ticks`, in canvas pixels."""
    position, scale, _, anchor, rotation = transform_ticks(placement, ticks)
    turn = math.radians(rotation)
    cos, sin = math.cos(turn), math.sin(turn)
    out = []
    for lx, ly in ((0, 0), (layer[0], 0), (layer[0], layer[1]), (0, layer[1])):
        dx, dy = (lx - anchor[0] * layer[0]) * scale[0] * factor, (ly - anchor[1] * layer[1]) * scale[1] * factor
        out.append((position[0] * canvas[0] + cos * dx - sin * dy, position[1] * canvas[1] + sin * dx + cos * dy))
    return np.array(out)


def shutter_times(cut: Cut, index: int, layer: tuple[int, int], canvas: tuple[int, int],
                  factor: float = 1.0) -> list[int]:
    """The times (ticks) a layer is placed at for comp frame `index`: its own time without
    motion blur, else spread over the shutter `[t, t + shutter]` (06-editing.md) at the
    middles of equal parts, one part for every pixel its corners travel on the canvas, so
    neighbouring samples are at most a pixel apart. The path is measured on a polyline whose
    segments are halved until there are at least as many as pixels it is long."""
    tick = ticks_per_frame(cut.comp_rate)
    start = index * tick
    degrees = cut.shutter_deg(start)
    if degrees <= 0 or cut.placement is None:
        return [start]
    span = degrees / 360 * tick
    segments = 1
    while True:
        points = np.array([_corners(cut.placement, round(start + span * k / segments), layer, canvas, factor)
                           for k in range(segments + 1)])
        length = float(np.linalg.norm(np.diff(points, axis=0), axis=2).sum(axis=0).max())
        if segments >= length:
            break
        segments *= 2
    count = max(1, math.ceil(length))
    return [round(start + span * (k + 0.5) / count) for k in range(count)]


def placed_layer(project: Project, cut: Cut, index: int, frame: np.ndarray, canvas: tuple[int, int],
                 factor: float = 1.0, threads: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """A layer's frame (working space, as shown) placed on the canvas by its transform,
    premultiplied, with its alpha (its own opacity included, a transition's not). With
    motion blur, placed at every `shutter_times` and averaged in linear light, weighted by
    coverage: the mean of the premultiplied linear samples over the mean coverage, back in
    the working space. `factor` scales the layer on the canvas (the editor's preview)."""
    layer = (frame.shape[1], frame.shape[0])
    mask = mask_alpha(cut.layer.masks, layer) if cut.layer.masks else None
    times = shutter_times(cut, index, layer, canvas, factor)
    if cut.placement is None:
        samples = [((0.5, 0.5), (1.0, 1.0), 1.0, (0.5, 0.5), 0.0)]
    else:
        samples = [transform_ticks(cut.placement, ticks) for ticks in times]
    if len(samples) == 1:
        position, scale, opacity, anchor, rotation = samples[0]
        placed, alpha = place(frame, canvas, position=position, anchor=anchor, scale=(scale[0] * factor, scale[1] * factor),
                              rotation=rotation, mask=mask)
        return placed * np.float32(opacity), alpha * np.float32(opacity)
    settings = project.file.color
    linear = np.ascontiguousarray(frame.copy())
    color.apply(color.cpu_processor(settings.config, settings.working, settings.linear), linear, threads)
    total = cover = None
    for position, scale, opacity, anchor, rotation in samples:
        placed, alpha = place(linear, canvas, position=position, anchor=anchor,
                              scale=(scale[0] * factor, scale[1] * factor), rotation=rotation, mask=mask)
        placed, alpha = placed * np.float32(opacity), alpha * np.float32(opacity)
        total = placed if total is None else total + placed
        cover = alpha if cover is None else cover + alpha
    total /= np.float32(len(samples))
    cover /= np.float32(len(samples))
    straight = np.ascontiguousarray(np.divide(total, cover, out=np.zeros_like(total), where=cover > 0))
    color.apply(color.cpu_processor(settings.config, settings.linear, settings.working), straight, threads)
    return np.ascontiguousarray(straight * cover, dtype=np.float32), cover


def in_comp(project: Project, cut: Cut, index: int, working: np.ndarray,
            comp: tuple[int, int]) -> tuple[np.ndarray, np.ndarray | float]:
    """A layer's working-space frame (as shown, after rotation) placed in the comp: by its
    transform at comp frame `index` (over its shutter, with motion blur), or at its own size
    in the middle when it has none, cut by its masks. Returns it premultiplied by its alpha
    (opacity and transitions included) and that alpha."""
    height, width, _ = working.shape
    faded = cut.opacity_at(index)
    if cut.placement is None and (width, height) == comp and not cut.layer.masks:
        return (working, 1.0) if faded == 1.0 else (working * np.float32(faded), faded)
    placed, alpha = placed_layer(project, cut, index, working, comp)
    return placed * np.float32(faded), alpha * np.float32(faded)


def composite(layers: list[tuple[np.ndarray, np.ndarray | float]], comp: tuple[int, int]) -> np.ndarray:
    """Premultiplied layers, bottom first, each over what is below it, on black, in the
    working space (DaVinci Intermediate by default, where Resolve composites; 04-color.md)."""
    return composite_alpha(layers, comp)[0]


def composite_alpha(layers: list[tuple[np.ndarray, np.ndarray | float]],
                    comp: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """`composite`, and the coverage of the result (HxWx1): over transparent rather than
    black, which is the same colour premultiplied."""
    width, height = comp
    canvas = np.zeros((height, width, 3), np.float32)
    cover = np.zeros((height, width, 1), np.float32)
    for premultiplied, alpha in layers:
        if isinstance(alpha, float) and alpha == 1.0:
            canvas, cover = premultiplied.copy(), np.ones_like(cover)
        else:
            canvas = premultiplied + canvas * (1 - alpha)
            cover = alpha + cover * (1 - alpha)
    return np.ascontiguousarray(canvas, dtype=np.float32), np.ascontiguousarray(cover, dtype=np.float32)


def raw_layers(project: Project, resolved: Resolved) -> Iterator[list[tuple[Cut, list[tuple[bytes, float]]]]]:
    """For every comp frame in order, its visible footage layers (bottom first) with the
    raw frame each shows; each layer's frames come from one decoder, opened at its first."""
    decoders: dict[str, Iterator[bytes]] = {}
    with ExitStack() as stack:
        for index in range(resolved.end):
            raws = []
            for cut in resolved.stack(index):
                if cut.layer.id not in decoders:
                    decoders[cut.layer.id] = stack.enter_context(_closing(_cut_frames(project, cut)))
                raws.append((cut, next(decoders[cut.layer.id])))
            for cut in resolved.cuts:
                if cut.end == index + 1 and cut.layer.id in decoders:
                    assert next(decoders.pop(cut.layer.id), None) is None, f"layer {cut.layer.id} had frames left"
            yield raws


def compose(project: Project, resolved: Resolved, index: int, raws: list[tuple[Cut, list]],
            captures: dict[str, bytes], threads: int | None = None, alpha: bool = False) -> np.ndarray:
    """Comp frame `index` from its layers' raw frames and its graphics' captures: each
    footage frame decoded, through its input transform and its layer's grade, placed, and
    the whole stack finished as `finish` does."""
    settings = project.file.color
    comp = tuple(resolved.comp.size)
    footage = {}
    for cut, sources in raws:
        video = cut.record.probe.video
        into = color.cpu_processor(settings.config, cut.record.color.space, settings.working)
        working = []
        for data, weight in sources:
            decoded = frame_rgb(data, video.size, pix_fmt=video.pix_fmt, chroma_location=video.chroma_location,
                                matrix=cut.record.color.matrix, range_=cut.record.color.range)
            color.apply(into, decoded, threads)
            working.append((decoded, weight))
        footage[cut.layer.id] = in_comp(project, cut, index, shown(project, cut, working, threads), comp)
    return finish(project, resolved, index, footage, captures, threads, alpha)


def captured(resolved: Resolved, index: int, pages: dict, prefix: str = "") -> dict[str, bytes]:
    """The graphics layers visible on comp frame `index`, precomps' included at the frame
    each precomp shows, captured, by path."""
    out = {}
    for overlay in resolved.overlays:
        if overlay.first <= index < overlay.end:
            _, page = pages[prefix + overlay.layer.id]
            out[prefix + overlay.layer.id] = graphics.frame_png(page, overlay.layer_seconds(index, resolved.rate))
    for nest in resolved.nests_at(index):
        out |= captured(nest.resolved, nest.frame(index), pages, f"{prefix}{nest.layer.id}/")
    return out


@contextmanager
def _closing(generator: Iterator):
    try:
        yield generator
    finally:
        generator.close()


# Comp frames sampled at once by `frames_at` and `lc verify`: each mostly waits on ffmpeg
# seeking into the originals (bench/verify_cost.py, bench/RESULTS.md).
FRAMES_AT_ONCE = 4


def frames_at(project: Project, resolved: Resolved, indices: Iterable[int], pages: dict, *,
              threads: int | None = None, alpha: bool = False) -> Iterator[np.ndarray]:
    """Comp frames `indices`, in order, as `frame_at` makes them, FRAMES_AT_ONCE at a time.
    Graphics are captured on the caller's thread, in order, since a page captures one frame
    at a time; the rest runs on the workers."""
    def made(index: int, captures: dict[str, bytes]) -> np.ndarray:
        return finish(project, resolved, index, footage_at(project, resolved, index, threads), captures, threads, alpha)

    jobs = ((index, captured(resolved, index, pages)) for index in indices)
    return in_order(made, jobs, workers=FRAMES_AT_ONCE, ahead=FRAMES_AT_ONCE)


def frame_at(project: Project, resolved: Resolved, index: int, threads: int | None = None,
             pages: dict | None = None, alpha: bool = False) -> np.ndarray:
    """Comp frame `index` alone, as `frames` renders it (for checks that sample a render);
    `pages` are the overlay pages when the caller keeps them open across frames."""
    footage = footage_at(project, resolved, index, threads)
    if pages is not None:
        return finish(project, resolved, index, footage, captured(resolved, index, pages), threads, alpha)
    with overlay_pages(project, resolved) as opened:
        return finish(project, resolved, index, footage, captured(resolved, index, opened), threads, alpha)
