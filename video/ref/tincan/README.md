# TinCan demo video

30-second hackathon demo, 1920×1080 at 60 fps.

- **v2** (current source): [`tincan-demo-v2.mp4`](tincan-demo-v2.mp4). The finale leaves the drawings for real NASA imagery: Carol's building becomes one city light on Earth, strings spread city to city, and a Starship carries the string from Starbase to Mars.
- **v1**: [`tincan-demo.mp4`](tincan-demo.mp4), with an all-vector finale. Its source is commit `0a4c414`.

Built with [Remotion](https://www.remotion.dev). The Grok Bot desktop UI is recreated in React with colours sampled from the real app, so every click, message and camera move is scripted and frame-exact. Voiceover and music are generated with ElevenLabs.

## Storyboard

| Time | Scene | Voiceover |
| --- | --- | --- |
| 0–3 s | Marketplace: search "TinCan", click Add | "Your Grok Bot is brilliant. And alone." |
| 3–6 s | Create a group: Carol, Carol's Grok, Albina, Albina's Grok | "Add TinCan. Invite people and their bots." |
| 6–8 s | Tin-can GIF: the pairs connect | "Every pair, connected." |
| 8–17 s | Group chat: Carol asks Albina's Grok for the video, the bots trade screenshots, Albina says "Ship it" | "Ask anyone's bot. Bots hand work to each other. You just steer." |
| 17–20 s | Albina's Grok posts the video; the camera dives into it | "That's how we made this video. TinCan." |
| 19–23 s | Music only: zoom out from the bots to Carol and Albina, then to their two buildings, each pair joined by a tin can | — |
| 23–25 s | The buildings shrink into one light: Prague at dusk on the real Earth. Strings run from it to cities around the world | — |
| 25–28 s | A Starship lifts off from Starbase, lays the string across to Mars and lands with a flip; a reply runs back to Earth | — |
| 28–30 s | End card: "Your people. Your bots. One conversation." | — |

## Re-render

```sh
cd demo-video
npm install
sh scripts/grok-sounds.sh   # copies the UI sounds from your local Grok Bot.app (not redistributed here)
npm run dev                 # Remotion Studio: scrub and preview
npm run render              # writes out/tincan-demo.mp4, loudness-normalised to -14 LUFS
```

The finale uses WebGL, so rendering runs Chrome with the ANGLE renderer (set in `remotion.config.ts`).

To regenerate the voiceover or music, put `ELEVENLABS_API_KEY=...` in `.env` and run `npm run audio` (or `npm run audio -- vo`, `npm run audio -- music`). `node scripts/stills.mjs 1.5 9 24` renders review stills at the given seconds.

## Files

- `src/app.tsx` – Grok Bot UI replica and the scripted marketplace, group and chat interactions
- `src/world.tsx` – the 2D zoom-out (bots → people → buildings) and the handoff to the 3D finale
- `src/space.tsx` – the 3D finale (three.js): Earth with day, night-lights and cloud layers, the city strings, a modelled Starship, Mars
- `public/space/` – NASA textures for the finale
- `src/scenes.tsx` – the tin-can beat and the end card
- `src/Demo.tsx` – timeline and audio mix
- `scripts/audio.mjs` – ElevenLabs voiceover (voice "Jessica") and music (Eleven Music v2.5)
- `scripts/finish.sh` – two-pass loudness normalisation

Credits: mouse click and whoosh from remotion.media; Grok Bot UI sounds (not included) from the Grok Bot app. Space imagery, all NASA and public domain: Blue Marble Next Generation (July 2004) and Blue Marble clouds (NASA Earth Observatory / Reto Stöckli), Black Marble 2016 city lights (NASA Earth Observatory), Mars from NASA 3D Resources, Milky Way from Deep Star Maps 2020 (NASA/Goddard Scientific Visualization Studio). The Starship is a simple model built in code, not SpaceX material.
