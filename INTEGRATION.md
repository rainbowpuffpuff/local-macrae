# Integration notes

> **Later change: the backend moved from AWS to Cloudflare.** See CLOUDFLARE.md (what changed, secrets, what was
> verified) and deploy/cloudflare.md (every command). The notes below describe the integration as it was done, with
> the AWS backend; module behaviour is unchanged except where CLOUDFLARE.md says otherwise.

Checked the six modules against CONTRACT.md and each other's NOTES.md, fixed the mismatches on the smaller side,
and ran everything locally. Python 3.12.3 (this machine has no 3.11), Node 18.19, Harbor 0.24.0, Modal 1.6.1.
Nothing is committed. `.venv/` (git- and docker-ignored) holds the installed requirements.

## What I fixed

| # | seam | problem | fix (side) |
|---|---|---|---|
| 1 | tasks → server (trace mapping) | The task flows' script steps (`python -m tasks.prepare_methods` / `prepare_calc`) run their RAG searches in-process and print `search: '<q>' → N passages` on stderr. The engine logs all of stderr as **one** `stderr: …` entry, and the server ignored that entry. The `$ python -m tasks.…` line was classified as **`calc`** (🧮) because it contains "python". Result: a methods-card run showed no searches and no citations, plus a fake calculation. | **server/trace.py**: `stderr:` entries are parsed line by line. Search lines become `search` events and `openalex: abstract for <doi>` becomes a `read` event with the paper's citation. `-m tasks.*` commands become `status`, titled from the step's `description:` in `<run>/flow.yaml`. The search title puts the count first so the 90-char clip doesn't cut it off. **server/classify.py**: added the past tenses the flows' descriptions need ("Pulled", "Copied", …). **tasks/prepare_methods.py, prepare_calc.py**: add `"citations": [...]` to their JSON output, which the server already turns into `cite` events. |
| 2 | tasks ↔ deploy | `tasks/requirements.txt` has `harbor[modal]>=0.24`, but Harbor is `Requires-Python >=3.12`. `deploy/Dockerfile` installs every requirements file into its Python 3.11, so **the image build would fail** at that layer. Harbor gets no import from Python here; it is only run as a CLI, which the Dockerfile already installs as a uv tool on 3.12. | **tasks/requirements.txt**: `harbor[modal]>=0.24; python_version >= "3.12"`. |
| 3 | server tests ↔ Makefile | `make test` used `--import-mode=importlib`, but `server/tests` does `from conftest import …`, which only works in pytest's default mode. `make test` failed at collection; a bare `pytest` passed. | **Makefile**: default import mode, so it now matches root `pytest`. The comment says test basenames must stay unique. **deploy/tests/test_artifacts.py** checks the new command line. |
| 4 | web ↔ Makefile | `make test` didn't run `web/tests` (18 page-logic tests). | **Makefile**: also runs `npm test` in `web/`. |
| 5 | tasks → web | Inputs can carry `options` (small-calc's ion). Tasks asked for a select, but the page showed a free-text field, so a typo gave a 400. | **web/app.js** (`inputControl`) + **style.css**: a `<select>` with the default chosen. |
| 6 | agent_runner → server (trace order) | The engine stamps no time on a **skipped** step. The server fell back to the run's start, so "Skipped collect" was the 2nd event, above everything it depended on. | **server/trace.py**: a skipped step without times is placed at the latest step finish. |
| 7 | tasks flows ↔ agent_runner | `until: "reward >= 1"`: when a trial errors, `reward` is `None` and the expression raises. The page then showed an extra ⚠️ "Retry condition failed: '>=' not supported…" for every attempt. | **tasks/flows/*.yaml**: `until: "(reward or 0) >= 1"`. |
| 8 | agent_runner → page | The "no Claude login" error, which the page shows verbatim, said to run `agent-runner token set NAME`. That command doesn't exist; it's `agent-runner accounts set NAME`. | **agent_runner/pool.py**: corrected the message. This is a vendored file, so the edit should go upstream too (see VENDORED.md). |
| 9 | deploy/env.example | The optional knobs other modules read weren't listed. Voice asked for its variables to be there. | Added commented `AGENT_RUNNER_MODAL_IMAGE`, `MACRAE_HARBOR_ENV`, `MACRAE_MAX_ACTIVE_RUNS`, `MACRAE_EMBEDDER`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_LLM`, `ELEVENLABS_API_BASE`. `envtool render` passes them through unchanged. |

New test: **tests/test_integration.py** (2 tests, real modules end to end). It runs `python -m rag ingest` on rag's
fixture PDFs, then the real `methods-card` flow through the agent_runner engine, with tasks' fake `harbor` standing in
for Modal and Claude. It then reads the server's `/api/runs`, `/api/runs/{id}/events` and `/api/tools/run_status`
and checks:
- 6 `search` + `cite` events (one with the paper's DOI) on the script step, and no `calc`;
- `read`/`think`/`result` on the agent step;
- `seq` with no gaps;
- on the failure path, the order and the login message.

With fix 1 disabled, it fails (`assert 0 == 6`).

Module NOTES updated where behaviour changed (server, tasks, web, deploy; plus a line in web/smoke.md).

Checked and found consistent (no change needed):
- Routes and bodies between web, the Worker, server and voice/tools.json.
- The `X-Macrae-Secret` header name, and `/api/health` open while everything else needs the secret.
- `?after` exclusive, and 404 for an unknown run.
- Client tool names and parameters (`show_citations`/`open_run`).
- rag's CLI flags as tasks calls them (`--k`, `--json`).
- `server.app:app` as deploy's start.py finds it.
- `harbor.IMAGE_DIR` (tasks had already fixed it).
- Harbor gets the server's environment (only `CLAUDE*` is stripped), so the container's `MODAL_TOKEN_*` reach it.
- `start_detached` returns right after spawning, which fits the 30 s voice tool timeout.

## What I verified

**Install**: `pip install -e ".[test]" -r server/… -r rag/… -r tasks/… -r deploy/requirements.txt`, which exited 0.
The 3.11 image's dependency layer resolves after fix 2. The original line fails:
```
$ uv pip compile --python-version 3.11 server/requirements.txt rag/requirements.txt tasks/requirements.txt deploy/requirements.txt
→ 68 pins (fastapi 0.143.0, fastembed 0.9.0, modal 1.6.1, pymupdf 1.28.2), no harbor
$ uv pip compile --python-version 3.11 <original tasks/requirements.txt>
error: … the requested Python version (>=3.11) does not satisfy Python>=3.12 and you require harbor[modal]>=0.24 …
```

**Tests**
```
$ python -m pytest -q                       (repo root)
248 passed, 1 warning                        (warning: starlette's httpx TestClient deprecation)
$ make test                                 exit 0
248 passed · cloudflare: # tests 25 # pass 25 # fail 0 · web: # tests 18 # pass 18 # fail 0
per module: tests 7 · server/tests 51 · rag/tests 50 · tasks/tests 65 · voice/tests 21 · deploy/tests 54 (all passed)
```

**Flows**
```
$ python -m agent_runner flow check tasks/flows/methods-card.yaml
level 0: sources (run)
level 1: card (agent)
level 2: collect (run)
$ python -m agent_runner flow check tasks/flows/small-calc.yaml
level 0: prepare (run)
level 1: calc (agent)
level 2: collect (run)
$ python -m tasks.runner check
ok: 2 tasks in /app/local-macrae/tasks/tasks.json
```

**Server, zero papers**: `python deploy/start.py --port 18080`, the `make serve` / container entrypoint. It ran
with an empty `AGENT_RUNNER_HOME` and no `papers/` or `index/`.
```
$ curl /api/health
{"ok":true,"papers":0,"chunks":0,"modal":false}
$ curl /api/tasks                                             (no secret)
401
$ curl -H "X-Macrae-Secret: …" /api/tasks
methods-card (tasks/flows/methods-card.yaml, inputs [doi]), small-calc (tasks/flows/small-calc.yaml, inputs [ion])
$ curl -H … -d '{"query":"ion specific effects at the air/water interface","k":6}' /api/search
{"passages":[]}
$ curl -H … -d '{"query":"ion specific effects"}' /api/tools/search_papers
{"answer_context":"The group's paper index has nothing on this. Say that the papers available here don't cover it, …","citations":[]}
$ curl -H … -d '{"task_id":"methods-card"}' /api/tools/start_task
{"run_id":"20261008-180522-methods-card-ab56","message":"Started “Methods card for a group paper” (run …). It runs on Modal …"}
$ curl -H … -d '{"inputs":{"ion":"Ca2+"}}' /api/tasks/small-calc/start
{"run_id":"20261008-180620-small-calc-b766"}
$ curl -H … -d '{"inputs":{"ion":"Ca2+; rm -rf /"}}' /api/tasks/small-calc/start
{"detail":"input ion contains characters that are not allowed (quotes, $, ;, |, &, <, >, \\)"} [400]
$ curl -H … -d '{"task_id":"nope"}' /api/tools/start_task
{"run_id":"","message":"There is no task called “nope”. Available tasks: methods-card (…), small-calc (…)."}
```
Events of the methods-card run (`GET /api/runs/{id}/events`), done after about 4 s. Abridged; skip and failure
order is from after fix 6:
```
1  status  -        Started Methods card for a group paper
3  status  sources  Pulled the paper's passages from the RAG index (plus its abstract when…
4–9 search sources  Searched papers: 0 passages for “Bayesian Learning for Accurate and Robust Biomole…” (×6 queries)
10 read    sources  Read the abstract of 10.1021/acs.jctc.5c02051 (OpenAlex)
11 cite    sources  Cited Košťál 2026: Bayesian Learning for Accurate and Robust Biomolecular Force Fields
12 result  sources  Finished sources
13 error   card     card failed   ← "RuntimeError: no Claude login: `agent-runner accounts set NAME` … or ANTHROPIC_API_KEY"
   status  collect  Skipped collect
   error   -        Run failed
```
To get past the login gate, I used a dummy `ANTHROPIC_API_KEY`. The **real Harbor 0.24 CLI** then ran with
`-e modal --task-template <run>/modal-template` and stopped at Modal auth. The page shows:
```
12 status calc     Started Claude Code on Modal, account api-key
13 error  calc     Agent run failed: AuthError: Modal profile 'acalincarol' was not found in /root/.modal.toml …
14 status calc     Attempt 1 did not pass (failed)
15 status calc     Started Claude Code on Modal, account api-key, attempt 2
16 error  calc     Agent run failed: AuthError: …
17 status calc     Attempt 2 did not pass (failed)
18 error  calc     calc failed
19 status collect  Skipped collect
20 error  -        Run failed
$ curl -H … -d '{"run_id":"latest"}' /api/tools/run_status
{"status":"failed","summary":"“Ion–water binding, computed live” failed after … at step calc: AuthError: Modal profile …"}
```

**rag ↔ server, live**: I ingested rag's two fixture PDFs into the running server's temporary index with the real
fastembed model. Health updated without a restart:
```
{'papers': 2, 'chunks': 2, 'embedder': 'fastembed:BAAI/bge-small-en-v1.5', 'unmatched': []}
/api/health → {"ok":true,"papers":2,"chunks":2,"modal":false}
/api/search "vesicle membrane curvature" → doi:10.1039/d6sm00560h#p1c1 score 1.0 [1] 2026 p. 1 · doi:10.1093/glycob/cwag064#p1c1 0.49 [2]
/api/tools/search_papers → "[1] Schachter et al. 2026, "Vesicle internalization …", Soft Matter, p. 1, doi:…", citation keys ['[1]', '[2]']
```

**Worker ↔ real backend**: `cloudflare/dev/serve.mjs` (worker.js on Node) with `BACKEND_URL` pointing at the
server above. The browser sent no secret:
```
GET /api/health  → 200 {"ok":true,…}        GET /api/tasks → 200 (secret added by the Worker)
GET /api/runs/{id}/events?after=15 → seqs [16..20], done True
POST /api/tools/start_task (no secret)       → 403 {"error":"this endpoint is for the voice agent"}
POST /api/tools/run_status (with secret)     → 200 (ElevenLabs may call through the Worker)
GET /voice/signed-url                        → 503 {"error":"voice is not configured", …}
backend stopped: GET /api/health             → 502 {"error":"agent offline","offline":true,…}; GET / still 200
```

**Voice**: `BACKEND_URL=http://127.0.0.1:18080 python voice/setup_agent.py --dry-run` exited 0. The tool URLs are
exactly `…/api/tools/{search_papers,start_task,run_status}`, task ids come from tasks.json, and no `{PLACEHOLDER}`
is left unfilled. It warns that 127.0.0.1 isn't reachable from ElevenLabs, which is correct.

## Not verified here
- A `docker build` (no Docker here) and anything on AWS or Cloudflare.
- A real Modal or Claude agent step, and a real ElevenLabs agent creation or voice session (no keys).
- The page in a browser after my `<select>` change. Only `node --check` and the node tests were run; it's on
  web/smoke.md.

## What still needs the owner
1. **Keys** in `deploy/.env` (`cp deploy/env.example deploy/.env`):
   - `MACRAE_TOOL_SECRET`
   - a Claude login: `ANTHROPIC_API_KEY` or `AGENT_RUNNER_TOKEN_<NAME>` from `claude setup-token`
   - Modal: `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`. On AWS, leave `MODAL_PROFILE` unset. Locally,
     `modal token new --profile acalincarol` works too; without either, agent steps fail exactly as shown above.
   - `ELEVENLABS_API_KEY`, optionally `ELEVENLABS_VOICE_ID`.
2. **Papers**: put the PDFs in `papers/`, then run `make index`. Expect about 25–30 min for about 480 papers on the
   first run. Papers whose preprint title differs from the journal title need a sidecar `<name>.json` with the
   journal DOI (see rag/NOTES.md). Then run `make backend-data`, or `make backend-ingest` on the server.
3. **AWS**: `make deploy-backend` (first build about 8 min, never run yet), then `make smoke`.
4. **Cloudflare + voice**, in this order, once the backend has its public URL:
   - `npx wrangler login`
   - `make deploy-web web-secrets`
   - `make voice-setup` (it prints `ELEVENLABS_AGENT_ID`; put it in `deploy/.env`)
   - `make web-secrets` again
   - Re-run `make voice-setup` after editing tasks.json.
5. **First real task runs**: one of each task on Modal.
   - Harbor 0.24's `exec -p` ignores the task template's Dockerfile, so each agent step installs Claude Code first
     (1–2 min). For a snappier demo, push `agent_runner/image` to a public registry and set
     `AGENT_RUNNER_MODAL_IMAGE` (tasks/NOTES.md).
   - Also confirm the ElevenLabs LLM id `claude-sonnet-5-5` in voice/agent.json is accepted. If it isn't, the 422
     names the field, and `ELEVENLABS_LLM` overrides it.
6. **Choices left alone (not mismatches)**:
   - The server doesn't call `rag.warmup()` at startup, so the first search after a restart loads the model and
     builds BM25 (a few seconds).
   - The run view's `inputs` show what the client sent; the runner canonicalises option case, e.g. `ca2+` → `Ca2+`.
   - The agent_runner edits (Modal support from tasks, plus fix 8) are local to a vendored copy and need upstreaming.
