#!/bin/zsh
# Decode, proxy and seek throughput for one camera clip, with the output checked.
#
# Usage: bench/decode.sh /path/to/clip.MP4 [workdir]
#
# Measures the workloads the engine has (docs/design/02-media.md):
#   1. sequential decode of the original, software and VideoToolbox, each checked
#      frame by frame (framemd5) against software decode: a fast decoder that drops or
#      changes frames is a failure, not a result
#   2. throughput and CPU use of each decoder that produced correct frames
#   3. building the 540p ProRes Proxy 10-bit preview file, with its range made explicit
#   4. random access into that proxy
# Writes its temporary files into workdir (default: a new temp directory).
# Pure zsh plus ffmpeg; no Python.
set -euo pipefail
zmodload zsh/datetime
setopt FORCE_FLOAT 2>/dev/null || true

src=${1:?usage: decode.sh CLIP [WORKDIR]}
work=${2:-$(mktemp -d)}
mkdir -p "$work"

packets=$(ffprobe -v error -select_streams v:0 -count_packets \
    -show_entries stream=nb_read_packets -of csv=p=0 "$src")
rate=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 "$src")
range=$(ffprobe -v error -select_streams v:0 -show_entries stream=color_range -of csv=p=0 "$src")
pix_fmt=$(ffprobe -v error -select_streams v:0 -show_entries stream=pix_fmt -of csv=p=0 "$src")
print "clip: $src"
print "packets: $packets  rate: $rate  range: $range  pix_fmt: $pix_fmt"
ffprobe -v error -select_streams v:0 \
    -show_entries stream=codec_name,profile,width,height,bit_rate -of default=nw=1 "$src"

# Decode to per-frame hashes in a fixed pixel format, so decoders can be compared.
hashes() {
    local out=$1; shift
    ffmpeg -v error "$@" -map 0:v:0 -pix_fmt "$pix_fmt" -f framemd5 - 2>/dev/null \
        | grep -v '^#' | awk -F, '{print $NF}' > "$out"
}

hashes "$work/sw.md5" -i "$src"
sw_frames=$(wc -l < "$work/sw.md5" | tr -d ' ')
print "software: $sw_frames frames"
[[ $sw_frames == $packets ]] || print "WARNING: software decode produced $sw_frames frames for $packets packets"

# Frames are kept in hardware surfaces and downloaded explicitly, so a silent software
# fallback fails instead of being compared with itself.
case $pix_fmt in
    yuv422p10le) hw_fmt=p210le ;;
    yuv420p10le) hw_fmt=p010le ;;
    yuv420p) hw_fmt=nv12 ;;
    yuv422p) hw_fmt=nv16 ;;
    *) print -u2 "no VideoToolbox download format known for $pix_fmt"; exit 1 ;;
esac
hw_log=$work/hw.log
ffmpeg -v error -hwaccel videotoolbox -hwaccel_output_format videotoolbox_vld -i "$src" \
    -map 0:v:0 -vf "hwdownload,format=$hw_fmt" -pix_fmt "$pix_fmt" -f framemd5 - 2> "$hw_log" \
    | grep -v '^#' | awk -F, '{print $NF}' > "$work/hw.md5" || true
[[ -s $hw_log ]] && print "videotoolbox reported: $(head -1 "$hw_log")"
hw_frames=$(wc -l < "$work/hw.md5" | tr -d ' ')
hw_ok=0
if cmp -s "$work/sw.md5" "$work/hw.md5"; then
    hw_ok=1
    print "videotoolbox: bit-exact against software on all $hw_frames frames"
else
    print "videotoolbox: NOT identical to software ($hw_frames frames against $sw_frames); do not use it for this codec"
fi

# Throughput and CPU use, without hashing. /usr/bin/time -p reports user and sys seconds;
# their sum over wall time is the number of cores kept busy.
timed_null() {
    local label=$1; shift
    local log=$work/time.txt
    /usr/bin/time -p ffmpeg -v error "$@" -map 0:v:0 -f null - >/dev/null 2> "$log"
    local real=$(awk '/^real/{print $2}' "$log")
    local user=$(awk '/^user/{print $2}' "$log")
    local sys=$(awk '/^sys/{print $2}' "$log")
    printf '%s: %d frames in %.1f s = %.1f fps, %.1f cores busy\n' \
        "$label" "$sw_frames" "$real" $(( sw_frames / real )) $(( (user + sys) / real ))
}
timed_null "throughput, software" -i "$src"
(( hw_ok )) && timed_null "throughput, videotoolbox" -hwaccel videotoolbox \
    -hwaccel_output_format videotoolbox_vld -i "$src" -vf "hwdownload,format=$hw_fmt"

# The proxy is written in limited range on purpose, and the conversion is stated rather
# than left to the scaler's default; its decode record must say tv (02-media.md).
case $range in
    pc) in_range=pc ;;
    tv) in_range=tv ;;
    *) print -u2 "unknown colour range '$range': this benchmark needs it stated"; exit 1 ;;
esac
t0=$EPOCHREALTIME
ffmpeg -v error -y -i "$src" \
    -vf "scale=960:540:flags=area:in_range=$in_range:out_range=tv" \
    -c:v prores_ks -profile:v 0 -pix_fmt yuv422p10le -color_range tv -an "$work/proxy.mov"
t1=$EPOCHREALTIME
printf 'proxy 540p ProRes Proxy 10-bit: %d frames in %.1f s = %.1f fps\n' \
    "$sw_frames" $(( t1 - t0 )) $(( sw_frames / (t1 - t0) ))
ls -l "$work/proxy.mov" | awk '{printf "proxy size: %.1f MB\n", $5 / 1e6}'
print "proxy range tag: $(ffprobe -v error -select_streams v:0 -show_entries stream=color_range -of csv=p=0 "$work/proxy.mov")"

# 50 seeks spread over the clip in a fixed scattered order (index k * 37 mod n).
num=${rate%/*}; den=${rate#*/}
n=$sw_frames
seeks=$(( n < 50 ? n : 50 ))
t0=$EPOCHREALTIME
for (( k = 0; k < seeks; k++ )); do
    i=$(( (k * 37) % n ))
    ffmpeg -v error -ss $(( i * den / num )) -i "$work/proxy.mov" -frames:v 1 -f null - </dev/null
done
t1=$EPOCHREALTIME
printf 'proxy random access: %d seeks in %.2f s = %.0f ms each (includes ffmpeg start)\n' \
    "$seeks" $(( t1 - t0 )) $(( (t1 - t0) / seeks * 1000 ))
