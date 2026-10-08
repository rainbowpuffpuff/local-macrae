# web: v2 (live traces, costs, planner, evolution)

The page side of CONTRACT.md's v2 addendum, built on the chat layout from `web/FIXES.md`, plus a mock backend that
speaks v2. You can demo and screenshot the page with no Modal, Claude or ElevenLabs.

Owned and changed: `web/` and `cloudflare/dev/mock-backend.mjs`. Nothing else was touched.

## What I built

### The current run (right panel → "Tasks & runs")
From top to bottom:

1. **The planner's decision is the first card** (`#run-plan`).
   - Headline: "Decided: GPU A10G, 7 stages, budget $2.00".
   - Then "because …" (the planner's `why`), the numbered stages and the params as small key/value chips.
   - Footer: "planned by claude-sonnet-5-5 · planning $0.019 · 5 lessons from earlier runs applied".
   - If the planner failed, the card turns amber: "Planner unavailable: task defaults (8 CPU)", with the reason.
   - The plan event is also the first timeline row, headline only.
   - The run chip in the chat shows the same headline under the run's title, and the final cost when it ends.
   - The voice agent gets the decision as a contextual update, so Jarvis can say why it picked a GPU.
2. **The cost meter** (`#run-costs`):
   - **Total**, with LLM $ and compute $ beside it.
   - **Tokens**: total, in, out, cache.
   - A **budget bar** against the plan's `budget_usd`. It turns amber at 80 % and red over 100 %.
   - **Time per phase**: plan, setup, work, check, as one bar with direct labels and the current phase pulsing.
   - A **per-step table** (folded): step, hardware or model, seconds, LLM $, compute $.
   - **It counts while the run goes.** Between polls of `GET /api/runs/{id}` (every 3 s):
     - compute $ grows at the rate seen between the last two polls;
     - LLM $ and tokens add the `cost` of each event that arrived since the last poll;
     - wall time and the current phase tick every second;
     - extrapolation stops 10 s after the last poll, so a stalled backend can't invent money;
     - the shown numbers never go down while the run is live;
     - when the run ends, the exact final numbers replace the estimate.
3. **A sticky strip.** Once the meter scrolls out of view, a strip sticks to the top of the panel: total $, LLM $,
   compute $, phase and time. The cost stays visible while the trace scrolls under it.
4. **The live trace.**
   - Rows appear as the events endpoint returns them. On a v2 backend these include tool calls from inside a
     running agent step.
   - Each row's small print has its step, the call's duration (`elapsed_s`) and its cost ("fit[0] · 38 s ·
     $0.019 · 25k tok").
   - For the first 12 s after a run opens, the panel stays at the top: the plan card and the meter are the
     opening shot. After that the trace follows its newest row, unless you scrolled the panel in the last 10 s.
   - The line under the trace says what happened last and how long ago ("Working · last: MD 24 of 32 done … ·
     8 s ago"). It ticks through a quiet MD stretch, so the page never looks frozen.
5. **At the end:**
   - The banner says "Finished in 6 min 12 s for $0.63".
   - Jarvis's closing message adds "and cost $0.63 ($0.61 LLM + $0.02 compute)", and the voice agent is told
     the cost.
   - When the backend has distilled lessons from the run (the page looks at +4, 15, 40 and 90 s), Jarvis posts
     "From that run I learned: … The next <task> run starts with it." with a link to the Evolution tab, and
     tells the voice agent.

### The Evolution tab (right panel → "Evolution", or `/?view=evolution`)
One card per task, in tasks.json order. Each card has:
- **Time** and **cost**, run over run, as sparklines.
  - One series each, no axes; earlier runs in muted ink, the latest in the accent.
  - Failed runs are hollow red rings.
  - Every point links to its run and has a tooltip.
  - Beside each line: the latest value and the change from the first passing run to the latest ("▼ 51 %").
- **Passed**: one ✓/✗ disc per run (shape and colour, never colour alone), and "4/5" for the last five.
- **What it learned**: the lessons, newest first, each with:
  - its kind (✅ do, ⛔ avoid, ⚙️ setting, 🧰 tool);
  - the tool's name, if there is one;
  - links to its evidence. "run 2 · fit[0] · #16 →" opens `/?run=<id>&seq=16`: the run opens, the panel scrolls
    to event 16 and flashes it.
- **All N runs**: a folded table of every run (result, time, cost, lessons used). This is the table view behind
  the sparklines.

The tab loads `GET /api/evolution` when opened, every 30 s while it's shown, and after runs end. If new lessons
arrive while you're on the other tab, a gold dot appears on "Evolution". If the backend has no
`/api/evolution` (404), the tab says so; the rest of the page is unchanged.

### Code
| file | what |
|---|---|
| `web/live.js` (new) | the plan from plan events (wherever the backend puts the object); costs from the run or the events; the live extrapolation; the plan card, meter, phase bar, per-step table and a row's cost text as HTML strings. No DOM |
| `web/evolution.js` (new) | normalising `/api/evolution` (several shapes accepted), evidence parsing, run-id times, improvement and pass rate, sparkline SVG, pass discs, lesson and task-card HTML. No DOM |
| `web/core.js` | `plan` event type (🧭); routes `/?run=<id>&seq=<n>` and `/?view=evolution` |
| `web/render.js` | rows show duration and cost; plan rows are headline-only; the run chip has a second line (plan · cost); the "Run over run" link chip |
| `web/app.js` | panel tabs; the plan card, meter, sticky strip and their per-second tick; run-info polling every 3 s with cost snapshots; follow-the-trace; evidence highlight; the lesson announcements; the evolution loading and rendering; costs in recent runs, the phone's peek bar, the end banner and Jarvis's messages |
| `web/index.html`, `web/style.css` | tabs, `#run-plan`, `#run-costs`, `#run-strip`, `#pane-evo`; v2 styles. The panel is 420 px from 1280 px wide |
| `web/package.json` | `npm test` runs `node --test tests/*.test.js`: a directory argument fails on Node 22, as CLOUDFLARE.md notes |
| `cloudflare/dev/mock-backend.mjs` | v2 mock, below |

**Colours.** The four phases use one periwinkle hue, light → dark in run order. It's an ordinal ramp, checked
with the dataviz palette validator against both surfaces (#ffffff light, #121a3d dark): monotone lightness,
visible steps, light end ≥ 2:1. Every segment is labelled, so colour is never the only cue. Sparklines use the
muted ink plus the accent for the latest point. Pass/fail and the budget states use the existing status colours,
always with a symbol or text.

### The mock backend (`cloudflare/dev/mock-backend.mjs`)
Same exports and v1 behaviour (the Worker e2e test still passes), plus:
- **A third task, `bff-charges`** ("Bayesian charges for a fragment (BFF)", inputs fragment + samples). About
  100 s of trace modelled on the BFF paper (`.reference/bff/paper_brief.txt`):
  - prepare: passages and the Košťál 2026 abstract;
  - an agent step on a "GPU A10G" that reads the brief and writes the config;
  - Latin-hypercube samples, then MD progress (8/16/24/32 of 32, GROMACS ns/day);
  - the GP surrogate with LOO errors;
  - MCMC with τ, then validation against CHARMM36;
  - `posterior.png`, `result.json`, citations (real DOIs from `data/group_publications.json`) and `explanation.md`;
  - the check, then the result.
  The numbers are invented but plausible.
- **A plan event first** on every run (`type: "plan"`, `step: "plan"`), with `plan: {plan: [...], hardware,
  params, budget_usd, why, planner_model, lessons_used, lessons, source}` and its own token `cost`.
  `MOCK_PLANNER=fail` makes the planner "fail": `source: "defaults"` and the reason.
- **Per-event `cost` and `elapsed_s`** on agent events. **`costs` on `GET /api/runs/{id}`** (and on the
  `/api/runs` items), recomputed on each request:
  - LLM from the events so far, with illustrative per-model prices;
  - compute = sandbox seconds × an illustrative Modal-like rate per hardware;
  - `by_step`, `wall_s`, and `phases` that add up to the wall time.
  The run also carries `plan` and `inputs`.
- **`GET /api/evolution`** with `by_task` (evolve.metrics() shape plus `started`) and a flat `lessons` list.
  - **Seeded history** (`MOCK_HISTORY=0` turns it off): 4–5 past runs per task over the last week, as real runs.
    Their traces open, with failures that the lessons then fix. Each run is faster and cheaper than the last and
    uses more lessons.
  - 3–4 lessons per task. Each lesson's evidence (`run_id/step/seq`) points at a real event in a seeded run, and
    a test checks that.
  - Every run that finishes in the mock adds a metrics row and distills a lesson whose evidence is its last
    calculation. A repeated lesson is confirmed again: it moves to the top with the new run as its evidence.
- The voice tool `run_status` mentions the cost so far.

Time in the mock is real time (`MOCK_SPEED` compresses the script). Live mock runs therefore take seconds, while
the seeded history took minutes, and the Evolution deltas look dramatic ("▼ 96 %") after a few mock runs. Only
the real backend's numbers mean anything.

## How to run it

```bash
# page + Worker tests (Node ≥ 18, no installs)
cd web && npm test                      # 61 tests: core, render, page statics, live.js, evolution.js, the v2 mock
cd cloudflare && npm test               # 39 Worker/e2e tests (unchanged, they run against the new mock)

# the page against the v2 mock
node cloudflare/dev/mock-backend.mjs &                   # :8080, MOCK_SPEED=3 for ~35 s BFF runs
BACKEND_URL=http://127.0.0.1:8080 MACRAE_TOOL_SECRET=dev-secret node cloudflare/dev/serve.mjs
open http://127.0.0.1:8787                               # run "Bayesian charges for a fragment (BFF)"
open "http://127.0.0.1:8787/?view=evolution"

# headless review (screenshots into web/review/), Playwright 1.47 + Chromium
MOCK_SPEED=3 node cloudflare/dev/mock-backend.mjs &
BACKEND_URL=http://127.0.0.1:8080 MACRAE_TOOL_SECRET=dev-secret node cloudflare/dev/serve.mjs &
PORT=8788 BACKEND_URL=http://127.0.0.1:9 MACRAE_TOOL_SECRET=dev-secret node cloudflare/dev/serve.mjs &
PLAYWRIGHT=/path/to/node_modules/playwright/index.mjs node web/review/review.mjs            # all scenarios
PLAYWRIGHT=… node web/review/review.mjs live evolution planfail v1backend v2dark v2phone    # just v2
```

**Verified here.**
- `web` npm test: 61/61. `cloudflare` npm test: 39/39. `deploy.sh`'s own command
  (`node --test test/*.test.js ../web/tests/*.test.js`): 100/100.
- `web/review/review.mjs` against the mock in headless Chromium: **81/81 checks**. That is the 39 from before (the 40th,
  the "before" half of the bug scenario, needs `OLD_WEB`), plus 42 new:
  - **live**:
    - the plan is the first card, above the meter and the trace, with the headline, why, 7 stages and params;
    - the first row is the plan, and the chat chip shows the decision;
    - the meter grows ($0.019 → $0.283 in 6 s) and the time grows;
    - tokens, the budget bar and the current phase show;
    - agent rows show while `fit[0]` is still `running`, each with its own time and cost;
    - the trace follows the newest row, and the strip sticks once the meter is out of view;
    - at the end: the banner has the final cost, Jarvis states it split into LLM and compute, the chip shows it,
      and the per-step table fills;
    - Jarvis posts the lesson, and its link opens Evolution with the run still open.
  - **evolution**:
    - `/?view=evolution` opens the tab;
    - one card per task, in task order with the tasks' titles;
    - three rows each, improvements, evidence links;
    - an evidence link opens the run at that event, flashed and in view;
    - Back returns to the tab.
  - **planfail**: the amber fallback card.
  - **v1backend**: with costs, plans and `/api/evolution` stripped, there is no card and no meter, the trace
    still streams, and the tab says it has no data.
  - **v2dark** and **v2phone**: no sideways scroll; the phone's peek bar shows the run's cost; Evolution fits.
  - No page errors in any of them.
- **Not verified:** the real backend and evolve (built in parallel; see the assumptions below), a real Modal run, a
  real voice session. pytest isn't installed in this sandbox; no Python was changed.

**Screenshots** (`web/review/`, not deployed: `.assetsignore` has `review/`)
| file | what |
|---|---|
| `10-v2-plan-decided.png` | BFF run 3 s in: the decision card (why, 7 stages, params, planner cost, lessons applied), meter at $0.019, the chat chip with the decision |
| `11-v2-live-trace-costs.png` | mid-run: MD and surrogate rows with their time and cost; the sticky strip $0.34 · LLM · compute · Work · 18 s |
| `12-v2-run-done-costs.png` | done: the final meter with phases, Jarvis's summary with the cost split, the chip with the cost |
| `12b-v2-lesson-message.png` | Jarvis: "From that run I learned: …", with the Run over run link |
| `13-v2-evolution.png` | Evolution: BFF and Ion–water cards, sparklines, ✓/✗, lessons with evidence links |
| `14-v2-lesson-evidence.png` | an evidence link opened a failed past run at its error event (flashed) |
| `15-v2-planner-fallback.png` | the planner failed: amber card, task defaults |
| `16-v2-dark-run.png`, `16-v2-dark-evolution.png` | dark theme |
| `17-v2-phone-run.png`, `17-v2-phone-peek.png`, `17-v2-phone-evolution.png` | phone: the sheet with the plan, the peek bar with the live cost, Evolution |
| `01`–`08`, `bug-after-slow-backend.png` | the earlier scenarios, re-shot with v2 in place (all still pass) |

## What I assumed about the other two modules

The page reads several shapes for each of these. Where an assumption is wrong, the feature hides; the page never
breaks. `v1backend` checks that.

**backend (server/)**
- **Plan events.**
  - Type `plan`, ideally the first events of the run.
  - The decision object is read from `event.plan`, then `event.data.plan` or `event.data`, then JSON in
    `event.detail`, then `run.plan`.
  - Keys: `plan` (or `stages`) as strings or `{name|title, hardware, minutes}`, `hardware`
    (`cpu-8`/`gpu-a10g`/…, or `{gpu|cpu|label}`), `params`, `budget_usd`, `why`.
  - Optional: `planner_model`, `lessons_used` or `lessons`.
  - Fallback is detected from `source: "defaults"|"default"|"fallback"|"task"`, `fallback: true` or `error`. With
    no object, it's detected from the words default/fallback/unavailable/failed in an event with step `plan`. The
    planner's token `cost` sits on the plan event.
- **Event `cost`** = that event's own share, not a running total. The page adds the costs of events newer than
  the last run poll. `cost.tokens` may be `{in,out,cache}` or Anthropic's `input_tokens` / `output_tokens` /
  `cache_read_input_tokens` / `cache_creation_input_tokens`.
- **Event `elapsed_s`** = the duration of that call. If it equals the event's offset from the run's start, the
  page assumes it means "since start" and doesn't show it as a duration.
- **`GET /api/runs/{id}`.**
  - `costs` exactly as in the contract. `phases` values are seconds (or `{seconds}` / `{start, end}`) and are
    updated live.
  - `total_usd` is optional (llm + compute).
  - The route must be cheap enough to poll every 3 s per open page while a run goes.
  - If `costs` is missing, the meter shows LLM cost from the events and "—" for compute.
- **`GET /api/evolution`**: `{"by_task": {...evolve.metrics()...}, "lessons": [{task_id, lesson, kind, evidence,
  created, tool?}]}`. The page also reads lessons keyed by task, and `by_task` entries shaped
  `{runs, lessons}`. Runs are ordered by `started` (if present), else by run id (which starts with its time).
  **404 means "not wired"**, and the tab says so.
- **Evidence `seq` must be the TraceEvent `seq` from `/api/runs/{id}/events`**, not a trajectory step index. That
  is what `/?run=<id>&seq=<n>` scrolls to. If evolve records trajectory indexes, the server should map them. If
  it can't, give only `run_id/step` and the link opens the run without the highlight.
- Optional: `costs.total_usd` on `/api/runs` items, which shows as the run's cost in Recent runs.
- `/api/live/*` is for the sandbox's wrapper only; the page never calls it. The Worker's existing `/api/*` proxy
  already carries `/api/evolution`.

**evolve (evolve/)**
- `kind` ∈ do/avoid/setting/tool (anything else shows as "do"), `created` in Unix seconds, `evidence` a list of
  `"run_id/step/seq"` strings (objects `{run_id, step, seq}` work too). A `tool` name, if present, is shown with
  the lesson.
- `metrics()` rows have `ok` (or a `reward`, read as ok when ≥ 1), `wall_s`, `total_usd` and `lessons_used`.
  `started` helps ordering but isn't required.
- `distill` runs within about 90 s of a run's end. The page looks at +4, 15, 40 and 90 s and stops watching a run
  after 2 min. Any lesson whose evidence cites that run is announced in the chat.
