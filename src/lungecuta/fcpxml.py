"""FCPXML 1.10 of a resolved comp, for Resolve's File > Import > Timeline (Phase 0).

The structure follows Jalara's writer (crates/jalara-export/src/fcpxml.rs), whose output
Resolve 19 and later import: FCPXML 1.10, footage as `asset-clip` elements on the spine,
every time attribute in the document over one denominator that is a whole multiple of
each frame rate (Resolve compressed a timeline whose times used another), and anchored
items inside the clip they belong to. What it adds is checked against Apple's FCPXML
reference and its 1.10 DTD, not against Resolve, until the Phase 0 import is done:

- an asset's `start` is the media's start timecode, and a clip's `start` is in that same
  time base (Apple's example, an asset at `start="10s"` cut from `start="15s"`), so in
  points are written against each clip's timecode;
- constant speed is a `timeMap` of two linear points from adjusted clip time (`time`) to
  original clip time (`value`), its first and last points bounding the clip, with the
  default `frameSampling="floor"`, the rule `lungecuta` uses for a source time between frames;
  remap keys are points at the keys; a style's speed track, whose integral no FCPXML
  interpolation reproduces, is a linear point on every comp frame;
- music is an audio-only asset of the 48 kHz WAV its beats were found in, as connected
  clips below the spine (negative lanes), each anchored to the spine element that holds
  its first sample, its offset in that element's local time, with a constant gain as the
  DTD's `adjust-volume amount` in dB;
- markers sit on the spine element (comp markers) or on the clip (layer markers) at the
  frame they land on, one frame long.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .comp import MIX_RATE, Cut, Resolved
from .schema.comp import Keys
from .literals import timecode_base
from .media import Project, music_wav, original
from .schema.media import MediaRecord
from .ticks import TICKS_PER_SECOND, format_rate, parse_rate, ticks_per_frame, ticks_per_sample

VERSION = "1.10"
FCPXML_DIR = Path("cache") / "lungecuta" / "fcpxml"
_TIMECODE = re.compile(r"(\d{2}):(\d{2}):(\d{2}):(\d{2})")


@dataclass(frozen=True)
class Clock:
    """FCPXML time values over one denominator, a multiple of every frame rate's frame
    and of the audio sample in the document."""

    denominator: int

    @classmethod
    def for_document(cls, resolved: Resolved) -> Clock:
        rates = [resolved.rate, *(cut.media_rate for cut in resolved.cuts)]
        # A frame of n/d fps lasts d/n s, a whole number of 1/n s; a sample lasts 1/48000 s.
        denominator = math.lcm(*(rate.numerator for rate in rates), *([MIX_RATE] if resolved.sounds else []))
        return cls(denominator)

    def time(self, ticks: int | Fraction) -> str:
        """A timing attribute, which has to fall on the document's denominator."""
        count = Fraction(ticks) * self.denominator / TICKS_PER_SECOND
        assert count.denominator == 1, f"{ticks} ticks is not a whole number of 1/{self.denominator} s"
        return _seconds(int(count), self.denominator)

    def exact(self, ticks: int | Fraction) -> str:
        """A time that need not fall on the denominator (a time map's source value)."""
        count = Fraction(ticks) * self.denominator / TICKS_PER_SECOND
        if count.denominator == 1:
            return _seconds(int(count), self.denominator)
        value = Fraction(ticks) / TICKS_PER_SECOND
        return f"{value.numerator}/{value.denominator}s"


def _seconds(count: int, denominator: int) -> str:
    if count == 0:
        return "0s"
    if count % denominator == 0:
        return f"{count // denominator}s"
    return f"{count}/{denominator}s"


def timecode_frames(text: str, rate: Fraction) -> int:
    """Frames since 00:00:00:00 of a non-drop-frame timecode at `rate`."""
    match = _TIMECODE.fullmatch(text)
    assert match, f"{text!r} is not non-drop-frame timecode HH:MM:SS:FF"
    hours, minutes, seconds, frames = (int(group) for group in match.groups())
    base = timecode_base(rate)
    assert frames < base, f"{text}: frames run from 00 to {base - 1} at {format_rate(rate)}"
    return ((hours * 60 + minutes) * 60 + seconds) * base + frames


def timecode_text(frames: int, rate: Fraction) -> str:
    base = timecode_base(rate)
    seconds, frame = divmod(frames, base)
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{second:02d}:{frame:02d}"


def start_tc_ticks(record: MediaRecord) -> int:
    """The media's start timecode as a time: frames of the timecode at the media's rate."""
    rate = parse_rate(record.time.rate)
    start_tc = record.time.start_tc
    return 0 if start_tc is None else timecode_frames(start_tc, rate) * ticks_per_frame(rate)


def file_uri(path: Path) -> str:
    assert path.is_absolute(), f"FCPXML names media by absolute file URL, not {path}"
    return "file://" + quote(str(path), safe="/")


@dataclass
class _Element:
    """A spine element: a clip, or a gap that fills the time between clips."""

    xml: ET.Element
    offset: int  # ticks, in the sequence
    duration: int
    local_start: int  # ticks, the element's local time at its offset

    def local(self, ticks: int) -> int:
        return self.local_start + ticks - self.offset


def build(project: Project, resolved: Resolved, graphics_files: list | None = None) -> str:
    """The FCPXML document of a resolved comp; `graphics_files` pairs each graphics layer
    with its rendered ProRes 4444 file, placed as connected clips above the spine."""
    clock = Clock.for_document(resolved)
    comp, rate = resolved.comp, resolved.rate
    frame = ticks_per_frame(rate)
    sample = ticks_per_sample(MIX_RATE)

    root = ET.Element("fcpxml", version=VERSION)
    resources = ET.SubElement(root, "resources")
    width, height = comp.size
    ET.SubElement(resources, "format", id="r0", name=f"lungecuta {width}x{height} {format_rate(rate)}",
                  frameDuration=clock.time(frame), width=str(width), height=str(height))
    assets = _assets(project, resolved, resources, clock)
    graphics_files = graphics_files or []
    graphic_assets = {}
    if graphics_files:
        ET.SubElement(resources, "format", id="rg", name=f"graphics {width}x{height} {format_rate(rate)}",
                      frameDuration=clock.time(frame), width=str(width), height=str(height))
    for overlay, movie in graphics_files:
        graphic_assets[overlay.layer.id] = f"g{len(graphic_assets) + 1}"
        asset = ET.SubElement(resources, "asset", id=graphic_assets[overlay.layer.id], name=overlay.layer.id,
                              start="0s", duration=clock.time((overlay.end - overlay.first) * frame), hasVideo="1",
                              format="rg", videoSources="1", hasAudio="0")
        ET.SubElement(asset, "media-rep", kind="original-media", src=file_uri(movie.resolve()))

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="lungecuta")
    project_element = ET.SubElement(event, "project", name=comp.id)
    sequence = ET.SubElement(project_element, "sequence", format="r0", duration=clock.time(resolved.end * frame),
                             tcStart="0s", tcFormat="NDF", audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(sequence, "spine")
    elements = _spine(spine, resolved, assets, clock)

    def containing(ticks: int) -> _Element:
        for element in elements:
            if element.offset <= ticks < element.offset + element.duration:
                return element
        raise AssertionError(f"{ticks} ticks is outside the sequence")

    # Children follow the DTD's order: the time map (written with the clip), anchored
    # items, then markers.
    lane_ends: list[int] = []
    anchored: dict[str, _Element] = {}
    for sound in sorted(resolved.sounds, key=lambda sound: sound.first):
        at = sound.first * sample
        lane = next((k for k, end in enumerate(lane_ends) if end <= sound.first), len(lane_ends))
        if lane == len(lane_ends):
            lane_ends.append(sound.end)
        else:
            lane_ends[lane] = sound.end
        parent = containing(at)
        source, duration = sound.source_first * sample, (sound.end - sound.first) * sample
        xml = ET.SubElement(parent.xml, "asset-clip", ref=assets[sound.record.key], lane=str(-(lane + 1)),
                            name=sound.layer.id, offset=clock.time(parent.local(at)), start=clock.time(source),
                            duration=clock.time(duration))
        if sound.layer.gain != 0:
            ET.SubElement(xml, "adjust-volume", amount=f"{_decimal_db(sound.layer.gain)}dB")
        anchored[sound.layer.id] = _Element(xml, at, duration, source)
    clips = (element for element in elements if element.xml.tag == "asset-clip")
    by_layer = {**{cut.layer.id: element for cut, element in zip(resolved.cuts, clips)}, **anchored}
    for k, (overlay, movie) in enumerate(graphics_files):
        at = overlay.first * frame
        parent = containing(at)
        duration = (overlay.end - overlay.first) * frame
        xml = ET.SubElement(parent.xml, "asset-clip", ref=graphic_assets[overlay.layer.id], lane=str(k + 1),
                            name=overlay.layer.id, offset=clock.time(parent.local(at)), start="0s",
                            duration=clock.time(duration))
        by_layer[overlay.layer.id] = _Element(xml, at, duration, 0)
    for at in resolved.markers:
        ticks = at.frame * frame
        element = by_layer[at.layer] if at.layer in by_layer else containing(ticks)
        attributes = dict(start=clock.time(element.local(ticks)), duration=clock.time(frame), value=at.marker.name)
        if at.marker.note:
            attributes["note"] = at.marker.note
        ET.SubElement(element.xml, "marker", **attributes)

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _assets(project: Project, resolved: Resolved, resources: ET.Element, clock: Clock) -> dict[str, str]:
    """One format per footage rate and size, one asset per media key; returns key -> asset id."""
    ids: dict[str, str] = {}
    formats: dict[tuple, str] = {}
    for cut in resolved.cuts:
        record = cut.record
        if record.key in ids:
            continue
        video = record.probe.video
        media_rate = cut.media_rate
        shape = (video.rate, *video.size)
        if shape not in formats:
            formats[shape] = f"r{len(formats) + 1}"
            ET.SubElement(resources, "format", id=formats[shape], name=f"{video.size[0]}x{video.size[1]} {video.rate}",
                          frameDuration=clock.time(ticks_per_frame(media_rate)),
                          width=str(video.size[0]), height=str(video.size[1]))
        ids[record.key] = f"a{len(ids) + 1}"
        attributes = dict(id=ids[record.key], name=record.key, start=clock.time(start_tc_ticks(record)),
                          duration=clock.time(video.frames * ticks_per_frame(media_rate)), hasVideo="1",
                          format=formats[shape], videoSources="1")
        audio = record.probe.audio
        if audio:
            attributes.update(hasAudio="1", audioSources=str(len(audio)), audioChannels=str(audio[0].channels),
                              audioRate=str(audio[0].rate))
        else:
            attributes["hasAudio"] = "0"
        asset = ET.SubElement(resources, "asset", **attributes)
        ET.SubElement(asset, "media-rep", kind="original-media", src=file_uri(original(project, record)))
    for sound in resolved.sounds:
        record = sound.record
        if record.key in ids:
            continue
        ids[record.key] = f"a{len(ids) + 1}"
        wav = record.wav
        asset = ET.SubElement(resources, "asset", id=ids[record.key], name=record.key, start="0s",
                              duration=clock.time(wav.samples * ticks_per_sample(MIX_RATE)), hasVideo="0",
                              hasAudio="1", audioSources="1", audioChannels=str(wav.channels), audioRate=str(wav.rate))
        ET.SubElement(asset, "media-rep", kind="original-media", src=file_uri(music_wav(project, record).resolve()))
    return ids


def _spine(spine: ET.Element, resolved: Resolved, assets: dict[str, str], clock: Clock) -> list[_Element]:
    """Clips in comp order with gaps between them, from 0 to the comp's end."""
    frame = ticks_per_frame(resolved.rate)
    elements: list[_Element] = []

    def gap(first: int, end: int) -> None:
        offset, duration = first * frame, (end - first) * frame
        xml = ET.SubElement(spine, "gap", name="gap", offset=clock.time(offset), start=clock.time(offset),
                            duration=clock.time(duration))
        elements.append(_Element(xml, offset, duration, offset))

    at = 0
    for cut in resolved.cuts:
        if cut.first > at:
            gap(at, cut.first)
        elements.append(_clip(spine, cut, assets, clock))
        at = cut.end
    if at < resolved.end:
        gap(at, resolved.end)
    return elements


def _clip(spine: ET.Element, cut: Cut, assets: dict[str, str], clock: Clock) -> _Element:
    frame = ticks_per_frame(cut.comp_rate)
    offset, duration = cut.first * frame, (cut.end - cut.first) * frame
    timecode = start_tc_ticks(cut.record)
    # The clip's start is the first source frame it shows, on the media's frame grid.
    start = timecode + cut.source_frame(cut.first) * ticks_per_frame(cut.media_rate)
    xml = ET.SubElement(spine, "asset-clip", ref=assets[cut.record.key], name=cut.layer.id, offset=clock.time(offset),
                        start=clock.time(start), duration=clock.time(duration), srcEnable="video", tcFormat="NDF")
    sampling = {None: "floor", "nearest": "nearest-neighbor", "blend": "frame-blending"}[cut.retime]
    if cut.remap is not None:
        # A point at the clip's first frame, one at each remap key inside it, one at its end:
        # adjusted clip time (on the comp's frame grid where it can be) against source time.
        time_map = ET.SubElement(xml, "timeMap", frameSampling=sampling)
        points = [offset, *(t for t in cut.remap.times if offset < t < offset + duration), offset + duration]
        eased = dict(zip(cut.remap.times, cut.remap.eases))
        for at in points:
            value = timecode + Fraction(round(cut.remap.at(at)[0]))
            ET.SubElement(time_map, "timept", time=clock.exact(start + at - offset), value=clock.exact(value),
                          interp="smooth2" if eased.get(at) in ("in", "out", "in_out") else "linear")
    elif cut.speed_track is not None:
        # A style's speed integrates to a source time no FCPXML interpolation reproduces
        # between keys, so it is written as a point on every comp frame, linear between.
        time_map = ET.SubElement(xml, "timeMap", frameSampling=sampling)
        for index in range(cut.first, cut.end + 1):
            at = index * frame
            ET.SubElement(time_map, "timept", time=clock.exact(start + at - offset),
                          value=clock.exact(timecode + cut.source_ticks(index)), interp="linear")
    elif cut.speed != 1 or cut.retime is not None:
        time_map = ET.SubElement(xml, "timeMap", frameSampling=sampling)
        source = timecode + cut.source_ticks(cut.first)
        ET.SubElement(time_map, "timept", time=clock.time(start), value=clock.exact(source), interp="linear")
        ET.SubElement(time_map, "timept", time=clock.time(start + duration),
                      value=clock.exact(source + cut.speed * duration), interp="linear")
    return _Element(xml, offset, duration, start)


def _decimal_db(value: float) -> str:
    """A gain in dB as a plain decimal (no exponent), as the DTD's `0dB` default is written."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def cut_list(resolved: Resolved) -> list[dict[str, Any]]:
    """What `lungecuta fcpxml` prints for each cut, so a few can be checked frame by frame in
    Resolve's viewer against `lungecuta still`."""
    out = []
    for cut in resolved.cuts:
        first, last = cut.source_frames
        media_rate = cut.media_rate
        start_tc = cut.record.time.start_tc
        tc_frames = timecode_frames(start_tc, media_rate) if start_tc else 0
        out.append({
            "layer": cut.layer.id,
            "media": cut.record.key,
            "comp_frames": [cut.first, cut.end],
            "timeline_tc_in": timecode_text(cut.first, resolved.rate),
            "source_frames": [first, last],
            "source_tc_in": timecode_text(tc_frames + first, media_rate),
            "speed": float(cut.speed),
            "check": f"lungecuta still --media {cut.record.key} --at f:{first}",
        })
    return out


def sound_list(resolved: Resolved) -> list[dict[str, Any]]:
    return [
        {"layer": sound.layer.id, "media": sound.record.key, "comp_samples": [sound.first, sound.end],
         "source_sample": sound.source_first}
        for sound in resolved.sounds
    ]



def export(project: Project, name: str, out: Path | None = None) -> dict[str, Any]:
    """Write the FCPXML of comp `name` and return what `lungecuta fcpxml` prints."""
    from .comp import load_comp, resolve
    from .files import write_atomic

    from fractions import Fraction as _Fraction

    from .graphics import render as render_graphic

    resolved = resolve(project, load_comp(project, name))
    assert not resolved.comp.transitions, "transitions are not written to FCPXML yet; add them in Resolve"
    assert not resolved.nests, "precomp layers are not written to FCPXML yet"
    for overlay in resolved.overlays:
        assert overlay.top, f"graphics layer {overlay.layer.id} is under footage, which is not written to FCPXML yet"
    resolved.one_track()
    for cut in resolved.cuts:
        assert cut.placement is None, (
            f"layer {cut.layer.id} has a transform, and transforms are not written to FCPXML yet"
        )
        assert not cut.layer.masks, f"layer {cut.layer.id} has masks, which are not written to FCPXML yet"
        assert cut.layer.motion_blur is None, (
            f"layer {cut.layer.id} has motion blur, which is not written to FCPXML yet"
        )
        assert not cut.layer.grade, (
            f"layer {cut.layer.id} has a grade, which FCPXML cannot carry; grade in Resolve, "
            "or render with lungecuta render"
        )
    for sound in resolved.sounds:
        # A constant gain is the DTD's `adjust-volume amount`; the parameter that animates it
        # is not read from Apple's reference yet, so keys and fades are refused, not guessed.
        assert not isinstance(sound.layer.gain, Keys) and not sound.fade_in and not sound.fade_out, (
            f"layer {sound.layer.id} has gain keys or fades, which are not written to FCPXML yet"
        )
        assert not sound.layer.effects and not sound.layer.pan, (
            f"layer {sound.layer.id} has effects or a pan, which are not written to FCPXML yet"
        )
    if out is None:
        out = project.directory / FCPXML_DIR / f"{name}.fcpxml"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Graphics layers go to Resolve as ProRes 4444 with alpha, rendered over their visible
    # frames by `lungecuta graphic`, next to the FCPXML.
    graphics_files = []
    for overlay in resolved.overlays:
        movie = out.with_name(f"{out.stem}.{overlay.layer.id}.mov")
        movie.unlink(missing_ok=True)
        render_graphic(project.directory / overlay.layer.page, movie, size=tuple(resolved.comp.size),
                       rate=resolved.rate, frames=overlay.end - overlay.first,
                       first_time=_Fraction(overlay.first * ticks_per_frame(resolved.rate) - overlay.start,
                                            TICKS_PER_SECOND),
                       params=overlay.layer.params)
        graphics_files.append((overlay, movie))
    write_atomic(out, build(project, resolved, graphics_files))
    grid = resolved.grid
    return {
        "fcpxml": str(out),
        "comp": name,
        "rate": format_rate(resolved.rate),
        "size": list(resolved.comp.size),
        "frames": resolved.end,
        "grid": None if grid is None else {"beats": len(grid.times), "downbeats": len(grid.downbeats)},
        "cuts": cut_list(resolved),
        "sounds": sound_list(resolved),
        "markers": [{"name": at.marker.name, "frame": at.frame, "layer": at.layer} for at in resolved.markers],
        "graphics": [{"layer": overlay.layer.id, "file": str(movie), "comp_frames": [overlay.first, overlay.end]}
                     for overlay, movie in graphics_files],
    }
