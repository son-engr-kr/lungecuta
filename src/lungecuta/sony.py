"""Sony's per-clip XML sidecar, `<stem>M01.XML` next to the clip
(docs/design/02-media.md#colour-space-detection)."""
from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SonyXml:
    file: str
    duration: int | None
    capture_gamma: str | None
    capture_primaries: str | None
    coding_equations: str | None
    capture_fps: str | None
    format_fps: str | None
    recording_mode: str | None
    ltc_start: str | None
    model: str | None


def sidecar_path(clip: Path) -> Path:
    """The only name the camera writes: the clip's stem plus `M01.XML`, in its directory."""
    return clip.with_name(f"{clip.stem}M01.XML")


def read_sidecar(clip: Path) -> SonyXml | None:
    path = sidecar_path(clip)
    if not path.is_file():
        return None
    root = ElementTree.parse(path).getroot()
    assert _local(root.tag) == "NonRealTimeMeta", f"{path.name} is not a Sony NonRealTimeMeta file"
    elements = list(root.iter())

    def attribute(tag: str, name: str) -> str | None:
        found = {e.get(name) for e in elements if _local(e.tag) == tag and e.get(name) is not None}
        assert len(found) <= 1, f"{path.name}: several <{tag} {name}> values {sorted(found)}"
        return found.pop() if found else None

    items = {e.get("name"): e.get("value") for e in elements if _local(e.tag) == "Item"}
    ltc = [e for e in elements if _local(e.tag) == "LtcChange"]
    duration = attribute("Duration", "value")
    return SonyXml(
        file=path.name,
        duration=int(duration) if duration is not None else None,
        capture_gamma=items.get("CaptureGammaEquation"),
        capture_primaries=items.get("CaptureColorPrimaries"),
        coding_equations=items.get("CodingEquations"),
        capture_fps=attribute("VideoFrame", "captureFps"),
        format_fps=attribute("VideoFrame", "formatFps"),
        recording_mode=attribute("RecordingMode", "type"),
        ltc_start=ltc[0].get("value") if ltc else None,
        model=attribute("Device", "modelName"),
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
