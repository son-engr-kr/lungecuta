"""What a DJI camera writes, clip by clip (docs/design/02-media.md, DJI files): the colour
mode and lens tags, the lens runs from the debug stream, and how the LRF beside each MP4
compares with it.

Usage: uv run python bench/dji_files.py [--sound N] DIR [DIR ...]

For every DJI_*.MP4 in each DIR (and its .LRF): ColorGammaSxS, LensType, EI and the lens
runs; the LRF's video size, pixel format and frame count against the MP4's, whether its
audio streams are the MP4's packet for packet, which fields of DJI's per-frame metadata
(`CAM meta`, protobuf read without a schema) differ between the two, and the LRF's luma
against the MP4's on every keyframe (the MP4's averaged over 3x3 blocks, in 8-bit codes).
With --sound N, the lag and correlation between the two audio streams of the first N clips.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

from lungecuta import dji
from lungecuta.tools import run, tool


def varint(data: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, at


def fields(data: bytes, path: tuple = ()) -> list | None:
    """Leaf fields of a protobuf message as (path, bytes), or None when `data` is not one."""
    out, at = [], 0
    while at < len(data):
        try:
            key, at = varint(data, at)
        except IndexError:
            return None
        number, wire = key >> 3, key & 7
        if number == 0:
            return None
        if wire == 0:
            try:
                start = at
                _, at = varint(data, at)
            except IndexError:
                return None
            value = data[start:at]
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            if at + size > len(data):
                return None
            value, at = data[at:at + size], at + size
        elif wire == 2:
            try:
                size, at = varint(data, at)
            except IndexError:
                return None
            if at + size > len(data):
                return None
            value, at = data[at:at + size], at + size
            inner = fields(value, path + (number,)) if size and not all(32 <= c < 127 for c in value) else None
            if inner is not None:
                out += inner
                continue
        else:
            return None
        out.append((path + (number,), value))
    return out


def aperture_runs(mp4: Path) -> list[tuple[int, int, str]]:
    """Runs of the aperture DJI's per-frame metadata gives (`CAM meta` field 3.2.11.1: two
    bytes, f-number times ten and ten), as (first frame, frames, f-number)."""
    out: list[list] = []
    for frame, packet in enumerate(dji.packets(mp4, stream(probe(mp4), "CAM meta"))):
        values = [v for p, v in fields(packet) if p == (3, 2, 11, 1)]
        assert len(values) == 1 and len(values[0]) == 2, f"frame {frame}: {values}"
        number = f"F{values[0][0] / values[0][1]:.1f}"
        if out and out[-1][2] == number:
            out[-1][1] += 1
        else:
            out.append([frame, 1, number])
    return [tuple(run) for run in out]


def probe(path: Path) -> dict:
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                           str(path)]).stdout)


def stream(info: dict, handler: str) -> int:
    return next(s["index"] for s in info["streams"] if s.get("tags", {}).get("handler_name") == handler)


def keyframes(path: Path, size: tuple[int, int], pix_fmt: str):
    """The luma plane of each keyframe, decoded one at a time."""
    width, height = size
    depth = 2 if pix_fmt.endswith("10le") else 1
    proc = subprocess.Popen([tool("ffmpeg"), "-v", "error", "-skip_frame", "nokey", "-i", str(path), "-map", "0:v:0",
                             "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"],
                            stdout=subprocess.PIPE)
    frame = width * height * 3 // 2 * depth
    while chunk := proc.stdout.read(frame):
        assert len(chunk) == frame
        plane = np.frombuffer(chunk[:width * height * depth], np.uint16 if depth == 2 else np.uint8)
        yield plane.reshape(height, width)
    assert proc.wait() == 0


def compare(mp4: Path) -> str:
    lrf = mp4.with_suffix(".LRF")
    a, b = probe(mp4), probe(lrf)
    va = next(s for s in a["streams"] if s.get("tags", {}).get("handler_name") == "VideoHandler")
    vb = next(s for s in b["streams"] if s.get("tags", {}).get("handler_name") == "VideoHandler")
    audio = [(sa["index"], sb["index"]) for sa, sb in zip(
        [s for s in a["streams"] if s["codec_type"] == "audio"], [s for s in b["streams"] if s["codec_type"] == "audio"])]
    same_audio = all(hashlib.sha256(b"".join(dji.packets(mp4, x))).digest()
                     == hashlib.sha256(b"".join(dji.packets(lrf, y))).digest() for x, y in audio)
    differ = set()
    for pa, pb in zip(dji.packets(mp4, stream(a, "CAM meta")), dji.packets(lrf, stream(b, "CAM meta"))):
        fa, fb = {}, {}
        for p, v in fields(pa):
            fa.setdefault(p, []).append(v)
        for p, v in fields(pb):
            fb.setdefault(p, []).append(v)
        differ |= {".".join(map(str, p)) for p in set(fa) | set(fb) if fa.get(p) != fb.get(p)}
    diffs, pa_, pb_ = [], [], []
    for ya, yb in zip(keyframes(mp4, (va["width"], va["height"]), va["pix_fmt"]),
                      keyframes(lrf, (vb["width"], vb["height"]), vb["pix_fmt"])):
        fy = va["height"] // vb["height"]
        small = ya.reshape(vb["height"], fy, vb["width"], fy).mean(axis=(1, 3)) / 4.0
        diffs.append(float((yb - small).mean()))
        pa_.append(np.percentile(small, [1, 50, 99]))
        pb_.append(np.percentile(yb, [1, 50, 99]))
    worst = float(np.abs(np.array(pa_) - np.array(pb_)).max())
    return (f"LRF {vb['width']}x{vb['height']} {vb['pix_fmt']} {vb['nb_frames']}f (MP4 {va['width']}x{va['height']} "
            f"{va['pix_fmt']} {va['nb_frames']}f), audio packets the same {same_audio}, CAM meta fields differing "
            f"{sorted(differ)}, CAM dbgi in LRF {any(s.get('tags', {}).get('handler_name') == dji.DEBUG_STREAM for s in b['streams'])}, "
            f"luma LRF - MP4 mean {min(diffs):+.3f} to {max(diffs):+.3f}, percentiles apart at most {worst:.2f}")


def sound(mp4: Path) -> str:
    tracks = []
    for k in (0, 1):
        raw = run(["ffmpeg", "-v", "error", "-i", str(mp4), "-map", f"0:a:{k}", "-f", "f32le", "-c:a", "pcm_f32le",
                   "-"]).stdout
        tracks.append(np.frombuffer(raw, np.float32).reshape(-1, 2)[:, 0].astype(np.float64))
    n = min(len(tracks[0]), len(tracks[1]), 48000 * 10)
    x, y = tracks[0][:n], tracks[1][:n]
    lag = max(range(-4096, 4097), key=lambda k: float(np.dot(x[max(0, k):n + min(0, k)], y[max(0, -k):n - max(0, k)])))
    xs, ys = x[max(0, lag):n + min(0, lag)], y[max(0, -lag):n - max(0, lag)]
    return f"audio 0 against 1: lag {lag} samples, correlation there {np.corrcoef(xs, ys)[0, 1]:.4f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sound", type=int, default=0)
    parser.add_argument("dirs", nargs="+", type=Path)
    args = parser.parse_args()
    clips = [p for d in args.dirs for p in sorted(d.glob("DJI_*.MP4")) if not p.name.startswith("._")]
    for k, mp4 in enumerate(clips):
        found = dji.read(mp4)
        runs = dji.lens_runs(dji.packets(mp4, found.debug_stream), dji.SENSORS[found.model])
        lenses = runs if isinstance(runs, str) else " | ".join(f"{r.first}+{r.frames} {r.lens}" for r in runs)
        apertures = aperture_runs(mp4)
        agree = not isinstance(runs, str) and [(r.first, r.frames) for r in runs] == [(a, n) for a, n, _ in apertures]
        print(f"clip {k + 1}: {found.tags.get('ColorGammaSxS')} LensType {found.tags.get('LensType')} "
              f"EI {found.tags.get('ExposureIndexAsa') or '-'}; lenses {lenses}; aperture runs "
              f"{' | '.join(f'{a}+{n} {f}' for a, n, f in apertures)}, on the lens runs' frames {agree}")
        print(f"  {compare(mp4)}")
        if k < args.sound:
            print(f"  {sound(mp4)}")


if __name__ == "__main__":
    main()
