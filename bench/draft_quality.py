"""The draft preset's H.264 by VideoToolbox against web-h264's x264, at the draft's size:
the measurement `DRAFT_QUALITY` in `src/lungecuta/encode.py` comes from.

Usage: uv run python bench/draft_quality.py PROJECT COMP FRAMES OUT_DIR [vt:Q ...] [x264:CRF ...]
  By default: vt:40 vt:50 vt:55 vt:60 vt:65 vt:70 x264:18.

The comp's first FRAMES draft frames are made as `lungecuta render --preset draft-h264`
makes them (the preview chain on the proxies with the graphics captured at its scale),
timed, and handed to every encoder as the same 8-bit 4:2:0 planes (`frame_bytes`, the
draft preset's dither included), kept as raw video, so each PSNR is the codec's loss
alone. Each encode is decoded and its frames counted against the reference's before a
number is quoted. Reported per setting: bitrate, encode speed, PSNR per plane against the
planes, and the frame-to-frame Y PSNR, which against the reference's own shows an encode
that smoothed away grain or motion (as `bench/hevc_quality.py` reports it).
"""
import re
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

from lungecuta import overlays
from lungecuta.compose import captured
from lungecuta.encode import DRAFT_QUALITY, PRESETS, frame_bytes
from lungecuta.media import Project
from lungecuta.preview import Preview
from lungecuta.render import draft_of, read
from lungecuta.ticks import format_rate
from lungecuta.tools import tool

PRESET = PRESETS["draft-h264"]


def settings(name: str) -> list[str]:
    kind, value = name.split(":")
    if kind == "vt":
        return ["-c:v", "h264_videotoolbox", "-profile:v", "high", "-q:v", value]
    assert kind == "x264", name
    return ["-c:v", "libx264", "-crf", value, "-profile:v", "high"]


def count(path: Path) -> int:
    out = subprocess.run([tool("ffprobe"), "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                         check=True).stdout
    return int(out.strip())


def psnr(raw: list[str], encoded: Path | None) -> str:
    """ffmpeg's PSNR summary of `encoded` against the raw planes, or of the raw planes'
    consecutive frames (encoded None) through the same filter on a one-frame offset."""
    if encoded is None:
        graph = "[0:v]split[a][b];[b]trim=start_frame=1,setpts=PTS-STARTPTS[c];[a][c]psnr"
        inputs = raw
    else:
        graph = "[1:v][0:v]psnr"
        inputs = [*raw, "-i", str(encoded)]
    out = subprocess.run([tool("ffmpeg"), "-v", "info", "-nostats", *inputs, "-lavfi", graph, "-f", "null", "-"],
                         capture_output=True, text=True, check=True).stderr
    return re.search(r"PSNR y:(\S+) u:(\S+) v:(\S+) average:\S+", out).group(0)


def frame_to_frame(path: Path) -> str:
    graph = "[0:v]split[a][b];[b]trim=start_frame=1,setpts=PTS-STARTPTS[c];[a][c]psnr"
    out = subprocess.run([tool("ffmpeg"), "-v", "info", "-nostats", "-i", str(path), "-lavfi", graph, "-f", "null",
                          "-"], capture_output=True, text=True, check=True).stderr
    return re.search(r"PSNR y:(\S+)", out).group(1)


def main() -> None:
    project = Project.load(Path(sys.argv[1]))
    name, frames, out_dir = sys.argv[2], int(sys.argv[3]), Path(sys.argv[4])
    names = sys.argv[5:] or ["vt:40", "vt:50", "vt:55", "vt:60", "vt:65", "vt:70", "x264:18"]
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved, _ = read(project, name)
    draft = draft_of(project, resolved, PRESET)
    indices = range(0, min(resolved.end, frames * draft["step"]), draft["step"])
    begun = time.perf_counter()
    pages = overlays.captures(project, resolved, indices, divisor=draft["divisor"])
    print(f"{draft}; captures {time.perf_counter() - begun:.1f} s")
    reference = out_dir / "draft.yuv"
    begun = time.perf_counter()
    with closing(Preview(project, name)) as preview, reference.open("wb") as file:
        for index in indices:
            data, _ = frame_bytes(preview.draft(index, captured(resolved, index, pages)), PRESET, seed=index)
            file.write(data)
    made = time.perf_counter() - begun
    print(f"{len(indices)} draft frames made on one thread: {1000 * made / len(indices):.1f} ms a frame")
    width, height = draft["size"]
    raw = ["-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{width}x{height}", "-framerate", draft["rate"],
           "-i", str(reference)]
    print(f"reference frame to frame Y: {psnr(raw, None)}")
    print(f"DRAFT_QUALITY now {DRAFT_QUALITY}; rate {format_rate(resolved.rate)} comp, {draft['rate']} draft")
    for setting in names:
        encoded = out_dir / f"{setting.replace(':', '_')}.mp4"
        begun = time.perf_counter()
        subprocess.run([tool("ffmpeg"), "-v", "error", "-y", *raw, *settings(setting), "-pix_fmt", "yuv420p",
                        str(encoded)], check=True)
        seconds = time.perf_counter() - begun
        assert count(encoded) == len(indices), f"{setting}: {count(encoded)} frames for {len(indices)}"
        mbps = encoded.stat().st_size * 8 / (len(indices) / float(eval(draft["rate"]))) / 1e6
        print(f"{setting}: {mbps:.2f} Mbps, {len(indices) / seconds:.0f} fps encode, {psnr(raw, encoded)}, "
              f"frame to frame Y {frame_to_frame(encoded)}")


if __name__ == "__main__":
    main()
