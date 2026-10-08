# web + cloudflare: notes

The page (`web/`) and the Cloudflare Worker that serves it (`cloudflare/`). Plain HTML/CSS/JS, no build step,
no npm dependencies. Look and feel copied from `.reference/tincan/web` (same tokens, type, pills, buttons, cards,
light/dark).

## What's here

| file | what it does |
|---|---|
| `web/index.html` | one page: a chat with Jarvis (thread + composer with mic) and a "Tasks & runs" panel; `/?run=<id>` opens that run in the panel |
| `web/app.js` | the page: health pill, thread, typed search, voice wiring, task cards, current run (polls events), recent runs |
| `web/render.js` | HTML builders (no DOM): chat messages, source chips and cards, trace rows, steps. Unit tested |
| `web/voice.js` | ElevenLabs voice session: loads `@elevenlabs/client@1.27.0` from jsDelivr (pinned, SRI-checked) on first use, gets a signed URL from the Worker, client tools, mute, contextual updates |
| `web/core.js` | pure helpers (no DOM): event merging, stats, citation handling, tool-param normalising, formatting. Unit tested |
| `web/live.js` | v2: the planner's decision, the run's costs and their live extrapolation, plan card / cost meter HTML (no DOM). Unit tested |
| `web/evolution.js` | v2: the Evolution tab: `/api/evolution` normalising, sparklines, lessons with evidence links (no DOM). Unit tested |
| `web/style.css` | tin-can's chat layout with macrae colours (navy, gold, periwinkle from the logo); light/dark; phone bottom sheet |
| `web/theme.js` | applies the saved theme before first paint (separate file because the CSP blocks inline scripts) |
| `web/.assetsignore` | keeps `package.json`, `tests/`, `*.md`, `review/` off the public site |
| `web/smoke.md` | manual checklist for the demo |
| `web/FIXES.md` | the chat redesign and the run-view bug fixes: causes, fixes, verification |
| `web/review/` | screenshots and the headless review harness (`review.mjs`); not deployed |
| `cloudflare/worker.js` | Worker `macrae`: serves `../web`, proxies `/api/*`, `GET /voice/signed-url`, security headers |
| `cloudflare/wrangler.toml` | assets binding, SPA fallback, two rate limits (task starts 6/min/IP, voice sessions 10/min/IP) |
| `cloudflare/deploy.sh` | runs the tests, then `npx wrangler@4 deploy` |
| `cloudflare/dev/serve.mjs` | runs `worker.js` on Node with `../web` as assets, for local dev without wrangler |
| `cloudflare/dev/mock-backend.mjs` | fake backend that speaks the contract API with scripted runs (real titles/DOIs from `data/group_publications.json`, invented passage text marked "mock") |

**v2 (live traces, costs, planner, evolution):** see `web/V2_NOTES.md`: the plan card, the live cost meter, the
Evolution tab, the v2 mock backend, the screenshots and what the page assumes about server/ and evolve/.

### The page
A chat in the style of tin-can's: header (macrae mark, health pill, theme, panel toggle), a full-height thread
with the composer pinned at the bottom, and a right-hand "Tasks & runs" panel (a bottom sheet on a phone).
Details, and the run-view bugs fixed in the redesign: `web/FIXES.md`.
- **Thread:** an empty-state hero with 3 suggestion chips, then user bubbles and Jarvis messages. `[n]` in a Jarvis
  answer is a button; numbered source chips under it open full source cards (title, authors, journal, year, page,
  DOI link). The thread is kept in `sessionStorage`, so a reload or a `/?run=` link keeps it.
- **Typed questions:** with no call running they go to `POST /api/search` and come back as a Jarvis answer, one
  bullet per passage with its `[n]`, so the demo still works if voice doesn't. During a call they go to the agent
  (`sendUserMessage`).
- **Voice:** the microphone button in the composer starts/ends the session (`voice.js`). Spoken turns are thread
  messages. `show_citations(citations)` (or a `search_papers` result, if the agent sends it) attaches sources to
  Jarvis's answer to the latest user turn. `open_run(run_id)` opens the run. Server tool calls show as grey lines.
  If the agent starts a task and forgets `open_run`, the page polls `/api/runs` every 5 s during a call and opens
  any new run. When you click a task, or a run finishes, the page tells the agent with `sendContextualUpdate`.
- **Tasks:** cards from `GET /api/tasks` with inputs inline (prefilled with `default`; `options` → a select), "Run"
  → `POST /api/tasks/{id}/start`. Starting a run (from a card or by the agent) posts a Jarvis message with a run
  chip linking to it, opens it in the panel and sets the URL to `/?run=<id>`; when it ends Jarvis posts the outcome
  with the cited papers.
- **Current run:** polls `GET /api/runs/{id}/events?after=<last seq>` every 1.5 s (no `after` on the first call),
  backing off to 10 s on errors, until `done`, and `GET /api/runs/{id}` every 4 s (and at once if events arrive
  first). Title and status come from whatever is known (run info, the task, the events, `done`), never stuck on
  "Loading". Timeline with an icon per type (📄 🔎 🧮 ✍️ ✅ ⚠️, plus 💭 think, 🔖 cite, • status), counters, steps
  with reward/attempt, the papers it cited, and a finished/failed banner.
- **Backend down:** a banner under the header, the pill says "Agent offline", tasks are greyed out and disabled
  (the last list is cached so they still show), typed questions get a clear "backend is offline" reply, the run
  shows a retrying notice. Health is re-checked every 20 s, and any successful `/api` answer counts as online.

### The Worker
- `/api/*` → `BACKEND_URL` + same path and query. Only GET/HEAD/POST (else 405), bodies ≤ 64 KB (else 413).
  Only `content-type`/`accept` are forwarded (no cookies), `X-Macrae-Secret` is set from `MACRAE_TOOL_SECRET`
  (anything the client sent is overwritten), and responses get `cache-control: no-store`. Timeout 30 s.
- If `BACKEND_URL` or the secret is missing → 503, if the backend is unreachable → 502, if it times out → 504.
  All three return `{"error": "agent offline", "offline": true}`, which the page shows as offline.
- `/api/tools/*` gets 403 unless the request already carries the right `X-Macrae-Secret` (constant-time
  compare). The browser can't call the voice agent's tools; ElevenLabs can, whether it calls the backend directly or goes through the Worker.
- `GET /voice/signed-url` → ElevenLabs `get-signed-url?agent_id=…` with `xi-api-key`, returning `{"signed_url"}`.
  It refuses cross-site requests (`Sec-Fetch-Site`), returns 503 "voice is not configured" when the key or agent
  id is missing, and returns 502 with ElevenLabs' message (never the key) on failure.
- Headers on every response: CSP (self, plus `cdn.jsdelivr.net` for the SDK and its worklets, plus
  `*.elevenlabs.io` for https/wss; no inline script, no `blob:` scripts), nosniff, no-referrer,
  `Permissions-Policy: microphone=(self)`, COOP, and HSTS.

## Run and test

```bash
# tests (Node ≥ 18, no installs)
node --test web/tests cloudflare/test        # 38 page tests (logic, rendering, static checks), 25 worker + end-to-end tests

# local, no backend, no wrangler: mock backend + worker on Node
node cloudflare/dev/mock-backend.mjs &        # :8080, secret "dev-secret"; MOCK_SPEED=4 for faster runs
cp cloudflare/.dev.vars.example cloudflare/.dev.vars
node cloudflare/dev/serve.mjs                 # http://127.0.0.1:8787

# local on the real Workers runtime (Node ≥ 22)
cd cloudflare && npx wrangler dev             # reads .dev.vars

# deploy (once: npx wrangler login, then the four secrets)
cd cloudflare
npx wrangler secret put BACKEND_URL           # https://… of the backend
npx wrangler secret put MACRAE_TOOL_SECRET    # same value as the backend's
npx wrangler secret put ELEVENLABS_API_KEY
npx wrangler secret put ELEVENLABS_AGENT_ID   # from voice/setup_agent.py
./deploy.sh
```

Verified here: the unit and end-to-end tests above. `wrangler deploy --dry-run` (wrangler 4.148) accepts the config.
`wrangler dev` (real workerd) serves the page, hides the dev files, proxies to the mock backend and gives the same
403/405/503 answers. A headless-Chromium run against the mock backend passed 44 checks: landing, search, start →
live run → finished, back/forward, dark theme, phone width with no overflow, offline states, "voice not
configured", the real SDK loading under the CSP with SRI and both worklets loading, and, with a stub in place of
the SDK, transcript, `show_citations`, `[n]` buttons, tool lines, mute, typed-to-agent, `open_run`, runs started by
the agent opening by themselves, and contextual updates. That harness lived in /tmp, not in the repo; `web/smoke.md`
is the manual version. Not verified: a real ElevenLabs call (no key here) and the real backend (not written yet when
this was built). After the chat redesign the browser checks live in the repo: `web/review/review.mjs` (40 checks,
including a before/after reproduction of the run-view bugs; screenshots next to it). See `web/FIXES.md`.

## What I assumed about other modules

**server/**
- The API is exactly as in CONTRACT.md. Errors are JSON. The page reads `error` or FastAPI's `detail`.
- `GET /api/runs/{id}` and `/events` return **404 for an unknown run**. The page shows "run not found" and stops polling
  (except for 30 s after this page started the run, when a 404 is read as "not on disk yet").
- `?after=<seq>` is exclusive (events with `seq > after`). `seq` is a number that grows within a run. `t` and
  `started`/`finished` are Unix seconds. `done: true` comes once, at the end, and the page then stops polling.
- `title` on Run/RunSummary may be empty. The page then uses the task's title (from `/api/tasks`) or `task_id`.
- Unknown TraceEvent types are drawn as `status`. `citation` may be null.

**voice/**
- Client tools are called `show_citations` (param `citations`: a list of Citation, or a JSON string of one) and
  `open_run` (param `run_id`). The page accepts a few variants (a bare list, `runId`, JSON strings).
- The ElevenLabs agent uses authentication (signed URLs). The Worker only needs `ELEVENLABS_AGENT_ID` and `ELEVENLABS_API_KEY`.
- Server tools are called `search_papers`, `start_task` and `run_status`; the names only affect the grey lines in the transcript. If
  the agent config turns on the `agent_tool_response` client event (ideally with the full payload), runs started
  by voice open at once. Otherwise the 5 s `/api/runs` poll during a call catches them.
- The tool webhooks point at `BACKEND_URL` directly. Pointing them at the Worker URL also works, because the Worker lets `/api/tools/*` through
  when the request carries the secret.

**tasks/**
- `tasks/tasks.json` keeps the contract's Task shape. Icons drawn: `flask`, `atom`, `book`, `card`, `calc`,
  `water`, `chart`, `molecule` (also `science`, `compute`, `calculator`, `paper`, `methods`); any other name gets a
  generic icon. `inputs[].default` prefills the form, and empty fields fall back to it.

**deploy/** (Makefile)
- Suggested targets: `web-dev` → `node cloudflare/dev/serve.mjs` (Node 18+) or `cd cloudflare && npx wrangler dev`
  (Node 22+). `deploy-web` → `cloudflare/deploy.sh`. `test` should include `node --test web/tests cloudflare/test`.
- The backend needs a public URL the Worker can reach. HTTPS is preferred, plain `http://` works too because the
  Worker makes that request server-side, not the browser.
- `deploy/env.example` lists the Worker's four variables. They are set with `wrangler secret put`, not in `wrangler.toml`.

## Known limits
- The rate limits are per Cloudflare location and approximate (Workers rate-limiting binding). They slow abuse
  down but don't stop it. The ElevenLabs agent's own limits and the backend still matter.
- The SDK is about 1 MB. It loads when the pointer reaches the talk button or on the first click, not at page load.
- Upgrading `@elevenlabs/client` means updating `SDK_VERSION` and `SDK_SRI` in `web/voice.js`:
  `curl -s https://cdn.jsdelivr.net/npm/@elevenlabs/client@X/dist/lib.iife.js | openssl dgst -sha384 -binary | openssl base64 -A`.
