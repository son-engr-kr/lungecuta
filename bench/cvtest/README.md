# cvtest: how macOS interprets video colour tags

Small probes behind item 8 of `docs/research/color-sources.md`. Measured on macOS 26.4.1 (25E253).

Make the clips, then build each probe outside the repo so no binaries land here:

```sh
clips=$(bench/cvtest/make_clips.sh | tail -1)        # or: bench/cvtest/make_clips.sh DIR
build=$(mktemp -d)
for s in cv cg rd pl k; do swiftc -O bench/cvtest/$s.swift -o $build/$s; done
```

| run | prints |
| --- | --- |
| `$build/cv $build` | For tag sets 1-1-1, 1-2-1 (no transfer key), 1-13-1, gamma 2.4 and gamma 2.2: the colour space CoreVideo builds from the attachments (name, or "nil colorspace"), and what codes 0.10 / 0.18 / 0.50 / 0.80 convert to in linear sRGB. It saves each ICC profile as `$build/<tag>.icc`. Needs no clips. |
| `$build/cg $build` | The same four codes decoded through the named CGColorSpace sRGB, Display P3, ITU-R 709 and ITU-R 2020. It saves `$build/named_<name>.icc`. |
| `$build/rd $clips/*.mov $clips/*.mp4` | For each clip, AVAssetReader output (RGBA half float, BT.709 primaries) with a linear and then an sRGB transfer: the green value at each of the four patches. |
| `$build/pl $clips/*.mov $clips/*.mp4` | For each clip, the first AVPlayerItemVideoOutput buffer: the transfer and primaries the player attaches, the colour space name built from them, and the unconverted patch values. |
| `$build/k` | The CoreVideo key and identifier strings for Apple Log and Apple Log 2. |
| `uv run bench/cvtest/icc_trc.py FILE.icc ...` | ICC version, tag list, description, the R/G/B tone curve (pure gamma, parametric parameters, or table length), and the primaries, white point and adaptation matrix. |

The patches hold luma codes 38, 55, 126 and 191, so the 0.18 patch is really (55 − 16)/219 = 0.1781. A pure gamma 1.9609375 decodes it to about 0.0339; `cv`, which converts an exact 0.18, gives 0.0346.

`pl` on the untagged clips shows the frame-size dependence: `h264_222.mp4` (256x64) gets
`primaries=SMPTE_C`, while `h264_222_480.mp4` and `h264_222_1080.mp4` get
`primaries=ITU_R_709_2`. (Their patch values differ only because `pl` samples fixed pixel
positions, which land on different patches after scaling.)

