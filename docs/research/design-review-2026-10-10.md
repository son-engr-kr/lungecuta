# Design review of the first draft (2026-10-10)

An adversarial review of the first draft of `docs/design/`, made by a separate agent that
re-ran the benchmarks. Its findings are listed here with what was done about each and
whether the measurement was reproduced in this repository. Decision D9 in
`docs/design/decisions.md` summarizes the adopted changes.

## Critical findings

| finding | reproduced here | outcome |
| --- | --- | --- |
| the proxy command converted full range to limited silently, so preview and final would disagree (S-Log3 black 95 shown as about 145) | yes: mean luma 213.96 in the source, 247.48 in the proxy, 247.2 predicted | proxy written in limited range on purpose with its own decode record and a parity test |
| H.264 4:2:2 10-bit VideoToolbox decode drops frames; the "70 fps" figure timed a broken decode | yes: 693 of 705 frames (`bench/decode.sh`) | decoder chosen per clip with a framemd5 check |
| HEVC VideoToolbox decode is bit-exact and nearly free on the CPU | yes: 705 of 705 identical, 0.2 to 0.3 against 12.4 cores | hardware for HEVC |
| interactive preview latency, the risk most likely to sink the project, was never measured | n/a (design gap) | Phase 1 spike with latency and refill criteria |
| exit criteria could be passed without the feature working | n/a (design gap) | roadmap rewritten: Phase 0 baseline, hand-labelled M0 check, M2 against a real deliverable, M3 on new edits against the baseline |

## High findings

| finding | reproduced here | outcome |
| --- | --- | --- |
| the output transform does not need the GPU: OCIO scales on threads | yes: 144 ms at 4K, 10 ms at 540p on 15 threads | CPU OCIO in year one |
| a 65^3 baked LUT fails on colours while passing on grey | yes: p99 5.84, max 77.2 codes (`bench/ocio_lut_error.py`) | dense, saturated test patterns; no baked view |
| OCIO 2.6 already ships the grading operators | yes: the transforms exist in the installed wheel | grading from OCIO |
| display-referred layers composited after the view broke stack order | design inspection | top overlays after the view, the rest through the inverse view (round trip measured: median under 0.15, max about 11 codes) |
| D-Log M through `Camera Rec.709` would be tone-mapped twice | design inspection | DJI LUT, then the inverse view |
| "data levels per curve" misread the papers | source: Nick Shaw, colour-science discussion #786 | range belongs to the file; curves read code values |
| tags do change the Mac decode; untagged primaries depend on size | yes: 1-13-1 decodes with the sRGB curve; untagged SMPTE-C at 256x64, BT.709 at 720x480 and 1920x1080 | tagging and viewing sections rewritten |
| speed and remap as two stores cannot round-trip losslessly | design inspection | source time canonical, speed compiled at placement |

## Medium findings adopted

Frames to the browser without chroma subsampling and without double colour management;
proxy size following the framing; pixel-sized parameters in comp-relative units; scopes
computed once, by the engine; audio clock from `getOutputTimestamp()`; content hashes
instead of revision numbers in files; normalization before the true-peak limiter; audio
muxed once over joined chunks; downbeats from a tracker that has them; vector motion blur;
flow estimated on a display-rendered copy; LUT references that name their spaces;
`lungecuta verify` checking bias and cut times.

## Recommendations not adopted as stated

- *Cut styles, expressions, privacy, shot match, film look and generated sound from year
  one.* Kept, but moved behind the go/no-go (M4 to M9), because the user asked for them;
  the order now follows risk instead.
- *The simplest baseline is the agent writing FCPXML for Resolve.* Adopted as Phase 0, and
  as the baseline M3 must beat, rather than instead of the editor.
