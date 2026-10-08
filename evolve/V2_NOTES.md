# evolve/: v2 notes

`evolve` is the "Evolution" part of the v2 addendum. After each run it reads the trace and turns it into lessons and
reusable scripts. The next run of the same task gets them in its agent instruction and its planner prompt. It also
serves the per-task run-over-run numbers behind the Evolution panel, and exports trajectories for fine-tuning.

Everything here was run on this machine. Not verified:
- a live Anthropic API call: there is no `ANTHROPIC_API_KEY` here;
- a real Modal run.

The Claude request was checked two ways:
- with the real `anthropic` 1.12.1 SDK against a local HTTP stub;
- with a fake SDK in the tests.

## What I built

| file | what |
|---|---|
| `evolve/__init__.py` | the public API: `distill`, `distill_in_background`, `distill_pending`, `context`, `flow_vars`, `hints`, `rule_plan`, `metrics`, `export_dataset`, `lessons`, `add_lesson`, `check_flows` |
| `runs.py` | reads a run the way the page saw it (sources below) |
| `rules.py` | the rule-based extractor, used without an API key or when the Claude call fails |
| `llm.py` | `call_json(system, user, schema, model=…)`: one structured Claude call through the Anthropic SDK. It returns `(data, usage)`, where usage includes `usd`, and raises `LLMError` on any failure. The backend's planner can reuse it. |
| `distill.py` | builds the trace digest, makes the Claude call (or uses the rules), merges lessons into the store, harvests tools, records outcomes, keeps the registry |
| `context.py` | the lessons block, `flow_vars`, `hints`, `rule_plan` (the planner's fallback) |
| `metrics.py` | `metrics()` for `GET /api/evolution` |
| `dataset.py` | `export_dataset(out)`: ATIF → chat JSONL (`all.jsonl`, `sft.jsonl`, `manifest.json`) |
| `store.py` | `lessons.jsonl` with a lock (thread + flock), merge/dedupe, ranking, retiring, the registry |
| `tools.py` | scripts from passing attempts → `tools/<task_id>/` plus `manifest.json` |
| `costs.py` | run cost. It uses the backend's numbers when `get_run()` returns `costs`. Otherwise it estimates: Claude Code's `total_cost_usd`, else tokens × price table, plus Modal seconds × rate table. |
| `config.py` | paths and env vars, read at call time |
| `__main__.py` | the CLI (below) |
| `tests/` | 46 tests, fixtures, a fake Harbor |
| **`tasks/flows/*.yaml`** | **the lesson-injection hook** (see below) |

`runs.py` reads these sources:
- `state.json`, `flow.yaml`, `macrae.json`;
- `logs/<step>.log`: attempts, `check exit N: …` output, Harbor command lines;
- each Harbor trial: `result.json`, ATIF `agent/trajectory.json`, the stream-json `agent/claude-code.txt`, `artifacts/app/`;
- v2 live files `<run>/live/<step>.jsonl`. They are used only when no trial has a transcript. Raw stream-json lines
  or `{"line": "<json>"}` wrappers both work.

`runs.py` gets TraceEvents (with the page's seq) in this order:
1. the backend's `$MACRAE_SERVER_DATA/events/<run>.jsonl`;
2. else `server.trace.build` in memory (never written);
3. else its own small builder.

### What a lesson is, and where it lives
`$AGENT_RUNNER_HOME/evolve/` (override: `MACRAE_EVOLVE_HOME`):

- **`lessons.jsonl`**: the contract's record `{task_id, lesson, evidence: ["<run_id>/<step>/<seq>"], kind: do|avoid|setting|tool, created}`.
  - It also has `id` (`L` + 6 hex), `updated`, `runs`, `hits`, `confidence`, `source` (claude | rules | manual),
    `key`, `used`, `used_ok`, `status` (active | retired).
  - `seq` is the TraceEvent seq the page shows. For a run-level event, step is `-`.
- **`tools/<task_id>/`**: the latest passing version of each script, plus `manifest.json`. Each entry has
  `{name, file, description, sha256, run_id, step, command (how it was run successfully), runs, versions}`.
- **`distilled.json`**: run_id → `{source, lessons, tools, usage, llm_error, …}`. This makes `distill()` idempotent.
- **`distill/<run_id>.json`**: the digest Claude read, its answer, the token usage and cost, and the candidate lessons
  (useful for showing *why* a lesson exists).

### How lessons evolve
- **Repeats reinforce.** A candidate that matches an existing lesson increases its `hits` and adds evidence instead
  of adding a duplicate. A match is the same rule key, or word-set Jaccard ≥ 0.6. Claude can also list existing ids
  in `reinforces`.
- **Outcomes count.** When a run that had lessons in its instruction is distilled, each of those lessons gets
  `used += 1`, plus `used_ok += 1` if the run passed. The ids are parsed from the `[Lxxxxxx]` markers in
  `state.json` `vars.lessons`. A lesson used ≥ 3 times with a success rate below 34% is **retired**.
- **Ranking.** Score = confidence × (1 + ½·log2 hits) × recency (21-day half-life) × outcome ((ok+1)/(used+2)·2) ×
  kind weight. `context(k=8)` takes the top k for the task, plus `task_id "*"` lessons.

### The Claude call (with `ANTHROPIC_API_KEY`)
- **SDK and model:** the official `anthropic` SDK, one `client.beta.messages.create` call. The model is
  `MACRAE_EVOLVE_MODEL`, default `claude-opus-5-5`, with `effort: medium`.
- **Output:** `output_config.format` json_schema, so the answer is guaranteed JSON:
  `{summary, lessons[{lesson, kind, evidence, confidence}], reinforces[id], tools[{file, description, reusable}]}`.
- **Refusal fallback:** server-side refusal fallback is on: `fallbacks: "default"` with beta
  `server-side-fallback-2026-07-01`. If the endpoint rejects it with a 400, the call is retried once without it.
- **What Claude reads:** a digest of up to about 60k characters:
  - status, wall time, costs, inputs and the lessons the run was given;
  - steps with attempts and check output;
  - every event as `run/step/seq` + type + title;
  - the agent's tool calls with timings, and the last lines of every failed command;
  - the head of each script from passing attempts;
  - the task's current lessons with their ids.
- **What is kept:** evidence ids that aren't in the trace are replaced by the run's last event. At most 6 lessons are
  kept. Text shorter than 12 characters is dropped, and confidence is clamped to 0–1. A script Claude marks
  `reusable: false` isn't kept.
- **Failures:** on any failure (API error, refusal, `max_tokens`, non-JSON, SDK missing) distill logs a warning,
  records `llm_error` and uses the rules. The deterministic "fastest passing run" baseline lesson is added in both
  modes.

### The rules (no key)
Each rule cites the event it came from.
- **check:** the exact problem lines a `check:` printed for a rejected attempt, and whether a later attempt fixed it.
- **missing module / command not found:** what made it work. That is the install between the failure and the first
  later success (e.g. `micromamba install … gromacs` for `gmx`), or that success itself (e.g. `/opt/calc/bin/python`
  instead of `python3`).
- **failed then fixed:** a command that failed, and the later similar one that worked.
- **unchanged retry:** the same failing command run again unchanged.
- **limits:** a time limit or out-of-memory on the hardware used. If a later attempt passed, the lesson says with
  which command, e.g. "`--steps 20000 --check-tau` instead of `--steps 100000`".
- **slow:** the slowest calculations of a passing run (not installs).
- **baseline:** the best passing run so far.

Infrastructure failures (Claude login, Modal auth, rate limits, no trials) produce no lessons.

What the rules produce from the fixture BFF run:
```
SETTING `gmx` is not on PATH in the sandbox with `gmx --version`; `micromamba install -y -n base -c conda-forge gromacs=2024.4` installed it (took 94 s). Do that first.
SETTING `fit` hit its time limit after 30.1 min on cpu-8; attempt 2 passed in 24.3 min with `python run_mcmc.py --walkers 40 --steps 20000 --check-tau` instead of `python run_mcmc.py --walkers 40 --steps 100000`. Shrink the problem …
SETTING “Run 64 short MD trajectories (LHS charge sets)” takes about 10.0 min on cpu-8 (41% of the agent's time); budget for it …
```

## The lesson-injection hook (`tasks/flows/methods-card.yaml`, `small-calc.yaml`)
Three edits per flow. Nothing else changed.
```yaml
vars:
  lessons: ""        # filled by the server from evolve
  tools_dir: ""      # filled by the server: scripts from earlier passing runs (→ /app/<task_id>)
steps:
  - id: calc
    paths: ["{{ prepare.output.dir }}", "{{ vars.tools_dir }}"]   # was path:; an empty tools_dir is dropped
    instruction: >
      …existing text…

      {{ vars.lessons }}
```
With empty vars, the rendered instruction and paths are exactly what they were before; a test checks this.

`tasks/tests` (65) and `tests/` (7) still pass, and `python -m tasks.runner check` is OK.

The second path is safe:
- Harbor uploads it as `/app/<task_id>`, and nothing the flows prepare has that name (`paper`, `calc`).
- The engine still picks the first path's folder for `check:` and `collect`.

**Any new flow (the BFF tasks) needs the same three edits.** `python -m evolve check-flows` and the test
`test_every_task_flow_injects_lessons_and_tools` flag a flow that's missing them.

The block the agent gets, from the end-to-end test through the real engine:
```
LESSONS FROM EARLIER RUNS OF THIS TASK (learned from their traces; follow them unless they contradict the instructions above):
- [L532497] SETTING: Python module `pyscf` is not importable in the sandbox with `python3 run_calc.py | tee output.log`; `/opt/calc/bin/python run_calc.py | tee output.log` worked. Do that first.
- …
REUSABLE SCRIPTS from earlier runs that passed the check are in /app/small-calc/: copy and adapt them instead of starting from scratch:
- run_calc.py: Ion–water scan with PySCF (B3LYP/def2-SVP), CP-corrected def2-TZVP at the minimum.
```

## How to run
```bash
pip install -r evolve/requirements.txt          # anthropic (optional: without it, rules only), pyyaml
python -m evolve distill                        # every finished run not yet distilled (or: distill <run_id>… [--force] [--no-llm])
python -m evolve context small-calc             # the block the planner reads; --vars → {"lessons", "tools_dir"}
python -m evolve lessons [task_id] [--all]      # list, with hits and used/ok
python -m evolve metrics                        # the /api/evolution JSON
python -m evolve hints bff-charges              # past runs per hardware, suggested hardware and budget
python -m evolve add '*' "Never type numbers into result.json by hand." --kind avoid
python -m evolve export out/                    # out/all.jsonl, out/sft.jsonl, out/manifest.json
python -m evolve watch [--every 30]             # distill each run as it ends, if the server doesn't call distill
python -m evolve check-flows                    # every task flow carries the hook
python -m evolve fixtures /tmp/demo             # the 4 sample runs into a scratch AGENT_RUNNER_HOME (demo / web dev)
```
Environment variables:
- `AGENT_RUNNER_HOME`;
- `MACRAE_EVOLVE_HOME` (default `$AGENT_RUNNER_HOME/evolve`);
- `MACRAE_SERVER_DATA` (default `$AGENT_RUNNER_HOME/macrae`);
- `ANTHROPIC_API_KEY`;
- `MACRAE_EVOLVE_MODEL` (default `claude-opus-5-5`);
- `MACRAE_EVOLVE_LLM=0` (rules only, even with a key);
- `MACRAE_EVOLVE_TOOLS=0` (don't hand scripts to new runs).

## Tests
```bash
python -m pytest -q evolve/tests      # 46 passed, about 5 s; no network, Docker, Modal or API key
python -m pytest -q                   # whole repo: 321 passed, 2 skipped (it was 275 passed, 2 skipped)
```

**Fixtures.** `evolve/tests/fixtures/home/` holds 4 synthesized runs, written by `fixtures/build.py`, which is
deterministic. No finished runs exist on this machine: the run ids in `.reference/` are on the owner's laptop.

The runs are built in the exact shapes the engine and Harbor 0.24 write. Time placeholders are rendered at install
time, so the `[HH:MM:SS]` log times work in any timezone. They are checked against the real
`server.trace.build`: events, seqs and classifications come out right.

| run | what happens |
|---|---|
| A | small-calc Na+. pyscf is not importable with the system python. The check rejects the ECC ratio and a bad `[4]` citation. It passes on attempt 2, in 17 min, for about $2. |
| B | small-calc K+, with 3 lessons injected. It passes first time in 8 min for $0.67. |
| C | BFF on cpu-8. `gmx` is missing, and MCMC with 100k steps hits the 30-minute limit. Attempt 2 passes with 20k steps. |
| D | methods-card. No Claude login, so it teaches nothing. |

**`test_evolve_flows.py::test_the_next_run_learns_from_the_last`** runs the **real** `small-calc` flow twice through
`agent_runner`, with a fake `harbor` that plays the agent:
1. Run 1 hits ModuleNotFoundError.
2. `distill` produces the lesson, with evidence `run/calc/seq`.
3. `flow_vars` produces the next run's vars.
4. Run 2's `harbor -i` instruction contains the lesson id and `/app/small-calc/run_calc.py`. Its second `-p` is the
   tools folder.
5. Distilling run 2 records `used=1, used_ok=1`.
6. `metrics()` shows 0 → N lessons used and a lower cost.

The other tests cover:
- the Claude request shape: model, schema, fallbacks, digest content;
- answer validation, and `reinforces` / reusable handling;
- each failure mode falling back to the rules;
- idempotency and reinforcement across runs;
- retiring;
- ranking;
- `metrics` shape and order;
- dataset contents (5 trajectories, 3 with reward ≥ 1, tool-call ids matched);
- the CLI.

## What I assumed about the other two modules

### backend (`server/`)
1. **Starting a run.** The backend merges `evolve.flow_vars(task_id)` into the flow vars, alongside the planner's
   vars. Both are strings.
   - `tasks.runner.start()` rejects unknown inputs. So the backend needs a path that adds vars, for example
     `runner.build_vars(task, inputs)` + `vars.update(...)` + `flows.start_detached(path, {k: runner._cli_value(v)})`.
     Multi-line text passes through `--var lessons=…` unchanged; this is tested.
   - If it only sets `lessons`, nothing breaks. `tools_dir` stays "" and the block still lists the scripts.
2. **Planner.** It puts `evolve.context(task_id)` in its prompt, and may also use `evolve.hints(task_id)`.
   - Without a key, or if the call fails, `evolve.rule_plan(task_dict, hardware_options)` returns
     `{"plan", "hardware", "params", "budget_usd", "why", "lessons"}` from past runs.
   - The planner can make its own Claude call with
     `evolve.llm.call_json(system, user, schema, model=os.environ.get("MACRAE_PLANNER_MODEL", "claude-sonnet-5-5"))`.
     `usage["usd"]` is its cost for the plan event.
3. **Run end.** It calls `evolve.distill_in_background(run_id)` when a run reaches ok/failed/cancelled.
   - It's idempotent, so calling it on every poll that sees a terminal status is fine.
   - It runs in a daemon thread and never raises into the caller.
   - Fallback without any wiring: `python -m evolve watch`.
4. **`GET /api/evolution`** returns `evolve.metrics()`. Import evolve lazily.
5. **Costs.** evolve calls `server.runs.get_run(id)` and uses `["costs"]` when it has a numeric `total_usd`.
   Otherwise it estimates.
6. **Hardware.** evolve reads the chosen hardware from `state.json` `vars.hardware`, or `<run>/plan.json`
   `{"hardware"}`. Names: `cpu-<n>` / `gpu-<type>[-<n>]`.
7. **Events.** evolve reads `$MACRAE_SERVER_DATA/events/<run>.jsonl` (`{"keys", "event"}` lines) read-only. Live
   lines are read from `<run>/live/<step>.jsonl`.
8. **Run-start event.** The backend's "Started …" event lists every var, so it will include the `lessons` text
   (clipped to 600 characters). Hide `lessons` and `tools_dir` there if that's too noisy.

### web (`web/`)
- The Evolution panel reads `metrics()`.
  - The contract's fields: `by_task[task_id]` = rows, **oldest first**, each with `run_id, ok, reward, wall_s,
    total_usd, lessons_used` (an int).
  - Extras: `status, started, llm_usd, compute_usd, tokens, hardware, lesson_ids, lessons_learned, distilled`.
  - Extra keys: `lessons[task_id]` (best first, each with `links: [{run_id, step, seq}]` for "learned from" links,
    plus `kind, hits, used, used_ok, source`) and `tools[task_id]`.
- Evidence strings are `run_id/step/seq` with `-` for a run-level event. Run ids contain no `/`.
- To try the panel against real data: `python -m evolve fixtures /tmp/demo && AGENT_RUNNER_HOME=/tmp/demo python -m evolve distill`,
  then run the backend with `AGENT_RUNNER_HOME=/tmp/demo`.

### Outside my folders (for the integrator)
- `Makefile` `TEST_DIRS` doesn't include `evolve/tests`, so `make test` skips them; a bare `pytest` at the root runs
  them. Add `evolve/tests`.
- `deploy/Dockerfile` should also install `evolve/requirements.txt` (`anthropic`).
- `tasks.runner.run_meta()` falls back to `state.json` vars when `macrae.json` is missing. It would then list
  `lessons`/`tools_dir` as inputs.
