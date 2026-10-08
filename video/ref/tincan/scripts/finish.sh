#!/bin/sh
# Two-pass loudness normalisation of the Remotion render to -14 LUFS / -1 dBTP (web/YouTube level).
# Usage: npm run render && sh scripts/finish.sh
set -e
IN=out/tincan-demo.raw.mp4
OUT=out/tincan-demo.mp4
M=$(ffmpeg -hide_banner -i "$IN" -vn -af loudnorm=I=-14:TP=-1:LRA=11:print_format=json -f null - 2>&1 | sed -n '/^{/,/^}/p')
get() { echo "$M" | python3 -c "import sys, json; print(json.load(sys.stdin)['$1'])"; }
ffmpeg -v error -y -i "$IN" -c:v copy \
  -af "loudnorm=I=-14:TP=-1:LRA=11:measured_I=$(get input_i):measured_TP=$(get input_tp):measured_LRA=$(get input_lra):measured_thresh=$(get input_thresh):offset=$(get target_offset):linear=true" \
  -ar 48000 -c:a aac -b:a 256k -movflags +faststart "$OUT"
echo "wrote $OUT"
