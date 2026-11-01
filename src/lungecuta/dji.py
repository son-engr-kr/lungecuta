"""What a DJI camera writes into its MP4s (docs/design/02-media.md#colour-space-detection):
the container's `com.dji.camera.*` tags, one of which names the colour mode, and a debug
stream whose every packet names the sensor mode the frame was read with, so the lens.

The tags hold the lens a clip starts on (`LensType`); a camera with two lenses can change
lens while it records (the Osmo Pocket 4P moves to its 60 mm camera when it zooms in), so
which frames come from which lens is read from the debug stream, frame by frame.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .schema.media import LensRun
from .tools import run

TAG_PREFIX = "com.dji.camera."
# The tags kept as evidence (the serial number is not).
TAGS = ("CameraModel", "ColorGammaSxS", "LensType", "ExposureIndexAsa", "WhiteBalanceKelvin", "WhiteBalanceTintCc")
DEBUG_STREAM = "CAM dbgi"  # the debug stream's handler name

# The sensor modes a model's debug stream names, each with the lens it reads, as the
# `LensType` tag names that lens. Seen on the Osmo Pocket 4P (PP-041, firmware 01.01.71.37)
# in 72 clips of two shoots, 2026-10-16 and 2026-10-30: every packet names one of the two,
# the clip's first frame the lens its LensType tag gives, and the aperture in DJI's own
# per-frame metadata (F2.0 or F1.8) changes on the same frames.
SENSORS = {
    "PP-041": {"OV50X50": "20mm F2", "IMX06C": "60mm F1.8"},
}


@dataclass(frozen=True)
class DjiFile:
    """The DJI tags of a file and its debug stream's index (None when it has none)."""

    encoder: str | None
    tags: dict[str, str]
    debug_stream: int | None

    @property
    def model(self) -> str | None:
        return self.tags.get("CameraModel")


def read(path: Path) -> DjiFile | None:
    """The file's DJI tags, or None when its container has no `com.dji.camera.` tag."""
    data = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                           str(path)]).stdout)
    format_tags = data["format"].get("tags", {})
    if not any(name.startswith(TAG_PREFIX) for name in format_tags):
        return None
    debug = [s["index"] for s in data["streams"]
             if s["codec_type"] == "data" and s.get("tags", {}).get("handler_name") == DEBUG_STREAM]
    assert len(debug) <= 1, f"{path.name} has {len(debug)} {DEBUG_STREAM!r} streams"
    return DjiFile(
        encoder=format_tags.get("encoder"),
        tags={name: format_tags[TAG_PREFIX + name].strip() for name in TAGS if TAG_PREFIX + name in format_tags},
        debug_stream=debug[0] if debug else None,
    )


def packets(path: Path, stream: int) -> list[bytes]:
    """The packets of one stream, as stored: its bytes copied out by ffmpeg, split by the
    sizes ffprobe lists."""
    sizes = [int(size) for size in run(["ffprobe", "-v", "error", "-select_streams", str(stream), "-show_entries",
                                        "packet=size", "-of", "csv=p=0", str(path)]).stdout.split()]
    data = run(["ffmpeg", "-v", "error", "-copy_unknown", "-i", str(path), "-map", f"0:{stream}", "-c", "copy",
                "-f", "data", "-"]).stdout
    assert sum(sizes) == len(data), f"{path.name}: stream {stream} holds {len(data)} bytes, its packets {sum(sizes)}"
    out, start = [], 0
    for size in sizes:
        out.append(data[start:start + size])
        start += size
    return out


def lens_runs(debug_packets: list[bytes], sensors: dict[str, str]) -> list[LensRun] | str:
    """The runs of frames read from one sensor, from the debug stream's packets (one a
    frame), or the reason they cannot be told: a packet naming none of `sensors`, or more
    than one."""
    runs: list[LensRun] = []
    for frame, packet in enumerate(debug_packets):
        named = [name for name in sensors if name.encode() in packet]
        if len(named) != 1:
            which = "none" if not named else " and ".join(named)
            return f"debug packet of frame {frame} names {which} of the sensors {sorted(sensors)}"
        if runs and runs[-1].sensor == named[0]:
            runs[-1] = runs[-1].model_copy(update={"frames": runs[-1].frames + 1})
        else:
            runs.append(LensRun(first=frame, frames=1, sensor=named[0], lens=sensors[named[0]]))
    return runs
