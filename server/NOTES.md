# server/: notes

> **v2** (live traces, costs, planner, `/api/evolution`): see [V2_NOTES.md](V2_NOTES.md). New files `live.py`,
> `costs.py`, `planner.py`, `launch.py`, `evolution.py`; task starts now go through `launch.py`.

The FastAPI backend from CONTRACT.md: every route under `/api`, run state and traces read from agent_runner and
Harbor files, and `rag` / `tasks.runner` imported lazily. The server starts and `/api/health` answers even when
those modules, the papers or the index are missing.

## Files
| file | role |
|---|---|
| `app.py` | FastAPI app `server.app:app`: routes, `X-Macrae-Secret` auth, input validation, voice-tool replies |
| `config.py` | env vars, read at call time; paths (`AGENT_RUNNER_HOME`, tasks file, index, papers); Modal check |
| `catalog.py` | `tasks/tasks.json` loader, task lookup (exact, or fuzzy for voice), run→task matching, DOI→citation from `data/group_publications.json` |
| `runs.py` | reads `runs/<id>/state.json` → `RunSummary` / `Run`; catches crashed engines (dead pid, or no state after 120 s); sidecar run→task file |
| `trace.py` | builds TraceEvents from state.json, `logs/<step>.log`, `outputs/`, and each Harbor trial (`agent/trajectory.json`, or the live `agent/claude-code.txt` while it runs) |
| `classify.py` | maps a tool call to an event type and title (the contract's mapping rules), and pulls citations out of search output |
| `events.py` | append-only event log per run, so `seq` stays stable across polls and restarts |
| `bridges.py` | lazy `rag` / `tasks.runner`; normalizes passages (dict, dataclass or pydantic); index stats |
| `live.py`, `costs.py`, `planner.py`, `launch.py`, `evolution.py` | v2: live agent output, costs, planner, task start, evolve bridge (V2_NOTES.md) |
| `__main__.py` | `python -m server` runs uvicorn on `$HOST:$PORT` (default `0.0.0.0:8080`) |
| `tests/` | 51 pytest tests, including an end-to-end run through the real agent_runner engine |

## Run
```bash
pip install -r server/requirements.txt        # plus `pip install -e .` for agent_runner, rag/ and tasks/ deps
export MACRAE_TOOL_SECRET=…                    # required; without it every route except /api/health gives 503
python -m server                               # or: uvicorn server.app:app --host 0.0.0.0 --port 8080
curl localhost:8080/api/health
curl -H "X-Macrae-Secret: $MACRAE_TOOL_SECRET" localhost:8080/api/tasks
```
Run it from the repo root, or anywhere: it puts the repo root on `sys.path` before importing `rag` and
`tasks.runner`. Relative `MACRAE_*` paths are looked up in the working directory first, then the repo root.
OpenAPI docs are served at `/api/docs`.

## Test
```bash
pip install -r server/requirements.txt pyyaml
python -m pytest server/tests -q               # 51 passed; no Docker, Harbor, Modal, rag or tasks needed
```
`test_e2e_agent_runner.py` starts a script-only flow with `agent_runner.flows.start_detached` (the call
`tasks.runner` makes), polls `/api/runs/<id>/events` until `done`, and checks there are no gaps or repeats in `seq`.
The repo's own `pytest tests` still passes.
A manual smoke test with uvicorn and curl, with no `rag/`, no `tasks/` and no papers: health returns
`{"ok":true,"papers":0,"chunks":0,"modal":false}`, `/api/search` returns 503, and the voice tools return
200 with a message the agent can say.

## Behaviour worth knowing
- **Auth**: `hmac.compare_digest` on `X-Macrae-Secret`, on every route except `/api/health`. If the secret is
  not set, the server refuses requests (503). `MACRAE_AUTH_DISABLED=1` opens every route, for local development
  only.
- **Events**: each poll rebuilds the candidate events from disk. Each event has stable keys; any key not seen
  before is appended with the next `seq`, and a new batch is sorted by time. An event that shows up late (for
  example a trajectory written when a trial ends) gets a new `seq` and never shifts earlier ones. Events seen live
  from `claude-code.txt` are not repeated when `trajectory.json` appears: they match on tool_call_id or on a hash
  of tool name and arguments or text. The log is mirrored to `$MACRAE_SERVER_DATA/events/<run>.jsonl`.
  `done` = the run is ok/failed/cancelled and the client has every event. Each poll returns at most 500 events.
  Run-level events have `"step": ""`.
- **Mapping**: Read/Grep/Glob/WebFetch of a paper (`paper`, `papers/`, `.pdf`, a DOI, publisher URLs) → `read`.
  The citation is attached when the DOI is a group paper. Reads of other files → `status` ("Opened x"). Bash with
  `rag … search` or a `search_papers`/rag tool → `search`, plus one `cite` event per citation if the output is
  JSON passages. Bash running python/xtb/psi4/openmm/numpy/…, or whose output looks computed (decimals, energy
  units) → `calc`, titled from Claude's Bash `description`, e.g. "Ran water dimer energy (xtb, 0.4 s)". A Bash
  call is held back until its output exists, because the output decides calc vs status. Write/Edit → `write`.
  Assistant text and TodoWrite → `think`. Step ok → `result`. Step failed, trial exception, failed check or
  rejected login → `error`. Script steps (`run:`) are classified from their command in the step log; a `python -m tasks.*` helper is a
  `status` titled from the step's `description:` (read from `<run>/flow.yaml`), and its stderr progress lines
  (`search: '<q>' → N passages`, `openalex: abstract for <doi>: …`) become `search` / `read` events (see
  INTEGRATION.md). A skipped step, which the engine leaves without timestamps, is placed after its upstream ended.
- **Start task**: only the task's declared inputs are accepted, and defaults are filled in before calling
  `tasks.runner.start`. Values must be strings or numbers, at most 500 characters, with no quotes, `$`, `;`, `|`,
  `&`, `<`, `>` or backslash, because flows may render them into shell `run:` commands. At most
  `MACRAE_MAX_ACTIVE_RUNS` task runs (default 4) can run at once; more gets 429.
- **Draining**: while the file `$MACRAE_DRAIN_FILE` exists (deploy/start.py creates it when the Cloudflare
  container gets SIGTERM), task starts answer 503 "the backend is restarting…" (the voice tool says the same).
  Everything else keeps answering while running flows finish.
- **Voice tools** never fail the conversation. An unknown task, a refused start or rag being offline comes back
  as 200 with a `message` / `answer_context` the agent can say. `start_task` also matches by title
  ("methods card"). `run_status` with no id, or `"latest"`, reports the newest run.
- `Run` (from `GET /api/runs/{id}`) adds `flow`, `inputs`, `error` and `engine_status`. Steps add `started`,
  `finished`, `note` and `fanout`, where a foreach parent has `fanout` = item count. Status is mapped to the
  contract's four values: starting → running, crashed → failed.

## Environment
Contract: `MACRAE_TOOL_SECRET`, `MACRAE_PAPERS_DIR`, `MACRAE_INDEX_DIR`, `MACRAE_TASKS_FILE`, `MODAL_PROFILE`,
`MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`, `AGENT_RUNNER_HOME`.
Server-only (optional; deploy may want them in `env.example`): `PORT` (8080), `HOST`, `LOG_LEVEL`,
`MACRAE_MAX_ACTIVE_RUNS` (4, 0 = no limit), `MACRAE_CORS_ORIGINS` (comma list; the Worker proxies same-origin
so it isn't needed), `MACRAE_SERVER_DATA` (default `$AGENT_RUNNER_HOME/macrae`), `MACRAE_AUTH_DISABLED`,
`MODAL_CONFIG_PATH` (default `~/.modal.toml`), `MACRAE_DRAIN_FILE` (set by deploy/start.py on Cloudflare).
`modal` in health is true if the token pair is set, or if `~/.modal.toml` has a `[$MODAL_PROFILE]` section with
a token.

## Assumptions about other modules
- **tasks**: `tasks.runner.start(task_id: str, inputs: dict) -> str` returns the agent_runner run id, from
  `start_detached`. The server has already validated the task id and filled in defaults. Raising
  `ValueError`/`KeyError`/`FileNotFoundError` becomes a 400; any other exception becomes a 500. `tasks/tasks.json`
  is a list of Task or `{"tasks": [...]}`, with `flow` relative to the repo root. Runs started some other way are
  matched to a task by flow file path, then by flow `name` == task id. Script steps that call
  `python -m rag search …` get `cite` events only if that command prints JSON (passages or citations). **Please
  have the methods-card flow use the JSON output.**
- **rag**: `rag.search(query, k=k)` returns Passages (dicts, dataclasses or pydantic all work).
  `rag.format_context(passages) -> (str, list[Citation])`. If `format_context` is missing, the server builds the
  numbered context itself. Health uses `rag.stats()` → `{"papers": int, "chunks": int}` if it exists. Otherwise it
  reads `index/manifest.json|meta.json|stats.json` (keys `papers`/`chunks`), then `index/chunks.jsonl`, then
  counts PDFs under `MACRAE_PAPERS_DIR`. **A `stats()` function in rag would make this exact.**
- **agent_runner**: reads `state.json` (`status started finished pid file name vars jobs_dir steps order`;
  per step `kind status account reward attempt error started finished job_dir output note fanout`) and
  `logs/<sanitized key>.log` lines `[HH:MM:SS] msg`. Job folders are `<jobs_dir>/<sanitized key>-a<n>[-r<m>]/<trial>/`.
  `trace.sanitize` must stay equal to `flows._sanitize`, which a test checks. A step runs on Modal if its
  logged harbor command contains `-e modal` / `--env modal`. Crash detection uses `os.kill(pid, 0)`, so the
  engine processes must run in the same container/PID namespace as the server, which is how `start_detached`
  works.
- **Harbor**: ATIF `trajectory.json` (`steps[].source/message/tool_calls[].function_name/arguments/tool_call_id/
  observation.results[].source_call_id/content`; message and content may be strings or lists of parts). The live
  view uses Claude Code's stream-json in `agent/claude-code.txt`. On Modal that file may only arrive when the
  trial ends; until then the page shows the step log events ("Started Claude Code on Modal, account …").
- **deploy**: install `server/requirements.txt` and run `uvicorn server.app:app --host 0.0.0.0 --port 8080`
  (or `python -m server`). Put `AGENT_RUNNER_HOME` on a persistent volume, so run history and event logs survive
  restarts.
- **web**: poll `GET /api/runs/{id}/events?after=<last seq>` every 1–2 s until `done`. 401/503 means a Worker
  or secret problem; 404 means the run doesn't exist.
- **voice**: tools POST JSON `{"query"}`, `{"task_id"}` (optionally `"inputs"`), `{"run_id"}` with the
  `X-Macrae-Secret` header.
