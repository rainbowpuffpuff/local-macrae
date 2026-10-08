# w4 · costs_ux: costs easy to see

Branch `w4/costs_ux` (4 commits on top of `web-agent` 37b6a51). The branch does **not** include the uncommitted WIP
that was in the working tree when I started (evolve/, server/trace.py, planner.py, launch.py, web/live.js, …). I left
that WIP alone and didn't touch any of those files, so my commits apply cleanly on top of it.

## What I did

### Prices: looked up 2026-10-08, cited in `server/costs.py`
The module docstring and every table name their source URL and date. `price_table()` sends the same links to the page.

| what | source | change |
|---|---|---|
| Claude API, per MTok (in / out / cache hit / 5-min write; 1-h write = 2× in) | https://platform.claude.com/docs/en/about-claude/pricing | Opus 5.5 $4 / $20 / $0.20 / $5 (unchanged). **Sonnet 5.5 cache hit is $0.10**, not $0.20 (the 5.5 models are 0.05× input). Added Mythos 5. **Haiku 5.5 long-prompt tier**: over 100k prompt tokens it costs $0.50 / $2.50 / $0.05 / $0.625. This tier is applied only per request (`llm_usd(..., one_request=True)`, used for each stream message and for answers). |
| Modal compute | https://modal.com/pricing | **Agent steps run in Modal Sandboxes** (Harbor `--env modal`), so CPU and memory now use the sandbox rates: **$0.00003942/core/s and $0.00000667/GiB/s**, about 3× the Functions rates that were in the table before. GPUs use the standard table (the A10G is listed as "A10"; added RTX PRO 6000 and B300). `MACRAE_COMPUTE_RATES` still overrides. Effect: `cpu-2` is $0.38/h, up from $0.13/h. |
| Backend container (typed answers) | https://developers.cloudflare.com/containers/pricing/ | New `BACKEND_RATES`, standard-1 (½ vCPU, 4 GiB, 8 GB): $0.074/h with the CPU counted as busy, so it's an upper bound. |
| Voice | https://elevenlabs.io/pricing/api | New `VOICE_USD_PER_MIN = 0.08` (`MACRAE_VOICE_USD_PER_MIN` overrides). ElevenLabs bills the voice agent's LLM on top, and we can't see that part. |

### Backend: `server/cost_api.py` (new)
Wired in `server/app.py` with 3 lines: `cost_api.install(app, auth)`.

- `GET /api/costs/prices` returns every rate, the hardware options with $/h, the sources and `as_of`.
- `GET /api/costs/estimates` returns `{"estimates": {task_id: Estimate}, "runs": {run_id: {total_usd, llm_usd, compute_usd, wall_s}}, "as_of"}`.
  - An Estimate is built from the task's last 10 finished runs (the successful ones when there are any): median $
    and time, 25th/75th percentiles (min/max under 4 runs), LLM/compute split, success rate, the most-used
    hardware, and the run ids.
  - With no past runs, `basis: "none"` and only `usd_per_hour` is filled.
  - Runs that were never opened get their `costs.json` computed and saved.
  - Results are cached for 15 s.
- `GET /api/tasks/{id}/estimate` returns one Estimate.
- A middleware adds `"cost": {llm_usd, compute_usd, total_usd, seconds, model, tokens, compute: "backend"}` to
  every 200 JSON answer of a POST to `CHAT_PATHS`, currently `/api/search`, `/api/chat`, `/api/ask` and
  `/api/answer`. If a route returns `"model"` and an Anthropic `"usage"`, the middleware prices them. If the route
  already set a `cost`, it is kept. The ElevenLabs `/api/tools/*` routes are not stamped, so the voice LLM doesn't
  see the cost.

### Page
- **`web/costs.js`** (new, pure): the text and popover HTML for an answer, step, run, estimate and session.
- **`web/cost_ui.js`** (new): the DOM side.
  - A session ledger in `sessionStorage` (`macrae-costs`, so it survives a reload) holds typed answers, runs started
    from this tab (by a click or by the voice agent), and voice minutes.
  - The header pill shows the session total and pulses gold while a run or a voice call is still adding to it.
  - One "what this cost" popover is used everywhere: it opens on any `[data-cost]` element. Esc and an outside click
    close it. On a phone it becomes a bottom sheet. Every popover ends with "List prices as of 2026-10-08" and the
    source links.
  - Task cards show "≈ $0.47 · ~7 min · 3 past runs" (it is a button: the breakdown, range, success rate and the
    runs it came from).
  - The "Starting X on Modal…" message adds "The last 3 runs took about 7 min and cost $0.33–0.62."
  - The recent-runs list gets each finished run's cost.
  - Runs from this session that aren't open in the panel are polled every 15 s while they go.
- **Hooks in existing files** (kept small on purpose):
  - `app.js`: create `costUI`, `noteAnswer` in `ask()` (it also times the request), `trackRun` in `runStarted`,
    `noteRun` in `setRunInfo`, `voice(live)` in `renderVoiceState`, `stepNote` in the step list, `startNote`, and
    `costUI.start`.
  - `render.js`: `messageHTML(m, {costHTML})` puts a `$ · time` chip under each answer. `stepHTML(s, cost)` shows the
    step's $ and time; the chip is clickable.
  - `index.html`: the pill `#session-cost`, `#cost-why` ("What this cost" under the run meter), `#cost-pop`, and
    `costs.css`.
- **`web/costs.css`** (new): uses only `style.css`'s tokens, so light and dark both work. `style.css` is untouched.
- **`cloudflare/dev/mock-backend.mjs`**: the three cost routes and `cost` on `/api/search`, so local demos show
  everything.

## How it's tested
- `server/tests/test_cost_api.py` (11 tests):
  - the list prices;
  - the Haiku tier applying per request only;
  - the price table and its sources (also that the URLs are in costs.py);
  - auth;
  - `answer_cost`;
  - `/api/search` carrying `cost`;
  - the middleware pricing `usage` and keeping a route's own cost, with errors and other routes untouched;
  - estimates: no history, quartiles, the success rate, preferring successes, failures-only, running runs skipped,
    costs.json computed for runs never opened, the `runs` map.
- `test_costs_evolution.py` uses the sandbox rates. `test_api.py`: `/api/search` on an empty query now returns
  `{"passages": [], "cost": …}`.
- `web/tests/costs.test.js` (10 tests):
  - formatting;
  - the answer, step (fan-out sum), run, estimate and session breakdowns;
  - escaping, and only https source links;
  - the hooks present in app.js/index.html;
  - cost_ui.js calling only `/api/costs/{prices,estimates}` and `/api/runs/{}`;
  - the mock's cost routes.
- Full run on this machine (Python 3.14, Node 22): **pytest 362 passed** (tests, server, rag, tasks, voice, deploy,
  evolve), **web 72 passed**, **cloudflare 39 passed**.
- **In a real browser** (Playwright Chromium, mock backend + `cloudflare/dev/serve.mjs`), desktop light, desktop dark
  and a 390 px phone:
  - the estimate popover;
  - an answer chip and its popover;
  - a live run with step costs;
  - the session popover while the run goes;
  - the step and run popovers after it ends;
  - the phone bottom sheet.

  No console errors. To reproduce:
  `MOCK_SPEED=4 node cloudflare/dev/mock-backend.mjs & BACKEND_URL=http://127.0.0.1:8080 MACRAE_TOOL_SECRET=dev-secret node cloudflare/dev/serve.mjs`
  then open http://127.0.0.1:8787.
- Not verified against the live site. https://macrae.acalincarol.workers.dev still runs an older backend (its
  `/api/runs/{id}` has no `costs`). There the page degrades: the estimates, the answer chips and the price links are
  missing, and the pill still adds up run costs once `costs` exists.

## What the integrator must know
1. **Compute costs went up about 3×.** This is a correction to the sandbox rates, not a bug. Planner budgets
   (`hardware_options()` $/h feeds the planner prompt) and evolve's run-over-run cost charts will step up for runs
   whose costs are recomputed. Old `<run>/costs.json` files keep the old numbers. Delete them to recompute, or leave
   them: they are a record.
2. **`evolve/costs.py` has its own copy of the price table**, with the old Sonnet 5.5 cache price and the Functions
   rates. It only matters when a run has no costs.json and the server can't be imported. The evolve owner should
   import `server.costs` or copy the new numbers. I didn't touch it because evolve/ has uncommitted WIP.
3. **New chat endpoints** (RAG answering with Claude, BFF research): add the path to `cost_api.CHAT_PATHS` if it
   isn't `/api/chat`, `/api/ask` or `/api/answer`, and return `"model"` + `"usage"`, or your own `"cost"`. The page
   shows a chip on any message that has `cost` (see `costUI.noteAnswer` in `ask()`). For a streaming endpoint
   (SSE), the middleware leaves non-JSON alone: send a final event with `cost` and call
   `costUI.noteAnswer(msgId, cost, seconds)`.
4. **Conflict hot spots:**
   - `app.js`: `ask()`, `runStarted()`, `setRunInfo()`, `renderRunHeader()`'s step line, `renderThread()`,
     `renderVoiceState()`, and the start block.
   - `render.js`: `messageHTML`'s jarvis line, `stepHTML`.
   - `index.html`: the header, the run-costs box, and before `</body>`.

   Each hook is a single line, so keep them when you merge.
5. `web/tests/page.test.js`'s "API calls are exactly the contract's" checks only `app.js`, and app.js makes no new
   API calls (cost_ui.js does, and its own test pins them). CONTRACT.md isn't edited. The new routes are documented
   in `server/cost_api.py`'s docstring. Add them to the contract if you keep a list.
6. The mock's own `MODELS`/`HARDWARE` prices (used for its fake runs) are still the old illustrative ones. Its price
   table route uses the real values.
7. Voice answers have no per-turn cost: ElevenLabs doesn't report one. The session counts voice minutes × $0.08
   instead, marked "≈".
8. Ideas I didn't do: give the planner the estimate (planner.py is in the WIP); put `costs.total_usd` in the
   RunSummary (`/api/runs`), which would make the estimates call's `runs` map unnecessary.
