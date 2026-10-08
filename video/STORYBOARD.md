# Macrae demo video: storyboard

30 s, 1920×1080, 60 fps (1800 frames). Built like the tin-can video (`ref/tincan`). Everything is a Remotion
composition drawn in React, so every click, row and camera move is scripted to the frame. Voiceover and music
come from ElevenLabs.

**Idea in one line:** the logo is a small Hamiltonian system. We fly into its glowing centre and find the
app inside. Jarvis answers a question with citations, runs a task on Modal while the trace fills in, and
then we pull back out into the orbits.

**Voice:** the narrator is Jarvis, speaking as itself in the "Jarvis (video)" voice. During the answer beat
the narration is also the app's spoken reply, and the orb shows it speaking.

---

## Storyboard

Times are seconds from frame 0. "z" is camera zoom with the `camAt` keyframes from `ref/tincan/src/lib.ts`
(exponential zoom, centre follows the zoom's fixed point). On app shots, x/y are in page px on a 1920×1080
page, the same scale as `ref/frontend/*.png`.

| Time | Shot | Motion / camera | On-screen text | Voiceover |
| --- | --- | --- | --- | --- |
| 0.00–2.60 | **1 · Logo** Navy field with the orbit system: 4 tilted ellipses, the particle chain, the glowing amber centre, a few stars. The orbit centre sits at (960, 380). | No black lead-in. The orbits draw on with stroke-dashoffset (0–0.6). The centre ignites at 0.1. Particles pop in along the chain, inner to outer (0.2–0.9). The wordmark rises 14 px and fades in (0.5–0.9). Particles already orbit slowly, and the centre glow breathes at 0.5 Hz. Camera z 1.00 → 1.04, centred on the centre. | `macrae` (wordmark, as in logo.png) | 0.40 "I'm Jarvis." · 1.45 "I've read the Jungwirth group's papers." |
| 2.60–4.20 | **2 · Flow** The system starts to move as a Hamiltonian flow. | Angular speed ∝ a^-3/2: the inner amber cluster sweeps fastest, so the connector chain shears into a winding spiral. The ellipses breathe, r(θ) = a·(1 + ε·sin(3θ − Ωt)) with ε ramping 0 → 0.04, and precess about −8°. Connector lines thin to hairlines with 2–3 fading ghost copies as trails (no motion blur). The wordmark sinks and fades (3.2–3.7). Camera z 1.04 → 1.5 into the centre. | (wordmark fading) | — (music rises) |
| 4.20–5.60 | **3 · Dive into the centre → reveal** | Exponential push z 1.5 → 60 on the centre, eased in. The rings sweep out past the frame. Particles turn into short radial streaks whose length follows zoom speed. The core gradient goes amber `#FFB347` → `#FFD99A` → white. From 4.9 the landing page shows inside the core, clipped to the core's circle and scaled to its diameter (opacity 0 → 1, 4.9–5.2), with a thin warm rim glow on the clip edge. At 5.35 the clip passes the frame corners (r > 1100 px). The navy is gone and the rim fades by 5.6. The page keeps growing to z 1.0, so the push carries on into the page. | — | — (music hits on the reveal, ≈ 5.4) |
| 5.60–7.40 | **4 · Landing** The real landing page, light theme. | Hold z 1.0 (5.6–6.4), then push to z 1.22 on (760, 470) by 7.0. 5.9: the health pill goes from "Checking…" to online with a green LED. 6.1: the two task cards replace their skeleton shimmer (pop, 0.12 s stagger). 6.2: the cursor enters bottom right and glides to the Talk button (330, 440) by 6.85. Click at 7.0 (press 6.95–7.10, scale 0.97). | "Ask the Jungwirth group's papers." · "Talk to an agent that has read them, and watch it work." · pill "Online · N papers · Modal ready" · task cards **Methods card for a paper** / **Small calculation on Modal** | 5.90 "Ask me anything about them." |
| 7.40–9.80 | **5 · Talk to Jarvis** Voice goes live and the question appears as a live caption. | Talk button: connecting (pulsing ring, "Connecting…", 7.05–7.35), then live (white surface, accent orb, "End conversation / Tap to hang up"). The header brand-mark wave pulses. The "Try asking" chips fade (7.5). A user bubble types word by word (7.6–9.0) while the orb ring scales with a fake mic level timed to the words. 9.1: tool line with spinner "Searching the papers…". Camera z 1.22 → 1.45 on (730, 560), framing the transcript. | Status "Listening…" · user bubble "Why does the group scale charges?" · "Searching the papers…" | — (user is silent; music lifts slightly) |
| 9.80–15.20 | **6 · Answer with citations** | 9.8: the orb turns solid accent ("Speaking…") and the tool line resolves to "Searched the papers · 6 passages". The Jarvis line streams in time with the VO (9.8–13.2), with inline ref chips ① ② appearing as the text reaches them. 13.3: the "Sources" block pops two citation cards (springs at 13.3 and 13.45). 13.6–14.6: card [1] flashes (accent border and glow) as Jarvis says "Kirby and Jungwirth". Camera drifts down with the text (y 560 → 640), then pushes to z 1.6 on the cards (600, 760) from 13.3 to 13.9. | Jarvis: "Scaled charges capture the electronic polarization that standard, non-polarizable force fields miss [1]. Scaling ion charges by about 0.75 brings ion pairing and binding in line with experiment [2]." · cards [1] *Charge Scaling Manifesto…* Kirby & Jungwirth · J. Phys. Chem. Lett. · 2019 · [2] *A practical guide to biologically relevant molecular simulations with charge scaling…* Duboué-Dijon et al. · J. Chem. Phys. · 2020 | 9.80 "Scaled charges capture electronic polarization that standard force fields miss." · 13.50 "Kirby and Jungwirth, 2019." |
| 15.20–17.40 | **7 · Click a task** | Pull back and pan right: z 1.6 → 1.3 on (1400, 430) by 15.9. The cursor travels to "Run it" on **Small calculation on Modal**. The card lifts on hover at 16.1 (strong border, shadow). Click at 16.6 (button press). 16.75–17.05: push into the clicked card (z 1.3 → 2.2) with a 0.12 s white dip, the same "zoom into what you open" motion as the logo dive. Land on the run view at z 1.0. | "Runs on Modal · traced" · "Run it" | 15.55 "Pick a task, and I run it on Modal." |
| 17.40–24.50 | **8 · Trace fills in** The run view: title, state, counters, papers used, timeline. | Camera z 1.0 → 1.25 on (750, 520) by 17.9. After that the *page scrolls* (header stays sticky, as in the app's follow-scroll) so the newest row sits about 70 % down the frame. Each row fades and slides up 8 px (0.3 s). Its counter bumps (accent and lift) at the same moment, and new papers drop into "Papers it used". The timer runs sped up. Row schedule in the table below. | Title "Small calculation on Modal" · pill **Running** · run id `20261008181727-small-calc-1` · counters 📄 papers read · 🔎 searches · 🧮 calculations · ✍️ files written · badge bottom right "▶▶ sped up 20×" | 18.60 "You see every paper I read…" · 21.00 "…and every number I calculate." |
| 24.50–26.60 | **9 · Done** | 24.4: the ✅ row lands, the pill goes **Running → Done** (green), the step chip dot turns green with "reward 1", and the "Working…" spinner hides. 24.6: the run-end banner slides in. Camera pulls back to z 1.0 by 25.4 and the page scrolls so the whole run is in view (counters, papers, timeline end). Hold. | "✅ Finished in 1 min 52 s. 2 papers · 3 calculations" · "Back to tasks" | 24.80 "Done. All of it, traced." |
| 26.60–30.00 | **10 · End card** Back into the orbits. | Reverse dive. 26.6–27.2: the page shrinks into a disc of light (circle clip closing, white → amber) while the camera pulls out z 60 → 1.0. The rings rush back in from outside the frame and settle with the particles back on their *unwarped* starting orbits (the system returns to where it began). The final hit lands at 27.2. Wordmark rises 27.3–27.7. Tagline 27.7–28.1, credit line 28.0–28.4. Particles keep orbiting slowly to the end. Music fades 29.4–30. | `macrae` · "Ask the papers. Watch the work." · "Jungwirth group · IOCB Prague" | 27.75 "Macrae." |

### Trace rows (shot 8)

The task is **Small calculation on Modal** with input *Ion: Na⁺*. Titles follow the CONTRACT mapping rules
(short, human). **All numbers here are placeholders.** Before building, run the task once for real, save
`GET /api/runs/{id}/events`, and copy titles, details and the final duration from it verbatim. Only the
timing is ours.

| At | Type | Row title | Detail (small, under the title) | Counter after |
| --- | --- | --- | --- | --- |
| 17.55 | status | Started on Modal · sandbox ready | step chip `calc[0]` · running | — |
| 18.20 | 🔎 search | Searched the papers: Na⁺ water binding, charge scaling | 6 passages | searches 1 |
| 18.75 | 📄 read | Read Kirby & Jungwirth 2019 · p. 2 | (quote from the index) | papers read 1, card [1] drops in |
| 19.35 | 📄 read | Read Duboué-Dijon et al. 2020 · p. 4 | (quote from the index) | papers read 2, card [2] drops in |
| 20.05 | think | Scan the Na⁺–O distance with DFT, then compare full and scaled point charges. | (muted style) | — |
| 20.90 | 🧮 calc | Ran Na⁺–water scan (PySCF, 12 points, 38 s) | minimum at r(Na–O) = 2.25 Å | calculations 1 |
| 21.60 | 🧮 calc | Binding energy, counterpoise-corrected: −23.4 kcal/mol | | calculations 2 |
| 22.30 | 🧮 calc | Point charges: full −31.2 · scaled ×0.75 −23.9 kcal/mol | | calculations 3 |
| 23.00 | ✍️ write | Wrote result.json | | files written 1 |
| 23.40 | ✍️ write | Wrote explanation.md (cites [1] [2]) | | files written 2 |
| 24.10 | ✅ result | Check passed: result.json has numbers · reward 1 | | — |

Each row shows its `ev-time` offset (+0:00 … +1:52) and its `ev-step` (`calc[0]`).

---

## Voiceover script

Voice: **"Jarvis (video)"** (ElevenLabs, looked up by name, see below). Jarvis speaks as itself: calm,
precise, warm, a little dry. It sounds like a colleague who has read everything, not an advert. There is one
clip per line, so each lands exactly on its beat (same method as the tin-can `audio.mjs`, with
previous_text/next_text for consistent delivery).

| id | Start | Line (as spoken) | Target length |
| --- | --- | --- | --- |
| vo1 | 0.40 | I'm Jarvis. | ≤ 0.9 s |
| vo2 | 1.45 | I've read the Jungwirth group's papers. | ≤ 2.0 s |
| vo3 | 5.90 | Ask me anything about them. | ≤ 1.4 s |
| vo4a | 9.80 | Scaled charges capture electronic polarization that standard force fields miss. | ≤ 3.6 s |
| vo4b | 13.50 | Kirby and Jungwirth, 2019. | ≤ 1.7 s |
| vo5 | 15.55 | Pick a task, and I run it on Modal. | ≤ 1.9 s |
| vo6 | 18.60 | You see every paper I read… | ≤ 1.6 s |
| vo7 | 21.00 | …and every number I calculate. | ≤ 1.8 s |
| vo8 | 24.80 | Done. All of it, traced. | ≤ 1.7 s |
| vo9 | 27.75 | Macrae. | ≤ 0.8 s |

Read straight through, it runs: *I'm Jarvis. I've read the Jungwirth group's papers. Ask me anything about
them. Scaled charges capture electronic polarization that standard force fields miss. Kirby and Jungwirth,
2019. Pick a task, and I run it on Modal. You see every paper I read… and every number I calculate. Done. All
of it, traced. Macrae.*

**Pronunciation and TTS text:**
- Jungwirth is said "YOONG-virt". If the model says "jung-worth", send the respelling "Yoong-virt" in the
  TTS text only (the screen keeps the real spelling), or use an ElevenLabs pronunciation dictionary.
- Macrae is said "muh-KRAY".
- Modal is said "MOH-dl".
- Send "twenty nineteen" in the TTS text for 2019.

**Fit rule:** if a clip runs past its target length, shorten the line rather than speeding it up. The beats
around vo4a and vo5 have no slack.

**User's question (7.6–9.0):** this is a caption only, no voice. The orb's level ring and a slight music
lift carry it. A second voice would blur "who is Jarvis". If the owner wants it audible, record a real group
member saying "Why does the group scale charges?" and drop it in as `vo-user.mp3`, and don't use a synthetic
second voice.

---

## Music brief (ElevenLabs Music, `music_v2_5`, composition plan)

- **Length:** exactly 30.0 s, instrumental, `respect_sections_durations: true`.
- **Tempo / key:** 100 BPM, major key with a suspended, open colour (e.g. D major, add9/sus2 voicings).
- **Mood:** wonder that turns into focus. Scientific, warm, quietly confident, curious. Not corporate, not
  epic-trailer.
- **Palette:** glassy celesta or music-box arpeggio that circles like the orbits, soft analog pads, warm sub,
  felt piano, plucked synth, muted kick, brushed shaker. Ticking hi-hats and a 16th-note sequencer pulse for
  the "computation" section.
- **Negative styles:** vocals, singing, EDM drop, dubstep, trailer braams, heavy distorted drums, big lead
  melody.

| Section | Span | Duration | Style prompts | Where it swells / drops |
| --- | --- | --- | --- | --- |
| `[Orbit Intro]` | 0.0–5.4 | 5400 ms | weightless ambient pad, glassy celesta arpeggio circling, soft sub drone, slow rise, reverse cymbal and airy riser in the last 2 s, wide stereo | Quiet under vo1/vo2, then swells from 3.4 through the dive. A soft bright bloom chord lands on the 5.4 downbeat (the reveal). |
| `[Demo Bed]` | 5.4–17.4 | 12000 ms | light warm curious electronic, plucked synth arpeggio, felt piano chords, muted kick on 1 and 3, brushed shaker, sparse, clear mid-range for voice, no lead | Drops to a bed right after the bloom. Thins a little 9.8–15.2 (the long answer). A small lift into 17.4. |
| `[Trace Pulse]` | 17.4–26.6 | 9200 ms | same key and tempo, ticking hi-hats, pulsing 16th-note sequencer, gentle build in density, still leaves room for voice | Builds steadily as rows fill. Holds at 24.4 (Done), then pulls back for a beat before the return. |
| `[Return and Final Hit]` | 26.6–30.0 | 3400 ms | 0.6 s reverse swell into one bright warm chord hit with chimes and celesta, long reverb tail, no drums after the hit | **Hit at 27.2** (orbits settle), then rings out to the end. |

**Mix (volume envelope in `Demo.tsx`, as in tin-can):** 0.28 under vo1–vo2 → 0.75 for the dive (3.4–5.6) →
0.20 under vo3 → 0.30 during the silent question (7.4–9.8) → 0.18 under the answer (9.8–15.2) → 0.22 under
the trace → 0.75 from 26.5 → duck to 0.55 under "Macrae." (27.7–28.4) → 0.70 → fade out 29.4–30.0. Finish with
the tin-can two-pass loudnorm to −14 LUFS / −1 dBTP.

**SFX** (same idea as tin-can: remotion.media click/whoosh, the rest from ElevenLabs sound effects):

| At | Sound | Volume |
| --- | --- | --- |
| 4.25 | whoosh (dive) | 0.40 |
| 5.05 | soft shimmer (bloom) | 0.25 |
| 7.00 | click (Talk) | 0.55 |
| 7.35 | connect blip | 0.30 |
| 9.10 | tick (search) | 0.10 |
| 13.30, 13.45 | chime-a, chime-b (cards) | 0.30 |
| 16.60 | click (Run it) | 0.55 |
| 16.80 | whoosh (into run) | 0.35 |
| each trace row | tick, a slightly brighter blip on 🧮 rows | 0.10–0.14 |
| 24.40 | done chime | 0.35 |
| 26.60 | reverse whoosh (pull-out) | 0.40 |

---

## UI states to recreate in React (`src/app.tsx`)

Colours, radii and type come from `web/style.css` (light theme: bg `#ffffff`, text `#0d0d0d`, text-2
`#5d5d5d`, text-3 `#8f8f8f`, border `#e6e6e6`, accent `#1d9bf0`, ok `#16a34a`). Markup comes from
`web/index.html` and the template strings in `web/app.js`. Layout comes from the screenshots in
`ref/frontend/` (1920×1080, 1:1). Load one font for every render machine (Inter) and bundle Noto Color Emoji,
so 📄🔎🧮✍️✅ look the same everywhere.

1. **Header bar:** brand mark (the bear SVG from `index.html`; the accent wave pulses while talking),
   "Macrae", "Jungwirth group · IOCB Prague", health pill (state *Checking…* → *Online · N papers · Modal
   ready* with green LED), theme button.
2. **Landing, loading → loaded:** hero h1, kicker, lead; the right column "TASKS IT CAN RUN" with two
   skeleton shimmers that turn into the two task cards.
3. **Task card (×2):** icon tile, title, subtitle, prompt, one input, footer "Runs on Modal · traced" and the
   "Run it" primary button. States: rest, hover-lift, pressed.
   - *Methods card for a paper*: flask icon. "Reads one paper's passages and writes a methods summary
     where every claim is cited". Input DOI `10.1021/acs.jctc.5c02051`.
   - *Small calculation on Modal*: atom icon. "Runs a tiny real computation on Modal and explains it with
     citations". Input Ion select `Na+`.
4. **Talk button / voice panel:** idle (black pill, "Talk to the agent / Voice, with citations") → connecting
   (pulsing accent ring, "Connecting…") → live-listening (surface pill with border, accent orb, ring scaled
   by `--level`, "End conversation / Tap to hang up", status "Listening…") → live-speaking (solid accent
   orb, status "Speaking…"). The status line sits under the button.
5. **Transcript:** the empty state with "Try asking" and 3 chips (fades out). Then a user bubble (typed word
   by word), a tool line with spinner ("Searching the papers…" → "Searched the papers · 6 passages"), and
   an agent line with the *who* label and streamed text containing inline `.ref` chips ①②. Last comes the
   "Sources" line with two `.cite` cards (key, title clamped to 2 lines, meta, quote, DOI) and the
   `.cite.flash` highlight.
6. **Cursor:** reuse the tin-can `Cursor` (black arrow, white stroke, press ring) on the light page.
7. **Run view header:** "‹ All tasks", h2 "Small calculation on Modal", state pill *Running* (accent) → *Done*
   (green), mono timer (sped up), mono run id. Step chip `calc[0]` with its dot (running → ok) and
   "reward 1".
8. **Counters:** four stat tiles with tabular numbers and the `bump` animation on change.
9. **Papers it used:** a `details` header with count badge and a `cite-grid` that gains cards as `read`
   rows land.
10. **Timeline:** the vertical rule, then one row per type: status (small grey dot), search, read, think
    (muted), calc (accent-tinted ring), write, result (green ring). Each row has a title, `ev-time`,
    `ev-step` and an optional `ev-detail`. The "Working…" spinner row sits at the bottom until done.
11. **Run end banner:** "✅ Finished in … · 2 papers · 3 calculations" with a "Back to tasks" button.
12. **Side column on the run view:** the voice panel moved to the side (live state), with the earlier
    question, answer and source cards still in the transcript. In the app this moves from the home column
    to `#side-voice-slot`.
13. **Page scroll:** a tall run-view canvas with a sticky header, scrolled by `scrollY(t)` rather than by
    moving the camera.

Logo pieces (`src/logo.tsx`), not UI but needed:

- **Orbit system as SVG:** rebuild it from `ref/logo.png`, which is a 720 px raster and would pixelate at 60×
  zoom. It has 4 concentric ellipses tilted about −25° about centre (360, 300), with the outer two dimmer.
  There is a small ring round the core and the core dot itself. The particle chain is coloured by orbit:
  amber `#FFB347` inner, peach/lilac middle, periwinkle `#8B93F5` outer, joined by thin grey connectors.
  Add 6–8 star dots. Background is navy (about `#0B1028`). Sample exact colours from the PNG.
- **Wordmark:** trace "macrae" to an SVG path. Don't substitute a font: it is a custom rounded geometric
  face, and a lookalike will show.
- **Parameters:** `warp(t)` (ε, precession, Kepler phase) must return the exact starting state at the end,
  so the bookend lands on the original logo. `dive(z)` handles core gradient, rim glow and streak length.

---

## File layout (mirrors `ref/tincan`)

```
video/
  STORYBOARD.md          this file
  README.md              storyboard table, re-render steps, credits (written with the first render)
  package.json           remotion, @remotion/cli, @remotion/media, react; no three.js needed
  remotion.config.ts  tsconfig.json
  src/
    index.ts  Root.tsx   composition "MacraeDemo", 1920×1080, 60 fps, 1800 frames
    lib.ts               copied from tin-can: FPS, ramp, pop, typed, camAt, FONT
    logo.tsx             orbit system, flow, dive-in, reveal clip, pull-out, end card
    app.tsx              Macrae web UI replica + scripted timeline T, MSGS, TRACE, camera keys
    Demo.tsx             shot switching, VO/SFX/music mix (same structure as tin-can Demo.tsx)
  scripts/
    audio.mjs            ElevenLabs: vo (voice "Jarvis (video)"), music (plan above), sfx
    finish.sh            two-pass loudnorm, −14 LUFS
    stills.mjs           review stills: node scripts/stills.mjs 1 3.5 5.2 8 13.8 17 21 25 28.5
  public/  vo/  sfx/  music.mp3  fonts/
  ref/                   (inputs, already here)
```

`audio.mjs` looks the voice up by name instead of hard-coding an id:
`GET /v2/voices?search=Jarvis` → the voice whose `name === "Jarvis (video)"`, else fail loudly. Same TTS call
as tin-can (`eleven_multilingual_v2`, mp3_44100_192, previous_text/next_text). Suggested settings: stability
0.5, similarity 0.8, style 0.25, speaker boost on.

**Review stills** (like `ref/stills`): 1.0 (logo), 3.5 (flow), 5.0 (reveal in the core), 6.8 (landing +
cursor), 8.6 (listening), 13.9 (card flash), 16.5 (Run it), 21.5 (calc rows), 25.2 (done), 28.5 (end card).

---

## Inputs still needed / decisions

1. **A real trace.** Run *Small calculation on Modal* once on the live system and save its events. The rows,
   numbers and "Finished in" come from it. The values above are placeholders.
2. **Real citation quotes and pages** for [1] Kirby & Jungwirth 2019 (doi 10.1021/acs.jpclett.9b02652) and
   [2] Duboué-Dijon et al. 2020 (doi 10.1063/5.0017775), taken from `python -m rag search` once those PDFs
   are indexed. Check that the answer sentence is backed by them, or swap in the papers the index actually
   returns.
3. **Paper count** for the health pill ("Online · N papers") from `GET /api/health`.
4. **Agent label.** The app labels replies "Agent". The video calls it Jarvis. Either rename it in the app
   (`app.js` line 660, `voice/agent.json`) or keep "Agent" on screen and let the voice carry the name.
   Recommendation: rename, so the film matches the product.
5. **Task titles.** The video uses the owner's placeholder titles. `tasks/tasks.json` currently says "Methods
   card for a group paper" and "Ion–water binding, computed live". Re-sync when the real tasks are chosen.
