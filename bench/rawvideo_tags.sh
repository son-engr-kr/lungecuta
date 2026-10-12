#!/bin/zsh
# Which colour tags survive when frames reach ffmpeg as rawvideo on a pipe, as the
# engine's will (docs/design/08-deliver.md, Tags).
#
# Usage: bench/rawvideo_tags.sh [workdir]
#
# Each encoder is run twice: once with the -color_* output options only, once with the
# setparams filter as well, and ffprobe reports what the file says.
set -euo pipefail

work=${1:-$(mktemp -d)}
mkdir -p "$work"
ffmpeg -version | head -1

for enc in libx265 libx264 prores_ks; do
    case $enc in
        prores_ks) ext=mov; pix=yuv422p10le ;;
        libx264) ext=mp4; pix=yuv422p10le ;;
        libx265) ext=mp4; pix=yuv422p10le ;;
    esac
    for mode in options setparams; do
        vf=()
        [[ $mode == setparams ]] && vf=(-vf "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")
        out=$work/$enc-$mode.$ext
        ffmpeg -v error -f lavfi -i testsrc2=s=64x36:r=30:d=0.4 -pix_fmt rgb48le -f rawvideo - \
            | ffmpeg -v error -y -f rawvideo -pix_fmt rgb48le -s 64x36 -r 30 -i - "${vf[@]}" \
                -c:v $enc -pix_fmt $pix \
                -color_primaries bt709 -color_trc bt709 -colorspace bt709 -color_range tv "$out"
        tags=$(ffprobe -v error -select_streams v:0 \
            -show_entries stream=color_range,color_space,color_transfer,color_primaries \
            -of default=nw=1 "$out" | tr '\n' ' ')
        print "$enc, $mode: $tags"
    done
done
