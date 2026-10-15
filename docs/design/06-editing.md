# Editing: cuts, speed, motion blur, transitions, tracking

## Edit operations

The Edit page works on comp layers, with the operations an editor expects from a
timeline: place, trim, ripple, roll, slip, slide, split, lift, nest into a precomp.
Snapping targets are beats, downbeats, cut points, markers and keyframes, in that order
of priority. Each operation is a function in the engine that both the UI and `lungecuta op` call
([09-agent-interface.md](09-agent-interface.md#commands)), so an agent's "move every cut in
the chorus onto the nearest downbeat" is the same code path as a person dragging one, and
an agent never has to ripple a timeline by editing every later layer's JSON by hand.

Which fields each operation changes follows After Effects
([03-project-format.md](03-project-format.md#layer-timing)): a move changes a layer's
`start` and carries its keyframes; a trim changes `in` or `out` and a slip changes the
source offset, and both leave keyframes where they are in the comp.

A span an edit writes keeps the unit it was written in when that unit holds the new
length exactly, and otherwise takes the unit of the time the edit was given in: a cut on a
beat of a clip whose length `place` wrote in frames is written in beats. A split's copy is
named after the layer with the next free number (`shot.2`, `shot.3`). `place` puts media
on the comp from a source in point (`--source`, its first frame by default) for a length
(`--for`, written as given), up to a position (`--to`, written as a split writes its cut,
so a layer from `0s` to `b:4` ends on the frame `b:4` lands on) or as long as it lasts up
to the comp's last frame, so one
`place` per shot builds a cut (`graphic` puts a page from `graphics/` on top the same way,
at a position for a length, with its params); it scales footage of another size to fit the comp, all of
it seen, as Resolve does by default (`--size fill` crops to fill it, `own` leaves it at
its own size), writing the scale out. When no unit holds a footage layer's new in or out
point exactly (a beat cut on a layer that starts at 0 s, before the first beat), it is
written in whole frames to the frame the time lands on, the frame the cut shows either
way; a style's anchor there is written in seconds, to 10 microseconds. A footage layer's
`source_start` may be negative, the media starting after its layer time 0 (a split's
part slipped back), as long as every frame it shows is in the clip.

Written so far (`src/lungecuta/ops/`, one module an area, and `lungecuta op OP COMP ...`): move, trim, slip, roll, ripple
delete, split and snap to beats. Amounts are signed spans (`f:-3`, `-0.5s`, `b:1`,
`b:-0.5`), a beat amount counted on the grid from where it applies. The new time is
computed in ticks and written back in the unit the field was written in, so a beat
literal stays a beat literal; a field never written (an `in` left at its default) takes
the unit of the amount given; a time the unit cannot hold exactly is an error, not a
rounding. A ripple delete moves every layer that starts after the removed one, except
the music the beat grid comes from; a split makes `ID.2` with the same start, source and
keys, as After Effects' split does; a snap puts the in and out points on the nearest
beats. Each operation validates the whole comp, writes it only if it is still the file it
read, and reports its changes in editing terms (`shot2 start moved b:4 to b:5`).
A slide moves a footage layer as a whole between its neighbours, the layer that ended
where it began ending later or earlier with it and the one that began where it ended
beginning with it, all by one amount in ticks counted from its in point, so the cuts stay
shared; a neighbour whose in point would go before its own start (where its layer time
begins) is refused rather than slipped. *(proposed: the absent-field unit and the
ripple's exception for the grid's music)*

Properties have their own operations. `key` sets a key of `position`, `scale`, `opacity`
or `remap` at a comp position, replacing one at that time or adding one (the first key
of a constant property animates it, as After Effects' stopwatch does); its time is
written as a span from the layer's start in the unit the position was given in, except
that a frame position on a layer starting between frames (on a beat) is written in beats,
since no whole number of frames spans from a beat to a frame (D15). `unkey`
deletes one; the last transform key leaves its value as the constant, and a remap keeps
two. `set` sets a constant where there are no keys. `remap` enables time remapping as
After Effects does, keys at the in and out points holding the source shown there, the
values written in seconds to five places and every frame of the layer checked to show
what it showed before (or nothing is written). `grade` replaces a layer's grade after
building it; `cut` places a list of shots, each as `place` does, in one edit, written once or not at all; `speed` plays a range at one speed ([Speed](#speed)); `style` and `unstyle` place and take off a style instance
([03-project-format.md](03-project-format.md#m2s-part-of-styles)).

### What the agent sees of the editor

The person talks to the agent about what is on their screen: "here", "this shot", "this
part, a little slower". The open editor reports what that is, once the playhead rests
(200 ms after it last moved): the comp, the playhead's frame, the selected layer, and a
range marked with I and O (in and out marks on comp frames, both included, as an
editor's in and out points; a mark set past the other starts a new range, and Alt+X, as
in Resolve, clears them). It goes to `cache/editor.json`, which the server removes when
it stops, and `lungecuta changes` prints it as `editor`: each frame placed in the comp as it
is now (its seconds, beat coordinate and the layers on screen), the selected layer with
whether it is still in the comp, and the marked range again as `from` and `to`
positions, `to` the frame after the out mark, as `lungecuta sheet --from --to` takes them. A
report is not an edit and is not journalled; a comp shortened since the report gives
the frames past its end as such rather than moving them (`src/lungecuta/view.py`).

## The beat grid

The beat map from [02-media.md](02-media.md#music-and-beats) is drawn under the
timeline: beats as ticks, downbeats as lines, bars numbered. Positions written as beat
literals (`b:64`) follow the grid if it is nudged; positions in seconds stay where they
are. A cut snapped to a beat is stored as a beat literal, and the picture cuts on the
first frame at or after the beat
([03-project-format.md](03-project-format.md#from-time-to-frames-and-samples)), which the
grid draws.

## Speed

One curve, two editors (see [03-project-format.md](03-project-format.md#time-remap)):

- the **remap graph**, After Effects' time remap: source time against output time. This is
  what the file stores, and the natural view for "land on exactly this source frame at
  this beat";
- the **speed graph**, Resolve's retime curve: its derivative, the natural view for "ramp
  up, then slow on the drop". An edit here is converted to remap keys, exactly for
  constant and linear segments and to a hundredth of a source frame for eased ones.

What is written so far is the constant segment: `speed` plays a footage layer at one
speed over a range, or over the remap segment around a position, which is what dragging
a segment of the speed graph up or down does. Keys go at both ends of the range, the
first holding the source shown there (in seconds to five places, as `remap` writes
them); the keys between them go; and every later key moves by the same source time, so
the rest of the layer keeps its speeds and only shows later or earlier source, while
the layer's in and out points stay where they are on the comp, as remap keys leave them
in After Effects (*proposed*: a speed change does not ripple the comp). A layer not
remapped is remapped first. A range is taken inside the layer's in and out points. A
new key may not fall inside an eased segment, since a named ease cannot be split into
two that keep its shape; the range must start and end on that segment's keys, or the
ease come off first. The source check refuses a speed that runs out of footage. Linear
and eased segments drawn in the speed graph are not written yet
(`src/lungecuta/ops/properties.py`, `tests/test_speed.py`).

The source-duration check runs on every change, so a ramp that would run out of footage
is flagged while it is being drawn.

### Frames between frames

When the source time falls between two frames, the layer's `retime` setting decides
(as written: absent shows the frame whose interval holds the time, the default rule of
[03-project-format.md](03-project-format.md#from-time-to-frames-and-samples); `nearest`
rounds to the closest frame; `blend` mixes the two neighbours by their distance, in
linear light (the working space's `linear` colour space), since frame interpolation
averages there (04-color.md); the FCPXML export writes them as `floor`,
`nearest-neighbor` and `frame-blending`; `flow` is not written yet):

| mode | method | use |
| --- | --- | --- |
| `nearest` | the nearest frame | stutter effects, the fastest preview |
| `blend` | linear mix of the two neighbours | cheap preview |
| `flow` | RIFE frame interpolation (PyTorch on MPS) | final-quality slow motion |

RIFE was trained on display-referred video, while the time sample here comes after the
clip grade, in the working space. So flow is estimated on a display-rendered copy of the
two neighbours and the resulting warp is applied to the linear working-space data.

`flow` has to earn its place: it is compared against Resolve's free-version Optical Flow
(Enhanced Better) on the same ramps of the user's footage, side by side, before it is
trusted. Fast motion with occlusion is where every interpolator fails, and the comparison
set must contain it.

### Repeated images

A shutter longer than a frame (1/5 s at 23.98p) makes the camera write one image for
several frames, a new one coming on a fixed period, which plays as a stepped, smeared
motion people shoot it for. A shot of such a clip that starts partway into an image shows
it for fewer frames than the rest, and the step stumbles at the cut, so it starts on a
frame where a new image begins (*proposed*). `lungecuta repeats KEY` finds them: each
frame's difference from the one before at 160x90 grey, and the period (2 to 24 frames)
and phase whose frames all differ more than every other frame does; the two sets not
overlapping is the test, with no threshold of difference, since an ordinary still shot
changes less from frame to frame than a dim new image (`bench/RESULTS.md`, "Repeated
images": seven slow clips of a real shoot separated by 2.8 to 54, twelve ordinary ones by
at most 0.59). `lungecuta lint` warns of a shot that starts into an image, for clips already
measured, and names the new-image frames either side (`src/lungecuta/repeats.py`).

## Motion blur

Footage is shot sharp on purpose (45 to 90 degree shutter, the user's shooting
practice), so blur is added in post. The layer has an output shutter angle (keyframable,
so a style can open it only during a whip). For an output frame at `t` the shutter
interval is `[t, t + shutter]`, and what the layer does during it has two parts:

- **motion inside the footage**, from the time remap: the source advances by
  `speed x shutter` (a 3x speed-up at 180 degrees covers one and a half source frames, a
  0.25x slow motion an eighth of one). This is rendered as **vector motion blur**: one
  flow field per output frame, scaled to the source interval, and the image smeared
  along it in linear light. That is the standard technique, and it costs one flow
  estimate per frame, against one RIFE run per sub-sample for averaging interpolated
  frames;
- **motion of the layer itself**, from its transform keyframes: sampled at several times
  inside the interval and averaged in linear light, which is cheap because it only
  resamples the same image.

Averaging interpolated sub-frames stays available as a quality option for shots where
vector blur visibly fails, and its cost is measured before it is offered.

Written so far (`src/lungecuta/compose.py`): the layer's own movement. A footage layer's
`motion_blur.shutter_deg` (0 to 720, a constant or keys in layer time) gives the shutter;
the layer is placed at the middles of equal parts of `[t, t + shutter]`, one part for
every pixel its corners travel on the canvas, so neighbouring samples are at most a pixel
apart and a still layer is placed once (at the shutter's middle). The path is measured on
a polyline whose segments are halved until there are at least as many as pixels it is
long. The samples are averaged in linear light, weighted by coverage, and brought back to
the working space; renders, checks, stills of a comp and the editor's preview all place
through the same function, the preview at its own scale. A render's cost grows with the
travel: a layer moving 40 pixels during the shutter is placed 40 times. `lungecuta op set` and
`lungecuta op key` take `shutter`, the inspector has a row for it and the graph editor a curve,
and FCPXML refuses it. Motion inside the footage (vector blur from flow) waits for M6.

## Transitions

A transition is an object on a cut point, Resolve style, because that is easy to place,
slide and retime. Internally it expands into keyframes and effects on the two layers,
After Effects style, so that the graph editor can open it up and a style can capture it.

The initial set is what past edits actually built (the catalogue of D4 in
[decisions.md](decisions.md), which also defines the project labels P1 to P3),
with the parameters they settled on as defaults:

| transition or hit | what it does | past values |
| --- | --- | --- |
| cut, dissolve, dip, fade | the basics | black beat between acts |
| photo flash | additive warm fill decaying as `exp(-k(t - a))` | P2: strength 0.1 to 0.55 over a run; P1: 0.08, k = 70, linear light; at most about 3 bright flashes per second |
| RGB split | channel offset decaying after the cut | P1 radial 0.004 to 0.039, decay 16/s; P2 channel shift |
| glitch | seeded horizontal slice offsets for a few frames around a cut | 50 to 80 px slices, 0.03 to 0.14 s |
| zoom punch with shake | scale snap plus a decaying shake | 1.42 to 1.52 in, snap to 1.78, shake 26 px times the kick |
| whip | crop keys with holds and a fast move, plus directional blur | whip pans across faces |
| wipes | line wipe, alternating direction, skewed brand bars | P3 promos |
| match cut | a frame fills the screen, then pulls back on a log-scale zoom | P3, a photo burst pulled back to a wall of photos |

Each one declares the space it blends in, `working` (DaVinci Intermediate, Resolve's
behaviour) or `linear`. A dissolve in linear light and one in DI look different, and a
flash added in linear light is what the P1 showreel did; the default is a choice written
in the file, not an accident of the implementation. Pixel-sized parameters (shake
amplitude, glitch slice height, grain size) are stored as fractions of comp height, so a
540p preview and a 2160p render move the same way.

Written so far (`_transitions` in `src/lungecuta/comp/resolve.py`): the basics. A `dissolve`
names the outgoing and incoming footage layers, which must meet on a cut (the first ends
on the frame the second starts on); its span is centred on the cut's frame (beats counted
on the grid either side), both layers are extended into their source handles over the
span (a layer without them is refused, naming the frame it would need), and the upper
layer's opacity ramps across it, so the blend is a composite in the working space. A
`dip` ramps the outgoing layer to black before the cut and the incoming one from black
after it, extending nothing; `fade_in` and `fade_out` ramp one layer from or to black over
a span from its in point or back from its out point. Ramps are linear in opacity over
the span's frames (D15). Only the working space is written; `linear` is refused
until it is. `lungecuta op transition COMP KIND LAYER... --length` puts one on (replacing the
same kind on the same layers) and `lungecuta op untransition` takes it off. FCPXML refuses
transitions until it writes them as its own `transition` elements.

## Framing

- **Crop keys** `(time, centre x, centre y, zoom)` per shot, eased, for 9:16 cuts from 16:9
  sources and for Ken Burns on stills. Past edits used smoothstep easing and a default 6 %
  push; here crop keys are ordinary transform keyframes with that as a style.
- `lungecuta framing` draws the start and end crop windows on the source frame with a 0.1 grid,
  the check past edits relied on before building a shot.
- Stop motion from a list of stills is a footage layer whose source is an image
  sequence with per-still crops.
- Rotation comes from the media record; the probe reads the `rotation` entry of
  `side_data_list` (there can be several entries) and allows a per-clip override, since
  some Sony files carry a wrong tag.

## Privacy: faces and plates

Past edits needed this on every piece with people in public, so it is an engine effect
rather than a script:

- **Detect** (YuNet at two scales, plus a 180-degree pass for inverted faces), **track**
  (link detections, interpolate gaps up to 40 frames, hold 45 frames past each end),
  **mask** (soft blur by default, mosaic as an option, feathered ellipse).
- **Identify**: faces of people who may be shown are unmasked by embedding similarity
  (SFace), using thresholds measured in a past project (private, not re-measured here;
  `bench/RESULTS.md`): same person 0.45 to 0.81, strangers up to 0.33, so unmask at 0.55
  and at least 0.06 above the best non-member score, faces of at least 36 px, voted per
  track. Positional overrides exist and require confirmation.
- **Audit**: after render, the detector runs on every final frame and `lungecuta face-audit`
  sheets every face it still finds. The detector misses strong profiles, so the sheet is
  looked at, not just counted.
- Licence plates: the same structure with a plate detector.

## Tracking

- **Point tracker**: the existing reframe script's method, moved into the engine:
  features seeded in a region (`goodFeaturesToTrack`), pyramidal Lucas-Kanade frame to
  frame, median displacement of surviving pairs. Output is a track: per-frame position,
  plus scale and rotation when enough points survive.
- **Tracks are data**, stored in the cache with a reference in the comp. Any property can
  follow a track (`"position": { "track": "shot3-lock" }`), which covers stabilizing on a
  point, attaching a title to a moving object and making a power window follow a face.
- **Track-lock reframing** is the existing reframe script as an effect: a crop that keeps
  the subject centred, with its hard and soft smoothing variants and its dead zone, plus a target
  aspect so a 16:9 original can become a 9:16 cut.
- A failed track stops where it fails and says at which frame; it does not extrapolate.

## Stabilization

`lungecuta stabilize KEY` runs Gyroflow, the open-source stabilizer that reads the gyro,
IBIS and lens data Sony and other cameras record, on a clip, and imports its output as
derived media of the original (`src/lungecuta/stabilize.py`, 02-media.md): a new clip in
the project's cache holding every frame of the original, frame for frame, with the
original's colour and time decisions and its sound, placed as any clip. Gyroflow is one
pinned release, fetched and checked as ffmpeg is, run as a subprocess (it is GPL-3.0), and
given a link to the original in a folder of its own, so that nothing it writes lands beside
the original. Resolve's own gyro stabilization only reads Blackmagic cameras (Resolve
manual, "Camera Gyro").

- `--mode stabilize` is Gyroflow's own stabilization; `--mode rolling-shutter` keeps the
  camera's movement and corrects only the sensor readout's skew and wobble, with one crop
  for the clip (a static zoom) so the framing does not breathe (*proposed*).
- `--preset` takes any of Gyroflow's stabilization settings and `--output` its codecs, as
  Gyroflow names them. By default the clip is lossless H.264, all intra, in the original's
  pixel format, so the only change is the warp: about 9 MB a 4K frame.
- Measured on the S-Log3 sample (`bench/RESULTS.md`, Gyroflow): Gyroflow moves code values
  by about a code (luma) and half a code (chroma), down, so a stabilized clip is not code
  for code; and with no warp asked for it still moves frames by up to 6 px, which may be the
  IBIS movement it removes. A clip recorded with the camera's distortion compensation keeps
  its lens only at Gyroflow's lens correction of 1.0; 0 adds the distortion back.
- Not written: stabilizing a part of a clip (Gyroflow's trim lands its first frame on the
  first at or after the trim's start, but where it ends was not found), and a stabilizer of
  lungecuta's own: a 2D one from point tracks for footage without gyro data, and one that
  applies the camera's orientation in the engine without an intermediate clip.

## Markers

Comp markers and layer markers, with a colour, a name and a note, at any time literal.
Markers are where an agent leaves notes for the person ("hit lands here, check the
flash") and where review comments land.
