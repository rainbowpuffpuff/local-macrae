# server/: v2 notes (live traces, costs, planner, evolution route)

Backend module, CONTRACT.md "v2 addendum". The module owns `server/`, `tasks/common/` and the env pass-through in
`agent_runner/`; the Worker's `/api/live/*` route was added to `cloudflare/worker.js` with permission.

## What I built

| file | what |
|---|---|
| `server/live.py` | `POST /api/live/{run_id}/{step}` ingest: per-run token (`X-Macrae-Live`, no shared secret), raw lines appended to `<run>/live/<step>.jsonl`, de-duplicated by (stream, offset), times corrected for sandbox clock skew. Reading those lines back for the trace. |
| `server/costs.py` | `PRICES` (per model: input/output/cache read/cache write), `COMPUTE_RATES` (Modal CPU core-s, GiB-s, per GPU type; one dict with its source), `HARDWARE` options, and `run_costs(state, plan)`: the live `costs` object. |
| `server/planner.py` | One Claude call (Anthropic SDK, structured JSON output, `fallbacks: "default"`) before the flow starts → `{plan, hardware, params, budget_usd, why}`; task defaults when the call can't be made; `<run>/plan.json`; the `plan` events. |
| `server/launch.py` | The v2 start: run id + `<run>/live/token` + `macrae.json` at once, then plan → engine in a background thread (same command `start_detached` uses, plus the plan/lessons vars). `POST …/start` now returns in milliseconds. |
| `server/evolution.py` | Lazy bridge to `evolve/`: `lessons()` (`evolve.context`), `metrics()`, `on_run_end()` (writes `<run>/costs.json`, then `evolve.distill(run_dir)` once per finished task run) and a watcher thread for runs nobody polls. |
| `server/trace.py` | Live lines become TraceEvents with the v1 mapping. Tool calls are keyed by `tool_use` id and text by Claude session id + hash, so nothing seen live repeats when `claude-code.txt` or `trajectory.json` arrives. Optional `cost` / `elapsed_s` on events. The planner's events come first. |
| `server/app.py` | New routes `POST /api/live/{run}/{step}` and `GET /api/evolution`. `GET /api/runs/{id}` gains `costs`, `plan`, `live`. Starts go through `launch.start` (the Worker's `X-Macrae-Origin` header gives the live URL). |
| `server/runs.py`, `events.py` | A run that is still planning isn't reported as crashed. The first poll that sees a task run end triggers `evolution.on_run_end`. |
| `agent_runner/harbor.py`, `flows.py` | Env pass-through: `live_agent_env(run_dir, run_id, step)` reads `<run>/live/token` + the URL (`MACRAE_LIVE_URL`, else `<run>/live/url`), and `exec_cmd`/`run_cmd` take `agent_env` → `--ae K=V`. The step log shows `MACRAE_LIVE_TOKEN=***`. With no token or URL nothing is added, so runs are unchanged. |
| `tasks/common/` | The Modal image (`Dockerfile`, `build.sh`) with the `claude` wrapper (`claude` sh shim + `claude_live.py`). See `tasks/common/README.md`. |
| `cloudflare/worker.js` | `/api/live/{run}/{step}` (POST only, needs `X-Macrae-Live`, forwards only the live headers, **never adds the secret**, 4 MB body). Every proxied request now carries `X-Macrae-Origin: <worker origin>`. `containerEnv` forwards `MACRAE_LIVE_URL`, `MACRAE_PLANNER*`, `MACRAE_COMPUTE_RATES`, `MACRAE_EVOLVE`. |

### API additions (all JSON)
- `POST /api/live/{run_id}/{step}`: body `{"stream", "offset", "sent", "lines": [str], "times": [float]}` (or
  NDJSON). Answers `{"ok", "accepted", "next"}`; 401 wrong token, 404 unknown run / live off, 410 more than 30 min
  after the run ended, 413 too big.
- `GET /api/runs/{id}`: in addition to v1,
  - `"costs"`: `{"llm_usd", "compute_usd", "total_usd", "tokens": {"in", "out", "cache_read", "cache_write", "cache"},
    "by_step": {step: {"llm_usd", "compute_usd", "seconds", "model", "hardware", "tokens"}}, "wall_s",
    "phases": {"plan", "setup", "work", "check"}, "hardware", "hardware_label", "usd_per_hour", "estimated", "updated"}`.
    `by_step` includes `"plan"` (the planner call). `estimated` is true while some LLM cost comes from token
    counts rather than Claude Code's own total.
  - `"plan"`: `{"status": "ok"|"default"|"planning", "plan", "hardware", "params", "budget_usd", "why", "model", "cost_usd", "error"}`.
  - `"live"`: bool.
- `GET /api/runs/{id}/events`: events keep the v1 shape. Optional extras:
  - `"elapsed_s"` (seconds since planning began) on every event.
  - `"cost": {"usd", "tokens": {"in", "out", "cache"}}` on agent messages (first event of each API message), on the
    `plan` event, and on each agent step's `result`/`error` (which also carries `llm_usd` and `compute_usd`) and
    the run's final event.
  - New type `"plan"`: the decision ("Decided: GPU A10G, 3 stages, budget $2.00", or "Using the task's defaults: …
    " with the reason), with `"plan": {plan, hardware, hardware_label, params, budget_usd, why, status, model}`.
    It comes after a `status` "Planning the run: …" and before "Started <task>".
- `GET /api/evolution`: whatever `evolve.metrics()` returns (`{"by_task": {...}}`) plus `"available": true`;
  without the evolve package or when it fails: `{"by_task": {}, "available": false, "detail": "…"}` (200).

### Start sequence
`POST /api/tasks/{id}/start` (or the voice tool) → inputs checked → run id made, `<run>/live/token` (+ `url`) and
`macrae.json` written → response. Background: `evolve.context(task_id)` → planner → `<run>/plan.json` →
`python -m agent_runner flow run FLOW --run-id ID --var …` with the task vars plus `lessons`, `plan` (JSON),
`hardware`, `budget_usd`, `plan_why` and the planner's `params`. The planner may set only flow `vars:` that are not
task inputs and not engine plumbing, with shell-safe values. If the engine can't start, a failed `state.json`
says why.

## How to run
```bash
pip install -r server/requirements.txt          # now includes anthropic>=1.0 (imported lazily)
export MACRAE_TOOL_SECRET=… ANTHROPIC_API_KEY=…   # the key is for the planner; without it: task defaults
export MACRAE_LIVE_URL=https://macrae.<you>.workers.dev   # optional on Cloudflare (the Worker sends its origin)
python -m server
```
For live events on Modal, build and push the agent image, then point agent steps at it:
```bash
tasks/common/build.sh ghcr.io/<you>/macrae-agent:latest --push
cd cloudflare && npx wrangler secret put AGENT_RUNNER_MODAL_IMAGE   # value: that image
npx wrangler secret put ANTHROPIC_API_KEY                           # the planner (and evolve) need an API key
cloudflare/deploy.sh                                               # or: cloudflare/deploy.sh restart
```
Optional env: `MACRAE_PLANNER_MODEL` (default `claude-sonnet-5-5`), `MACRAE_PLANNER_EFFORT` (`medium`),
`MACRAE_PLANNER_TIMEOUT` (60 s), `MACRAE_PLANNER=off`, `MACRAE_PLANNER_API_KEY` (instead of `ANTHROPIC_API_KEY`),
`MACRAE_COMPUTE_RATES` (JSON override of the Modal rates), `MACRAE_EVOLVE=off` (don't distill). Optional per-task
fields in tasks.json: `hardware` (one of `costs.HARDWARE`, default `cpu-2`) and `budget_usd` (default 2), used as
the planner's defaults.

## How to test
```bash
python -m pytest -q server/tests            # 80 tests (52 before v2), ~20 s; no network, no API key needed
make test                                   # every module: 303 passed, 2 skipped; cloudflare 39, web 38
```
New test files:
- `test_live.py`: token rules, idempotent retries, NDJSON, limits, clock skew, U+2028. Live lines become events
  while the step runs; a later trajectory with the same session adds no duplicates; costs grow from live lines
  (each message counted once; Claude Code's `total_cost_usd` wins).
- `test_claude_wrapper.py`: a **fake `claude` that prints stream-json**, run through `tasks/common/claude_live.py`
  against the real app on uvicorn.
  - stdout passes through byte for byte, stderr is the CLI's own, and the exit code is kept.
  - Lines reach the backend while the CLI is still running.
  - A backend that is down or refusing never breaks the run.
  - `--version` and missing env go straight to the real binary, and the sh shim falls back correctly.
  - Unit tests cover batching and retry.
- `test_planner_launch.py`: the decision is cleaned (invalid hardware → default; params limited to tunable flow
  vars with safe values). Fallbacks are tested for no key, error, refusal and off. The SDK request shape is
  checked with a stub client. A **real engine run** through `POST /api/tasks/{id}/start`: the plan and lessons
  arrive as flow vars (the script step prints `gpu-l4 7 0.8`), the events come in the order planning → decision →
  started, and costs include the planner. An engine that fails to start shows the reason. A run that is still
  planning is not reported as crashed.
- `test_costs_evolution.py`:
  - prices and rates;
  - `/api/evolution` with and without evolve;
  - distill runs exactly once, after `costs.json` exists;
  - lessons failures are harmless;
  - **agent_runner pass-through**: the real engine with a fake `harbor` on PATH gets the four `--ae` pairs, and the
    token is masked in the step log;
  - **worker.js on node**: `/api/live` forwarding (no secret, token header, 401/404/405/413), `X-Macrae-Origin`,
    and `containerEnv`.

Manual smoke, done here:
- `uvicorn` backend + `cloudflare/dev/serve.mjs` (the Worker on node) → `POST /api/tasks/small-calc/start` through
  the Worker.
- The events were: planning → "Using the task's defaults: 2 CPU cores, 3 stages, budget $2.00 …" → prepare
  step's searches/cites → calc failed. It failed only because this machine has no Claude login.
- Then `claude_live.py` with a fake CLI, posting to the **Worker** URL with the run's token. 3 lines were stored,
  and `costs.by_step.calc.llm_usd` was 0.01 (the CLI's reported total).

## Not verified / known limits (read before filming)
1. **Hardware is decided, priced and passed to the flow, but not enforced on Modal.** Harbor 0.24's `exec -p`
   rebuilds the task's `[environment]` from the input paths, so a template's `cpus`/`gpus` are dropped (checked in
   `harbor/compile/compiler.py`). Harbor does have `override_cpus` / `override_memory_mb` / `override_gpus` on its
   trial environment config (`harbor exec --config`). Making `{{ vars.hardware }}` real needs that wired into
   agent_runner's Modal support (the tasks/agent_runner owner), or the flow has to use it some other way. Until
   then, compute cost is "seconds × the planned hardware's rate", and a GPU decision runs on Harbor's default
   sandbox.
2. **Live events need the custom image.** Without `AGENT_RUNNER_MODAL_IMAGE` set to the `tasks/common` image, there
   is no wrapper: the run works, but agent events appear only when the trial ends, as in v1. Neither the image
   build nor a real Modal pull was run here (no Docker in this sandbox).
3. The Modal rates in `costs.COMPUTE_RATES` are the published per-second list prices as I know them. Check them
   against modal.com/pricing (sandboxes may have their own rates) and override with `MACRAE_COMPUTE_RATES` without
   a deploy. Claude prices are from Anthropic's price list of 2026-10-06. With an `AGENT_RUNNER_TOKEN_*`
   (subscription) login, the LLM figure is the API-equivalent cost, not a bill.
4. No real Anthropic API call was made (no key here). The request shape follows the current SDK (`anthropic`
   1.12.1: `client.beta.messages.create(..., output_config={"effort", "format": json_schema}, fallbacks="default",
   betas=["server-side-fallback-2026-07-01"])`) and is tested with a stub client.
5. Live batches can arrive up to 30 min after the run ends. Events after the run's final event aren't added: the
   event log is closed then, so the wrapper's last ≤ 1 s may only show up through the trajectory.
6. The planner runs before the engine and has a 60 s timeout plus 1 SDK retry. A run without `state.json` is kept
   as "starting" for up to 5 min while `plan.json` says `planning`.

## What I assumed about the other modules
- **web**:
  - Polls events as in v1 and renders type `plan` (the `plan` object on that event) and the optional
    `cost`/`elapsed_s`.
  - Reads `GET /api/runs/{id}` → `costs` (`llm_usd`, `compute_usd`, `total_usd`, `tokens`, `phases`, `wall_s`) for
    the meter, polled every 1–2 s while running.
  - Shows "no data yet" when `/api/evolution` has `available: false`.
  - Event `seq`, `?after` and `done` are unchanged.
- **evolve**:
  - `evolve.context(task_id, k=8) -> str`, with lessons as lines starting with `- `, which is how the planner
    event counts "Used N lessons".
  - `evolve.metrics() -> {"by_task": {...}}` (JSON-able).
  - `evolve.distill(run_dir)`: called once per finished task run, in a background thread, after `<run>/costs.json`
    is written. Its return value is only logged.
  - For distill and metrics, the run folder has `state.json`, `logs/`, `live/<step>.jsonl` (raw stream-json),
    `plan.json` (the decision, model, `cost_usd`, and the `lessons` text that was used), `costs.json` (the costs
    object above) and `macrae.json` (task id, inputs).
  - The flow var `lessons` is always passed (possibly `""`). Flows that reference `{{ vars.lessons }}` should
    declare `lessons: ""` under `vars:` so CLI runs work too.
  - `evolve` must be importable from the repo root.
- **tasks / flows**: optional task fields `hardware` and `budget_usd`. The planner may tune any flow `vars:` entry
  that isn't a task input or plumbing (`environment`, `python`, `task_id`, `task_title`, `lessons`, `plan`,
  `hardware`, `budget_usd`, `plan_why`). `tasks.runner` keeps `get_task`, `flow_path`, `build_vars`; without them the
  server falls back to `runner.start` without plan vars.
- **deploy**: `server/requirements.txt` now has `anthropic>=1.0` (Python ≥ 3.10; the image is 3.11). `deploy/r2sync`
  syncs `runs/` as before, which now includes `live/*.jsonl`, `plan.json` and `costs.json`. `deploy/env.example`
  should list `MACRAE_LIVE_URL`, `MACRAE_PLANNER_MODEL`, `MACRAE_PLANNER`, `MACRAE_COMPUTE_RATES`, `MACRAE_EVOLVE`
  (I didn't edit deploy/).
