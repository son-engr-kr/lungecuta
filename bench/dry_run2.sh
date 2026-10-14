#!/bin/sh
# A second edit made the way an agent makes one: a cut built one `place` per shot, a title
# page on top, a sound on every cut, a time-remap ramp on one shot; then the checks and a
# verified render, every stage timed. The sound is a click synthesized here, so the run
# needs only the footage and the music.
#
# Usage: sh bench/dry_run2.sh FOOTAGE MUSIC WORKDIR
#   FOOTAGE: a clip with a decided colour space, at least 22 s long (the S-Log3 sample);
#   MUSIC: a song with a steady beat. WORKDIR is emptied and holds the project and the log.
#   LC=lc runs the lc on the PATH (README.md's install) instead of `uv run lc`.
set -eu
FOOTAGE=$1; MUSIC=$2; WORK=$3
rm -rf "$WORK"; mkdir -p "$WORK/sounds"
P="$WORK/promo.lc"; LOG="$WORK/log.txt"
stage() { name=$1; shift; t0=$(date +%s); "$@" > "$WORK/$name.out"; echo "$name $(( $(date +%s) - t0 )) s" | tee -a "$LOG"; }
LC=${LC:-uv run lc}
lc() { command $LC "$@" --project "$P"; }

ffmpeg -v error -f lavfi -i "sine=frequency=1800:duration=0.12,afade=t=out:st=0.01:d=0.11" -ar 48000 -ac 2 \
  "$WORK/sounds/click.wav"
command $LC init "$P" > /dev/null
lc root samples "$(dirname "$FOOTAGE")" > /dev/null
lc root music "$(dirname "$MUSIC")" > /dev/null
lc root sounds "$WORK/sounds" > /dev/null
stage import-footage lc import "$FOOTAGE" --root samples --key clip
stage import-music lc import "$MUSIC" --root music --key song
stage import-sound lc import "$WORK/sounds/click.wav" --root sounds --key click --sound
cat > "$P/graphics/title.html" <<'HTML'
<!doctype html><html><head><style>html,body{margin:0;background:transparent}</style></head><body>
<canvas id="c" width="1920" height="1080"></canvas><script>
const params = JSON.parse(new URLSearchParams(location.search).get("params") || "{}");
const ctx = document.getElementById("c").getContext("2d");
window.renderAt = (t) => {
  ctx.clearRect(0, 0, 1920, 1080);
  ctx.globalAlpha = Math.min(1, t / 0.5);
  ctx.fillStyle = "#ffffff";
  ctx.font = "bold 140px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(params.text || "", 960, 580);
};
window.ready = true;
</script></body></html>
HTML
edit() {
  lc new-comp promo --size 1920x1080 --rate 30000/1001 --end b:16 --music song
  lc op place promo clip --at 0s --to b:4 --source 1s
  lc op place promo clip --at b:4 --for b:4 --source 6s
  lc op place promo clip --at b:8 --for b:4 --source 12s
  lc op place promo clip --at b:12 --for b:4 --source 18s
  lc op remap promo clip.3
  lc op key promo clip.3 remap --at b:10 --value 13.5s --ease in_out
  lc op graphic promo graphics/title.html --at 0s --to b:4 --params '{"text": "LUNGECUTA"}'
  lc op sfx promo click --on cuts --gain -6
  lc licence song --name "a test record" --source "local sample" --checked 2026-10-13
  lc licence click --name "synthesized" --source "bench/dry_run2.sh" --checked 2026-10-13
}
stage edit edit
stage lint lc lint promo
stage audio-check lc audio-check promo
stage sheet lc sheet promo --every b:1 --columns 4 --rows 4
stage still lc still promo --at b:8
stage render lc render promo --preset web-h264
FILE=$(uv run python -c "import json, sys; print(json.load(open(sys.argv[1]))['file'])" "$WORK/render.out")
stage verify lc verify "$FILE" --comp promo --preset web-h264
stage flash lc flash "$FILE"
