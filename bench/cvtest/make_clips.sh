#!/bin/zsh
# Grey-patch test clips for the macOS colour-tag measurements
# (docs/research/color-sources.md, item 8).
#
# Usage: bench/cvtest/make_clips.sh [OUTDIR]
#
# Writes one raw frame, ramp.yuv: 256x64 yuv420p, four vertical patches of 64 columns
# each, limited-range luma for code values 0.10, 0.18, 0.50, 0.80, chroma 128.
# It is then encoded with different H.273 colour tags (primaries-transfer-matrix):
#   ProRes 422 HQ (prores_ks profile 3), yuv422p10le, .mov: 1-1-1, 1-2-1, 1-13-1, 1-4-1
#   H.264 (libx264 crf 1), yuv420p, .mp4:                   1-1-1, 1-2-1, 2-2-2
#   H.264 2-2-2 scaled to 720x480 and 1920x1080 (nearest neighbour), for default primaries
# All clips are limited range ("tv"), 24 fps, and hold the single frame in ramp.yuv.
# OUTDIR defaults to a new temp directory.
set -euo pipefail

out=${1:-$(mktemp -d)}
mkdir -p "$out"

# Luma code = round(16 + 219*v) for v = 0.10, 0.18, 0.50, 0.80 gives 38, 55, 126, 191
# (octal 046, 067, 176, 277). Chroma 128 is octal 200.
patch() { repeat 64 printf "\\$1" }
row() { patch 046; patch 067; patch 176; patch 277 }
{
  repeat 64 row
  repeat $(( 128 * 32 * 2 )) printf '\200'
} > "$out/ramp.yuv"

# Same bytes as the frame behind the numbers in color-sources.md.
[[ $(md5 -q "$out/ramp.yuv") == a409b8c9390562520b05284c9ff34f1e ]] || {
  print -u2 "ramp.yuv does not match the reference frame"; exit 1; }

raw=(-f rawvideo -pix_fmt yuv420p -s 256x64 -r 24 -i "$out/ramp.yuv" -frames:v 24)

# ProRes: label:transfer pairs, primaries and matrix fixed at BT.709.
for t in 111:bt709 121:unknown 1131:iec61966-2-1 141:bt470m; do
  tag=${t%%:*}; trc=${t#*:}
  ffmpeg -loglevel error -y "${raw[@]}" \
    -vf "format=yuv422p10le,setparams=range=tv:color_primaries=bt709:color_trc=${trc}:colorspace=bt709" \
    -c:v prores_ks -profile:v 3 -movflags +write_colr "$out/ramp_${tag}.mov"
done

# H.264: label:primaries:transfer:matrix.
for t in 111:bt709:bt709:bt709 121:bt709:unknown:bt709 222:unknown:unknown:unknown; do
  parts=(${(s.:.)t})
  ffmpeg -loglevel error -y "${raw[@]}" \
    -vf "setparams=range=tv:color_primaries=${parts[2]}:color_trc=${parts[3]}:colorspace=${parts[4]}" \
    -c:v libx264 -crf 1 -pix_fmt yuv420p "$out/h264_${parts[1]}.mp4"
done

# Untagged H.264 at larger sizes: macOS picks default primaries by frame size
# (SMPTE-C for 256x64, BT.709 for 720x480 and 1920x1080, measured on 26.4.1).
for size in 720x480 1920x1080; do
  ffmpeg -loglevel error -y "${raw[@]}" \
    -vf "scale=${size/x/:}:flags=neighbor,setparams=range=tv:color_primaries=unknown:color_trc=unknown:colorspace=unknown" \
    -c:v libx264 -crf 1 -pix_fmt yuv420p "$out/h264_222_${size#*x}.mp4"
done

for f in "$out"/ramp_*.mov "$out"/h264_*.mp4; do
  print "${f:t}: $(ffprobe -v error -select_streams v \
    -show_entries stream=color_range,color_primaries,color_transfer,color_space -of csv=p=0 "$f")"
done
print "$out"
