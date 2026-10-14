# Phase 0: what to check in Resolve

The Phase 0 questions an agent cannot answer, because they need Resolve's GUI
(File > Import > Timeline; scripting is Studio-only in 21.1). Each step says what to
produce with `lungecuta`, what to do in Resolve, and what to report back. Results go into
`docs/research/resolve-notes.md` with the Resolve version and the date.

## Before: what to produce

1. A project with footage and music imported (`lungecuta init`, `lungecuta root`, `lungecuta import`), a comp
   in `comps/` that cuts the footage on beats, with one clip at constant speed other than
   100 % and a few markers ([03-project-format.md](../design/03-project-format.md#phase-0s-part-of-the-comp-format)),
   then `lungecuta validate`.
2. `lungecuta fcpxml COMP`: the FCPXML and, printed, each cut's comp frames, timeline timecode,
   source frames and source timecode, with the `lungecuta still` command for its first frame.
3. `uv run python bench/fcpxml_ramp.py cache/lungecuta/fcpxml/COMP.fcpxml ramp.fcpxml LAYER`: the
   same timeline with one clip at 100 % (LAYER) turned into a variable speed ramp that
   uses no more source than the cut did.
4. `lungecuta patch-card DIR --size WxH --rate R` at the timeline's size and rate: `card.mov`,
   ProRes 4444 with alpha, and `card.json`.

## In Resolve (21.0.4 free)

1. **Import the FCPXML** (File > Import > Timeline), with the defaults, into a project
   whose colour management is DaVinci YRGB Color Managed, DaVinci Wide Gamut /
   DaVinci Intermediate timeline, Rec.709 Gamma 2.4 output.
   - Did it import without warnings? Note any.
   - **Markers:** do the comp and layer markers appear, on the timeline or on the clips,
     with their names and notes?
   - **Constant speed:** does the half-speed clip show 50 % in the Inspector?
   - **Music:** is the music clip there, from the WAV, with its waveform in place? Does
     Resolve accept the 32-bit float WAV?
2. **In points:** for two or three cuts, park on the cut's first frame (the printed
   `timeline_tc_in`) and compare the viewer with `lungecuta still --media KEY --at f:N` (the
   printed `check`). Also read the source timecode Resolve shows for that frame against
   the printed `source_tc_in`. Same frame, or off by how many?
3. **Cuts:** do the cut points sit on the printed comp frames (timeline timecode)?
4. **Variable speed:** import `ramp.fcpxml`. Is the ramp an editable speed curve
   (Retime Curve) or one keyframe per frame, as the manual says ("About Speed Effects")?
5. **Graphics:** put `card.mov` on a track above footage at full frame, unscaled, export
   (Deliver: H.264 or ProRes 422 HQ, Rec.709, the timeline's size) a few seconds over it,
   then `lungecuta patches EXPORT --card DIR/card.json --at f:N` for a frame inside the card.
   Report `largest_difference` and the patches that moved: if the greys and colours come
   back within a code or two, Resolve passed the graphic's code values through; if the
   light patches moved down and the darks up, it tone-mapped them.
   - Also note the clip's Alpha Mode in Resolve's Clip Attributes, and the input colour
     space Resolve gave `card.mov`.
6. **Music gain** (only if the comp's music layer has a constant `gain`, which the exporter
   writes as `adjust-volume amount="-4.5dB"`): does the music clip arrive with that level
   on its volume control in the Inspector, and does the timeline's level change by it?
7. **An alpha render** (`lungecuta render COMP --preset prores-4444-alpha`): put it over footage.
   Are the edges clean (straight alpha read as straight), or dark-fringed (read as
   premultiplied)? Note the Alpha Mode Resolve gave it.

## What to report

For each numbered step: what was seen, with screenshots where a curve or a dialog
matters, and the `lungecuta patches` JSON. Nothing has to be judged; the agent writes the
results up and adjusts the exporter.
