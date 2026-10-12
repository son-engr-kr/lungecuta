# Audio

The requirements come from what past edits built by hand in numpy and scipy
(catalogued in [decisions.md](decisions.md), D4). The biggest lesson from them is
structural: picture and sound kept their cue times in two places (the audio builder held
a copy of the scene timings, commented "must match"), and every version duplicated the
builder. Here audio layers live on the same comp timeline as picture, with the same time
literals and the same beat map, so a cue has exactly one definition.

## Layers, buses, master

- **Audio layers** on the comp: music, location sound (linked to its footage layer, so a
  slip moves both), voice-over, SFX, generated sounds. Each has in/out, source in point,
  gain (dB, keyframable), pan (constant power), fades, an effects stack and sends.
- **Buses**: music, dialogue, SFX, a reverb return, master. A layer sends to one bus and
  optionally to the reverb return at a send level.
- **Master chain**, in order: high-pass at 28 Hz, loudness normalization gain to the
  preset's target, then a true-peak limiter last (5 ms look-ahead, 100 to 120 ms release,
  ceiling at the preset's true peak). Normalizing after limiting would move the peaks the
  limiter had just set. Internal format float32 at 48 kHz; delivery 24-bit PCM in masters
  and AAC in web files.

## Effects

The set past edits needed, each a parameterized engine effect:

| effect | parameters | past use |
| --- | --- | --- |
| high-pass, low-pass, band-limit (Butterworth) | cutoff(s), order, keyframable cutoff | VO band-limited to 150-7000 Hz; filter sweeps 30 to 400 Hz in builds |
| gain ramp | keyframes | builds of -7 to +10 dB |
| reverb send | send level; return uses convolution with an IR (synthetic: exponentially decaying noise, low-passed at 5 kHz, 2.0 to 2.2 s) | voice "sitting inside the music" |
| sidechain duck | key source (layer or bus), depth, attack, recovery | music ducked 45 to 55 % per kick, 70 to 90 ms recovery |
| span duck | ranges, depth, ramp time | music down 3 to 10 dB under speech, 0.1 s ramps |
| gate | threshold, hold | true silence for a black beat |
| reverse, repitch | semitones or percent | reversed boom swells; shutter clicks repitched by up to 3 % |
| soft clip | drive | master warmth in past builds |

## SFX placement

- **Anchor.** An SFX is placed by a chosen point in the sample, not by its first
  sample: `start`, `peak`, or `rise` (the steepest rise of a 2 ms envelope). Past edits
  settled on `rise` because impact files carry a pre-swell. Pre-roll before time 0 of the
  comp is cut.
- **Pools.** A pool is a set of samples with a rotation rule and a repitch jitter, so
  "a different shutter on every cut" is one layer, not twenty. The rule used before,
  index `(k * 7) mod n`, is the default.
- **Placement on events.** An SFX layer can be placed on every event of another layer:
  every cut in a range, every keyframe of a property, or every named event a graphics
  layer emits (a typed character, a flash). This replaces the old `cue()` file that the
  renderer wrote and the audio builder read back.

## Music editing

- Music is cut on bars of its beat map, with short crossfades (12 ms default) or hard
  stops, so a 32-bar track can become a 50-beat edit without leaving the grid.
- librosa's tempo estimate is quantized; the beat map re-derives tempo from the mean beat
  interval, which is what past edits had to do by hand. Downbeats come from a tracker
  that has them (Beat This! or madmom, [02-media.md](02-media.md#music-and-beats)).

## Generated sound

- **Synth voices** for punctuation: kick, hat, clap, blip, sub drop, riser, whoosh, as
  generated audio layers with parameters (the P3 promo synthesized all of its music this
  way).
- **TTS** as a generated voice-over layer (kokoro-onnx was used before), seeded so the
  same line always renders the same audio, with its duration known before placement.

## Analysis and checks

- Beat map, onsets, spectrogram, RMS per half beat, loudness per section (BS.1770 via
  pyloudnorm in the engine, ffmpeg `ebur128` for the post-render check).
- `lc audio-check` reports, for every cut in a range, the offset in milliseconds to the
  nearest onset, and whether the build actually climbs (RMS per half beat), the two
  things past edits found they had to measure rather than assume.
- Targets come from the deliver preset: -14 LUFS integrated for music promos, -16 for
  speech-led pieces, true peak at most -1 dBTP.

## Preview

The engine renders the mixdown for the visible span and the browser plays it; the
picture follows its clock ([01-architecture.md](01-architecture.md#playback)). A change
re-renders only the spans whose inputs changed. The agent cannot hear, so every audio
change it makes ends with `lc audio-check`; the final listening call is the person's.
