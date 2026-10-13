"""Rendering a comp (M2's start): every comp frame through the same chain as `lc still`,
the music mixed from the WAVs, encoded by a delivery preset, with a manifest of the
versions that decided the pixels (docs/design/01-architecture.md, 08-deliver.md).

What renders now is Phase 0's part of the comp format: one picture track of cuts at
constant speed, each comp frame showing the source frame that holds its source time
(*proposed*), black between cuts, music layers summed at the mix rate and put through the
master chain at the preset's targets (`audio.py`). A footage layer is shown at its own
size, so its frame as shown (after rotation) must be the comp's size until transforms are
written.
"""
from __future__ import annotations

import os
import platform
import subprocess
from collections import deque
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np

from . import color, grade, graphics
from .audio import master, true_peak_db
from .comp import MIX_RATE, Cut, Nest, Overlay, Resolved, load_comp, resolve
from .decode import Layout, frame_rgb, read_raw, seek_seconds
from .encode import PRESETS, TAGS, Preset, frame_bytes
from .files import content_hash, json_text, write_atomic
from .geometry import mask_alpha, oriented, place
from .media import Project, music_wav
from .music import read_wav, wav_bytes
from .ticks import TICKS_PER_SECOND, format_rate, nearest_sample, ticks_per_frame
from .tools import run, tool

RENDER_DIR = Path("cache") / "lc" / "render"


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


def footage_at(project: Project, resolved: Resolved, index: int, threads: int | None = None) -> dict[str, tuple]:
    """Comp frame `index`'s footage layers read one frame at a time (no streaming decode),
    placed: what checks, stills of a comp and precomps use."""
    from .still import open_clip

    footage = {}
    for cut in resolved.stack(index):
        clip = open_clip(project, cut.record)
        working = []
        for source, weight in cut.samples(index):
            decoded = clip.read(source)
            color.apply(clip.into, decoded, threads)
            working.append((decoded, weight))
        footage[cut.layer.id] = in_comp(cut, index, shown(project, cut, working, threads), tuple(resolved.comp.size))
    return footage


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
    if nest.placement is None:
        position, scale, opacity, anchor, rotation = (0.5, 0.5), (1.0, 1.0), 1.0, (0.5, 0.5), 0.0
    else:
        ticks = index * ticks_per_frame(nest.comp_rate)
        position, scale, opacity = nest.placement.at(ticks)
        anchor, rotation = nest.placement.anchor, nest.placement.rotation_at(ticks)
    placed, alpha = place(straight, comp, position=position, anchor=anchor, scale=scale, rotation=rotation, mask=mask)
    return placed * np.float32(opacity), alpha * np.float32(opacity)


def frames(project: Project, resolved: Resolved, threads: int | None = None) -> Iterator[np.ndarray]:
    """Every frame of the comp as display-encoded float R'G'B', in order, one at a time."""
    with overlay_pages(project, resolved) as pages:
        for index, raws in enumerate(_raw_layers(project, resolved)):
            yield compose(project, resolved, index, raws, _captures(resolved, index, pages), threads)


def in_comp(cut: Cut, index: int, working: np.ndarray, comp: tuple[int, int]) -> tuple[np.ndarray, np.ndarray | float]:
    """A layer's working-space frame (as shown, after rotation) placed in the comp: by its
    transform at comp frame `index`, or at its own size in the middle when it has none, cut
    by its masks. Returns it premultiplied by its alpha (opacity and transitions included)
    and that alpha."""
    height, width, _ = working.shape
    faded = cut.opacity_at(index)
    mask = mask_alpha(cut.layer.masks, (width, height)) if cut.layer.masks else None
    if cut.placement is None:
        if (width, height) == comp and mask is None:
            return (working, 1.0) if faded == 1.0 else (working * np.float32(faded), faded)
        position, scale, opacity, anchor, rotation = (0.5, 0.5), (1.0, 1.0), 1.0, (0.5, 0.5), 0.0
    else:
        ticks = index * ticks_per_frame(cut.comp_rate)
        position, scale, opacity = cut.placement.at(ticks)
        anchor, rotation = cut.placement.anchor, cut.placement.rotation_at(ticks)
    opacity *= faded
    placed, alpha = place(working, comp, position=position, anchor=anchor, scale=scale, rotation=rotation, mask=mask)
    return placed * np.float32(opacity), alpha * np.float32(opacity)


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


def _raw_layers(project: Project, resolved: Resolved) -> Iterator[list[tuple[Cut, list[tuple[bytes, float]]]]]:
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
        footage[cut.layer.id] = in_comp(cut, index, shown(project, cut, working, threads), comp)
    return finish(project, resolved, index, footage, captures, threads, alpha)


def _captures(resolved: Resolved, index: int, pages: dict, prefix: str = "") -> dict[str, bytes]:
    """The graphics layers visible on comp frame `index`, precomps' included at the frame
    each precomp shows, captured, by path."""
    out = {}
    for overlay in resolved.overlays:
        if overlay.first <= index < overlay.end:
            _, page = pages[prefix + overlay.layer.id]
            out[prefix + overlay.layer.id] = graphics.frame_png(page, overlay.layer_seconds(index, resolved.rate))
    for nest in resolved.nests_at(index):
        out |= _captures(nest.resolved, nest.frame(index), pages, f"{prefix}{nest.layer.id}/")
    return out


@contextmanager
def _closing(generator: Iterator):
    try:
        yield generator
    finally:
        generator.close()


def comp_samples(resolved: Resolved) -> int:
    """The comp's length at the mix rate, its end landing on the nearest sample."""
    return nearest_sample(resolved.end * ticks_per_frame(resolved.rate), MIX_RATE)


def mix(project: Project, resolved: Resolved) -> np.ndarray:
    """The music layers summed, each through its gain and fades, frames x 2 channels at
    48 kHz, before the master chain (float, so a sum over full scale is kept for the
    limiter)."""
    total = comp_samples(resolved)
    out = np.zeros((total, 2), np.float32)
    for sound in resolved.sounds:
        signal, rate = read_wav(music_wav(project, sound.record))
        assert rate == MIX_RATE and signal.shape[1] in (1, 2), f"{sound.record.key}: a {rate} Hz WAV"
        end = min(sound.end, total)
        piece = signal[sound.source_first:sound.source_first + end - sound.first]
        piece = piece if piece.shape[1] == 2 else np.repeat(piece, 2, axis=1)
        out[sound.first:end] += piece * sound.envelope()[:end - sound.first, None].astype(np.float32)
    return out


def mastered(project: Project, resolved: Resolved, preset: Preset) -> tuple[np.ndarray, dict[str, Any]]:
    return master(mix(project, resolved), MIX_RATE, target_lufs=preset.loudness, ceiling_dbtp=preset.true_peak)


DELIVERY_PASSES = 6
PEAK_STEP = 0.01  # dB: the precision the delivered true peak is reported at


def delivered_audio(project: Project, resolved: Resolved, preset: Preset, base: Path) -> tuple[Path, list[str], dict]:
    """The comp's sound encoded as the preset delivers it: the file to mux, the arguments
    that mux it, and what the master chain did.

    A lossy codec raises the true peak (AAC at 256 kbps by 0.2 to 0.5 dB on the music
    sample, `bench/master_chain.py`), and the ceiling holds for the delivered file. So
    lossy audio is encoded on its own, decoded and measured, and mastered again under a
    lower ceiling until the decoded file is under the preset's ceiling; then it is muxed as
    it is. The decoded peak moves by less than the ceiling does (0.88 dB per dB on the
    Phase 0 check comp), so each new ceiling is a secant step through the last two
    passes, aimed one reporting step under the preset's ceiling, since a step aimed at the
    ceiling itself only approaches it from above. 24-bit PCM is muxed from the master
    directly."""
    mixed = mix(project, resolved)
    wav = base.with_name(f".{base.stem}.mix.wav")
    lossless = preset.audio[1].startswith("pcm_")
    ceiling, aim, previous = preset.true_peak, preset.true_peak - PEAK_STEP, None
    for _ in range(DELIVERY_PASSES):
        signal, chain = master(mixed, MIX_RATE, target_lufs=preset.loudness, ceiling_dbtp=ceiling)
        wav.write_bytes(wav_bytes(signal, MIX_RATE))
        if lossless or chain.get("silent"):
            return wav, list(preset.audio), chain
        encoded = base.with_name(f".{base.stem}.audio.m4a")
        run([tool("ffmpeg"), "-v", "error", "-y", "-i", str(wav), *preset.audio, str(encoded)])
        decoded = np.frombuffer(run([tool("ffmpeg"), "-v", "error", "-i", str(encoded), "-f", "f32le", "-"]).stdout,
                                np.float32).reshape(-1, 2)
        peak = true_peak_db(decoded, MIX_RATE)
        if peak <= preset.true_peak:
            wav.unlink()
            return encoded, ["-c:a", "copy"], chain | {"ceiling_used": round(ceiling, 3),
                                                       "delivered_true_peak": round(peak, 3)}
        slope = 1.0
        if previous is not None:
            slope = (peak - previous[1]) / (ceiling - previous[0])
            assert slope > 0, f"lowering the ceiling to {ceiling:.3f} did not lower the encoded peak ({peak:.3f})"
        previous = (ceiling, peak)
        ceiling += (aim - peak) / slope
    raise AssertionError(f"the encoded audio stayed over {preset.true_peak} dBTP after {DELIVERY_PASSES} passes "
                         f"(last {peak:.3f} at a ceiling of {ceiling:.3f})")


def manifest(project: Project, resolved: Resolved, preset: Preset, out: Path, clipped: float,
             audio: dict[str, Any]) -> dict:
    """What decided the file's pixels and samples (01-architecture.md, Playback)."""
    comp_path = project.directory / "comps" / f"{resolved.comp.id}.json"
    ffmpeg = run(["ffmpeg", "-hide_banner", "-version"]).stdout.decode().splitlines()[0]
    settings = project.file.color
    return {
        "file": out.name,
        "hash": content_hash(out),
        "preset": preset.name,
        "comp": {"id": resolved.comp.id, "hash": content_hash(comp_path), "frames": resolved.end,
                 "rate": format_rate(resolved.rate), "size": list(resolved.comp.size)},
        "media": sorted({(c.record.key, c.record.hash) for c in resolved.cuts}
                        | {(s.record.key, s.record.wav.hash) for s in resolved.sounds}),
        "luts": sorted({(op.path, content_hash(project.directory / op.path))
                        for c in resolved.cuts for op in grade.operations(c.layer.grade) if op.op == "lut"}),
        "color": settings.model_dump(),
        "versions": {"lungecuta": version("lungecuta"), "opencolorio": version("opencolorio"),
                     "numpy": np.__version__, "ffmpeg": ffmpeg, "python": platform.python_version()},
        "clipped_pixels": round(clipped, 6),
        "audio": audio,
    }


def render(project: Project, name: str, preset_name: str, out: Path | None = None,
           threads: int | None = None, workers: int | None = None) -> dict[str, Any]:
    """Render comp `name` with a delivery preset, `workers` frames at a time (by default
    the machine's core count); returns what `lc render` prints."""
    workers = workers or os.cpu_count() or 1
    assert preset_name in PRESETS, f"unknown preset {preset_name!r} (presets: {', '.join(PRESETS)})"
    preset = PRESETS[preset_name]
    resolved = resolve(project, load_comp(project, name))
    if out is None:
        out = project.directory / RENDER_DIR / f"{name}.{preset_name}{preset.suffix}"
    assert out.suffix == preset.suffix, f"a {preset_name} file is a {preset.suffix} file, not {out.name}"
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(f".{out.stem}.partial{out.suffix}")
    width, height = resolved.comp.size
    inputs = ["-f", "rawvideo", "-pix_fmt", preset.pix_fmt, "-s", f"{width}x{height}",
              "-framerate", format_rate(resolved.rate), "-i", "-"]
    maps, audio_args = ["-map", "0:v:0"], []
    mix_path, chain = None, {"none": "the preset has no audio"}
    if preset.audio:
        mix_path, audio_args, chain = delivered_audio(project, resolved, preset, out)
        inputs += ["-i", str(mix_path)]
        maps += ["-map", "1:a:0"]
    encoder = subprocess.Popen([
        tool("ffmpeg"), "-v", "error", "-y", *inputs, *maps,
        "-vf", TAGS, *preset.video, "-pix_fmt", preset.pix_fmt, *audio_args, str(partial),
    ], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    clipped, count = 0.0, 0

    def job(index: int, raws: list, captures: dict[str, bytes]) -> tuple[bytes, float]:
        frame = compose(project, resolved, index, raws, captures, threads=1 if workers > 1 else threads,
                        alpha=preset.alpha)
        return frame_bytes(frame, preset, seed=index)

    # Frames are read and graphics captured in order here; their colour work runs on
    # `workers` threads (numpy and OCIO release the GIL) and they are written in order.
    with overlay_pages(project, resolved) as pages, ThreadPoolExecutor(workers) as pool:
        pending: deque = deque()
        for index, raws in enumerate(_raw_layers(project, resolved)):
            pending.append(pool.submit(job, index, raws, _captures(resolved, index, pages)))
            while len(pending) > 2 * workers or (pending and pending[0].done()):
                data, outside = pending.popleft().result()
                encoder.stdin.write(data)
                clipped, count = clipped + outside, count + 1
        while pending:
            data, outside = pending.popleft().result()
            encoder.stdin.write(data)
            clipped, count = clipped + outside, count + 1
    encoder.stdin.close()
    error = encoder.stderr.read().decode(errors="replace").strip()
    assert encoder.wait() == 0 and not error, f"ffmpeg failed encoding {out.name}: {error}"
    assert count == resolved.end, f"{count} frames rendered for {resolved.end}"
    if mix_path is not None:
        mix_path.unlink()
    partial.rename(out)
    record = manifest(project, resolved, preset, out, clipped / count, chain)
    write_atomic(out.with_name(out.name + ".manifest.json"), json_text(record))
    return {"file": str(out), "manifest": str(out.with_name(out.name + ".manifest.json")), "frames": count,
            "seconds": round(resolved.end * ticks_per_frame(resolved.rate) / TICKS_PER_SECOND, 6),
            "clipped_pixels": record["clipped_pixels"], "audio": chain}


def frame_at(project: Project, resolved: Resolved, index: int, threads: int | None = None,
             pages: dict | None = None, alpha: bool = False) -> np.ndarray:
    """Comp frame `index` alone, as `frames` renders it (for checks that sample a render);
    `pages` are the overlay pages when the caller keeps them open across frames."""
    footage = footage_at(project, resolved, index, threads)
    if pages is not None:
        return finish(project, resolved, index, footage, _captures(resolved, index, pages), threads, alpha)
    with overlay_pages(project, resolved) as opened:
        return finish(project, resolved, index, footage, _captures(resolved, index, opened), threads, alpha)
