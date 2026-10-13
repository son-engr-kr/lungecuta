"""`lc verify`: a rendered file checked against its comp and preset (docs/design/08-deliver.md,
Verification): what the file is, its picture against the engine's own reference render of
the same frames, every cut against its neighbours, and its loudness."""
from __future__ import annotations

import json
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from . import compose
from .comp import load_comp, resolve
from .compose import captured, finish, footage_at, overlay_pages
from .decode import Layout, frame_rgb, read_raw, read_rgb
from .encode import PRESETS
from .media import Project
from .mixdown import comp_samples, mastered
from .parallel import in_order
from .ticks import format_rate, parse_rate
from .tools import run

# A wrong matrix or range shows as several codes of bias (bench/RESULTS.md, "Y'CbCr to
# R'G'B'": about 4 at white); codec loss and dither average out. *proposed*
BIAS_LIMIT = 1.0  # 10-bit codes, per channel, over the sampled frames
# EBU R 128-2023: "a tolerance of ±0.2 LU is allowed in order to take account of
# measurement errors" (i); the true peak "shall not exceed -1 dBTP" (m).
LOUDNESS_TOLERANCE = 0.2  # LU
# Alpha is written at 10 bits, full range, rounded (half a code), and ffmpeg's ProRes
# decoder gives it back at 12 bits, a quarter of a code a step: the limit allows two such
# steps on top. Measured on a 4K render with a feathered mask: 0.685 (bench/RESULTS.md).
ALPHA_LIMIT = 1.0  # 10-bit codes


def _check(name: str, expected: Any, found: Any) -> dict[str, Any]:
    return {"check": name, "expected": expected, "found": found, "ok": expected == found}


def probe_file(path: Path, *, audio: bool = True) -> dict[str, Any]:
    data = json.loads(run(["ffprobe", "-v", "error", "-count_packets", "-show_streams", "-show_format",
                           "-of", "json", str(path)]).stdout)
    video = [s for s in data["streams"] if s["codec_type"] == "video"]
    sound = [s for s in data["streams"] if s["codec_type"] == "audio"]
    assert len(video) == 1 and len(sound) == int(audio), f"{path.name}: {len(video)} video and {len(sound)} audio streams"
    return {"video": video[0], "audio": sound[0] if audio else None, "format": data["format"]}


def loudness(path: Path) -> dict[str, float]:
    """Integrated loudness and true peak by ffmpeg's ebur128 filter (BS.1770)."""
    text = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
                "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr.decode(errors="replace")
    summary = text[text.rindex("Summary:"):]
    integrated = float(re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)[1])
    peak = float(re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)[1])
    return {"integrated_lufs": integrated, "true_peak_dbtp": peak}


def verify(project: Project, name: str, file: Path, preset_name: str, *, threads: int | None = None) -> dict[str, Any]:
    preset = PRESETS[preset_name]
    resolved = resolve(project, load_comp(project, name))
    found = probe_file(file, audio=bool(preset.audio))
    video, audio = found["video"], found["audio"]
    width, height = resolved.comp.size
    checks = [
        _check("video codec", preset.expect_codec, video["codec_name"]),
        _check("profile", preset.expect_profile, video.get("profile")),
        _check("pixel format", preset.decodes_as or preset.pix_fmt, video["pix_fmt"]),
        _check("size", [width, height], [video["width"], video["height"]]),
        _check("rate", format_rate(resolved.rate), format_rate(parse_rate(video["r_frame_rate"]))),
        _check("frames", resolved.end, int(video["nb_read_packets"])),
        _check("colour tags", ["bt709", "bt709", "bt709", "tv"],
               [video.get("color_primaries"), video.get("color_transfer"), video.get("color_space"),
                video.get("color_range")]),
    ]
    if audio is not None:
        checks.append(_check("audio rate and channels", [48000, 2], [int(audio["sample_rate"]), audio["channels"]]))
    if preset.expect_codec == "h264":
        checks.append(_check("chroma location", preset.chroma_location, video.get("chroma_location")))
    measured, chain = {}, {"none": "the preset has no audio"}
    if audio is not None:
        samples = comp_samples(resolved)
        audio_frames = int(Fraction(audio["duration_ts"]) * Fraction(audio["time_base"]) * 48000) \
            if "duration_ts" in audio else None
        if preset.audio[1] == "pcm_s24le":
            checks.append(_check("audio samples", samples, audio_frames))
        measured = loudness(file)
        _, chain = mastered(project, resolved, preset)
    if audio is None:
        pass
    elif not chain.get("silent"):
        off = measured["integrated_lufs"] - preset.loudness
        checks.append({"check": "integrated loudness, LUFS", "expected": f"{preset.loudness} within {LOUDNESS_TOLERANCE}",
                       "found": measured["integrated_lufs"], "ok": abs(off) <= LOUDNESS_TOLERANCE + 1e-9})
        checks.append({"check": "true peak, dBTP", "expected": f"at most {preset.true_peak}",
                       "found": measured["true_peak_dbtp"], "ok": measured["true_peak_dbtp"] <= preset.true_peak})
    else:
        checks.append(_check("audio is silent (the comp's mix is)", float("-inf"), measured["true_peak_dbtp"]))

    # Picture: the two frames on either side of every cut, decoded from the file, each against
    # the engine's reference for itself and for the frame across the cut, which shows
    # something else; plus the comp's first and last frames.
    across: dict[int, int | None] = {0: None, resolved.end - 1: None}
    for cut in resolved.cuts:
        for inside, outside in ((cut.first, cut.first - 1), (cut.end - 1, cut.end)):
            if 0 <= outside < resolved.end:  # both sides of each cut, each against the other
                across[inside], across[outside] = outside, inside
            else:
                across.setdefault(inside, None)
    with overlay_pages(project, resolved) as pages:
        totals, cuts, alpha_error = _sample(project, resolved, file, video, preset, across, threads, pages)
    bias = [round(float(b), 3) for b in totals["sum"] / totals["pixels"]]
    mse = float(totals["squares"].sum() / (3 * totals["pixels"]))
    psnr = None if mse == 0 else round(10 * np.log10(1023 ** 2 / mse), 2)
    checks.append({"check": "picture bias, 10-bit codes per channel", "expected": f"within {BIAS_LIMIT}",
                   "found": bias, "ok": max(abs(b) for b in bias) <= BIAS_LIMIT})
    if alpha_error is not None:
        checks.append({"check": "alpha, largest error in 10-bit codes", "expected": f"at most {ALPHA_LIMIT}",
                       "found": round(alpha_error, 3), "ok": alpha_error <= ALPHA_LIMIT})
    checks.append({"check": "every cut's frames are closer to their own reference than to the frame across the cut",
                   "expected": True, "found": all(c["ok"] for c in cuts), "ok": all(c["ok"] for c in cuts)})
    return {
        "file": str(file), "comp": name, "preset": preset_name,
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "picture": {"psnr_db": psnr, "bias": bias, "sampled": cuts},
        "loudness": measured | {"target_lufs": preset.loudness, "ceiling_dbtp": preset.true_peak, "master": chain}
        if audio is not None else chain,
    }


def _decoded(file: Path, index: int, resolved, video: dict, preset) -> tuple[np.ndarray, np.ndarray | None]:
    """A frame of the file as R'G'B' over black (premultiplied by its alpha, if it has one),
    and the alpha in [0, 1]."""
    width, height = resolved.comp.size
    if not preset.alpha:
        return read_rgb(file, index, rate=resolved.rate, size=(width, height), pix_fmt=video["pix_fmt"],
                        matrix="bt709", range_="limited", chroma_location=preset.chroma_location), None
    layout = Layout.of(video["pix_fmt"])
    data = read_raw(file, index, rate=resolved.rate, size=(width, height), pix_fmt=video["pix_fmt"])
    colour = frame_rgb(data, (width, height), pix_fmt=video["pix_fmt"], chroma_location=preset.chroma_location,
                       matrix="bt709", range_="limited")
    values = np.frombuffer(data, "<u2" if layout.bits > 8 else np.uint8)
    alpha = values[3 * width * height:4 * width * height].reshape(height, width, 1).astype(np.float32)
    alpha /= np.float32((1 << layout.bits) - 1)
    return colour * alpha, alpha


def _reference(project: Project, resolved, index: int, threads, captures, preset) -> tuple[np.ndarray, np.ndarray | None]:
    footage = footage_at(project, resolved, index, threads)
    if not preset.alpha:
        return np.clip(finish(project, resolved, index, footage, captures, threads), 0, 1), None
    rgba = finish(project, resolved, index, footage, captures, threads, alpha=True)
    return np.clip(rgba[..., :3], 0, 1) * rgba[..., 3:], rgba[..., 3:]


def _runs(indices: list[int]) -> list[list[int]]:
    """Sorted frame indices in runs of consecutive ones: a frame and the one across its cut
    are always in the same run."""
    runs: list[list[int]] = []
    for index in indices:
        if runs and runs[-1][-1] == index - 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    return runs


def _sample(project: Project, resolved, file: Path, video: dict, preset, across: dict, threads,
            pages) -> tuple[dict, list, float | None]:
    """Each sampled frame of the file against its reference and the frame across its cut;
    with alpha, colour is compared over black, and the alpha's largest error in codes is
    returned too. Each reference is rendered once. A run of consecutive frames is one job
    and several run at once, since rendering a reference mostly waits on ffmpeg seeking
    into the originals (compose.FRAMES_AT_ONCE); graphics are captured here, in
    order, since a page captures one frame at a time."""

    def measured(run: list[int], captures: dict[int, dict]) -> tuple[dict, list, float | None]:
        decoded = {index: _decoded(file, index, resolved, video, preset) for index in run}
        references = {index: _reference(project, resolved, index, threads, captures[index], preset) for index in run}
        totals = {"sum": np.zeros(3), "squares": np.zeros(3), "pixels": 0}
        cuts, alpha_error = [], None
        for index in run:
            (frame, alpha), (reference, reference_alpha) = decoded[index], references[index]
            if alpha is not None:
                off = float(np.abs(alpha - reference_alpha).max()) * 1023
                alpha_error = off if alpha_error is None else max(alpha_error, off)
            signed = ((frame - reference) * 1023).reshape(-1, 3).astype(np.float64)
            totals["sum"] += signed.sum(axis=0)
            totals["squares"] += (signed ** 2).sum(axis=0)
            totals["pixels"] += signed.shape[0]
            rmse = float(np.sqrt(np.mean(signed ** 2)))
            other = across[index]
            if other is None:
                cuts.append({"frame": index, "rmse": round(rmse, 3), "across_the_cut": None, "ok": True})
                continue
            to_other = float(np.sqrt(np.mean(((frame - references[other][0]) * 1023) ** 2)))
            cuts.append({"frame": index, "rmse": round(rmse, 3), "across_the_cut": other,
                         "rmse_to_it": round(to_other, 3), "ok": rmse < to_other})
        return totals, cuts, alpha_error

    jobs = ((run, {index: captured(resolved, index, pages) for index in run}) for run in _runs(sorted(across)))
    totals = {"sum": np.zeros(3), "squares": np.zeros(3), "pixels": 0}
    cuts, alpha_error = [], None
    for part, run_cuts, run_alpha in in_order(measured, jobs, workers=compose.FRAMES_AT_ONCE, ahead=compose.FRAMES_AT_ONCE):
        totals = {key: totals[key] + part[key] for key in totals}
        cuts += run_cuts
        if run_alpha is not None:
            alpha_error = run_alpha if alpha_error is None else max(alpha_error, run_alpha)
    return totals, cuts, alpha_error
