#!/bin/sh
# An edit made the way an agent makes one, from an empty project to a verified render,
# every stage timed: the check that the commands an agent reaches for first work together.
#
# Usage: sh bench/dry_run.sh FOOTAGE MUSIC WORKDIR
#   FOOTAGE: a clip with a decided colour space (the S-Log3 sample); MUSIC: a song with a
#   steady beat. WORKDIR is emptied and holds the project and the log.
#   LC=lc runs the lc on the PATH (README.md's install) instead of `uv run lc`.
#
# The edit: a 1080p comp 32 beats long on the song, the clip placed at 0 s and split on
# every bar, two parts slipped, a punch-in style on every other part, a look on every
# part, a dissolve, a licence record; then lint, audio-check, a beat sheet, the web-h264
# render, verify and flash.
set -eu
FOOTAGE=$1; MUSIC=$2; WORK=$3
rm -rf "$WORK"; mkdir -p "$WORK"
P="$WORK/promo.lc"; LOG="$WORK/log.txt"
stage() { name=$1; shift; t0=$(date +%s); "$@" > "$WORK/$name.out"; echo "$name $(( $(date +%s) - t0 )) s" | tee -a "$LOG"; }
LC=${LC:-uv run lc}
lc() { command $LC "$@" --project "$P"; }

command $LC init "$P" > /dev/null
lc root samples "$(dirname "$FOOTAGE")" > /dev/null
lc root music "$(dirname "$MUSIC")" > /dev/null
stage import-footage lc import "$FOOTAGE" --root samples --key clip
stage import-music lc import "$MUSIC" --root music --key song
cat > "$P/styles/punch.json" <<'JSON'
{"id": "punch", "name": "a punch-in on the beat that settles over half a beat", "anchor": "beat",
 "params": {"intensity": {"default": 1.0, "min": 0, "max": 2}},
 "tracks": {"scale": [{"at": "b:0", "v": 1.12}, {"at": "b:0.5", "v": 1.0, "ease": "out"}]}}
JSON
cat > "$P/styles/warm.json" <<'JSON'
{"id": "warm", "name": "warm, lifted blacks",
 "grade": [{"op": "primary", "offset": 0.02, "contrast": 1.05}, {"op": "cdl", "slope": [1.06, 1.0, 0.92]}]}
JSON
edit() {
  lc new-comp promo --size 1920x1080 --rate 30000/1001 --end b:32 --music song
  lc op place promo clip --at 0s
  last=clip; n=2
  for at in b:4 b:8 b:12 b:16 b:20 b:24 b:28; do lc op split promo $last --at $at; last="clip.$n"; n=$((n + 1)); done
  lc op slip promo clip.2 --by f:240
  lc op slip promo clip.6 --by f:-230
  for k in 2 4 6 8; do lc op style promo clip.$k punch --at b:$(( (k - 1) * 4 )); done
  for id in clip clip.2 clip.3 clip.4 clip.5 clip.6 clip.7 clip.8; do lc op look promo $id warm; done
  lc op transition promo dissolve clip.4 clip.5 --length b:1
  lc licence song --name "a test record" --source "local sample" --checked 2026-10-13
}
stage edit edit
stage lint lc lint promo
stage audio-check lc audio-check promo
stage sheet lc sheet promo --every b:4 --columns 4 --rows 2
stage render lc render promo --preset web-h264
FILE=$(uv run python -c "import json, sys; print(json.load(open(sys.argv[1]))['file'])" "$WORK/render.out")
stage verify lc verify "$FILE" --comp promo --preset web-h264
stage flash lc flash "$FILE"
