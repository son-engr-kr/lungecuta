# Editing: cuts, speed, motion blur, transitions, tracking

## Edit operations

The Edit page works on comp layers, with the operations an editor expects from a
timeline: place, trim, ripple, roll, slip, slide, split, lift, nest into a precomp.
Snapping targets are beats, downbeats, cut points, markers and keyframes, in that order
of priority. Each operation is a function in the engine that both the UI and `lc` call,
so an agent's "move every cut in the chorus onto the nearest downbeat" is the same code
path as a person dragging one.

## The beat grid

The beat map from [02-media.md](02-media.md#music-and-beats) is drawn under the
timeline: beats as ticks, downbeats as lines, bars numbered. Positions written as beat
literals (`b:64`) follow the grid if it is nudged; positions in seconds stay where they
are. A cut snapped to a beat is stored as a beat literal.

## Speed

One curve, two editors (see [03-project-format.md](03-project-format.md#time-remap)):

- the **remap graph**, After Effects' time remap: source time against output time. This is
  what the file stores, and the natural view for "land on exactly this source frame at
  this beat";
- the **speed graph**, Resolve's retime curve: its derivative, the natural view for "ramp
  up, then slow on the drop". An edit here is converted to remap keys, exactly for
  constant and linear segments and to a hundredth of a source frame for eased ones.

The source-duration check runs on every change, so a ramp that would run out of footage
is flagged while it is being drawn.

### Frames between frames

When the source time falls between two frames, the layer's `retime` setting decides:

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

## Framing

- **Crop keys** `(time, centre x, centre y, zoom)` per shot, eased, for 9:16 cuts from 16:9
  sources and for Ken Burns on stills. Past edits used smoothstep easing and a default 6 %
  push; here crop keys are ordinary transform keyframes with that as a style.
- `lc framing` draws the start and end crop windows on the source frame with a 0.1 grid,
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
- **Audit**: after render, the detector runs on every final frame and `lc face-audit`
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

Gyro stabilization stays in the existing ingest scripts (Gyroflow), and its output is imported as
derived media of the original. Resolve's own gyro stabilization only reads Blackmagic
cameras (Resolve manual, "Camera Gyro"), so there is nothing to defer to there either. A
2D stabilizer from point tracks can follow later for footage without gyro data.

## Markers

Comp markers and layer markers, with a colour, a name and a note, at any time literal.
Markers are where an agent leaves notes for the person ("hit lands here, check the
flash") and where review comments land.
