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

from . import dji
from .camera_spaces import DLOG2
from .schema.media import ColorDecided, ColorUndecided, LensRun, Probe, TimeDecided, TimeUndecided
from .sony import SonyXml, read_sidecar
from .ticks import format_rate, parse_rate

# (CaptureGammaEquation, CaptureColorPrimaries) -> OCIO colour space. Seen: the S-Log3 sample;
# rec709 on three clips of a 23.98p shoot from the same body (2026-10-12), whose streams are
# tagged BT.709 throughout and limited range: the camera's Rec.709 curve, so its inverse
# brings them into scene light as the S-Log3 clips are (04-color.md, Input camera spaces).
SONY_COLOR_SPACES = {
    ("s-log3-cine", "s-gamut3-cine"): "S-Log3 S-Gamut3.Cine",
    ("rec709", "rec709"): "Camera Rec.709",
}
# CodingEquations -> YCbCr matrix. Seen: the S-Log3 sample.
SONY_MATRICES = {"rec709": "bt709"}
# captureFps and formatFps -> rate. Seen: the S-Log3 sample (29.97p), a 60p HLG clip (59.94p),
# a shoot of 176 clips at 23.98p (both fields, 2026-10-12, its streams 24000/1001).
SONY_RATES = {"23.98p": "24000/1001", "29.97p": "30000/1001", "59.94p": "60000/1001"}
# The stream's own range tag: range belongs to the file, not to the curve (D5).
RANGES = {"pc": "full", "tv": "limited"}

# (CameraModel, ColorGammaSxS) of a DJI file's container tags -> colour space. Seen: the Osmo
# Pocket 4P (PP-041), 65 D-Log2 and 7 D-Log clips of two shoots (2026-10-16, 2026-10-30).
# D-Log2 is DJI's published D-Log2 / D-Gamut2 (camera_spaces.py); D-Log is the 2017 white
# paper's, which DJI's Pocket 4P downloads list with a DCTL of the same formula and matrix
# (docs/research/color-sources.md, 3 and 4b). Their streams are tagged BT.709 throughout and
# limited range: the matrix and range are taken from those tags, as Resolve decodes such a
# file before DJI's DCTL is applied; the transfer and primaries tags are not the log curve's.
DJI_COLOR_SPACES = {
    ("PP-041", "D-Log2"): DLOG2,
    ("PP-041", "D-Log"): "D-Log D-Gamut",
}
# Colour modes DJI makes on one lens only: "10-bit D-Log 2 only supports 1x zoom" (DJI's Osmo
# Pocket 4P product page, its footnotes, read 2026-11-01), the 20 mm camera.
DJI_ONE_LENS = {("PP-041", "D-Log2"): "20mm F2"}
# The stream's matrix tag -> YCbCr matrix, for a camera whose own metadata does not name it.
STREAM_MATRICES = {"bt709": "bt709"}


@dataclass(frozen=True)
class Detection:
    time: TimeDecided | TimeUndecided
    color: ColorDecided | ColorUndecided
    lenses: list[LensRun] | None = None

    @property
    def decided(self) -> bool:
        return self.time.status == "decided" and self.color.status == "decided"


def detect(clip: Path, probe: Probe) -> Detection:
    video = probe.video
    assert video is not None, f"{clip.name} has no video stream"
    rate = format_rate(parse_rate(video.rate))  # a rate outside the supported set is an error
    xml = read_sidecar(clip)
    mismatch = _sidecar_mismatch(xml, video.frames)
    tags = video.tags.model_dump()
    time = _time(xml, rate, probe.timecode, mismatch)
    if xml is None:
        made_by_dji = dji.read(clip)
        if made_by_dji is not None:
            color, lenses = _dji_color(clip, made_by_dji, tags, video.frames)
            return Detection(time=time, color=color, lenses=lenses)
    return Detection(time=time, color=_color(xml, tags, mismatch))


def _dji_color(clip: Path, found: dji.DjiFile, tags: dict[str, str],
               frames: int) -> tuple[ColorDecided | ColorUndecided, list[LensRun] | None]:
    """A DJI clip's colour from its container tags, and its lens runs from its debug
    stream: decided only when both are read and agree with each other."""
    evidence: dict[str, Any] = {"source": "dji-mp4", "encoder": found.encoder, **found.tags, "stream_tags": tags}

    def undecided(reason: str, lenses: list[LensRun] | None = None) -> tuple[ColorUndecided, list[LensRun] | None]:
        return ColorUndecided(status="undecided", reason=reason, evidence=evidence), lenses

    model, mode = found.model, found.tags.get("ColorGammaSxS")
    space = DJI_COLOR_SPACES.get((model, mode))
    if space is None:
        return undecided(f"no mapping for dji {model} + {mode}")
    sensors = dji.SENSORS.get(model)
    assert sensors is not None, f"DJI_COLOR_SPACES maps {model} and dji.SENSORS does not name its sensors"
    if found.debug_stream is None:
        return undecided(f"no {dji.DEBUG_STREAM!r} stream, so the lens of each frame is not known")
    lenses = dji.lens_runs(dji.packets(clip, found.debug_stream), sensors)
    if isinstance(lenses, str):
        return undecided(lenses)
    if sum(run.frames for run in lenses) != frames:
        return undecided(f"the debug stream has {sum(run.frames for run in lenses)} packets and the clip {frames} "
                         "frames")
    evidence["lens_runs"] = len(lenses)
    if lenses[0].lens != found.tags.get("LensType"):
        return undecided(f"LensType says {found.tags.get('LensType')} and the first frame is {lenses[0].lens}", lenses)
    only = DJI_ONE_LENS.get((model, mode))
    other = [run for run in lenses if only is not None and run.lens != only]
    if other:
        return undecided(f"{mode} is made on the {only} lens only, and frames {other[0].first} to "
                         f"{other[0].first + other[0].frames - 1} are {other[0].lens}", lenses)
    matrix = STREAM_MATRICES.get(tags["color_space"])
    if matrix is None:
        return undecided(f"no matrix for the stream's matrix tag {tags['color_space']}", lenses)
    range_ = RANGES.get(tags["color_range"])
    if range_ is None:
        return undecided(f"the stream does not state its range (color_range {tags['color_range']})", lenses)
    return ColorDecided(status="decided", space=space, matrix=matrix, range=range_, evidence=evidence), lenses


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
