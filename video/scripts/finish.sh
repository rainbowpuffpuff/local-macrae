#!/bin/sh
# Two-pass loudness normalisation of the Remotion render to -14 LUFS / -1 dBTP (web/YouTube level).
# Usage: npm run render  (runs this after `remotion render`)
set -e
IN=out/macrae-demo.raw.mp4
OUT=out/macrae-demo.mp4
if ! ffmpeg -v error -i "$IN" -map 0:a:0 -t 0.1 -f null - 2>/dev/null; then
  # rendered before `npm run audio`: no audio stream to normalise
  cp "$IN" "$OUT"
  echo "wrote $OUT (no audio track)"
  exit 0
fi
M=$(ffmpeg -hide_banner -i "$IN" -vn -af loudnorm=I=-14:TP=-1:LRA=11:print_format=json -f null - 2>&1 | sed -n '/^{/,/^}/p')
get() { echo "$M" | python3 -c "import sys, json; print(json.load(sys.stdin)['$1'])"; }
if [ "$(get input_i)" = "-inf" ]; then
  cp "$IN" "$OUT"
  echo "wrote $OUT (silent audio track, not normalised)"
  exit 0
fi
ffmpeg -v error -y -i "$IN" -c:v copy \
  -af "loudnorm=I=-14:TP=-1:LRA=11:measured_I=$(get input_i):measured_TP=$(get input_tp):measured_LRA=$(get input_lra):measured_thresh=$(get input_thresh):offset=$(get target_offset):linear=true" \
  -ar 48000 -c:a aac -b:a 256k -movflags +faststart "$OUT"
echo "wrote $OUT"
