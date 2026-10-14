# Audio

The requirements come from what past edits built by hand in numpy and scipy
(catalogued in [decisions.md](decisions.md), D4). The biggest lesson from them is
structural: picture and sound kept their cue times in two places (the audio builder held
a copy of the scene timings, commented "must match"), and every version duplicated the
builder. Here audio layers live on the same comp timeline as picture, with the same time
literals and the same beat map, so a cue has exactly one definition.

## Layers, buses, master

- **Audio layers** on the comp: music, location sound (linked to its footage layer, so a
  slip moves both and it cuts on the footage's frames), voice-over, SFX, generated sounds.
  Each has `start`, in/out and source start
  ([03-project-format.md](03-project-format.md#layer-timing)), gain (dB, keyframable), pan
  (constant power), fades, an effects stack and sends. Audio times land on the nearest
  sample, not on a frame ([03-project-format.md](03-project-format.md#from-time-to-frames-and-samples)).
- **Buses**: music, dialogue, SFX, a reverb return, master. A layer sends to one bus and
  optionally to the reverb return at a send level.
- **Master chain**, in order: high-pass at 28 Hz, loudness normalization gain to the
  preset's target, then a true-peak limiter last (5 ms look-ahead, 100 to 120 ms release,
  ceiling at the preset's true peak). Normalizing after limiting would move the peaks the
  limiter had just set. Internal format float32 at 48 kHz; delivery 24-bit PCM in masters
  and AAC in web files.

### The master chain as built (`src/lungecuta/audio.py`)

Written ahead of M7 so that renders deliver at their preset's loudness; the numbers are in
[bench/RESULTS.md](../../bench/RESULTS.md#master-chain-uv-run-python-benchmaster_chainpy-wav-target).

- The high-pass is a second-order Butterworth, run forwards as a console's filter would
  (D15: the order is not given above).
- Loudness is BS.1770 integrated loudness by pyloudnorm; true peak is the largest sample
  after 4x oversampling by soxr. On the music sample both agree with ffmpeg's `ebur128`
  to its printed precision (0.1 LU, 0.1 dB).
- The limiter: the gain a sample needs is the ceiling over its true peak; the gain applied
  is the least needed over the next 5 ms, averaged over 5 ms so it is down when the peak
  arrives, recovering exponentially with a 110 ms time constant (the middle of 100 to
  120 ms). Gain that changes between samples moves the inter-sample peaks by a few
  thousandths of a dB, so the result is measured again and each further pass aims under
  the ceiling by what the previous one overshot.
- The limiter lowers the loudness of a mix whose peaks it cuts, so the result is measured
  and the gain before the limiter raised by what is missing (a secant step, since under
  limiting each dB of gain adds less than a dB of loudness) until the loudness is at the
  target to 0.01 LU (D15). A target that more gain cannot reach under the ceiling is
  an error, not a quieter file.
- A lossy codec raises the true peak: AAC at 256 kbps by 0.2 dB with the limiter idle and
  0.5 dB with it cutting 7 dB, on the music sample. The ceiling is for the delivered file
  (EBU R 128-2023, m: permitted maximum true peak levels "may be lower for different
  distribution systems and data reduction rates"), so lossy audio is encoded on its own,
  decoded, measured, and mastered again under a lower ceiling until the decoded file is
  under the preset's ceiling; that stream is muxed as it is (D15). The decoded peak
  moves by less than the ceiling (0.88 dB per dB on the Phase 0 check comp), so each new
  ceiling is a secant step aimed 0.01 dB under the preset's ceiling, the precision the
  render reports; a step aimed at the ceiling itself only approaches it from above.
- Silence stays silence: a mix with no loudness to measure is not normalized.
- The editor plays the mix through the same chain, at the `web-h264` preset's targets
  unless the request names another preset.
- Before the master, each music layer is scaled by its gain (dB, keys evaluated on every
  sample) and its fades (linear in amplitude, D15) and summed in float, so a sum
  over full scale reaches the limiter intact; a precomp adds its own mix where its layer
  is visible. Pan, buses, sends and effects are not written yet.

## Location sound

A clip's own sound, as built (`src/lungecuta/comp/resolve.py`, `media.py`,
`ops/sounds.py`). The design above has it linked to its footage layer, so a slip moves
both and it cuts on the footage's frames. It is written as part of the footage layer
itself, a `sound` field with its gain, pan and fades, as a footage layer carries its
audio in After Effects: then a move, trim, slip, split or ripple delete of the shot
carries its sound with no operation of its own, and there is no second layer whose link
could break (*proposed*: the field rather than a linked audio layer).

- It plays over the comp frames the layer shows, from the source time its first frame
  shows, landing on the nearest sample, so picture and sound cut together on the
  layer's frames (03-project-format.md, From time to frames and samples, *proposed*).
- Only a layer at 100 % plays it (*proposed*): a slowed, ramped or styled shot would need
  its sound retimed, which is not written. A shot whose sound should go on through a slow
  part is split first.
- The clip's first audio stream is decoded on first use, not at import, since few of the
  clips imported are heard: to a 48 kHz float WAV in the cache named by the original's
  hash, its sample 0 at the clip's first presented frame. A stream that starts after the
  picture by its container's start time is led in with silence by the difference, and
  one that starts before is trimmed (`tests/test_footage_sound.py`: a click at a known
  time lands on its sample, also from a stream that starts 0.1 s late). On the S-Log3 and
  HLG samples both streams start at 0.
- It is mixed, levelled, checked by `lungecuta audio-check` and drawn as a waveform on its
  footage row like any sound. Lint's music-licence and SFX rules leave it out (it is the
  person's own recording). FCPXML refuses it for now: Resolve imports the clip with its
  audio, so it is raised there.

**Ducking** (`lungecuta op duck`, `ops/sounds.py`): the music goes down by a given number of
dB while a layer plays (a shot with its own sound, a sound, a title), or over a span: four
gain keys on the music, the outer two a given ramp before and after the span, on the
nearest frames, at the level the music had there, and the inner two on the span's first
frame and the frame after it. How far and how fast are the caller's: no default is
given, since none is measured here. A gain key already inside the duck is refused rather
than merged, so neighbouring shots are ducked once, over their whole span.

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

Written so far (`src/lungecuta/effects.py`): an audio layer's `effects`, in order,
`highpass` and `lowpass` (`hz`, `order`), `band` (`low`, `high`, `order`), all Butterworth
run forwards from the layer's first sample; `reverse`; `repitch` (`semitones` or
`percent`, pitch with speed, resampled by soxr, the source it plays checked against the
file's length); and its `pan`, constant power with each channel `sqrt(1 -/+ pan)`, the
centre at 0 dB a channel, the law a past project's audio builder used. Keyed cutoffs,
ducking, the gate, soft clip and the reverb send wait for what the table leaves out (an
envelope follower's attack, a clip curve, the impulse response's details). FCPXML refuses
effects and pan.

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

Written so far: `lungecuta import FILES --root R --sound` imports sound alone as a sound, its WAV
decoded as music's is and no beat map found, so it never feeds the beat grid. An audio
layer's `anchor` (`start`, the default, `peak` or `rise`) is the point of the sound that
lands on its `start`, the sound beginning that much earlier and its pre-roll before the
comp's start cut. `peak` is its loudest sample on either channel; `rise` is the steepest
rise of its 2 ms envelope, the moving mean over 96 samples of the channels' mean
magnitude, searched over the first 0.6 s, the definition a past project's audio builder
used (`src/lungecuta/comp/resolve.py`). Music is placed by its start. `lungecuta op sfx COMP SOUND...
--on cuts|beats|downbeats|markers [--from P --to P]` places a layer on every such event
in the span, anchored there (`rise` by default), sound `(k * 7) mod n` of the n given on
event k, each as long as its sound and cut at the comp's end; seven or fourteen sounds
are refused, since the rule would repeat one. A cut is a footage layer's first frame
after the comp's start, from black included. Pools with repitch jitter, keyframes and
graphics events as events are not written yet.

## Music editing

- Music is cut on bars of its beat map into several audio layers, with short crossfades
  (12 ms default) or hard stops, so a 32-bar track can become a 50-beat edit without
  leaving the grid. The comp's grid is built from all of those layers together
  ([03-project-format.md](03-project-format.md#beat-maps)), so the edit's beats are the
  ones that play.
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
- `lungecuta audio-check` reports, for every cut in a range, the offset in milliseconds to the
  nearest onset, and whether the build actually climbs (RMS per half beat), the two
  things past edits found they had to measure rather than assume.
- Targets come from the deliver preset: -14 LUFS integrated for music promos, -16 for
  speech-led pieces, true peak at most -1 dBTP.
- Written so far (`src/lungecuta/audio_check.py`): each cut's signed offset to the nearest
  beat of the grid, and the mix's RMS (before the master chain, both channels) on every
  half beat with its least-squares slope in dB per beat. The slope is reported, not
  judged: no threshold for "the build climbs" is published or measured. Each cut's offset
  to the nearest onset of the mix comes from SuperFlux (`src/lungecuta/onsets.py`: S. Boeck
  and G. Widmer, "Maximum filter vibrato suppression for onset detection", DAFx-13, read
  2026-10-13), every parameter the paper states taken from it at its own 44.1 kHz, and
  what it leaves out (frame placement, filter edges, edge handling, the threshold delta,
  which it tunes per dataset) from its authors' reference implementation. Delta is that
  program's default, 1.1, tuned there with its own peak-picking windows, so the pairing
  with the paper's windows is unverified; on a synthetic drum track it finds every kick,
  hat and click and nothing else, each one or two frames (5 to 10 ms) early, a lead
  reported rather than corrected (`tests/test_onsets.py`). The filterbank built that way
  has 139 bands where the paper says 138.

## Preview

The engine renders the mixdown for the visible span and the browser plays it; the
picture follows its clock ([01-architecture.md](01-architecture.md#playback)). A change
re-renders only the spans whose inputs changed. The agent cannot hear, so every audio
change it makes ends with `lungecuta audio-check`; the final listening call is the person's.
