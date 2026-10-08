# tasks/: v3 notes (a research agent in the open, and the seed of the capability story)

Module: tasks (owns `tasks/` except `tasks/common/`, and `research/`). Brief: make the research tasks look and act
like a real research agent:
- a lab notebook (`NOTES_TO_SELF.md`) that evolve ingests;
- a manuscript written progressively at `results/manuscript.md` (math, figures, [n] citations, drafted early and
  revised), entirely through Write/Edit so the trace has the full edit stream;
- a check for all of that;
- the first Modal runs' lessons turned into the seed of the capability story (expected learned capability: a
  pinned calc image).

**Read first: there are no BFF flows in this tree.** `tasks/flows/` has `methods-card.yaml` and `small-calc.yaml`
only. The BFF tasks were still being built by the `bff-ideas` run (`.reference/STATUS.md`); only
`evolve/tests/fixtures` mention `bff-charges`.
- So the protocol is a shared piece (`tasks/research.py` + `checks.py research`) that any flow wires in with three
  edits (below), with a generic flow step for flows whose prep script isn't ours.
- `python -m tasks.runner check` and a test **fail for any task that lacks the hook**, so the BFF flows can't land
  without it. This is the same approach evolve used for its lesson hook.
- small-calc is fully converted. methods-card (a reading task, not research) keeps a notebook but writes no
  manuscript.

## What I built

| file | what |
|---|---|
| `tasks/research.py` | **The research protocol.**<br>`protocol(workspace, python=, packages=, figure_hint=, numbers_hint=)` returns the instruction block.<br>`prepare_workspace(dir, refs)` writes `PROTOCOL.md` (detailed rules + the reference list in the exact `## References` form) and creates `results/`.<br>`parse_notes` / `notes_to_lessons` / `gaps` turn `NOTES_TO_SELF.md` into evolve-shaped lessons.<br>CLI: `python -m tasks.research prepare …` (the generic step for any flow) and `notes FILE`. |
| `tasks/checks.py` | New subcommands:<br>`research` (manuscript + notebook + edit stream);<br>`calc --research` (small-calc: numbers + research; `explanation.md` is no longer required, the manuscript replaces it);<br>`notes`;<br>`card --notes`;<br>`image-capability DIR`.<br>Stdlib only, as before. |
| `tasks/flows/small-calc.yaml` | The agent is now a research agent. It opens a notebook, checks the environment before installing, starts the scan in the background, drafts the manuscript while it runs, writes `result.json` (+ `figures`), revises the manuscript as numbers come in, and finishes the notebook.<br>New var `calc_image: ""` → the step's `image:` (the learned capability).<br>The check is `calc … --research`; collect copies the manuscript, figures and notes. |
| `tasks/flows/methods-card.yaml` | + `paper/NOTES_TO_SELF.md` (instruction paragraph, `check … --notes`, collected). |
| `tasks/prepare_calc.py` | Also writes `calc/PROTOCOL.md` + `calc/results/` and prints `protocol`, `manuscript`, `notes`. |
| `tasks/collect.py` | `--files` takes sub-paths and globs (`results/manuscript.md 'results/fig*.png'`) and keeps relative paths, so `<run>/result/results/manuscript.md` still finds `fig1.png`. The output gains `manuscript` `{path, title, words, figures, citations}` and `notes_to_self` (path). |
| `tasks/runner.py` | `research_problems(task, spec)`, part of `check_all()`: every task needs a `"research"` entry (or `"research": false`) and a flow that asks for the notebook and, if it has a manuscript, includes the protocol and checks it. |
| `tasks/tasks.json` | small-calc subtitle/prompt describe the research run; optional `"research": {"workspace", "manuscript", "figures", "notes"}` per task. |
| `tasks/capabilities/` | **The capability seed.**<br>`first_modal_runs.json`: the dusk evidence.<br>`calc-image/`: the expected capability (`Dockerfile`, `requirements.lock`, `smoke.py`, `build.sh`, `manifest.json`).<br>`python -m tasks.capabilities show \| check \| rehash \| gaps FILE… \| lessons [--apply]`.<br>`detect_gaps(text)` is a deterministic gap recogniser, the fallback when an agent doesn't print `CAPABILITY_GAP`. |
| `tasks/tests/reference_run.py` + `fixtures/small-calc-na/` | A **real** reference run plus the deterministic agent side (draft, 9 revisions, notebook, ATIF trajectory). `apply_edits(DRAFT, EDITS) == FINAL` is asserted. |
| `tasks/tests/fake_harbor.py` | Modes:<br>`calc` replays the reference run;<br>`calc-no-math` / `calc-shell` / `calc-once` / `calc-legacy` each break one rule;<br>card modes also write a notebook.<br>`CALC_RESULT` is now the real result (−25.46 kcal/mol at 2.20 Å).<br>`write_research_files()` / `RESEARCH_CALLS` are for other fakes. |
| `research/demo/small-calc-na/` | A replayable bundle for web, the mock backend and filming: `edit_stream.json` in the shape of `GET /api/runs/{id}/manuscript`, draft, final, `fig1.png`, notebook, trajectory, result. A test keeps it current. |
| `research/README.md` | What `research/` holds. |

### The research protocol (what every research agent is told)
1. **Lab notebook.**
   - `<ws>/NOTES_TO_SELF.md` is created with Write in the first few actions and kept current with Edit.
   - Sections: `## What I tried`, `## What was slow` (seconds), `## What to do differently next time`,
     `## Capability gaps`.
2. **Manuscript** at `<ws>/results/manuscript.md`.
   - **Only through Write and Edit** (no redirection, tee, sed, heredoc or Python writes).
   - Drafted before the main calculation finishes, with `**TBD**` for missing numbers.
   - Revised with Edit as results arrive: replace TBDs, rewrite contradicted sentences, delete unsupported claims.
   - Final structure: `# title`, Abstract (80–200 words, key numbers), Methods, Results, Discussion with
     limitations, References.
   - Math: inline `$…$` and ≥ 1 display `$$…$$`, KaTeX-safe.
   - Figures: matplotlib, saved as `results/fig1.png…`, embedded as `![Figure 1. caption](fig1.png)` and referred
     to in the text.
   - Citations: `[n]` only from `PROTOCOL.md`, listed under References exactly as shown there.
3. **Time.** Never sleep > 15 s. Long jobs run in the background, the log is polled every 10–15 s, and the
   manuscript is written meanwhile.
4. **Environment.** Check before installing (`/opt/calc/bin/python -c "import pyscf, numpy, matplotlib"`). If
   something is missing, install it, time it, and print `CAPABILITY_GAP: calc-image: <what, seconds>`. If venv
   fails with ensurepip, `apt-get install python3-venv` first.

### What `checks.py research` fails on (a retry re-runs the whole calculation, so only what matters fails)
- **Manuscript:**
  - missing; no `# title` first;
  - a missing section (abstract, methods, results, discussion|conclusion, references);
  - < 300 words of prose; abstract outside 50–260 words;
  - leftover `TBD`/`TODO`/`???`.
- **Math:**
  - no display equation; no inline math;
  - unbalanced `$$` or braces; `\begin{align|equation}`.
- **Figures:**
  - no embedded figure, or a missing file;
  - not a real PNG ≥ 200×120 px;
  - a figure never referred to as "Figure n".
- **Citations:**
  - fewer than 2 distinct `[n]` (1 if only one source);
  - unknown numbers;
  - cited numbers missing from References;
  - a References line whose DOI contradicts the source.
- **Numbers:** the abstract doesn't state the `--numbers` keys of `result.json` at the precision written
  (`−25.5` or `−25` for −25.46; sign optional). This catches a stale draft number that was never revised.
- **Notebook:** missing, or a required section empty. Placeholders like "(nothing yet)" count as empty.
- **Edit stream:** read from the Harbor trial next to the returned folder (`<trial>/agent/trajectory.json`, else
  `claude-code.txt` stream-json). It fails if the manuscript was written from the shell, never went through
  Write/Edit, or was written once and never revised.
- **Notes only (printed, never failing):**
  - all edits back to back;
  - a figure made but not shown;
  - a notebook written from the shell;
  - no transcript found (e.g. when the check runs on a loose folder).

It also prints a summary line the trace can show, e.g. `manuscript: 933 words, 3 display + 23 inline equations,
1 figure(s), citations [1, 2, 3, 4, 5]` / `edit stream: 1 Write + 9 Edit on the manuscript`.

## The BFF flows: three edits per flow (plus the tasks.json entry)
When `tasks/<bff-id>/` + `tasks/flows/<bff-id>.yaml` land, `python -m tasks.runner check` will say
`no "research" entry`. Apply:
```yaml
  # 1. a generic step after the flow's prepare step (or call tasks.research.prepare_workspace in the prep script)
  - id: research
    description: Open the lab notebook and the manuscript (research protocol)
    run: >-
      "{{ vars.python }}" -m tasks.research prepare --dir "{{ prepare.output.dir }}"
      --references "{{ prepare.output.references_file }}" --task-id "{{ vars.task_id }}"
      --python /opt/bff/bin/python --packages bfflearn --figure "the posterior of …" --numbers "the Bayes factor …"
    outputs: auto
  # 2. in the agent step's instruction, before {{ vars.lessons }}:
      {{ research.output.protocol }}
  # 3. the agent step's check: the BFF check, then the research check
    check: >-
      python3 tasks/<bff-id>/check.py … && python3 "{{ research.output.checker }}" research .
      --references "{{ prepare.output.references_file }}" --numbers <result.json keys the abstract must state>
```
- In `tasks.json`: `"research": {"workspace": "<dir>", "manuscript": "results/manuscript.md", "figures":
  "results/fig*.png", "notes": "NOTES_TO_SELF.md"}`.
- In `collect`: add `results/manuscript.md 'results/fig*.png' NOTES_TO_SELF.md`.
- `test_the_generic_research_step_hooks_any_flow` runs exactly this shape through the real engine.
- Adapt `--python/--packages` to the BFF image, e.g. its venv and `bff`/`bfflearn`, and give the image its own
  capability spec like `calc-image/` if it doesn't exist yet.

## The capability seed: dusk evidence → gap → expected capability
`tasks/capabilities/first_modal_runs.json` holds the observations behind the gap.

| id | what | s | lesson kind | fixed by |
|---|---|---|---|---|
| `agent-image-build` | first agent image build on Modal | 170 | setting | a pushed, pinned image (calc-image) |
| `pyscf-install` | the agent pip-installed PySCF itself, every run | ~60 | tool | calc-image |
| `sleep-wait` | a 4-min sleep while waiting | 240 | avoid | the protocol (background + poll + write) |
| `ensurepip` | `python3 -m venv` failed: ensurepip is not available | – | setting | calc-image (or apt-get python3-venv) |
| `omp-oversubscription` | 1 s HF test took 10.8 s with a thread per host core (1.4 s with `OMP_NUM_THREADS=1`) | 9.4 | setting | – (measured here, see below) |

The first four come from `.reference/STATUS.md` and the brief. The last one I measured on this machine (a shared
16-core host at load ≈ 50); it matters for small Modal boxes.

**Expected capability `calc-image`** (`tasks/capabilities/calc-image/`, kind `image`):
- `Dockerfile`: `FROM ${BASE_IMAGE}` (the macrae agent image from `tasks/common`, so Claude Code and the live
  wrapper stay), then `python3-venv`, then `/opt/calc` from `requirements.lock`; `smoke.py` runs at build time.
- `requirements.lock`: 15 exact pins resolved here (CPython 3.12, x86_64 = ubuntu 24.04): pyscf 2.14.0,
  numpy 2.5.3, scipy 1.18.1, matplotlib 3.11.2, h5py 3.16.0 and their dependencies.
- `smoke.py`: Na⁺–water HF/STO-3G energy (must be bound) plus a matplotlib figure; one JSON line, exit 0/1.
- `manifest.json`:
  - `name, kind, version, purpose, provides, for_tasks, use {flow_var: calc_image, check_in_sandbox}, smoke,
    tests`;
  - `requires {secrets: [], network: [], registry_write: false}`, `build_network` (apt + PyPI hosts);
  - `expected_effect {setup_s_before: 130, setup_s_after: 0}`, `created_by`, `files {name: sha256}`.
- `build.sh IMAGE [--base AGENT_IMAGE] [--push]`: runs `checks.py image-capability`, then docker build (which runs
  the smoke test), runs the smoke test again in the built image, and pushes.
- **TEST** = `checks.check_image_capability(dir)`:
  - every requirement pinned `==` (no URLs, `-e` or index options);
  - FROM `${BASE_IMAGE}`; installs exactly the lock; runs `smoke.py` at build;
  - no `curl | sh`; no secret-like ENV/ARG;
  - manifest fields present; sha256 of every listed file matches;
  - `requires` asks for no secrets, network or registry write.
  - The backend's CREATE step can run it against the spec the agent writes. `tasks/capabilities/calc-image` is the
    yardstick: it is **not** installed by default, so dusk stays honest.
- **USE AGAIN** = the server sets the flow var `calc_image=<pushed image>`. The `calc` step's `image:` makes
  agent_runner pass `--image` instead of `--task-template` (tested). The agent's first protocol action (checking
  `/opt/calc`) then succeeds and it installs nothing. The run's notebook should then say so, and the dawn
  benchmark's setup time drops.

## Verified here, and how
- **Real science.** The flow's calculation ran for real with PySCF 2.14.0: Na⁺–water B3LYP/def2-SVP scan,
  CP-corrected def2-TZVP at the minimum, −25.46 kcal/mol at 2.20 Å, BSSE 0.59 kcal/mol, 141 s on a loaded host.
  - This matches the first Modal run (−25.5 at 2.2 Å).
  - Its `result.json`, `output.log`, `run_calc.py` and `fig1.png` are the fixture; the reference manuscript passes
    `checks.py calc --research` with the real `references.json` that `prepare_calc` builds offline.
- **The calc image's environment.**
  - Fresh venv + `pip install -r requirements.lock --no-cache-dir`: 67 s. `pip freeze` equals the lock exactly.
    The first unpinned install was 130 s, on this machine under load.
  - `smoke.py`: ok, 1.4 s.
  - `checks.py image-capability`: ok. Tampering (unpinned, URL, FROM, no smoke, secret ENV, curl|sh, hash, asked
    authority) is caught.
- **The flows through the real agent_runner engine with the fake harbor:**
  - small-calc passes; the instruction carries the protocol; `PROTOCOL.md` reaches the sandbox;
  - the check log shows `edit stream: 1 Write + 9 Edit`; collect keeps `results/…`;
  - each broken rule fails with the exact feedback in the retry instruction;
  - `calc_image` becomes `--image` (and no template);
  - methods-card keeps its notebook;
  - a BFF-shaped flow with the generic step passes.
- **Tests.**
  - `pytest -q tasks/tests tests`: **141 passed, 1 skipped**. The skip is the smoke test, which needs PySCF; it
    passed in the PySCF venv.
  - Whole repo: **418 passed, 2 skipped, 1 failed**. The failure is evolve's end-to-end test (see "integrator").
    With the 3-line patch below applied in a scratch copy, the whole repo passes: **419 passed, 2 skipped**.
  - `python -m tasks.runner check` and `python -m evolve check-flows`: ok.

**Not verified:**
- no Docker on this machine, so the image itself was never built;
- no Modal pull of it;
- no real Claude agent run following the new protocol (no credentials here).

The instruction was read end to end as rendered (about 940 words).

## For the other modules (please check)

### integrator
1. **evolve's end-to-end test needs its fake agent to follow the protocol.** (verified: whole repo 419 passed with it)
   `evolve/tests/test_evolve_flows.py::test_the_next_run_learns_from_the_last` runs the real small-calc flow; its
   fake writes no manuscript, so the new check fails it. I don't own `evolve/`. The patch:
   ```diff
   --- evolve/tests/fake_harbor_evolve.py
   -from fake_harbor import CALC_RESULT  # noqa: E402  (the tasks module's passing result.json)
   +from fake_harbor import CALC_RESULT, RESEARCH_CALLS, write_research_files  # noqa: E402  (tasks' passing outputs)
   @@ main()
   +    write_research_files(calc)  # manuscript, figure, notebook (the research protocol), real result.json
        (calc / "result.json").write_text(json.dumps(CALC_RESULT))
   @@ after the "Run the scan with the venv's python" call
   +    for name, args, out in RESEARCH_CALLS:  # the manuscript drafted and revised, the notebook kept
   +        call(name, args, out, 3)
   ```
   `RESEARCH_CALLS` holds only the manuscript/notebook Write/Edit calls, so evolve's rule-based lessons
   (pyscf / venv) are unchanged. The backend's v3 seed-demo fakes need the same if they run small-calc.
2. **The v3 workflow's merge step drops most of this module.** In `.reference/v3-frankenstein.yaml`, `merge`
   copies only `owned['tasks'] = ['tasks/flows', 'research']`. That leaves out:
   - `tasks/*.py` (research.py, checks.py, collect.py, runner.py, prepare_calc.py);
   - `tasks/capabilities/`;
   - `tasks/tests/`, `tasks/tasks.json` and this file.

   The flows then reference a `tasks.research` that isn't there. Copy `tasks/` minus `tasks/common/` instead.
   The `research/` folder is complete on its own (README + demo bundle).

### backend (server/, evolve/, agent_runner/)
1. **Authority (important).** The planner may set any flow var that isn't a task input or in
   `server/planner.py` `RESERVED_VARS`.
   - `calc_image` (new) and `tools_dir` (v2) are not reserved, so an LLM plan could choose the sandbox image or the
     folder uploaded into it.
   - Add both to `RESERVED_VARS`: only the capability registry and evolve may fill them.
   - In **dusk** mode, leave `calc_image=""` (as well as `lessons`/`tools_dir`).
2. **Notes → evolve.** After a run:
   - the notebook is `<run>/result/NOTES_TO_SELF.md` (collect's output field `notes_to_self`);
   - while running, it's the Write/Edit calls on `…/NOTES_TO_SELF.md` in the trace;
   - `tasks.research.notes_to_lessons(text, task_id)` gives `{task_id, lesson, kind: do|avoid|setting|tool, source:
     "notes"[, capability]}`; evidence ids are yours to attach (e.g. the seq of the last notebook Edit);
   - `tasks.research.gaps(text)` gives the `CAPABILITY_GAP` lines + the notes' gap bullets.
3. **Gaps.**
   - Agents print `CAPABILITY_GAP: <name>: <why>` (small-calc's protocol uses the name `calc-image`).
   - Fallback recogniser for agents that didn't: `tasks.capabilities.detect_gaps(text_of(trajectory))` →
     `{name, why, evidence, seconds, said_by_agent, spec}`. It sees ensurepip failures, `No module named 'pyscf'`,
     pip installs of PySCF, and a missing `/opt/calc`.
   - `wasted_sleeps(text)` flags `sleep ≥ 60`.
4. **Image capabilities.**
   - TEST with `checks.check_image_capability(dir)` (or `python3 tasks/checks.py image-capability DIR`). The
     manifest shape is above; adapt it to your registry's manifest, and keep `requires` empty.
   - INSTALL by pushing the image (`build.sh … --push`) and recording `use.flow_var=calc_image` → value.
   - To record setup time before/after: the dusk number is in `first_modal_runs.json` `setup_s`; the agent's
     notebook states its own install seconds.
   - `python -m tasks.capabilities lessons --apply` seeds the five dusk lessons into evolve. It's idempotent, but
     don't run it before a dusk benchmark.
5. **Manuscript endpoint.**
   - Paths are `/app/<workspace>/results/manuscript.md` (small-calc: `/app/calc/…`); match the suffix. Tools are
     `Write`, `Edit`, and possibly `MultiEdit`.
   - Figures are linked relative to the manuscript (`fig1.png` = `results/fig1.png`); `results/fig1.png` (relative
     to the workspace) also passes the check, so resolve against the manuscript's folder first, then the
     workspace.
   - After collect: `<run>/result/results/{manuscript.md, fig*.png}`. During a run, the trial's
     `artifacts/app/calc/results/` only exists after the trial ends.
   - `research/demo/small-calc-na/edit_stream.json` shows the exact shape I assumed:
     `{seq, t, op: write|edit, path, old, new, content-after}`.
6. **Catalog.** `server/catalog.py` passes known task fields only, so tasks.json's new optional `"research"` entry
   (workspace, manuscript, figures, notes) is dropped from `GET /api/tasks`. Pass it through if the page wants to
   know which tasks write a manuscript.

### web
- Demo data for the Manuscript and "What it learned" views: `research/demo/small-calc-na/`.
  - 15 ops; the manuscript is 1 Write + 9 Edits; op 7 deletes a premature def2-SVP claim.
  - Drafts contain `**TBD**`, worth a subtle highlight while replaying.
  - Math is plain KaTeX (`$…$`, `$$…$$`, `\mathrm`, `\frac`, `\sqrt`, `\varepsilon`, sub/superscripts; no
    `align`).
  - Citations are `[n]` / `[1, 3]`, and `## References` lines carry DOIs.
- The notebook (`NOTES_TO_SELF.md`) has fixed sections, so it can be shown as four short lists
  (`tasks.research.parse_notes`).

## How to run
```bash
pip install -e . && pip install pyyaml pytest
python -m tasks.runner check                                  # tasks + flows + the research hook
python -m pytest -q tasks/tests tests                         # 141 passed, 1 skipped (PySCF smoke)
python -m tasks.capabilities show | check | gaps FILE         # the capability seed
python -m tasks.tests.reference_run --demo research/demo/small-calc-na   # regenerate the demo bundle
tasks/capabilities/calc-image/build.sh ghcr.io/<you>/macrae-calc:$(date +%F) --base ghcr.io/<you>/macrae-agent:latest --push
agent-runner flow run tasks/flows/small-calc.yaml --var ion=K+ --var calc_image=ghcr.io/<you>/macrae-calc:…
```
