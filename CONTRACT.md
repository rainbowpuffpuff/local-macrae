# macrae web agent: build contract

A website where you talk (ElevenLabs voice) to an agent that knows the Jungwirth group's papers, answers with
citations (RAG), and when you click a predefined task it runs real work on Modal through Harbor, so every run
leaves a trace: which papers it read, which calculations it did. The page shows that trace live.

```
browser ── Cloudflare Worker (web/ + cloudflare/) ── /api/* proxied ──► backend on AWS (server/)
   │  ElevenLabs voice (signed URL from Worker)                              │  rag/ (search with citations)
   └─────────────── ElevenLabs agent ── server tools (webhooks) ────────────► │  agent_runner flows (tasks/)
                                                                             └─► harbor --env modal (MODAL_PROFILE)
```

Language: Python 3.11 for server/rag/tasks/voice/deploy, plain HTML/CSS/JS (no build step) for web/, a JS
Worker for cloudflare/. Secrets come only from environment variables; never commit keys, papers or indexes.

## Ownership (each module touches ONLY its folders)
| module | owns |
|---|---|
| web | `web/`, `cloudflare/` |
| server | `server/` |
| rag | `rag/` |
| tasks | `tasks/`, `agent_runner/` (only: Modal support, see below) |
| voice | `voice/` |
| deploy | `deploy/`, `Makefile` |

## Environment variables (backend)
`MACRAE_TOOL_SECRET` (shared secret the ElevenLabs tools and the Worker send as header `X-Macrae-Secret`),
`MACRAE_PAPERS_DIR` (default `papers/`), `MACRAE_INDEX_DIR` (default `index/`), `MACRAE_TASKS_FILE`
(default `tasks/tasks.json`), `MODAL_PROFILE` (default `acalincarol`) or `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`,
Claude logins for agent steps: `AGENT_RUNNER_TOKEN_<NAME>` or `ANTHROPIC_API_KEY`, `AGENT_RUNNER_HOME`.
Worker vars/secrets: `BACKEND_URL`, `MACRAE_TOOL_SECRET`, `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`.

## HTTP API (server/, FastAPI, all JSON, prefix `/api`)
Every route except `GET /api/health` requires header `X-Macrae-Secret: $MACRAE_TOOL_SECRET`.
The Worker adds that header when proxying browser requests, so the browser never sees it.

| method + path | body / query | response |
|---|---|---|
| `GET /api/health` | | `{"ok": true, "papers": int, "chunks": int, "modal": bool}` |
| `GET /api/tasks` | | `{"tasks": [Task]}` |
| `POST /api/tasks/{task_id}/start` | `{"inputs": {...}}` (optional) | `{"run_id": str}` |
| `GET /api/runs` | | `{"runs": [RunSummary]}` (newest first, max 50) |
| `GET /api/runs/{run_id}` | | `Run` |
| `GET /api/runs/{run_id}/events` | `?after=<seq>` | `{"events": [TraceEvent], "done": bool}` (poll every 1–2 s) |
| `POST /api/search` | `{"query": str, "k": int=6}` | `{"passages": [Passage]}` |
| `POST /api/tools/search_papers` | `{"query": str}` | `{"answer_context": str, "citations": [Citation]}` (ElevenLabs tool) |
| `POST /api/tools/start_task` | `{"task_id": str}` | `{"run_id": str, "message": str}` (ElevenLabs tool) |
| `POST /api/tools/run_status` | `{"run_id": str}` | `{"status": str, "summary": str, "recent": [str]}` (ElevenLabs tool) |

### Shapes
```jsonc
// Task (tasks/tasks.json; the owner will edit titles/prompts later, keep the schema)
{"id": "methods-card", "title": "…", "subtitle": "…", "icon": "flask", "prompt": "what the agent does, in words",
 "flow": "tasks/flows/methods-card.yaml", "inputs": [{"name": "doi", "label": "DOI", "default": "10.1021/…"}]}

// Passage (rag)
{"id": "doi:10.1021/acs.jctc.5c02051#p3c2", "text": "…", "score": 0.81, "citation": Citation}
// Citation
{"key": "[1]", "title": "…", "authors": "V. Košťál; …", "year": 2026, "journal": "…", "doi": "10.1021/…",
 "page": 3, "url": "https://doi.org/…", "quote": "≤ 240 chars from the passage"}

// RunSummary / Run  (built from agent_runner state.json: ~/.local/share/agent-runner/runs/<id>/state.json)
{"run_id": "…", "task_id": "…", "title": "…", "status": "running|ok|failed|cancelled", "started": 1760000000.0,
 "finished": null, "steps": [{"key": "card[0]", "kind": "agent|run", "status": "…", "account": "…",
 "reward": 1.0, "attempt": 1, "error": ""}]}

// TraceEvent: what the page streams. Built from (a) the run's state.json and step logs, (b) each Harbor trial's
// agent/trajectory.json (ATIF: steps[] with source, message, tool_calls[{function_name, arguments}], observation)
{"seq": 12, "t": 1760000000.0, "step": "card[0]", "type": "status|read|search|calc|write|think|cite|result|error",
 "title": "Read paper/paper.json", "detail": "≤ 600 chars", "citation": Citation|null}
```
Mapping rules for trajectory tool calls → TraceEvent.type: Read/Glob/Grep/WebFetch of a paper or `papers/`
→ `read`; a search_papers/rag call → `search`; Bash running python/xtb/psi4/numpy, or any command whose output
contains numbers from a computation → `calc`; Write/Edit → `write`; assistant text → `think`; step finished →
`result`; exception → `error`. Keep `title` short and human ("Ran water dimer energy (xtb, 0.4 s)").

## RAG (rag/)
Python package `rag` with: `ingest(papers_dir, index_dir, metadata="data/group_publications.json")` (PDF →
text per page via pymupdf, chunks ~800 tokens with page numbers, metadata matched to group_publications.json by
DOI found in the PDF or by fuzzy title), `search(query, k=6) -> list[Passage]` (hybrid: BM25 + embeddings with
`fastembed` BAAI/bge-small-en-v1.5, reciprocal rank fusion), `format_context(passages) -> (str, citations)`
producing numbered `[n]` context for the LLM. CLI: `python -m rag ingest`, `python -m rag search "…"`.
Index on disk under `index/` (no external DB). Must work with zero papers (empty results, not a crash).

## Tasks (tasks/ + agent_runner Modal support)
- agent_runner: a flow-level and step-level `environment: modal` (default docker) that passes `-e modal` to
  `harbor exec` / `harbor run`, exports `MODAL_PROFILE` (default from env), and, because Modal cannot use the
  local `agent-runner/agent-base` image, uses `--task-template` with an `environment/Dockerfile` copied from
  `agent_runner/image/Dockerfile` so Modal builds and caches it. Add a test for the command it builds.
- `tasks/tasks.json` with 2 placeholder tasks the owner will replace, each with a flow in `tasks/flows/`:
  1. `methods-card`: given a DOI from data/group_publications.json, script step pulls RAG passages for it
     (`python -m rag search`), an agent writes a cited methods card (every claim has [n]).
  2. `small-calc`: an agent step on Modal sets up and runs a tiny real computation relevant to the group
     (e.g. ion–water interaction energy with xtb or a 10 ps water box with OpenMM), writes `result.json` and a
     cited explanation; `check:` verifies `result.json` has numbers.
- `tasks/runner.py`: `start(task_id, inputs) -> run_id` using `agent_runner.flows.start_detached` with vars.

## Voice (voice/)
`voice/agent.json` (ElevenLabs Conversational AI agent config: first message, system prompt, LLM =
Claude via ElevenLabs, voice placeholder), `voice/tools.json` (the three server tools above, URL
`{BACKEND_URL}/api/tools/...`, header `X-Macrae-Secret`), `voice/setup_agent.py` (create or update the agent
through the ElevenLabs API using `ELEVENLABS_API_KEY`, prints the agent id). System prompt rules: answer
from search_papers only, always cite `[n]` with the paper's first author + year when speaking, say when the
papers don't cover something, offer the clickable tasks, narrate run progress from run_status in one sentence.
Client tools the page implements: `show_citations(citations)`, `open_run(run_id)`.

## Web (web/) and Worker (cloudflare/)
- Look and feel: follow `.reference/tincan/web` (layout, `style.css` tokens, light/dark, type, spacing).
- Landing: hero line, 2–3 task cards from `GET /api/tasks`, a big "Talk to the agent" button (ElevenLabs via
  `@elevenlabs/client` from a CDN, session from `GET /voice/signed-url` on the Worker), live transcript.
- Run view: clicking a task (or the agent's `open_run`) shows a timeline of TraceEvents (icons per type:
  read 📄, search 🔎, calc 🧮, write ✍️, result ✅, error ⚠️), citations as cards linking to DOIs.
- Works with the backend down: show "agent offline" state, never a blank page.
- Worker (`cloudflare/`, name `macrae`): serves `../web` as assets, proxies `/api/*` to `BACKEND_URL` adding
  `X-Macrae-Secret`, implements `GET /voice/signed-url` (ElevenLabs
  `GET https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?agent_id=…` with `xi-api-key`),
  security headers like the reference. `deploy.sh` runs `npx wrangler deploy`.

## Deploy (deploy/)
`deploy/Dockerfile` for the backend (python 3.11, uv, harbor CLI, `pip install -e .` + server/rag deps, runs
uvicorn on 8080), `deploy/aws.md` + `deploy/aws_deploy.sh` for a single small EC2 instance or ECS Express
service (pick the simplest that gives an HTTPS URL; document the trade-off), `deploy/env.example` listing every
variable above without values, `Makefile` targets: `index`, `serve`, `test`, `web-dev`, `deploy-web`,
`deploy-backend`.

## Dependencies
Python modules list their pip deps in `<module>/requirements.txt` (server, rag, tasks); `deploy/Dockerfile`
installs all of them plus `pip install -e .` (agent_runner). Don't edit the root `pyproject.toml`.

## Done means
Each module ships tests where it has logic (pytest for Python; for web, a `web/smoke.md` checklist), runs them,
and writes `<module>/NOTES.md`: what it built, how to run it, what it assumed about other modules.

---

# v2 addendum: live traces, costs, planner, evolution ("Frankenstein")

Goal: a demo where clicking a task shows, live, the agent deciding what to run and where, then every read,
command and calculation as it happens (BFF data modelling on Modal), with token cost, compute cost and time
growing in real time, and an "Evolution" view proving the agent gets better run over run by learning from traces.

## Ownership (v2)
| module | owns |
|---|---|
| backend | `server/` (new files preferred: `server/live.py`, `server/costs.py`, `server/planner.py`), `tasks/common/` (Modal image wrapper), the env pass-through in `agent_runner/` |
| web | `web/` |
| evolve | `evolve/` (new package), `tasks/flows/*.yaml` only to add the lesson-injection hook |

## Live events (sandbox → backend → page)
- The Modal image gets `/usr/local/bin/claude` as a wrapper around the real binary: it runs the real CLI, passes
  stdout through unchanged (Harbor still tees it to `claude-code.txt`), and also forwards each stream-json line,
  batched every ≤ 1 s, to `POST {MACRAE_LIVE_URL}/api/live/{run_id}/{step}` with header `X-Macrae-Live: <token>`.
  Forwarding failures never break the run. Env vars reach the agent via Harbor agent env (`--ae`), set by the flow
  engine from `MACRAE_LIVE_URL` + a per-run token the server creates at task start (stored in the run folder).
- `POST /api/live/{run_id}/{step}` (no X-Macrae-Secret; per-run token instead) appends raw lines to
  `<run>/live/<step>.jsonl`. `GET /api/runs/{id}/events` merges these into TraceEvents immediately (same mapping
  rules as the trajectory), de-duplicated against the final trajectory when it arrives (stable keys: tool_use id).
- Events stay the v1 shape, plus optional `"cost": {"usd": float, "tokens": {"in": int, "out": int, "cache": int}}`
  and `"elapsed_s": float` on the events that have them.

## Costs and time
`GET /api/runs/{id}` gains `"costs": {"llm_usd", "compute_usd", "total_usd", "tokens": {...},
"by_step": {step: {"llm_usd", "compute_usd", "seconds", "model", "hardware"}}, "wall_s", "phases":
{"plan", "setup", "work", "check"}}`, updated live. LLM cost = tokens × a price table in `server/costs.py`
(per model, input/output/cache read/write; Claude Code's own `total_cost_usd` wins when present). Compute cost =
Modal sandbox seconds × a rate table (CPU core-seconds, memory, GPU type), with the rates in one dict with a
source comment. Planner calls count too.

## Planner (deciding what and where)
On `POST /api/tasks/{id}/start`, before the flow starts, `server/planner.py` makes one Claude call (Anthropic API,
model from env `MACRAE_PLANNER_MODEL`, default `claude-sonnet-5-5`) with the task, its inputs, recent lessons from
evolve, and the hardware options; it returns JSON `{"plan": [stage...], "hardware": "cpu-8|gpu-a10g|...",
"params": {...}, "budget_usd": x, "why": "..."}` that becomes flow vars. It is recorded as the run's first events
(type `plan`, with its token cost) so the page shows the decision. If the API fails, use the task's defaults and
say so in an event.

## Evolution (agent improving from its traces)
Package `evolve/`:
- `evolve.distill(run_dir) -> lessons`: after a run finishes (called by the server when a run ends), one Claude
  call reads the trace (events, errors, timings, check output, costs) and writes structured lessons into
  `$AGENT_RUNNER_HOME/evolve/lessons.jsonl` `{task_id, lesson, evidence: [run_id/step/seq], kind: "do|avoid|setting|tool", created}`;
  reusable scripts the agent wrote that passed checks go to `$AGENT_RUNNER_HOME/evolve/tools/<task_id>/` with a manifest.
- `evolve.context(task_id, k=8) -> str`: the best recent lessons + tool list, injected into each agent step's
  instruction (flows reference `{{ vars.lessons }}`, filled by the server when starting the run) and into the planner.
- `evolve.metrics() -> {"by_task": {task_id: [{"run_id", "ok", "reward", "wall_s", "total_usd", "lessons_used"}]}}`
  for `GET /api/evolution` (backend wires the route).
- `evolve.export_dataset(out) -> path`: every finished agent trajectory (ATIF) with its instruction, lessons used and
  check outcome as JSONL (`{"messages": [...], "reward": r, "run_id": ...}`) for fine-tuning; only reward ≥ 1 in the
  "sft" file, all in "all". CLI: `python -m evolve distill|context|metrics|export`.

## Web (v2)
Current run panel: events appear live (no waiting for step end), a running cost meter (LLM $, compute $, total,
tokens) and time per phase, the planner decision as the first card ("Decided: GPU A10G, 3 stages, budget $2 —
because …"). A new "Evolution" panel: per task, run-over-run sparkline of time / cost / success, and the lessons
learned with links to the runs they came from.
