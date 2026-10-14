"""A variable speed ramp in an FCPXML that `lungecuta fcpxml` wrote, for the Phase 0 question of
whether Resolve imports it as an editable curve or as one speed keyframe per frame.

Usage: uv run python bench/fcpxml_ramp.py IN.fcpxml OUT.fcpxml CLIP
  CLIP is the `name` of one spine `asset-clip` (the comp's layer id).

The clip keeps its offset, start and duration; its time map becomes three `smooth2`
points, the interpolation the 1.10 DTD defaults to: the middle of the clip shows the source
a quarter of the clip's duration in, and the end as much source as the clip is long, so it
runs slow and then fast and needs no source beyond what the cut used. The ramp is a probe,
not a style: nothing in lungecuta writes these points yet.
"""
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path


def seconds(text: str) -> Fraction:
    assert text.endswith("s"), f"{text!r} is not an FCPXML time"
    return Fraction(text[:-1])


def time_text(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}s" if value.denominator != 1 else f"{value.numerator}s"


def main() -> None:
    source, target, name = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    tree = ET.parse(source)
    clips = [c for c in tree.getroot().iter("asset-clip") if c.get("name") == name and c.get("lane") is None]
    assert len(clips) == 1, f"{len(clips)} spine clips are named {name!r}"
    clip = clips[0]
    start, duration = seconds(clip.get("start")), seconds(clip.get("duration"))
    old = clip.find("timeMap")
    if old is not None:
        clip.remove(old)
    time_map = ET.Element("timeMap")
    points = [(start, start), (start + duration / 2, start + duration / 4), (start + duration, start + duration)]
    for time, value in points:
        ET.SubElement(time_map, "timept", time=time_text(time), value=time_text(value), interp="smooth2")
    clip.insert(0, time_map)  # the DTD puts timing parameters first among a clip's children
    ET.indent(tree, space="  ")
    target.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
                      + ET.tostring(tree.getroot(), encoding="unicode") + "\n")
    print(f"{target}: {name} ramps through {[(float(t - start), float(v - start)) for t, v in points]} "
          "(clip seconds, source seconds from its in point)")


if __name__ == "__main__":
    main()
