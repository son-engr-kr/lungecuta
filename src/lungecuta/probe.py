"""What ffprobe says about a file, as the media record's `probe` entry."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schema.media import AudioProbe, Probe, StreamTags, VideoProbe
from .tools import run

_COLOR_TAGS = ("color_range", "color_space", "color_transfer", "color_primaries")


def ffprobe(path: Path) -> dict[str, Any]:
    out = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    return json.loads(out.stdout)


def probe(path: Path) -> Probe:
    data = ffprobe(path)
    streams = data["streams"]
    videos = [s for s in streams if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")]
    assert len(videos) <= 1, f"{path.name} has {len(videos)} video streams; one is supported"
    return Probe(
        video=_video(videos[0], path) if videos else None,
        audio=[_audio(s) for s in streams if s["codec_type"] == "audio"],
        data_streams=sum(1 for s in streams if s["codec_type"] == "data"),
        timecode=_timecode(streams, data["format"], path),
        creation_time=data["format"].get("tags", {}).get("creation_time"),
    )


def _video(stream: dict[str, Any], path: Path) -> VideoProbe:
    rate, average = stream["r_frame_rate"], stream["avg_frame_rate"]
    # Variable frame rate has no policy yet (docs/research/design-review-2026-10-11.md).
    assert rate == average, (
        f"{path.name}: frame rate {rate} but average {average}; variable frame rate is not supported yet"
    )
    assert "nb_frames" in stream, f"{path.name}: the container does not state a frame count"
    matrices = [entry for entry in stream.get("side_data_list", []) if "rotation" in entry]
    for entry in matrices:
        assert not _mirrored(entry["displaymatrix"]), (
            f"{path.name}: the display matrix mirrors the picture; only rotations are supported"
        )
    rotations = {entry["rotation"] for entry in matrices}
    assert len(rotations) <= 1, f"{path.name}: the stream carries several rotations {sorted(rotations)}"
    return VideoProbe(
        codec=stream["codec_name"],
        profile=stream.get("profile"),
        pix_fmt=stream["pix_fmt"],
        size=(stream["width"], stream["height"]),
        rate=rate,
        frames=int(stream["nb_frames"]),
        # ffprobe leaves a tag out of its JSON where `-show_entries` prints "unknown".
        tags=StreamTags(**{tag: stream.get(tag, "unknown") for tag in _COLOR_TAGS}),
        chroma_location=stream.get("chroma_location", "unspecified"),
        rotation=rotations.pop() if rotations else 0,
    )


def _mirrored(text: str) -> bool:
    """Whether a display matrix as ffprobe prints it (three rows `a b u`, `c d v`, `x y w`)
    flips the picture: its 2x2 part then has a negative determinant."""
    rows = [[int(value) for value in line.split(":", 1)[1].split()] for line in text.strip().splitlines()]
    assert len(rows) == 3 and all(len(row) == 3 for row in rows), f"unreadable display matrix {text!r}"
    (a, b, _), (c, d, _), _ = rows
    return a * d - b * c < 0


def _audio(stream: dict[str, Any]) -> AudioProbe:
    return AudioProbe(
        codec=stream["codec_name"],
        channels=stream["channels"],
        layout=stream.get("channel_layout"),
        rate=int(stream["sample_rate"]),
    )


def _timecode(streams: list[dict[str, Any]], fmt: dict[str, Any], path: Path) -> str | None:
    found = {s["tags"]["timecode"] for s in streams if "timecode" in s.get("tags", {})}
    if "timecode" in fmt.get("tags", {}):
        found.add(fmt["tags"]["timecode"])
    assert len(found) <= 1, f"{path.name}: streams disagree on the start timecode {sorted(found)}"
    return found.pop() if found else None
