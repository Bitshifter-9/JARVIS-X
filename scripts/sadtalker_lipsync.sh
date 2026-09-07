#!/bin/bash
# Adapter between the pipeline's lip-sync contract ({video} {audio} {out}) and
# SadTalker, which animates a still image: take the hero video's first frame,
# drive it with the cloned audio, hand back one mp4.
#
# Wired via: JARVIS_YOUTUBE_LIPSYNC_CMD=bash scripts/sadtalker_lipsync.sh {video} {audio} {out}
set -e
VIDEO="$1"; AUDIO="$2"; OUT="$3"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ST="$ROOT/vendor/SadTalker"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ffmpeg -y -v error -i "$VIDEO" -frames:v 1 "$WORK/face.png"
cd "$ST"
# ponytail: 256px + crop preprocess, no enhancer — minutes not hours on MPS/CPU.
# Raise --size to 512 and add "--enhancer gfpgan" when quality matters more than time.
.venv/bin/python inference.py \
  --driven_audio "$AUDIO" \
  --source_image "$WORK/face.png" \
  --result_dir "$WORK/out" \
  --still --preprocess crop --size 256
mv "$(ls -t "$WORK"/out/*.mp4 2>/dev/null | head -1)" "$OUT"
