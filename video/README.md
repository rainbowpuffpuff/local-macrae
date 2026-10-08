# Macrae demo video

30-second demo, 1920×1080 at 60 fps (1800 frames). The full spec is [`STORYBOARD.md`](STORYBOARD.md).

Built with [Remotion](https://www.remotion.dev), like the tin-can video in `ref/tincan`. Everything is drawn in React, so every click, timeline row and camera move is scripted to the frame:

- **The logo is vectors, not the PNG.** The orbit system (4 tilted ellipses, the three-armed particle chain, the core and its ring, stars) is rebuilt in SVG from measurements of `ref/logo.png`: ellipse fits (−28°, axes 67.6/118.2/168.9/219.6 px, ratio 0.574), particle centres and radii, and sampled colours. The `macrae` wordmark is traced from the PNG to an SVG path (`src/wordmark.ts`), not set in a lookalike font. That is why the 60× dive stays sharp.
- **The app is a React replica.** Colours, radii and type come from `web/style.css` (light theme), markup and strings from `web/index.html` and the `web/app.js` templates, and layout from `ref/frontend/*.png`. Inter, JetBrains Mono and a Noto Color Emoji subset are bundled in `public/fonts`, so 📄 🔎 🧮 ✍ ✅ look the same on every render machine.
- **Voiceover and music come from ElevenLabs** (`scripts/audio.mjs`). The composition renders without any audio files: it checks `public/` and skips whatever is missing.

## Storyboard

| Time | Shot | Voiceover |
| --- | --- | --- |
| 0.00–2.60 | **1 · Logo**: the orbits draw on, the core ignites, particles pop along the chain, the wordmark rises | 0.40 "I'm Jarvis." · 1.45 "I've read the Jungwirth group's papers." |
| 2.60–4.20 | **2 · Flow**: Kepler shear (ω ∝ a^-3/2) winds the chain into a spiral, the rings breathe and precess, connectors leave ghost trails, the wordmark sinks | (music rises) |
| 4.20–5.60 | **3 · Dive**: exponential push z 1.5 → 60 into the core. Particles become radial streaks, the core goes amber → white, and the landing page grows inside the core's circle | (music hits on the reveal) |
| 5.60–7.40 | **4 · Landing**: the health pill goes online, the task cards replace their skeletons, the cursor clicks Talk | 5.90 "Ask me anything about them." |
| 7.40–9.80 | **5 · Talk to Jarvis**: connecting → live. "Why does the group scale charges?" is typed as a caption while the orb follows a mic level, then "Searching the papers…" | — |
| 9.80–15.20 | **6 · Answer with citations**: the orb speaks, the answer streams with ① ② chips, two source cards pop, and card [1] flashes | 9.80 "Scaled charges capture electronic polarization that standard force fields miss." · 13.50 "Kirby and Jungwirth, 2019." |
| 15.20–17.40 | **7 · Click a task**: pan to the tasks, hover-lift, click "Run it" on *Small calculation on Modal*, then push into the card through a white dip | 15.55 "Pick a task, and I run it on Modal." |
| 17.40–24.50 | **8 · Trace fills in**: 11 rows land on schedule, counters bump, papers drop into "Papers it used", the page follow-scrolls, and a "sped up" badge shows | 18.60 "You see every paper I read…" · 21.00 "…and every number I calculate." |
| 24.50–26.60 | **9 · Done**: Running → Done, "reward 1", the end banner, and a pull-back to the whole run | 24.80 "Done. All of it, traced." |
| 26.60–30.00 | **10 · End card**: the page closes into a disc of light and the camera pulls out of the core. The system unwinds back to the exact original logo at 27.2. Then `macrae`, "Ask the papers. Watch the work.", "Jungwirth group · IOCB Prague" | 27.75 "Macrae." |

## Commands

```sh
cd video
npm install
npm run audio      # voiceover, music and sound effects into public/ (needs ELEVENLABS_API_KEY in .env)
npm run render     # writes out/macrae-demo.mp4, loudness-normalised to -14 LUFS / -1 dBTP
```

- `npm run dev` opens Remotion Studio, where you can scrub and preview.
- `npm run audio -- vo`, `-- music` or `-- sfx` regenerates one part. Copy `.env.example` to `.env` first. The voice is "Jarvis (video)", id `ELEVENLABS_VOICE_ID` (default `wNrcUqZN35sbTl2vAQU2`). By default the TTS text respells "Jungwirth" as "Yoong-virt" and says 2019 as "twenty nineteen". Set `RESPELL=0` to send the real spelling. The script prints clip lengths and warns about any line that runs past its target (the fit rule: shorten the line, don't speed it up).
- To make the question audible, drop a real recording into `public/vo/vo-user.mp3` and it plays at 7.6 s. The script never generates a second voice.
- `node scripts/stills.mjs` renders the review stills from the storyboard (1, 3.5, 5, 6.8, 8.6, 13.9, 16.5, 21.5, 25.2, 28.5 s) into `out/stills/`. You can also pass your own seconds; `SCALE=1` gives full size.
- `scripts/finish.sh` (run by `npm run render`) needs `ffmpeg` and `python3` on the PATH. If the render has no audio yet, it just copies the file.

## Before the final render: placeholders

The storyboard marks these as inputs that are still needed. They live as constants at the top of `src/app.tsx`:

1. **The trace** (`TRACE`, `FINISHED_S`, `RUN_ID`): run *Small calculation on Modal* once, save `GET /api/runs/{id}/events`, and copy titles, details and the duration verbatim. Only each row's `at` (video time) is ours. Row offsets and the timer are derived from `FINISHED_S`, and so is the badge: it reads "sped up 17×" (112 s shown in 6.55 s), not the storyboard's placeholder 20×.
2. **Citation quotes and pages** (`CITES`): take them from `python -m rag search` once both PDFs are indexed, and check that the answer is backed by them.
3. **Paper count** in the health pill (`PAPERS`, now 479 = entries in `data/group_publications.json`): take it from `GET /api/health`.
4. **Agent label** (`AGENT = "Jarvis"`): the app currently says "Agent".
5. **Task titles** (`TASKS`): these are the owner's video titles; `tasks/tasks.json` has different ones.

## Files

- `src/logo.tsx`: the orbit system. `warp(t)` is the flow (Kepler phase, breathing ε, precession) and returns the exact starting state at 0 s and 27.2 s. `dive(z)` sets the core colour, dot size and streak length. It also holds the reveal/pull-out clip with the page inside the core, and the end card.
- `src/wordmark.ts`: the traced `macrae` path.
- `src/app.tsx`: the Macrae UI replica plus the scripted timeline `T`, the trace `TRACE`, the camera keys and the cursor path.
- `src/Demo.tsx`: shot switching and the audio mix (VO, SFX, music volume envelope).
- `src/lib.ts`: tin-can helpers (`ramp`, `pop`, `typed`, `camAt`) and font loading.
- `scripts/audio.mjs`, `scripts/finish.sh`, `scripts/stills.mjs`: as above.

Credits: mouse click and whoosh from remotion.media; other sound effects, voice and music by ElevenLabs. Fonts: Inter (rsms), JetBrains Mono and Noto Color Emoji (Google), all SIL OFL; licences are in `public/fonts`.
