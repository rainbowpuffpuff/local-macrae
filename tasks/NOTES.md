# tasks: notes

This module owns `tasks/` and the Modal support in `agent_runner/`. Everything below has been run here. Real Modal
and Claude runs could not be done (no credentials on this machine). The real Harbor 0.24 CLI was run, though, and
it accepted the exact command agent_runner builds and got as far as Modal authentication (details below).

## What I built

### agent_runner: Modal support (small and backwards compatible)
- `environment: modal | docker` can be set per step, at flow level, or in `defaults`. `{{ }}` templates are allowed.
  The default is `docker`, which builds the same commands as before (no `-e` flag).
- `harbor.exec_cmd(..., environment=, task_template=)` and `harbor.run_cmd(..., environment=)` add `-e modal` and
  `--task-template DIR`.
- `harbor.modal_env(env)` exports `MODAL_PROFILE` (taken from the environment, default `acalincarol`) to the Harbor
  process. It is skipped when `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` are both set.
- `harbor.modal_template(dir)` writes `<run>/modal-template/environment/Dockerfile`, a copy of
  `agent_runner/image/Dockerfile`.
- How a Modal agent step picks its image:
  1. the step's `image:`, or `AGENT_RUNNER_MODAL_IMAGE`, or settings.json `modal_image` (a registry image) is passed
     as `--image`;
  2. otherwise the step gets `--task-template` with the copied Dockerfile.
- `state.json` steps now carry `"environment": "modal"`.
- Bug fix: `harbor.IMAGE_DIR` pointed at `<repo>/image`, which doesn't exist in this vendored layout, so
  `agent-runner image build` was broken. It now finds `agent_runner/image`.
- `agent_runner/README.md` has a new "Modal" section.
- `VENDORED.md` says local edits are lost on the next sync, so this patch should go upstream as well.

**Harbor 0.24 caveat (checked against its compiler source and by running it).** With `harbor exec -p …`, Harbor
rebuilds the task's `environment/` folder from the input paths and pins `docker_image = "ubuntu:latest"`. The
template's Dockerfile is therefore ignored. Steps still work: Harbor's claude-code agent installs Claude Code at
trial start (about 1–2 extra minutes per agent step). For a faster demo, push the image once and set
`AGENT_RUNNER_MODAL_IMAGE`; Modal pulls and caches it:
`docker build -t ghcr.io/<you>/agent-base agent_runner/image && docker push ghcr.io/<you>/agent-base`
(it must be public, or needs a Modal registry secret).

### tasks/
| file | what |
|---|---|
| `tasks.json` | `{"tasks": [Task]}`: `methods-card` and `small-calc`. Inputs may add optional `options` (allowed values) and `pattern` (regex); the runner enforces both. |
| `flows/methods-card.yaml` | `sources` (script: `python -m tasks.prepare_methods`) → `card` (agent on Modal writes `methods-card.md`, every claim cited `[n]`, `check:` + 1 retry) → `collect` |
| `flows/small-calc.yaml` | `prepare` (script: `python -m tasks.prepare_calc`) → `calc` (agent on Modal: PySCF B3LYP ion–water scan, counterpoise-corrected def2-TZVP binding energy, full vs ECC-scaled (×0.75) point-charge Coulomb; writes `result.json`, `run_calc.py`, `output.log`, `explanation.md` citing group papers) → `collect` |
| `runner.py` | `start(task_id, inputs) -> run_id` (validates, then `agent_runner.flows.start_detached` with vars), `list_tasks()`, `get_task()`, `run_meta(run_id)`, `check_all()`, plus a CLI |
| `sources.py` | publication lookup by DOI, the RAG wrapper, OpenAlex abstract, numbered `[n]` sources with contract-shaped Citations |
| `prepare_methods.py` | RAG passages for one DOI: 6 method-oriented queries, filtered to the paper's DOI, in page order. Adds the OpenAlex abstract when fewer than 3 passages come from the paper, then up to 3 related group-paper passages. Writes `paper/{paper.json,sources.json,context.md}`. |
| `prepare_calc.py` | ion (Li+, Na+, K+, Mg2+, Ca2+) → `calc/{task.json,references.json,context.md}`. References come from RAG passages, else from charge-scaling (ECC) group papers in the publication list (title only). |
| `checks.py` | stdlib-only checks run by `check:`. **card**: required sections; every sentence ≥40 chars has `[n]` or says the sources don't state it; only existing numbers; every cited number is listed under References. **calc**: `result.json` numbers are finite and plausible (r_min 1.4–4 Å, E_int −200…−1 kcal/mol, ≥5 scan points, ECC/full ratio 0.75, matching ion); `run_calc.py`, `output.log` and `explanation.md` (≥120 words, valid `[n]`) exist. The output is the retry feedback. |
| `collect.py` | copies the agent's files to `<run>/result/`, writes `result/summary.txt`, prints `{"result_dir", "files", "summary", "data"}` |
| `requirements.txt` | `pyyaml`, `harbor[modal]>=0.24` on Python ≥ 3.12 only (Harbor's minimum; deploy's 3.11 image gets the CLI as a uv tool) |

User input never reaches a shell command:
- inputs are validated (known names only, one line of ≤300 chars, `options`/`pattern`);
- flows pass inputs to scripts through `env:` (`TASK_DOI`, `TASK_ION`), not into the command text;
- agent instructions only contain values the prep script already validated.

## How to run
```bash
pip install -e . && pip install -r tasks/requirements.txt
python -m tasks.runner check                      # tasks.json + every flow valid
python -m tasks.runner list
python -m tasks.runner start methods-card --input doi=10.1021/acs.jctc.5c02051     # prints run id
python -m tasks.runner start small-calc --input ion=Ca2+
agent-runner flow show <run-id>                   # or python -m agent_runner flow show <run-id>
MACRAE_HARBOR_ENV=docker python -m tasks.runner start small-calc   # local Docker instead of Modal
agent-runner flow run tasks/flows/small-calc.yaml --var ion=K+       # foreground, without the runner
```
Needs:
- a Claude login: `ANTHROPIC_API_KEY` or `AGENT_RUNNER_TOKEN_<NAME>`;
- a Modal login: `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET`, or `~/.modal.toml` with profile `MODAL_PROFILE`;
- `harbor` on PATH.

Expected time per run: about 1 min of script work, plus about 3–8 min per agent step on Modal (more without
`AGENT_RUNNER_MODAL_IMAGE`). A real PySCF check here: 9 B3LYP/def2-SVP scan points for Na+–water took 23 s,
minimum near 2.2 Å.

## Tests
```bash
pip install pyyaml pytest
pytest -q tasks/tests tests          # 70 passed (includes the existing tests/test_flows.py)
```
- `test_agent_runner_modal.py`: the commands built (modal vs docker, template vs registry image), `MODAL_PROFILE`
  rules, the template's Dockerfile, and a real engine run through a fake `harbor` on PATH that records argv and env.
- `test_task_flows.py`: both task flows end to end through the real engine with a fake harbor. Covers the check
  passing, a retry whose feedback is the check output, a bad DOI failing in the script step, and result files plus
  summary.
- `test_tasks_runner.py`, `test_tasks_checks.py`, `test_tasks_prepare.py`: input validation (shell-injection
  attempts rejected), var round-trip through the agent_runner CLI, `run_meta`, checks, and prep scripts with a fake
  `rag` module, an empty index and no network (`MACRAE_OFFLINE=1`).

## Assumptions about other modules (integrator: check these)
- **server**
  - Uses `from tasks import runner`:
    - `runner.list_tasks()` for `GET /api/tasks`;
    - `runner.start(task_id, inputs)` for `POST /api/tasks/{id}/start` and `/api/tools/start_task`;
    - `runner.TaskNotFound` (a KeyError) → 404, `runner.TaskInputError` (a ValueError) → 400;
    - `runner.run_meta(run_id)` gives the task id and title for RunSummary.
  - The same fields are in `state.json` `vars` (`task_id`, `task_title`, inputs) and in `runs/<id>/macrae.json`.
  - Paths resolve against the repo root, so the server's working directory doesn't matter. Set `AGENT_RUNNER_HOME`
    before importing agent_runner.
  - For the trace:
    - script steps log `search: '<query>' → N passages` lines to `logs/<step>.log` (→ `search` events), and
      `prepare_methods` / `prepare_calc` print `"citations": [Citation]` in their JSON output (→ `cite` events);
    - retries use `until: "(reward or 0) >= 1"`, since `reward` is None when a trial errors;
    - the final summary sentence is the `collect` step's `output.summary` (in state.json, cut at 400 chars) and
      `<run>/result/summary.txt`;
    - result files are in `<run>/result/`;
    - agent steps' Harbor trials are in `state.steps[key].job_dir`.
  - A Modal or login failure shows up as the step's `error` (for example
    `AuthError: Modal profile 'acalincarol' was not found …`).
- **rag**
  - `rag.search(query, k=…)` returns Passages as dicts, dataclasses or pydantic models, each with a `citation` that
    has `doi` and `page`.
  - If the package can't be imported, I fall back to `python -m rag search "<q>" --k N --json`. Those flags are a
    guess; if they don't exist the fallback just returns no passages.
  - Any rag exception means "no passages", so tasks work before ingest.
- **deploy**
  - Install `tasks/requirements.txt`. It brings `harbor[modal]`; a plain `harbor` install fails on Modal with
    `MissingExtraError`.
  - No Docker is needed on the backend.
  - On AWS, set `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` and **leave `MODAL_PROFILE` unset**, unless `~/.modal.toml`
    has that profile. Modal fails with "profile not found" otherwise.
  - Optional: `AGENT_RUNNER_MODAL_IMAGE` (faster steps), `MACRAE_HARBOR_ENV`.
  - The backend needs outbound HTTPS (OpenAlex, Modal, Anthropic), and `python3` on PATH for the stdlib checks.
  - The repo's `data/group_publications.json` must be in the image.
  - Detached flow processes write under `AGENT_RUNNER_HOME`; put it on a persistent volume.
- **web**: task inputs can carry `options` (render a select) and `pattern`. `icon` values are `flask` and `atom`.
- **voice**: `start_task` sends only `task_id`, so the defaults are used (DOI `10.1021/acs.jctc.5c02051`, ion `Na+`).
