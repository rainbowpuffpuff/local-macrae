# agent-runner

Runs agents as **flows**: YAML graphs of agent steps (Claude Code, or any Harbor agent, inside a Docker container)
and script steps (plain commands on this machine). Steps run in parallel where they can, pass results to each other,
retry with feedback until a check passes, and spread over every Claude account that has a login. Every agent step
leaves a Harbor trace: transcript, reward, changed files, cost.

```bash
pip install -e .                       # from the local-macrae checkout
agent-runner image build              # once: Docker image with Claude Code preinstalled (~80 s)
claude setup-token                     # per Claude account, then:
agent-runner accounts set alice        # paste the sk-ant-oat01-… token (stored in the GNOME keyring)
agent-runner flow run $(agent-runner examples | grep hello)
agent-runner flow run my-flow.yaml --var n=5 --detach
agent-runner flow show <run-id>
```

Needs: Python ≥ 3.10 with PyYAML, Docker, [Harbor](https://github.com/harbor-framework/harbor) (`harbor` on PATH).

## Logins
An account is a name with a login, from any of:
- keyring (`secret-tool`): `agent-runner accounts set NAME`
- environment: `AGENT_RUNNER_TOKEN_<NAME>=sk-ant-oat01-…` (servers, CI, teammates without a keyring)
- environment: `ANTHROPIC_API_KEY` → account `api-key` (API billing)

`account: auto` picks the least busy account (max 2 runs each). An account whose login is rejected, or that hits a
usage limit, is parked and its runs move to another account. Tokens are only placed in the environment of that
account's Harbor process; other accounts' tokens never reach a run.

## Flows
```yaml
name: fix-modules
workdir: .                 # relative paths resolve here (default: the flow file's folder)
concurrency: 6
vars: {target: src}
defaults: {agent: claude-code, account: auto, model: ""}
steps:
  - id: list               # script step
    run: python3 list_modules.py {{ vars.target }}
    outputs: json          # json | lines | text (auto = json if it parses)

  - id: fix                # agent step = harbor exec on a copy of `path`
    foreach: "{{ list.output }}"
    path: "{{ item }}"
    instruction: Make the tests in {{ item }} pass.
    check: python3 -m pytest -q          # runs in the returned folder: exit 0 = reward 1, output = feedback
    retry: {max: 2, until: "reward >= 1", escalate: [{model: claude-opus-5-5}]}

  - id: gate
    needs: [fix]           # needed when a script reads results via $FLOW_CONTEXT instead of {{ }}
    run: python3 check_all.py
    always: true           # run even if some fixes failed

  - id: report
    when: "{{ not gate.ok }}"
    instruction: Explain what failed in {{ [f.error for f in fix if not f.ok] }}
    path: "{{ fix[0].files }}"

  - id: bench              # task step = harbor run on a task folder or dataset
    task: tasks/my-task    # or dataset: terminal-bench@2.0
    attempts: 3
```
- `{{ expr }}` is Python over earlier results. A string that's exactly one `{{ }}` keeps its type. Quote it in YAML.
- Dependencies = `needs` + every step id used in `{{ }}`, `when`, `foreach`, `until`.
- A step is skipped when an upstream step failed, unless `always: true`.
- Results: `ok status output error attempts`; agent/task steps add `reward rewards account job_dir trials files
  artifacts cost_usd verifier`. `files` = the folder passed as `path`, after the agent's changes. foreach → list.
- Script steps get `FLOW_CONTEXT` (JSON of all results so far), `FLOW_RUN_DIR`, `FLOW_JOBS_DIR`, `FLOW_ITEM`,
  `FLOW_ATTEMPT`. `check:` commands get `FLOW_FILES`.
- On retry, the agent's instruction gets the previous attempt's error, check output and final message appended.

## Modal
`environment: modal` (on a step, at flow level, or in `defaults`; `{{ }}` allowed; default `docker`) runs agent and
task steps on [Modal](https://modal.com) instead of local Docker: `harbor exec|run … -e modal`. Needs
`harbor[modal]` and a Modal login: `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET`, or a profile in `~/.modal.toml`
(`MODAL_PROFILE`, default `acalincarol`, is exported to Harbor unless the token variables are set).
Modal can't use the local `agent-runner/agent-base` image. When a registry image is configured (the step's `image:`,
`AGENT_RUNNER_MODAL_IMAGE`, or `modal_image` in settings.json) it is passed as `--image`, and Modal pulls and caches
it. Otherwise agent steps get `--task-template <run>/modal-template`, whose `environment/Dockerfile` is
`agent_runner/image/Dockerfile`. Harbor 0.24's `exec -p` still pins its default `ubuntu` image over the template,
so Claude Code is installed at trial start (about 1–2 min more per step). To avoid that, push the image once:
`docker build -t ghcr.io/you/agent-base agent_runner/image && docker push ghcr.io/you/agent-base`.

## Examples (`agent_runner/examples/`, list them with `agent-runner examples`)
| flow | what it shows |
|---|---|
| `hello.yaml` | one agent run with a check |
| `mesh-example.yaml` | plan → parallel agents with retry/escalation → merge → conditional summary |
| `retrospective.yaml` | reads runs, events, Claude Code sessions and this code; 4 parallel reviews → ranked improvements |

## Where things go
| | default | change with |
|---|---|---|
| state (runs, leases, cooldowns, events, settings) | `~/.local/share/agent-runner` | `AGENT_RUNNER_HOME` |
| Harbor jobs = traces | `$AGENT_RUNNER_HOME/jobs/<run-id>/<step>-a<attempt>/` | `AGENT_RUNNER_JOBS` |
| agent image | `agent-runner/agent-base:latest` | `settings.json` → `agent_image` |

## Module map
| file | role |
|---|---|
| `flows.py` | flow format, dependency analysis, the async engine, run management |
| `harbor.py` | builds `harbor exec` / `harbor run` commands, reads jobs, trials and trajectories |
| `pool.py` | account leases, cooldowns, auto-spread |
| `tokens.py` | logins from keyring / environment |
| `events.py`, `retro.py` | usage event log and retrospective input |
| `cli.py` | `agent-runner` |

Claude Code: copy `.claude/skills/agent-runner/` into a project (or `~/.claude/skills/`) and Claude will write,
launch and follow flows for you.

## Development
```bash
pip install -e ".[test]" && pytest        # engine tests, no Docker needed
```
