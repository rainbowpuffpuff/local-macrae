# Traces: how to read them and how to download them

A **trace** is everything one Macrae run did: the planner's decision, every paper passage it searched and read,
every command and calculation, every file it wrote (including the manuscript), every retry, the checks that passed
or failed, the tokens and seconds it used and what they cost. Macrae keeps the trace of every run, shows it live on
the page, lets anyone download it, and learns from it.

The same trace exists at three levels, from most readable to most raw:

| level | what | where |
|---|---|---|
| 1 · **Timeline** | One row per event with a short human title, details and source cards | Run view on the page; `GET /api/runs/{id}/events` |
| 2 · **Report and zip** | A self-contained HTML report, and a zip of every file behind the run | **Open report** / **Download trace** on the run view; `GET /api/runs/{id}/trace.zip` |
| 3 · **Raw files** | `state.json`, step logs, live stream lines, Harbor trials with `trajectory.json` | The zip; on disk under `$AGENT_RUNNER_HOME`; in R2 under `state/` |

Contents: [The timeline](#1--the-timeline) · [Downloading](#2--downloading-a-trace) ·
[What is in the zip](#3--what-is-in-the-zip) · [Reading `trajectory.json`](#4--reading-trajectoryjson) ·
[Where traces are stored](#5--where-traces-are-stored) · [Traces as training data](#6--traces-as-training-data) ·
[What a trace contains, and what it doesn't](#7--what-a-trace-contains-and-what-it-doesnt)

## 1 · The timeline

Open a run: press **Run** on a task, click a run chip in the chat, pick one under **Recent runs**, or open
`/?run=<run_id>`. The run view shows, top to bottom:

1. **The plan card.** The planner's decision ("Decided: 32 CPU cores, 4 stages, budget $1.00") with its reason,
   its stages and parameters, the model that planned it and what the planning cost. If the planner was not
   available, the card says the task's defaults were used and why.
2. **The cost meter.** Total $, split into LLM $ (Claude tokens) and compute $ (Modal sandbox seconds), tokens
   (in, out, cache), the budget bar, and time per phase (plan, setup, work, check). It grows while the run goes.
3. **The timeline.** One row per event, in order, with the step it belongs to, how long into the run it happened and,
   where known, what it cost.

### Event types

| icon | type | means | typical title |
|---|---|---|---|
| 🧭 | `plan` | the planner's decision, always first | Decided: GPU A10G, 3 stages, budget $2.00 |
| • | `status` | the run or a step started, finished, retried, was skipped | Started Claude Code on Modal, attempt 2 |
| 🔎 | `search` | a search of the group's paper index | Searched papers: 6 passages for "ECC calcium…" |
| 📄 | `read` | the agent read a paper, a passage file or an abstract | Read paper/context.md |
| 🔖 | `cite` | a paper the run cites, with its source card | Cited Košťál 2026: Bayesian Learning for… |
| 🧮 | `calc` | a command that computed something (python, PySCF, GROMACS, BFF…) | Ran water dimer energy (PySCF, 41 s) |
| ✍️ | `write` | the agent wrote or edited a file | Wrote results/manuscript.md |
| 💭 | `think` | the agent's own words between tool calls | |
| ✅ | `result` | a step or the run finished, with its reward | calc finished, reward 1 |
| ⚠️ | `error` | something failed: the message is in the details | Agent run failed: … |
| | `gap` | the agent said a capability is missing (`CAPABILITY_GAP: name: why`) | Missing: block-averaged error bars |
| | `create`, `test` | a step wrote a new tool and its tests, and ran them | Tests passed: 6 of 6 |
| | `install` | the server checked the tool against the fixed policy and added it to the registry | Installed rdf-tool (sha256 3fa1…) |
| | `use` | a later run used an installed capability | Used rdf-tool |
| | `rejected` | the installer refused a capability that asked for more authority, with the reason | Rejected: asks for a secret |

Click a row for its details (up to 600 characters: the command, the passage, the error). A row with a source card
links to the paper's DOI.

### How an event is made

The server builds the timeline from files, never from guesses:

- the flow's `state.json` and step logs (`logs/<step>.log`): starts, ends, attempts, checks, the script steps'
  searches and citations;
- while an agent step is running, the **live** lines the sandbox streams back (`live/<step>.jsonl`): every
  Claude Code message and tool call within about a second;
- when the step ends, Harbor's trial files: `agent/trajectory.json` (or `agent/claude-code.txt`) and `result.json`.

Tool calls map to types by what they did: Read/Glob/Grep/WebFetch of a paper → `read`, a paper search → `search`,
Bash running python/xtb/psi4/PySCF/GROMACS or printing computed numbers → `calc`, Write/Edit → `write`, assistant
text → `think`. An event seen live is not repeated when the final trajectory arrives (tool calls are matched by their
`tool_use` id). Each event has a stable `seq` (1, 2, 3, … with no gaps), so polling with `?after=<seq>` never skips
or duplicates anything, also across container restarts.

### The API

The Worker adds the shared secret, so these work from a browser or `curl` against the live site:

```bash
SITE=https://macrae.acalincarol.workers.dev        # or http://127.0.0.1:8787 locally
curl -s $SITE/api/runs | jq '.runs[] | {run_id, task_id, status}'
RUN=20261008-192909-small-calc-3935
curl -s $SITE/api/runs/$RUN | jq '{status, costs: .costs.total_usd, steps: [.steps[] | {key, status, reward}]}'
curl -s "$SITE/api/runs/$RUN/events?after=0" | jq -r '.events[] | "\(.seq)\t\(.type)\t\(.step)\t\(.title)"'
```

An event:

```jsonc
{"seq": 12, "t": 1791488001.2, "step": "calc", "type": "calc",
 "title": "Ran Na+–water scan (PySCF, 41 s)", "detail": "$ python run_calc.py …",
 "citation": null,                                   // a Citation on read/cite/search events
 "elapsed_s": 251.3,                                 // seconds since planning began
 "cost": {"usd": 0.019, "tokens": {"in": 25012, "out": 410, "cache": 24100}}}   // when known
```

The manuscript is a trace too: `GET /api/runs/{id}/manuscript` returns the ordered edit stream of
`results/manuscript.md` (`[{seq, t, op, path, old, new, content}]`) rebuilt from the agent's Write/Edit calls; the
**Manuscript it wrote** box under a finished run shows the result. Figures are served by `GET /api/runs/{id}/artifacts/<path>`.

## 2 · Downloading a trace

On the run view:

- **Download trace** saves `<run_id>.zip`: every file behind the run (section 3).
- **Open report** opens a single HTML file that works offline: the timeline, every tool call with its input and
  output, the costs, the manuscript, the citations and the check results. Save it and it still works without the
  site.

From the command line:

```bash
curl -fsS -o $RUN.zip "$SITE/api/runs/$RUN/trace.zip"
unzip -l $RUN.zip | head -40
```

Downloads work for running runs too (you get what exists so far) and for runs from before the last container
restart (the container reads them back from R2).

## 3 · What is in the zip

The zip mirrors the run's folders on disk. Paths below are relative to the zip root:

```
runs/<run_id>/
  state.json                 the flow engine's record: every step, attempt, status, reward, times, errors
  flow.yaml                  the flow exactly as it ran (task vars, lessons and plan filled in)
  macrae.json                the task id and the inputs the visitor gave
  plan.json                  the planner's decision, its model, its cost, and the lessons it was given
  costs.json                 final costs: LLM $, compute $, tokens, per step, per phase
  logs/<step>.log            each step's log: commands, stdout/stderr, check output, retries
  live/<step>.jsonl          Claude Code's stream-json lines as they arrived from the sandbox
  live/<step>.batches.jsonl  arrival times of those lines
  result/                    what the collect step kept: result.json, explanation, manuscript, figures
jobs/<run_id>/<step>-a<attempt>/            one Harbor job per attempt of each agent step
  <trial>/
    result.json              the trial: start/end times per phase, tokens, cost_usd, reward, exception
    agent/trajectory.json    ATIF: every message, tool call and tool output (section 4)
    agent/claude-code.txt    Claude Code's raw stream-json output
    artifacts/app/…          the sandbox's working directory after the run: every file the agent made
macrae/events/<run_id>.jsonl the timeline exactly as the page showed it (one TraceEvent per line)
```

Where to look first:

| question | file |
|---|---|
| Did it pass, and how long did each step take? | `runs/<id>/state.json` |
| Why did a check fail, and what did the retry get told? | `runs/<id>/logs/<step>.log` (look for `check exit`) |
| What exactly did the agent do and see? | `jobs/…/<trial>/agent/trajectory.json` |
| What did it cost? | `runs/<id>/costs.json`; per trial, `result.json` → `agent_result` |
| What did it produce? | `runs/<id>/result/` and `jobs/…/artifacts/app/` |
| Why did it choose this hardware? | `runs/<id>/plan.json` → `why` |
| What did it learn from earlier runs? | `plan.json` → `lessons`, and the instruction (first step) in `trajectory.json` |

Quick reading without the page:

```bash
mkdir t && unzip -q $RUN.zip -d t
jq '.steps | to_entries[] | {step: .key, status: .value.status, reward: .value.reward}' t/runs/*/state.json
jq -r '.type + "\t" + .title' t/macrae/events/*.jsonl            # the timeline, as text
```

## 4 · Reading `trajectory.json`

Harbor writes each agent trial in **ATIF** (Agent Trajectory Interchange Format): a list of `steps`, each from a
`source` (`user` = the instruction, `agent` = Claude, `system`), with a `message`, optional `tool_calls` and the
`observation` they produced.

```jsonc
{"steps": [
  {"step_id": 1, "source": "user", "message": "You are a computational chemist… (the step's instruction)"},
  {"step_id": 7, "source": "agent", "model_name": "claude-opus-5-5", "message": "",
   "tool_calls": [{"tool_call_id": "toolu_…", "function_name": "Bash",
                   "arguments": {"command": "python run_calc.py --ion Na+"}}],
   "observation": {"results": [{"source_call_id": "toolu_…", "content": "r=2.20 E=-25.5 kcal/mol …"}]},
   "metrics": {"prompt_tokens": 25012, "completion_tokens": 410, "cached_tokens": 24100}}]}
```

Useful one-liners:

```bash
T=t/jobs/*/calc-a1/*/agent/trajectory.json
jq -r '.steps[] | select(.tool_calls) | .tool_calls[] | .function_name + "  " + (.arguments | tostring)[0:120]' $T
jq -r '.steps[] | select(.tool_calls) | .tool_calls[] | select(.function_name=="Bash") | .arguments.command' $T
jq '[.steps[].metrics // {} | .completion_tokens // 0] | add' $T          # output tokens
jq -r '.steps[] | select(.source=="agent" and .message != "") | .message' $T   # what the agent said
```

`agent/claude-code.txt` and `live/<step>.jsonl` are Claude Code's own stream-json, one JSON object per line
(`system`, `assistant`, `user` with tool results, and a final `result` with `total_cost_usd` and `usage`). They exist
while a step runs; `trajectory.json` appears when the trial ends.

## 5 · Where traces are stored

| place | path | who can see it |
|---|---|---|
| Local backend | `$AGENT_RUNNER_HOME/runs/<run_id>/`, `$AGENT_RUNNER_HOME/jobs/<run_id>/`, `$AGENT_RUNNER_HOME/macrae/events/` (default `~/.local/share/agent-runner`) | you |
| Container | the same under `/data/agent-runner/` (temporary disk) | nobody directly; via the API |
| R2 bucket `macrae-data` | `state/runs/…`, `state/jobs/…`, `state/macrae/…` | the owner (Cloudflare dashboard or `wrangler r2 object get … --remote`) |

The container mirrors every change to R2 after each step and every 60 s, and restores from R2 when it starts, so
traces survive sleep, restarts and deploys. At most about a minute of detail can be lost if the host dies without
warning; a run that was cut off that way shows as failed (`crashed`) with the reason. Runs are append-only: nothing
in a trace is edited or deleted after the fact.

## 6 · Traces as training data

Macrae learns from its traces in two ways:

- **Lessons and capabilities.** After each run, `evolve` reads the trace (events, errors, timings, check output,
  costs) and writes lessons (`do`, `avoid`, `setting`, `tool`) with evidence pointing at `run_id/step/seq`. The next
  run of the task gets them in its plan and its instruction; the **Evolution** tab shows them with links to the
  runs they came from. Capability events (gap → create → test → install → use) are recorded in a ledger shown in the
  **Capabilities** section of the Evolution tab.
- **Fine-tuning data.** `python -m evolve export out/` writes every finished agent trajectory as chat JSONL with
  its instruction, the lessons it was given and its reward: `all.jsonl`, `sft.jsonl` (reward ≥ 1 only) and
  `manifest.json`.

## 7 · What a trace contains, and what it doesn't

- **It contains paper text.** Searches and reads include passages of the group's papers, some of which are
  paywalled. Share zips and reports with that in mind.
- **It does not contain secrets.** Keys stay in the Worker and the container's environment; tools in the sandbox get
  no secrets (fixed policy). The per-run live token is masked in step logs (`MACRAE_LIVE_TOKEN=***`) and is not part
  of the download.
- **Costs are API-equivalent.** With a Claude subscription login, LLM $ is what the same tokens would cost on the
  API, not a bill. Compute $ is Modal sandbox seconds × the published rates in `server/costs.py`.
