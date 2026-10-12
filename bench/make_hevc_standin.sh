#!/bin/zsh
# The HEVC stand-in for XAVC HS (clip B in bench/RESULTS.md): the H.264 original
# re-encoded with VideoToolbox as 4:2:2 10-bit HEVC at 100 Mbps. Not a camera file;
# its GOP structure is the encoder's. Replace with a real XAVC HS original when one exists.
#
# Usage: bench/make_hevc_standin.sh SOURCE.MP4 OUT.mp4
set -euo pipefail
src=${1:?usage: make_hevc_standin.sh SOURCE OUT}
out=${2:?usage: make_hevc_standin.sh SOURCE OUT}
ffmpeg -v error -y -i "$src" -c:v hevc_videotoolbox -profile:v main42210 -b:v 100M -tag:v hvc1 -an "$out"
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,profile,pix_fmt,bit_rate -of csv=p=0 "$out"
