---
name: agent-runner
description: >-
  Run agent work as flows through Harbor with agent-runner (`agent-runner` / `python -m agent_runner`):
  write a YAML flow of agent steps (Claude Code in Docker containers, spread over the Claude accounts that have a
  login) and script steps, check it, launch it in the background, follow it, read the traces, plan the next flow.
  Use when asked to run, fan out, delegate or parallelize work across agents or accounts, to build something
  "with agents", to process many papers/files at once, to look at runs or traces, or to run a retrospective.
---

# Running work as flows with agent-runner

You plan; the runner executes. Agent steps run in Harbor containers on Claude accounts that have a login,
every step leaves a trace. Don't use your own subagents for work meant to fan out: they share your account.

## Commands
```bash
agent-runner accounts                       # logins available (keyring, AGENT_RUNNER_TOKEN_<NAME>, ANTHROPIC_API_KEY)
agent-runner flow check FILE.yaml           # validate + dependency levels. ALWAYS before running
agent-runner flow run FILE.yaml --detach    # background, prints RUN_ID
agent-runner flow run FILE.yaml --var n=5   # override vars (JSON values allowed)
agent-runner flow show RUN_ID [--json]      # per step: status, account, reward, attempt, error
agent-runner flow list | flow cancel RUN_ID
agent-runner run -p FOLDER -i "instruction" --workdir DIR --detach   # one-off agent run
agent-runner examples                       # example flows (hello, mesh, retrospective)
```
Without the console script: `python3 -m agent_runner …`.
Runs: `$AGENT_RUNNER_HOME/runs/RUN_ID/` (default `~/.local/share/agent-runner`): `state.json`, `logs/<step>.log`.
Traces: `$AGENT_RUNNER_HOME/jobs/RUN_ID/<step>-a<attempt>/<trial>/`: `agent/trajectory.json`,
`agent/claude-code.txt`, `artifacts/app/<folder>` (changed files), `result.json`.

## Flow format
```yaml
name: short-name
workdir: .                 # relative paths resolve here (default: the flow file's folder)
concurrency: 6
vars: {n: 3}
defaults: {agent: claude-code, account: auto}
steps:
  - id: plan               # script step: runs on this machine
    run: python3 plan.py --n {{ vars.n }}
    outputs: json          # json | lines | text
  - id: build              # agent step: harbor exec on a COPY of `path`; the whole folder comes back as .files
    foreach: "{{ plan.output }}"
    path: "{{ item.dir }}"
    instruction: Do X. Write the result to <folder>/result.md.
    check: test -s result.md            # runs in the returned folder; exit 0 = reward 1, output = retry feedback
    retry: {max: 2, until: "reward >= 1", escalate: [{model: claude-opus-5-5}]}
  - id: merge
    needs: [build]         # required when a script reads results from $FLOW_CONTEXT instead of {{ }}
    always: true           # run even if some upstream instances failed
    run: python3 merge.py  # $FLOW_CONTEXT = JSON of all results; $FLOW_RUN_DIR = this run's folder
  - id: report
    when: "{{ all(b.ok for b in build) }}"
    path: "{{ merge.output }}"
    instruction: ...
```
- `{{ expr }}` is Python over earlier results; quote it in YAML. A string that is exactly one `{{ }}` keeps its type.
- Results: `ok status output error attempts`; agent steps add `reward account files job_dir cost_usd verifier`.
  `output` of an agent step = its final message. foreach → a list of results.
- Agents never see each other. Pass work explicitly (`output` into instructions, `files` into `path`) and add a
  merge script after a fan-out: each agent edited its own copy.
- Always give agent steps a `check:`; without it the reward only means "the folder came back".
- Don't name vars after dict methods (`items`, `keys`, `values`, `get`…); `flow check` flags it.

## Loop
1. `agent-runner accounts`. None → tell the user: `claude setup-token`, then `agent-runner accounts set NAME`.
2. Write the flow next to the work, `flow check`, fix every ✗.
3. `flow run … --detach`; give the user the run id.
4. Check `flow show` at intervals that fit the work (agent steps take 0.5–15 min), not in a tight loop.
5. For failures read `logs/<step>.log`, then the trial's `result.json`, `agent/claude-code.txt` tail and
   `verifier/test-stdout.txt`. Classify: flow bug, task too big (split), environment (image/paths), login/limits.
6. Plan the next flow from results. Keep flows under ~30 min. After a body of work, run the retrospective example.

## Rules
- Never read, print or copy tokens. `agent-runner accounts` is enough.
- `account: auto` spreads over accounts (max 2 runs each). One person using several seats on one machine can trip
  abuse filters; don't raise concurrency unless the user asks.
- Don't edit files under the runs/ or jobs/ folders; they are the record.
