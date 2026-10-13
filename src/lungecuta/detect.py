"""A clip's colour space and time, decided from its evidence or left undecided
(docs/design/02-media.md#colour-space-detection, #rates-and-timecode).

Each mapping below has one row per combination seen on a real file. Evidence that matches
no row leaves the clip undecided, with the evidence and the reason; it is never matched
to a nearby row, and Rec.709 is never assumed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema.media import ColorDecided, ColorUndecided, Probe, TimeDecided, TimeUndecided
from .sony import SonyXml, read_sidecar
from .ticks import format_rate, parse_rate

# (CaptureGammaEquation, CaptureColorPrimaries) -> OCIO colour space. Seen: the S-Log3 sample.
SONY_COLOR_SPACES = {
    ("s-log3-cine", "s-gamut3-cine"): "S-Log3 S-Gamut3.Cine",
}
# CodingEquations -> YCbCr matrix. Seen: the S-Log3 sample.
SONY_MATRICES = {"rec709": "bt709"}
# captureFps and formatFps -> rate. Seen: the S-Log3 sample (29.97p), a 60p HLG clip (59.94p).
SONY_RATES = {"29.97p": "30000/1001", "59.94p": "60000/1001"}
# The stream's own range tag: range belongs to the file, not to the curve (D5).
RANGES = {"pc": "full", "tv": "limited"}


@dataclass(frozen=True)
class Detection:
    time: TimeDecided | TimeUndecided
    color: ColorDecided | ColorUndecided

    @property
    def decided(self) -> bool:
        return self.time.status == "decided" and self.color.status == "decided"


def detect(clip: Path, probe: Probe) -> Detection:
    video = probe.video
    assert video is not None, f"{clip.name} has no video stream"
    rate = format_rate(parse_rate(video.rate))  # a rate outside the supported set is an error
    xml = read_sidecar(clip)
    mismatch = _sidecar_mismatch(xml, video.frames)
    return Detection(
        time=_time(xml, rate, probe.timecode, mismatch),
        color=_color(xml, video.tags.model_dump(), mismatch),
    )


def _sidecar_mismatch(xml: SonyXml | None, frames: int) -> str | None:
    """A sidecar that disagrees with its clip's length describes some other clip."""
    if xml is not None and xml.duration != frames:
        return f"sidecar {xml.file} says {xml.duration} frames and the clip has {frames}"
    return None


def _color(xml: SonyXml | None, tags: dict[str, str], mismatch: str | None) -> ColorDecided | ColorUndecided:
    if xml is None:
        evidence: dict[str, Any] = {"source": "stream-tags", "stream_tags": tags}
        described = "/".join(tags[t] for t in ("color_space", "color_transfer", "color_primaries"))
        return ColorUndecided(
            status="undecided",
            reason=f"no camera sidecar, and no mapping for stream tags {described}",
            evidence=evidence,
        )
    evidence = {
        "source": "sony-xml",
        "file": xml.file,
        "CaptureGammaEquation": xml.capture_gamma,
        "CaptureColorPrimaries": xml.capture_primaries,
        "CodingEquations": xml.coding_equations,
        "stream_tags": tags,
    }

    def undecided(reason: str) -> ColorUndecided:
        return ColorUndecided(status="undecided", reason=reason, evidence=evidence)

    if mismatch:
        return undecided(mismatch)
    space = SONY_COLOR_SPACES.get((xml.capture_gamma, xml.capture_primaries))
    if space is None:
        return undecided(f"no mapping for sony-xml {xml.capture_gamma} + {xml.capture_primaries}")
    matrix = SONY_MATRICES.get(xml.coding_equations)
    if matrix is None:
        return undecided(f"no matrix for sony-xml CodingEquations {xml.coding_equations}")
    range_ = RANGES.get(tags["color_range"])
    if range_ is None:
        return undecided(f"the stream does not state its range (color_range {tags['color_range']})")
    return ColorDecided(status="decided", space=space, matrix=matrix, range=range_, evidence=evidence)


def _time(xml: SonyXml | None, rate: str, start_tc: str | None, mismatch: str | None) -> TimeDecided | TimeUndecided:
    if xml is None:
        return TimeDecided(
            status="decided", rate=rate, capture_rate=None, start_tc=start_tc, evidence={"source": "probe"}
        )
    evidence = {
        "source": "sony-xml",
        "file": xml.file,
        "captureFps": xml.capture_fps,
        "formatFps": xml.format_fps,
        "RecordingMode": xml.recording_mode,
        "LtcChange": xml.ltc_start,
    }

    def undecided(reason: str) -> TimeUndecided:
        return TimeUndecided(status="undecided", rate=rate, reason=reason, start_tc=start_tc, evidence=evidence)

    if mismatch:
        return undecided(mismatch)
    written, captured = SONY_RATES.get(xml.format_fps), SONY_RATES.get(xml.capture_fps)
    if written is None or captured is None:
        return undecided(f"no mapping for sony-xml rates {xml.capture_fps} / {xml.format_fps}")
    if written != rate:
        return undecided(f"sidecar formatFps {xml.format_fps} does not match the stream's rate {rate}")
    if captured != written:
        return undecided(
            f"captured at {xml.capture_fps} and written at {xml.format_fps}: "
            "slow-and-quick recording has no rule until a real file confirms the fields"
        )
    return TimeDecided(status="decided", rate=rate, capture_rate=captured, start_tc=start_tc, evidence=evidence)
