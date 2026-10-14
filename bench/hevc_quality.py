"""HEVC Main 10 by VideoToolbox against x265, on one ProRes 4444 reference: the measurement
08-deliver.md asks for before a `web-hevc10` preset is written.

Usage: uv run python bench/hevc_quality.py REFERENCE.mov OUT_DIR [vt:Q ...] [x265:CRF ...] [x264:CRF ...]
                                            [file:PATH ...]
  REFERENCE is a ProRes 4444 render of a comp (`lungecuta render COMP --preset prores-4444-alpha`;
  its alpha is ignored). By default: vt:50 vt:60 vt:70 vt:80 x265:18 x265:22. `x264:CRF` is
  web-h264's video settings at that CRF, 8-bit, from the same 4:2:0 conversion; `file:PATH`
  measures an encode of the same frames made elsewhere (`lungecuta render COMP --preset web-h264`),
  whose chroma went through the engine's own decimation, so only its Y is comparable.

Each setting encodes the reference to 4:2:0 10-bit (`p010le` for VideoToolbox, as it
takes, `yuv420p10le` for x265, both the same samples), then, before a number is quoted:
the encode is decoded and its frames counted against the reference's. Reported per
setting: the bitrate, the encode time, and PSNR per plane against the reference brought to
4:2:0 10-bit by the same conversion the encoder's input went through, so the number is
the codec's loss alone. The quality scale of VideoToolbox (`-q:v`, 1 to 100) is Apple's
and undocumented in its meaning; it is swept, not interpreted.

PSNR alone cannot tell grain kept from grain thrown away once the error is down near the
footage's own frame-to-frame noise (VideoToolbox at q50 scored 39 dB with a nearly frozen,
smoothed picture), so each encode's frame-to-frame Y PSNR is reported next to the
reference's: an encode whose frames differ from each other much less than the reference's
do has discarded grain or motion.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path


def frames(path: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                         check=True).stdout
    return int(out.strip())


def encode(reference: Path, out: Path, setting: str) -> list[str]:
    kind, value = setting.split(":")
    if kind == "vt":
        codec = ["-c:v", "hevc_videotoolbox", "-profile:v", "main10", "-q:v", value, "-pix_fmt", "p010le"]
    elif kind == "x264":  # web-h264's video settings (src/lungecuta/encode.py), at this CRF
        codec = ["-c:v", "libx264", "-crf", value, "-profile:v", "high", "-pix_fmt", "yuv420p"]
    elif kind == "x265":
        codec = ["-c:v", "libx265", "-preset", "medium", "-crf", value, "-pix_fmt", "yuv420p10le",
                 "-x265-params", "log-level=error"]
    else:
        raise AssertionError(f"unknown setting {setting!r}: vt:Q, x265:CRF, x264:CRF or file:PATH")
    tag = [] if kind == "x264" else ["-tag:v", "hvc1"]
    return ["ffmpeg", "-v", "error", "-y", "-i", str(reference), "-map", "0:v:0", *codec, *tag,
            "-movflags", "+faststart", str(out)]


def psnr(encoded: Path, reference: Path) -> dict[str, float]:
    graph = "[0:v]format=yuv420p10le[a];[1:v]format=yuv420p10le[b];[a][b]psnr"
    text = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(encoded), "-i", str(reference),
                           "-lavfi", graph, "-f", "null", "-"], capture_output=True, text=True, check=True).stderr
    found = re.search(r"PSNR y:([\d.]+) u:([\d.]+) v:([\d.]+) average:([\d.]+)", text)
    assert found, text[-500:]
    return dict(zip(("y", "u", "v", "average"), (float(x) for x in found.groups())))


def steadiness(path: Path) -> float:
    """Y PSNR of every frame against the next, over the whole file: how much its frames change."""
    n = frames(path) - 1
    graph = (f"[0:v]trim=end_frame={n},setpts=PTS-STARTPTS,format=yuv420p10le[a];"
             "[1:v]trim=start_frame=1,setpts=PTS-STARTPTS,format=yuv420p10le[b];[a][b]psnr")
    text = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-i", str(path), "-lavfi", graph,
                           "-f", "null", "-"], capture_output=True, text=True, check=True).stderr
    found = re.search(r"PSNR y:([\d.]+)", text)
    assert found, text[-500:]
    return float(found.group(1))


def main() -> None:
    reference, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    settings = sys.argv[3:] or ["vt:50", "vt:60", "vt:70", "vt:80", "x265:18", "x265:22"]
    out_dir.mkdir(parents=True, exist_ok=True)
    count = frames(reference)
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                       "stream=width,height,r_frame_rate,pix_fmt", "-of", "json", str(reference)],
                                      capture_output=True, text=True, check=True).stdout)["streams"][0]
    num, den = (int(x) for x in probe["r_frame_rate"].split("/"))
    seconds = count * den / num
    print(json.dumps({"reference": reference.name, "frames": count, **probe,
                      "frame_to_frame_y_db": steadiness(reference)}), flush=True)
    for setting in settings:
        took = None
        if setting.startswith("file:"):
            out = Path(setting.removeprefix("file:"))
            stream = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                                "stream=bit_rate", "-of", "json", str(out)], capture_output=True,
                                               text=True, check=True).stdout)["streams"][0]
            mbps = int(stream["bit_rate"]) / 1e6  # the video stream's own rate: the file may carry audio
        else:
            out = out_dir / f"{setting.replace(':', '-')}.mp4"
            start = time.perf_counter()
            subprocess.run(encode(reference, out, setting), check=True)
            took = time.perf_counter() - start
            mbps = out.stat().st_size * 8 / seconds / 1e6
        decoded = frames(out)
        assert decoded == count, f"{setting}: {decoded} frames decoded for {count}"
        row = {"setting": setting, "mbps": round(mbps, 2), "frames": decoded, "psnr_db": psnr(out, reference),
               "frame_to_frame_y_db": steadiness(out)}
        if took is not None:
            row |= {"encode_s": round(took, 1), "encode_fps": round(count / took, 1)}
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
