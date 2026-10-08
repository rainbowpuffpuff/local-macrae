# Macrae

**A research agent for the Pavel Jungwirth group at IOCB Prague. It knows the group's papers, runs real
computational chemistry in traced cloud sandboxes, and gets better at it from its own traces.**

Live: <https://macrae.acalincarol.workers.dev> (no login) · Architecture: [`docs/architecture.html`](docs/architecture.html)
· Traces: [`docs/TRACES.md`](docs/TRACES.md) · Demo script: [`docs/DEMO.md`](docs/DEMO.md)

You talk to **Jarvis**, by voice (ElevenLabs) or by typing. Jarvis chats like a capable colleague and, when the
group's work is relevant, answers from the group's own papers with numbered citations (first author, year, page).
When you click a task, or ask for one, Macrae plans the run, starts Claude Code in a sandbox on Modal through
Harbor, and streams every paper it reads, every command and calculation it runs, and the cost in dollars onto the
page while it works. Every run leaves a downloadable trace. After each run Macrae distills lessons from that trace,
and when the trace shows a missing capability it builds, tests and installs a tool for itself, under an authority
policy that the agent cannot change.

## Contents

- [Status: what works, what is proven, what is not](#status-what-works-what-is-proven-what-is-not)
- [Proof: dusk → dawn](#proof-dusk--dawn)
- [The group](#the-group)
- [The Frankenstein brief](#the-frankenstein-brief)
- [How to use it](#how-to-use-it)
- [How it was built](#how-it-was-built)
- [How it runs](#how-it-runs)
- [Repository map](#repository-map)
- [Run it locally](#run-it-locally)
- [Deploy](#deploy)
- [Documentation index](#documentation-index)
- [Data: the group's publication list](#data-the-groups-publication-list)
- [agent-runner](#agent-runner)

## Status: what works, what is proven, what is not

Checked on the live site on 2026-10-09 (UTC+2) after the last deploy, unless a line says otherwise.

| | what | how it was checked |
|---|---|---|
| ✅ live | **Chat with Jarvis** over the group's papers: 97 group papers, 1210 chunks, BM25 + `bge-small` embeddings; answers cite `[n]` with author, year and page, and say which parts don't come from the papers | `POST /api/chat` on the live site, e.g. "What charge scaling factor does the group use in ECC?" → "about 0.75 … q_eff = q/√ε_el … [3][4]", 5 cited sources, $0.013 per answer |
| ✅ live | **Voice**: ElevenLabs agent with server tools (`search_papers`, `start_task`, `run_status`) and page tools (`show_citations`, `open_run`) | `/voice/signed-url` returns a signed URL; tools are webhooks to the Worker with a shared secret |
| ✅ live | **Real research runs** on Modal: *Ion–water binding, computed live* (PySCF DFT, B3LYP/def2-SVP scan + counterpoise def2-TZVP, full vs ECC-scaled charges) | see [Proof](#proof-dusk--dawn): every run below is a real Claude Code agent on Modal with its trace |
| ✅ live | **Live trace + costs**: every tool call streams to the page within ~1 s; LLM $ from tokens, compute $ from Modal seconds; per answer, per step, per run | runs below; prices with sources at `/api/costs/prices` |
| ✅ live | **Learning from traces**: lessons distilled after each run and fed to the next run of the task | 12 real lessons from the first runs, saved in [`proof/live-before-final-deploy/evolution.json`](proof/live-before-final-deploy/evolution.json) (e.g. "`python3 -m venv` has no pip in this sandbox: use `--without-pip` and get-pip") |
| ✅ live | **Authority does not evolve**: a forge whose tests failed in a fresh sandbox was **rejected** at install, with the reason in the ledger | [`proof/live-before-final-deploy/capabilities.json`](proof/live-before-final-deploy/capabilities.json) |
| ✅ live | **Public without login**: per-IP / per-session / global rate limits, daily spend cap ($20), kill switch, inputs never reach a shell, paper text fenced as untrusted data for the LLM; benchmark, forge and kill switch need an operator secret | `/api/safety`; a `POST /api/benchmark/dusk` without the secret gets 403 |
| ✅ live | **Downloadable proof**: `trace.zip` (Harbor trajectories, logs, events, costs) and a self-contained HTML report for every run | buttons in the run view; `GET /api/runs/{id}/trace.zip`, `/report.html` |
| 🟡 built, tested | **Methods card** task (an agent writes a methods summary of a group paper where every claim is cited) | end-to-end test with a fake Harbor; not re-run live after the last deploy |
| 🟡 built, tested | Full capability lifecycle gap → create → test (fresh sandbox) → install → use; the pinned `calc-image` capability | 500+ tests, see below; live result in [Proof](#proof-dusk--dawn) |
| ✅ on the page | The capability ledger with the Authority card, the dusk → dawn comparison (Evolution tab), and the manuscript a finished run wrote | `web/frankenstein.js`, `web/manuscript.js`; node tests on the real live payloads |
| ❌ not built | The two BFF research tasks (Bayesian charge scaling for acetate; Ca²⁺–acetate binding with error bars) | scouted in `research/scout/`, ideas approved, not built |

Tests: `make test` runs every module's pytest, the Worker's and the page's node tests. At the final commit:
**Python 513 passed, 3 skipped · Worker 44 passed · page 87 passed** (Harbor, Modal and Claude are faked in tests;
the live runs are the real check).

### Known gaps

- **The BFF tasks** were designed but not built; only the two tasks above are on the site.
- **agent-runner marks an agent step `ok` when Claude hits a rate or session limit and the step has no strict
  check.** That is how parts of the v3 and w4 waves were silently dropped (see [How it was built](#how-it-was-built));
  their work was recovered by hand from the Harbor artifacts and merged. The fix is to treat an agent exception as a
  failed attempt; not done yet.
- Lessons and capabilities are kept in R2 since this deploy (`state/evolve/`); the lessons learned before it were
  lost when the container restarted, and are kept as a snapshot in `proof/live-before-final-deploy/`.
- The manuscript is shown when a run has finished; it is not replayed edit by edit while the run goes, although
  the API serves the full edit stream (`GET /api/runs/{id}/manuscript`).
- The gap recogniser that reads traces sometimes lists shell fragments as package names (`2>&1`, `tail` in the
  ledger above); the decision it leads to (bake the packages into a pinned image) is right.
- The ElevenLabs agent's prompt in `voice/agent.json` ("chat naturally") is in the repo; pushing it to ElevenLabs
  needs `make voice-setup` with the ElevenLabs key.

## Proof: dusk → dawn

**The first live runs (2026-10-08, before the final deploy).** Real Claude Code agents on Modal; numbers from
`/api/evolution`, saved in [`proof/live-before-final-deploy/`](proof/live-before-final-deploy/).

| run | task | result | wall time | cost (LLM + compute) | lessons distilled |
|---|---|---|---|---|---|
| `20261008-190228-small-calc-229d` | Ion–water binding, Na⁺ | ✅ passed its check (reward 1) | 355 s | $0.277 ($0.271 + $0.006) | 3 |
| `20261008-192909-small-calc-3935` | Ion–water binding | ✅ passed (reward 1) | 300 s | $0.247 ($0.242 + $0.005) | 4 |
| `20261008-202740-forge-2742` | forge `small-calc-env` (the gap both runs showed: ~40 s reinstalling PySCF) | ❌ built and tested, **rejected at install**: its tests failed in a fresh sandbox | 518 s | $0.336 | 5 |

The first verified run (Harbor → Modal, started from a laptop the same day) found a Na⁺–water binding energy of
−25.5 kcal/mol at 2.20 Å (PySCF B3LYP/def2-TZVP with counterpoise); that run is kept as a replayable bundle in
[`research/demo/small-calc-na/`](research/demo/small-calc-na/) (trajectory, manuscript edit stream, figure).

**Dusk → dawn on the live site (2026-10-09).** The fixed suite (small-calc Na⁺ and K⁺) at *dusk* (nothing learned:
no lessons, no capabilities) and at *dawn* (everything learned since). `GET /api/dawn-report` gives the comparison;
the page shows it in **Evolution → Dusk → Dawn**.

<!-- PROOF-DUSK-DAWN -->

## The group

[Pavel Jungwirth](https://jungwirth.group.uochb.cz/en)'s group at the Institute of Organic Chemistry and
Biochemistry of the Czech Academy of Sciences (IOCB Prague) uses molecular simulation, from quantum chemistry to
classical molecular dynamics, to understand ions and molecules in water: specific ion effects and ions at aqueous
interfaces, how ions and peptides bind to and cross cell membranes, the early chemistry of alkali metals in water,
and the force fields all of this rests on. A thread through much of that work is the **electronic continuum
correction (ECC)**: scaling ionic charges by about 0.75–0.8 so that a non-polarizable force field accounts for
electronic polarization. The group's newest tool, **BayesicForceFields (BFF)** (Košťál, Shanks, Jungwirth,
Martinez-Seara, *JCTC* 2026), learns force-field partial charges as a probability distribution, fitted to
*ab initio* molecular dynamics through a Gaussian-process surrogate and Markov chain Monte Carlo.

Macrae is built on top of that work and is meant to celebrate it: it reads the group's papers, cites them by name,
and runs research tasks that extend them.

- `data/group_publications.json`: 479 publications of the group (2004–2026) from the IOCB site.
- The paper index (RAG, retrieval-augmented generation) is built from the PDFs the owner provides (97 group papers
  for the demo). PDFs and the index are never committed; the index reaches the server through R2 (below).

## The Frankenstein brief

The hackathon track "Frankenstein":

> Build an agent that can build itself. It must recognise a missing capability, create it, test it, install it and
> use it again later. Its capabilities may evolve, but its authority may not.
> "By dawn, show a creature that learned to do things it could not do at dusk."

How Macrae answers it:

| brief | in Macrae |
|---|---|
| **recognise** a missing capability | An agent step that needs something it doesn't have says so (`CAPABILITY_GAP: <name>: <why>`); the server records a `gap` event. Slow installs and version breakage in the traces count too. |
| **create** and **test** it | A dedicated flow step in a Modal sandbox writes the tool *and* its tests and runs them; the step's check passes only if the tests do. Environment capabilities (a pinned calculation image) are tested by building them on Modal and running a smoke command. |
| **install** it | The server checks the SHA-256, the test result and the manifest against a fixed policy, then adds it to the capability registry (`install` event). |
| **use it again** | Later runs get installed tools mounted in their sandbox and listed in their instruction (`use` events), plus the lessons distilled from earlier traces. |
| **authority may not evolve** | `server/policy.py` is a set of constants no capability can change: tools run only inside the sandbox, get no secrets, reach no new network hosts, cannot write the registry, and have a maximum runtime. A manifest asking for more is `rejected` with the reason. |
| **dusk → dawn** | A fixed benchmark suite runs twice: at *dusk* (no capabilities, no lessons) and at *dawn* (everything learned). The Dusk → Dawn view and `GET /api/dawn-report` compare success, time, setup time, $ LLM, $ compute and errors run for run. |

The lessons it is expected to learn are real ones from the first Modal runs: about a minute reinstalling PySCF on
every run, a 4-minute `sleep` while waiting, a virtual environment created without pip (`ensurepip` missing), and a
170 s agent image build. The learned fix is, among others, a pinned calculation image with the packages
preinstalled.

## How to use it

Open the site. Nothing to install and no login.

1. **Ask.** Type in the composer, or press **Talk** and speak. Ask about the group's work ("What charge scaling
   does the group use for calcium, and why?") or anything else. `[n]` in an answer opens the source card: title,
   authors, journal, year, page and a DOI link. Jarvis says which parts come from the papers and which don't. Each
   answer shows what it cost.
2. **Run a task.** Open the right panel (**Tasks & runs**) and press **Run** on a task, or ask Jarvis to run one.
   The page shows an estimate first.
   - *Ion–water binding, computed live*: pick an ion (Li⁺, Na⁺, K⁺, Mg²⁺, Ca²⁺). An agent runs DFT (PySCF) on
     Modal, a B3LYP/def2-SVP distance scan plus a counterpoise-corrected def2-TZVP energy at the minimum, and compares
     it with full and ECC-scaled (×0.75) point charges. It keeps a lab notebook and writes a short manuscript with
     equations, a figure and citations of the group's papers. About 5–6 minutes, about $0.25–0.35.
   - *Methods card for a group paper*: give a DOI. An agent writes a methods summary in which every claim cites a
     passage of the paper.
3. **Watch it work.** The run view opens: first the planner's decision (hardware, stages, budget, and why), then a
   live timeline of what the agent does (📄 read, 🔎 search, 🧮 calc, ✍️ write, 🔖 cite, ✅ result, ⚠️ error), with
   a cost meter (LLM $, compute $, tokens, time per phase). When it finishes, the manuscript it wrote is shown with
   its math and figure.
4. **See what it learned.** The **Evolution** tab shows the lessons distilled from each run's trace, run over run per
   task, the **Capabilities** ledger (gap → create → test → install → use, and rejections) with the fixed
   **Authority** card, and the **Dusk → Dawn** comparison.
5. **Take the proof with you.** Every run has **Download trace** (a zip of everything the run did) and **Open
   report** (a self-contained HTML page). See [`docs/TRACES.md`](docs/TRACES.md).

Limits for a public site: per-visitor and global rate limits on task starts, 20 chat answers a minute per address, a
global daily spend cap, and a kill switch for the operator.

## How it was built

Macrae was built in about a day, mostly by the same machinery it uses to do research: `agent_runner` flows of
Claude Code agents (Claude Opus 5.5) in Harbor sandboxes, spread over three Claude accounts. Each wave was a flow:
a contract (`CONTRACT.md`), one agent per module working in parallel on its own branch or copy, then an integrator
agent that merged and tested. 28 flows ran on 2026-10-08 (the flow files are in `.reference/`, which is not in git).

| when (UTC+2) | wave | what it added |
|---|---|---|
| Oct 8, 17:50 | data | the group's publication list from the IOCB site API: 479 papers ([`scripts/fetch_group_pubs.py`](scripts/fetch_group_pubs.py)) |
| 19:29 | agent-runner | the flow engine (agents as traced flows through Harbor, retries until a check passes, accounts) |
| 19:38 → 20:50 | v1 | the web agent: FastAPI backend, RAG, task flows, ElevenLabs voice, the page, Cloudflare Worker + Container + R2 ([`INTEGRATION.md`](INTEGRATION.md)) |
| 20:43 → 21:53 | v2 | live traces from inside the sandbox, costs in dollars, a planner that decides hardware and budget, lessons distilled from traces |
| 21:33 → 22:25 | v3 "Frankenstein" | capability gaps, the forge flow (create → test in a fresh sandbox → install), the fixed authority policy, dusk → dawn benchmark, research protocol (lab notebook + manuscript written through Write/Edit) |
| 21:41 | RAG | 97 group papers (PDFs, not in git) indexed: 1210 chunks |
| 21:55 → 22:28 | w4 | ten parallel jobs: public safety, costs UX, trace download, docs, voice, proof, design, … |
| Oct 9, 00:20 → | final integration | by hand with Claude Code, see below |

What went wrong, and how it was found. Many agent steps in v3 and w4 hit the Claude rate or session limit
("You've hit your session limit"), and agent-runner still marked them `ok` (see [Known gaps](#known-gaps)). The
integrators then applied partial results. Reading the test failures and diffing against the Harbor artifacts of
each step showed three losses:

1. The v3 integration took the new task flows (which call `checks.py … --notes` / `--research`) but not the
   `checks.py`, `research.py` and `calc-image` capability that came with them. **Every task run on the live site
   would have failed its check.** Recovered from the v3 tasks agent's artifacts.
2. The same apply was made from an older tree and **reverted the typed chat** (`/api/chat`) committed minutes
   before. Re-applied.
3. Four finished w4 branches (docs, costs UX, trace download, public safety) were never merged, because the w4
   integrator hit the limit; five other w4 jobs (QA end-to-end, concurrency, voice, proof pack, design polish) left no branch. Merged by hand. Three gaps were closed on the way: the
   benchmark, forge and kill-switch routes were open to any visitor, the chat had no rate limit or spend-cap check, and
   what Macrae learned was not synced to R2, so it was lost on every container restart.

The v3 page (capability ledger, dusk → dawn, manuscript) was never wired either; it was built in the final
integration (`web/frankenstein.js`).

## How it runs

```
 browser ──────────────► Cloudflare Worker "macrae"  (cloudflare/)
   │  page (web/)          • serves web/            • /voice/signed-url (ElevenLabs key stays here)
   │  voice ◄──┐           • /api/* → container, adding the shared secret (the browser never sees it)
   │           │           • rate limits, security headers
   │           │                 │ Durable Object "MacraeBackend" (exactly one instance, sleeps after 2 h idle,
   │           │                 ▼ never while a run is going)
   │           │           Cloudflare Container (deploy/Dockerfile, standard-1: ½ vCPU, 4 GiB)
   │           │             server/  FastAPI: tasks, runs, trace events, chat, costs, planner, capabilities
   │           │             rag/     BM25 + embeddings over the group's papers, [n] citations with page
   │           │             evolve/  lessons, capability registry, dusk → dawn benchmark, dataset export
   │           │             agent_runner/ flows ──► harbor exec --env modal ──► Modal sandbox per agent step
   │           │                 │                                               Claude Code (Claude Opus 5.5),
   │           │                 │  mirror: every step + 60 s                    PySCF; streams
   │           │                 ▼                                               every line back to /api/live
   │           │           R2 bucket "macrae-data": index/, state/runs, state/jobs (the traces), state/macrae,
   │           │                                     state/evolve (lessons, capabilities, benchmarks)
   │           │
 ElevenLabs agent "macrae" (voice "Jarvis", Claude via ElevenLabs) ── tool webhooks ──► Worker /api/tools/*
```

- **Cloudflare Worker + Container + R2.** One Worker serves the page and fronts everything. The backend is a
  Cloudflare Container owned by a Durable Object (`MacraeBackend`), so there is exactly one backend instance; it
  sleeps after 2 hours without use but never while a run is going, and on a restart it pauses task starts and gives
  running flows up to 14 minutes to finish. The container's disk is temporary, so `deploy/r2sync.py` mirrors runs,
  Harbor traces and server event logs to the R2 bucket `macrae-data` after every step and every 60 s, and restores
  them on start. The container reaches R2 through the Worker (`http://r2.macrae`), so it needs no S3 keys. Details:
  [`CLOUDFLARE.md`](CLOUDFLARE.md), [`deploy/cloudflare.md`](deploy/cloudflare.md).
- **Harbor on Modal.** Tasks are `agent_runner` flows (`tasks/flows/*.yaml`): script steps run in the container,
  agent steps run Claude Code through [Harbor](https://github.com/harbor-framework/harbor) with `--env modal`, one
  Modal sandbox per step, CPU or GPU. Harbor saves each trial: the ATIF `trajectory.json`, the raw Claude Code
  stream, the reward from the step's `check:`, and the files the agent changed. A wrapper around `claude` in the
  sandbox image (`tasks/common/`) forwards every stream line to `POST /api/live/{run}/{step}` within about a second,
  so the page shows tool calls while the agent is still working.
- **ElevenLabs.** The voice agent (`voice/agent.json`, `voice/tools.json`, created by `voice/setup_agent.py`) runs
  Claude through ElevenLabs. Its server tools (`search_papers`, `start_task`, `run_status`) are webhooks to the Worker with a shared secret; its page tools (`show_citations`, `open_run`)
  run in the browser. The browser gets a short-lived signed URL from the Worker; the ElevenLabs key never leaves it.
- **Anthropic API.** The typed chat (`POST /api/chat`, streamed), the planner (one call before each run decides
  hardware, stages and budget) and the lesson distiller call Claude directly with the container's API key.
- **Costs.** LLM $ = tokens × a price table (Claude Code's own total wins when it reports one); compute $ = Modal
  sandbox seconds × a rate table. Both live in `server/costs.py` with their sources.
- **Public without login.** Anonymous session ids, per-IP and per-session rate limits, a fair queue with a global
  cap on active Modal runs (`MACRAE_MAX_ACTIVE_RUNS`), a daily spend cap, a secret-protected kill switch, and task
  inputs that never reach a shell.

The interfaces are fixed in [`CONTRACT.md`](CONTRACT.md) (v1 plus the v2 addendum); the rendered overview with
diagrams is [`docs/architecture.html`](docs/architecture.html).

## Repository map

| folder | what |
|---|---|
| `web/` | The page: chat with Jarvis, voice, the Tasks & runs panel (run timeline, costs, manuscript, trace download), the Evolution tab (lessons, capabilities and authority, dusk → dawn). Plain HTML/CSS/JS, no build step. |
| `cloudflare/` | The Worker (`index.js`, `worker.js`), `wrangler.toml`, `deploy.sh`, and a local dev server and mock backend in `dev/`. |
| `server/` | FastAPI backend: tasks, runs, trace events, live ingest, chat, costs, planner, capabilities and policy, trace downloads. |
| `rag/` | PDF → pages → chunks; BM25 + `BAAI/bge-small-en-v1.5` embeddings, fused ranking; `[n]` citations with page. |
| `tasks/` | `tasks.json`, the flows in `tasks/flows/`, their preparation and check scripts; `tasks/common/` is the Modal agent image with the live wrapper. |
| `evolve/` | Lessons distilled from traces, the capability registry, the dusk → dawn benchmark, metrics, fine-tuning dataset export. |
| `agent_runner/` | Runs flows of agent and script steps through Harbor, with retries, checks and accounts (vendored, see `agent_runner/README.md`). |
| `voice/` | ElevenLabs agent config, tools and setup script. |
| `deploy/` | Backend Dockerfile, `start.py` (supervisor and drain), `r2sync.py` (R2 mirror), env and secret tooling. |
| `research/` | Scouting notes on the group's papers and on BFF; `demo/small-calc-na/`, a real run kept as a replayable bundle. |
| `proof/` | Saved live payloads (runs, lessons, capability ledger, dusk → dawn report) behind the numbers in this README. |
| `data/`, `scripts/` | The group's publication list and the script that fetches it. |
| `docs/` | Architecture page, demo script, traces guide. |
| `w4-notes/` | What each w4 branch did, how it was tested, what its integrator had to know. |
| `video/` | The film (Remotion) and its script. |

Most modules have a `NOTES.md` (and `V2_NOTES.md` / `V3_NOTES.md` where it changed): what was built, how to run it,
what it assumes about the others.

## Run it locally

Python 3.12, Node 22.

```bash
make install                      # agent_runner (-e) + server/rag/tasks/deploy/evolve requirements
make test                         # every module's pytest, plus the Worker's and the page's node tests

# the page against a mock backend: no keys, no Modal, scripted runs
node cloudflare/dev/mock-backend.mjs &          # :8080, MOCK_SPEED=4 for faster runs
cp cloudflare/.dev.vars.example cloudflare/.dev.vars
node cloudflare/dev/serve.mjs                   # http://127.0.0.1:8787

# the real backend
cp deploy/env.example deploy/.env               # fill in what you have; see deploy/env.example
make index                                      # optional: papers/*.pdf → index/
make serve                                      # http://127.0.0.1:8080
make web-dev                                    # the Worker + page on :8787, /api → the local backend
```

Without a Claude login or Modal token the server still answers health, tasks, search and runs; agent steps fail
with a clear message on the timeline. Without `ANTHROPIC_API_KEY` the planner uses the task's defaults and says so.

## Deploy

Everything is one command once the secrets are in place (`deploy/cloudflare.md` has every step):

```bash
cd cloudflare && npx wrangler login
make cf-check                     # preflight: node, docker, login, every secret present; changes nothing
make deploy                       # page + Worker + backend container + R2 index (cloudflare/deploy.sh)
make voice-setup                  # ElevenLabs agent and tools pointing at the Worker; then set ELEVENLABS_AGENT_ID
make smoke                        # /api/health and /api/tasks through the Worker
```

Secrets, by name only (values in `deploy/.env` or `npx wrangler secret put NAME`): `MACRAE_TOOL_SECRET`,
`MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `ANTHROPIC_API_KEY` and/or `AGENT_RUNNER_TOKEN_<NAME>`,
`ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, and `MACRAE_ADMIN_SECRET` (the operator's: `X-Macrae-Admin` for the
benchmark, forge and kill-switch routes and for `cloudflare/deploy.sh restart|status`; the voice agent never has it).
After changing a secret: `make cf-restart`.

Starting the proof benchmark (operator only):

```bash
curl -X POST -H "X-Macrae-Admin: $MACRAE_ADMIN_SECRET" https://<worker>/api/benchmark/dusk   # then …/dawn
curl https://<worker>/api/dawn-report
```

A deploy that changes the backend image makes Cloudflare replace the running container, and that rollout does not
wait for running flows: deploy when no run is active.

## Documentation index

| document | for |
|---|---|
| [`docs/DEMO.md`](docs/DEMO.md) | presenting Macrae live: a timed script, the fallbacks |
| [`docs/TRACES.md`](docs/TRACES.md) | reading a run's trace on the page, downloading it, and what is in the zip |
| [`docs/architecture.html`](docs/architecture.html) | the whole system on one page, with diagrams (open in a browser) |
| [`CONTRACT.md`](CONTRACT.md) | the build contract: every route, shape and module boundary (v1 + v2 addendum) |
| [`CLOUDFLARE.md`](CLOUDFLARE.md), [`deploy/cloudflare.md`](deploy/cloudflare.md) | the Cloudflare setup, secrets, sync, troubleshooting |
| [`INTEGRATION.md`](INTEGRATION.md), [`w4-notes/`](w4-notes/), [`tasks/V3_NOTES.md`](tasks/V3_NOTES.md) | how the waves of parallel work were merged and verified; the final integration is in [How it was built](#how-it-was-built) |
| `<module>/NOTES.md` | per-module details |

## Data: the group's publication list

`scripts/fetch_group_pubs.py` pulls the list from the IOCB site API
(`https://jungwirth.group.uochb.cz/en/api/publications`, paged 10 per call) into
`data/group_publications.{json,csv,md}`: 479 papers, 2004–2026, 473 with a DOI.

- The site lists papers by any group member, so 206 of the 479 don't have P. Jungwirth as an author.
- Nothing before 2004 (the site covers the IOCB years only).
- 6 entries have no DOI (Czech popular-science articles, RSC book chapters, one proceedings). One missing DOI was
  filled from Crossref (`DOI_FIXES` in the script).
- One Early View paper has no year yet (the site shows it as 1970).

Run: `python3 scripts/fetch_group_pubs.py`

## agent-runner

`agent_runner/` runs agents as traced flows through Harbor, spread over Claude accounts: agent steps run Claude Code
in sandboxes (Docker locally, Modal in Macrae), script steps run in place, steps run in parallel, pass results on
and retry until a check passes. Docs: [`agent_runner/README.md`](agent_runner/README.md). Macrae itself was built
this way: a contract, then waves of Claude Opus agents working in parallel, each wave merged by an integrator agent.

- `examples/group-papers.yaml`: newest group papers → abstracts from OpenAlex → one agent per paper writes a card
  (summary, methods, key result, open questions) in parallel → merged into one file.
- Claude Code in this repo picks up `.claude/skills/agent-runner` and can launch and follow flows itself.

Run: `pip install -e .` then `agent-runner flow run examples/group-papers.yaml --var n=5`
(needs Docker or Modal, [Harbor](https://github.com/harbor-framework/harbor) and a Claude login: `claude setup-token`,
then `agent-runner accounts set NAME`, or `ANTHROPIC_API_KEY`).
