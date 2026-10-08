#!/bin/sh
# Copies the Grok Bot UI sounds (blip, tick, send, chimes) from the locally installed app.
# They belong to the Grok Bot app, so they are not committed to this repo.
set -e
ASAR="/Applications/Grok Bot.app/Contents/Resources/app.asar"
TMP=$(mktemp -d)
npx -y @electron/asar extract "$ASAR" "$TMP/app"
A="$TMP/app/dist/renderer/assets"
mkdir -p public/sfx
cp "$A"/tmp-1-open-blip-*.wav public/sfx/blip.wav
cp "$A"/tmp-2-tick-*.wav public/sfx/tick.wav
cp "$A"/tmp-3-double-tick-*.wav public/sfx/send.wav
cp "$A"/tmp-4-chime-a-*.wav public/sfx/chime-a.wav
cp "$A"/tmp-5-chime-b-*.wav public/sfx/chime-b.wav
rm -rf "$TMP"
echo "copied 5 Grok Bot sounds into public/sfx"
