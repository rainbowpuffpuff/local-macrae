# web: chat redesign and run-view fixes

## What changed

**Layout: a chat with Jarvis, like tin-can's chat.** `index.html`, `style.css` and `app.js` were rewritten. The
old landing page and separate run view are gone.

- **Header** (58 px): the macrae mark and wordmark, "Jungwirth group · IOCB Prague", the health pill, the theme
  toggle, and a button that shows or hides the panel.
- **Main column**: a full-height thread with the composer pinned at the bottom centre (max 768 px, as in tin-can).
  - **Empty state.** The thread opens on a hero: the large mark, one line about Jarvis, and 3 suggestion chips.
  - **Bubbles.** User bubbles sit on the right. Jarvis messages have an avatar (the orbit mark) and the name
    "Jarvis". Spoken turns are marked "spoken" or "voice".
  - **Citations.** `[n]` in a Jarvis answer is a button. Under the answer, numbered source chips ("1 · Košťál et al.
    2026") open full source cards: title, authors, journal · year · page, quote, and a DOI link. Clicking `[n]` opens
    and flashes card n. "Show all" opens every card.
  - **Composer.** It has a **microphone button** that starts and stops the ElevenLabs session through `voice.js`
    (unchanged), a mute button during a call, a voice status line, and Send.
  - **Where typed text goes.** During a call it goes to the agent (`sendUserMessage`). Otherwise it goes to
    `POST /api/search`, and the passages come back as a Jarvis answer: one bullet per passage, each ending in its
    `[n]`, with the source chips underneath.
  - **Voice turns.** `show_citations` (and a `search_papers` result, if the agent config sends it) attach their
    sources to Jarvis's answer to the user's latest turn. If Jarvis hasn't answered yet, they wait for that answer.
    Tool calls show as small grey lines.
- **Right panel "Tasks & runs"** (384 px, on `--sidebar` like tin-can's drawer):
  - **Tasks.** One card per task from `GET /api/tasks`: icon, title, subtitle, prompt, inputs inline, and a Run
    button. The section folds away while a run is open.
  - **Current run.** The live trace:
    - title, state pill and timer
    - steps (kind · status · reward · attempt)
    - four counters: read, searches, calcs, files
    - the timeline, with an icon per event type (📄 🔎 🧮 ✍️ ✅ ⚠️, plus 💭 🔖 •), the step, the detail (clipped,
      "Show more"), and inline source cards
    - "Papers it cited", and a done/failed banner at the end
  - **Recent runs.** The current one is highlighted.
- **Starting a task** (from a card, or by the agent through `start_task`, `open_run` or the 5 s watch during a
  call) posts a Jarvis message in the thread with a run chip linking to the run (`/?run=<id>`). The chip's state
  follows the run. When the run ends, Jarvis posts the outcome, with the papers it cited as sources.
- **Phone (≤ 860 px).** The panel becomes a bottom sheet. Collapsed, a 62 px bar shows "Tasks & runs" with the
  current run or the task count. Tap it (or start a run) to open it to 86% of the height, over a scrim. The pill
  shrinks to "Online".
- **Offline.**
  - A red banner under the header ("Jarvis is offline…", "Check again"), and the pill says "Agent offline".
  - The chat still takes input and answers every typed question with a clear "my backend is offline" message.
  - Tasks are greyed out and disabled. The last task list is cached in `localStorage`, so the cards still show
    (greyed) when the backend is down at page load.
  - Recent runs show a note. Voice stays available, since it doesn't need the backend.
- **Conversation survives reloads.** The thread is kept in `sessionStorage` for the browser session.
- **Branding.**
  - **The mark.** Rebuilt as inline SVG from `video/ref/logo.png`: four tilted orbits, particle chains in periwinkle,
    lilac, peach and gold with thin links, a gold centre with a ring, and a few stars. It appears in the header
    (simplified at small size), in the hero, and as the Jarvis avatar. The "macrae" wordmark is set in a geometric
    sans (Avenir Next / Futura / Century Gothic fallbacks).
  - **Favicon.** A simplified mark on navy, as an SVG data URI (the CSP allows `img-src data:`).
  - **Colours.** Light theme: white with navy text and a navy Send button, periwinkle `#4b57c9` accent, gold
    highlights. Dark theme: the logo's navy night `#0b1029`, cream text, periwinkle `#8b97ff`, gold `#ffb847`.
- **Code layout.**
  - `render.js` (new) holds the HTML builders for messages, source cards, trace rows and steps. It is DOM-free, so
    it's unit-tested.
  - `core.js` gained `runHeadline`, `taskIdFromRunId`, `eventsOutcome`, `numberCitations`, `passagesAnswer`,
    `citationTarget` and `citationShort`. `healthLabel` now takes `{reachable}`.
  - `voice.js` and `theme.js` are unchanged.
- **API calls** are exactly CONTRACT.md's: `GET /api/health`, `GET /api/tasks`, `POST /api/tasks/{id}/start`,
  `GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/events[?after=]`, `POST /api/search`, plus
  `GET /voice/signed-url` on the Worker. A test enforces this list.

## The run-view bugs (video/ref/frontend/run-methods-card.png)

The screenshot shows a methods-card run whose counters read 3 · 1 · 0 · 1 and whose "Papers it used" is filled.
Yet the timeline is empty, the title says "Loading the run…", the state says "Starting", and the top-right pill
says "Checking…".

**Reproduced.** I restored the old `app.js`, `core.js` and `index.html` from this session's log; they were never
committed. I served them through Playwright request interception against the mock. With `/api/health` and
`GET /api/runs/{id}` delayed by 15 s, as a slow backend (cold container, first `rag` import in `/api/health`) or a
slow `state.json` read would delay them, the old page shows exactly the screenshot's state 3.5 s after opening a
run: `title="Loading the run…" state=Starting pill=Checking… counters=3/1/0/1`. The timeline `<li>` rows were
in the DOM (8 of them), so the empty timeline was a rendering problem, not missing data.

**Causes.**
1. **Title and status came from one source only.** They were only ever set from `GET /api/runs/{id}`
   (`renderRunHeader` returned early while `r.info` was null), and that call was retried every 4 s. The events
   endpoint said a lot about the run: there were events, `done`, and the task id in the run id. None of it reached
   the header. A slow or failing run-info call left "Loading the run…" and "Starting" next to a full trace.
2. **The pill depended only on `/api/health`.** Nothing else could tell the page the backend was up, so a slow
   first health call (the backend's `health()` calls `index_stats()`, which can import `rag`) left "Checking…" while
   tasks, runs and events loaded fine. Worse, the page called `applyRoute()` *before* `refreshHealth()` at start,
   so any throw while opening a run meant the health check never ran at all.
3. **Rows started invisible.** Each row had `animation: fade-in .3s` with `@keyframes fade-in { from { opacity: 0 } }`.
   A frame captured before that animation runs, as in a headless capture or a background tab where Chrome doesn't
   tick animations, shows every row at opacity 0. Counters and source cards had no such animation, so they showed.
   That is the "empty timeline although counters show events".

**Fixes.**
1. **Header from everything known so far.** `runHeadline()` in `core.js` builds title and status from all of it:
   the run info, the hint saved when the run was started (task title), the task matched from the run id, the
   events (any event means "Running"), and `done`, which settles Done or Failed even if `state.json` still says
   running. The header re-renders after every events poll. If events arrive while run info is still missing, run
   info is requested again at once (one request in flight at most). "Opening the run…" only shows before the
   first answer.
2. **Any answer means online.** `api()` reports reachability on every call. Any successful `/api` response marks
   the backend online ("Online" until the health details arrive). An offline error triggers a health check. Health
   now starts first, before the route is applied, and `applyRoute` and the renderers run inside `safe()`, so one
   throw can't stop the rest.
3. **Rows never start hidden.** There is no entrance animation from opacity 0 anywhere: trace rows are visible on
   their first frame. The timeline is rendered before the counters, and each renderer is isolated, so a failure
   can't leave counters without rows.
4. **Short 404 grace.** A run this page started less than 30 s ago that answers 404 is treated as "starting", not
   "run not found". `server/` already covers this with a synthetic state, so this is only a safety net.

## How I verified it

**Unit tests.** `node --test web/tests cloudflare/test` passes all 63 (web 38: `core.test.js`, `render.test.js`,
`page.test.js`; Worker and end-to-end 25, unchanged). The new web tests cover the bugs:
- `runHeadline`: a title and status from events alone; "Opening the run…" only before any answer; `done` overrides
  a stale "running".
- `healthLabel`: "Checking…", "Online" from other answers, and offline.
- `taskIdFromRunId` for mock and agent_runner ids, and `eventsOutcome`.
- No `@keyframes` starts at `opacity: 0`, and `.ev` has no animation.
- Every `$("#id")` in `app.js` exists in `index.html`, and health starts before `applyRoute`.
- The API calls match the contract, and the browser never calls `/api/tools/*`.
- Rendering: `[n]` buttons, source chips and cards with every citation field, one row per trace event with its icon,
  escaping, and run chips.

**Browser checks.** I ran headless Chromium (Playwright 1.47) against the mock:
`MOCK_SPEED=3 node cloudflare/dev/mock-backend.mjs`, `node cloudflare/dev/serve.mjs` with
`BACKEND_URL=http://127.0.0.1:8080`, and a second `serve.mjs` on :8788 with a dead `BACKEND_URL` for the offline
case. The harness is `web/review/review.mjs` (how to run it is in its header). It passed 40/40 checks:
- **bug, before.** The old page under a slow `/api/health` and `GET /api/runs/{id}` reproduces the screenshot.
- **bug, after.** The new page shows the right title, "Running", "Online", one row per event, and computed row
  opacity 1 with no animation.
- **desktop.** Hero and chips, then a typed question answered with `[n]`. `[1]` opens a card with a doi.org link.
  Run starts a task: a Jarvis message with a run chip, and the URL `/?run=<id>`. The timeline streams, the run
  ends "Done", Jarvis posts the result with sources, and the chip says Done. A reload keeps the conversation and the
  run. No console errors.
- **dark.** Dark theme, empty and with a run.
- **phone** (iPhone 13). No sideways scroll. The sheet opens and closes (scrim). A task runs from the sheet.
- **offline.** Banner and red pill. A typed question gets a clear message. When the backend goes down mid-session,
  tasks grey out and disable, then recover on "Check again".
- **voice.** I stubbed `window.ElevenLabsClient` with a fake microphone and a stubbed `/voice/signed-url`:
  - the mic starts the session, and spoken turns appear as messages
  - `show_citations` attaches sources to Jarvis's answer
  - typing during the call goes to `sendUserMessage`
  - a `start_task` tool result opens the run and posts a message
  - the mic ends the session
- **notconfigured.** The real Worker's 503 shows "Voice isn't set up on this deployment yet."

**Not verified here:** a real ElevenLabs call (no key) and the real backend.

**Screenshots** are in `web/review/`. `.assetsignore` has `review/`, so they're not served (checked: `/review/*.png`
falls back to the page in `serve.mjs`; wrangler applies the same file).

| file | what |
|---|---|
| `review/bug-after-slow-backend.png` | the screenshot's situation (slow health and run info), now correct |
| `review/01-empty-light.png` | empty state: hero, chips, composer, panel with tasks and recent runs |
| `review/02-answer-with-sources.png` | a typed question answered from `/api/search`, source 1 open |
| `review/03-run-live.png` | a task started: Jarvis message with run chip, live trace in the panel |
| `review/04-run-done.png` | the run finished: Done, Jarvis's summary with the cited papers open |
| `review/05-dark-empty.png`, `review/05-dark-run.png` | dark theme |
| `review/06-phone-*.png` | phone: empty, answer, sheet with tasks, sheet with a run, the run message |
| `review/07-offline-fresh.png` | backend down from the start |
| `review/07-offline-mid-session.png` | backend went down mid-session: banner, greyed tasks, clear reply |
| `review/08-voice-call.png`, `review/08-voice-agent-run.png` | a (stubbed) voice call with sources, and a run the agent started |
