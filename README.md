# Macrae

**A research agent for the Pavel Jungwirth group at IOCB Prague that knows the group's papers, runs real
computational chemistry in traced cloud sandboxes, and gets better at it from its own traces.**

Live: <https://macrae.acalincarol.workers.dev> · Architecture page: [`docs/architecture.html`](docs/architecture.html)
· Demo script: [`docs/DEMO.md`](docs/DEMO.md) · Traces: [`docs/TRACES.md`](docs/TRACES.md)

You talk to **Jarvis**, by voice (ElevenLabs) or by typing. Jarvis chats like a capable assistant and, whenever the
group's work is relevant, answers from the group's own papers with numbered citations (first author, year, page).
When you click a task, or ask for one, Macrae plans the run, starts Claude Code agents in sandboxes on Modal
through Harbor, and streams every paper it reads, every command and calculation it runs, and every sentence of the
manuscript it writes onto the page, with the cost in dollars and the time growing live. Every run leaves a full,
downloadable trace. After each run Macrae distills lessons from that trace and, when it notices a missing
capability, builds, tests and installs a new tool for itself, so the next run is faster, cheaper or possible at all.

## Contents

- [The group](#the-group)
- [The Frankenstein brief](#the-frankenstein-brief)
- [How to use it](#how-to-use-it)
- [How it runs](#how-it-runs)
- [Repository map](#repository-map)
- [Run it locally](#run-it-locally)
- [Deploy](#deploy)
- [Documentation index](#documentation-index)
- [Data: the group's publication list](#data-the-groups-publication-list)
- [agent-runner](#agent-runner)

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

1. **Ask.** Type in the composer, or press the microphone and talk. Ask about the group's work ("What charge
   scaling does the group use for calcium, and why?") or anything else. `[n]` in an answer opens the source card:
   title, authors, journal, year, page, and a DOI link. Jarvis says which parts come from the papers and which don't.
2. **Run a task.** Open the right panel (**Tasks & runs**) and press **Run** on a task, or ask Jarvis to run one.
   Tasks:
   - *Methods card for a group paper*: give a DOI; an agent writes a methods summary in which every claim is cited.
   - *Ion–water binding, computed live*: pick an ion; an agent runs DFT (PySCF) on Modal and compares the binding
     energy with full and ECC-scaled point charges.
   - *Is 0.8 the right charge-scaling factor? A Bayesian answer with BFF* (acetate, ECC 0.70–0.90, Bayes factor).
   - *Ca²⁺–acetate binding with error bars: from the BFF charge posterior to experiment.*
3. **Watch it work.** The run view opens: first the planner's decision (hardware, stages, budget, and why), then a
   live timeline of what the agent does (📄 read, 🔎 search, 🧮 calc, ✍️ write, 🔖 cite, ✅ result, ⚠️ error,
   and the capability events gap / create / test / install / use), with a cost meter (LLM $, compute $, tokens,
   time per phase). The **Manuscript** tab replays the paper the agent writes, with math, figures and citations,
   as it types and revises.
4. **See what it learned.** The **What it learned** tab shows the agent's notes to itself, the distilled lessons, and
   this run against the previous run of the same task (setup time, total time, $, errors). **Capabilities** shows
   the dusk → dawn ledger and the fixed Authority card. **Dusk → Dawn** shows the benchmark comparison.
5. **Take the proof with you.** Every run has **Download trace** (a zip of everything the run did) and **Open
   report** (a self-contained HTML report). See [`docs/TRACES.md`](docs/TRACES.md).

A run takes from about 1 minute (methods card) to 10–15 minutes (BFF tasks) and costs cents to about a dollar; the
page shows an estimate before you start and the real figure as it goes. There is a per-visitor rate limit, a queue
when many runs are active ("queued, position N") and a global daily spend cap.

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
   │           │                 │  mirror: every step + 60 s                    PySCF, BFF, GROMACS; streams
   │           │                 ▼                                               every line back to /api/live
   │           │           R2 bucket "macrae-data": index/, state/runs, state/jobs (the traces), state/macrae
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
  Claude through ElevenLabs. Its server tools (`search_papers`, `start_task`, `run_status`, and the cost and
  learnings tools) are webhooks to the Worker with a shared secret; its page tools (`show_citations`, `open_run`)
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
| `web/` | The page: chat with Jarvis, voice, the Tasks & runs panel (run timeline, costs, manuscript, what it learned, capabilities), Dusk → Dawn. Plain HTML/CSS/JS, no build step. |
| `cloudflare/` | The Worker (`index.js`, `worker.js`), `wrangler.toml`, `deploy.sh`, and a local dev server and mock backend in `dev/`. |
| `server/` | FastAPI backend: tasks, runs, trace events, live ingest, chat, costs, planner, capabilities and policy, trace downloads. |
| `rag/` | PDF → pages → chunks; BM25 + `BAAI/bge-small-en-v1.5` embeddings, fused ranking; `[n]` citations with page. |
| `tasks/` | `tasks.json`, the flows in `tasks/flows/`, their preparation and check scripts; `tasks/common/` is the Modal agent image with the live wrapper. |
| `evolve/` | Lessons distilled from traces, the capability registry, the dusk → dawn benchmark, metrics, fine-tuning dataset export. |
| `agent_runner/` | Runs flows of agent and script steps through Harbor, with retries, checks and accounts (vendored, see `agent_runner/README.md`). |
| `voice/` | ElevenLabs agent config, tools and setup script. |
| `deploy/` | Backend Dockerfile, `start.py` (supervisor and drain), `r2sync.py` (R2 mirror), env and secret tooling. |
| `research/` | Scouting notes and the ideas behind the BFF tasks. |
| `data/`, `scripts/` | The group's publication list and the script that fetches it. |
| `docs/` | This documentation: architecture page, demo script, traces guide. |
| `video/` | The film (Remotion) and its script. |

Each module has a `NOTES.md` (and `V2_NOTES.md` / `V3_NOTES.md` where it changed): what it built, how to run it,
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
`ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`. After changing a secret: `make cf-restart`.

## Documentation index

| document | for |
|---|---|
| [`docs/DEMO.md`](docs/DEMO.md) | presenting Macrae live: a timed script, the fallbacks |
| [`docs/TRACES.md`](docs/TRACES.md) | reading a run's trace on the page, downloading it, and what is in the zip |
| [`docs/architecture.html`](docs/architecture.html) | the whole system on one page, with diagrams (open in a browser) |
| [`CONTRACT.md`](CONTRACT.md) | the build contract: every route, shape and module boundary (v1 + v2 addendum) |
| [`CLOUDFLARE.md`](CLOUDFLARE.md), [`deploy/cloudflare.md`](deploy/cloudflare.md) | the Cloudflare setup, secrets, sync, troubleshooting |
| `INTEGRATION.md`, `INTEGRATION_V2.md`, `INTEGRATION_V3.md`, `INTEGRATION_W4.md` | how each wave of parallel work was merged and verified |
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
