# w4/docs: README, demo script, traces guide, architecture page

Branch `w4/docs`. Documentation only, plus one test file. No code outside `tests/test_docs.py` was touched.

## What I did

| file | what |
|---|---|
| `README.md` | Rewritten for the final Macrae: what it is, the Jungwirth group, the Frankenstein brief and how each part of it maps to Macrae, how to use the site, how it runs (Worker + Durable Object + Container + R2, Harbor on Modal, ElevenLabs, Anthropic API) with an ASCII diagram, repository map, local run, deploy (secret **names** only), documentation index. The old content (publication-list notes, agent-runner) is kept at the end, lightly edited. |
| `docs/DEMO.md` (new) | Presenter's script: a 7-minute live demo as a timed table (time, beat, what to say, what to click), the evening-before and 30-minutes-before checklists (benchmark done, run ids noted, trace downloaded, voice tested, container warm), a fallback table (offline, voice fails, queued/cap, run fails, empty manuscript, no dawn column), a 3-minute cut, and likely questions with answers. |
| `docs/TRACES.md` (new) | How to read and download traces: the three levels (timeline, zip + report, raw files); every event type with icon and example title; how events are built and de-duplicated; the API with `curl`/`jq` examples; the zip layout and "where to look first"; reading ATIF `trajectory.json` with one-liners; where traces are stored (local, container, R2); traces as training data; what a trace does and doesn't contain (paper text yes, secrets no). |
| `docs/architecture.html` | Updated to the final state, same style, structure and diagrams: header and intro; main diagram labels (panel tabs, Dusk → Dawn, voice tools, container's chat/planner/costs/evolve, R2 `state/evolve/`, sandbox writes the manuscript and uses installed tools, live streaming, pinned image); Status rewritten by wave (v1, v2, v3, w4); "What happens when you…" (streamed `/api/chat`, queue, planner, capabilities, checks, distill); traces (three levels incl. zip/report); costs (no longer "in progress"; per answer/run/session, estimates, spend cap); BFF tasks built; Frankenstein (three capability kinds, notes to self, ledger, dusk → dawn benchmark and sky clock); code map (adds `agent_runner/`, `proof/`, `tests/`, `docs/`); "Open to everyone, no login" paragraph; how it was built (all waves); owner items (where each secret lives, no values); glossary additions. The one label that overflowed its box ("CAPABILITY_GAP: name: why") is shortened. No account names (the live URL is not on this page), no secrets, no e-mail addresses. Removed the line about replacing a leaked key. |
| `tests/test_docs.py` (new) | 13 tests: the four docs exist; no secret-shaped strings or e-mail addresses in any of them; no account name in the architecture page; the page is well-formed HTML (balanced tags); in-page `#links` resolve; every SVG label starts inside its box and the drawing; **a headless-Chromium test** that renders the page and measures every label (no overflow, no overlap; skipped when Python Playwright/Chromium aren't installed); relative Markdown links and same-file `#anchors` in README/DEMO/TRACES resolve. |

## How it's tested

- `python -m pytest -q tests/test_docs.py`: 13 passed with Playwright + Chromium installed (12 + 1 skipped without).
  The browser test was checked against a deliberately broken copy (an over-long label, a shifted label): it reports
  both the overflow and the overlap.
- I rendered the page in headless Chromium (1200 px, dark and light) and looked at the screenshots: the main diagram
  and the tables are clean in both themes.
- Full suite: see "Test results" below.

## Test results

Run here with Python 3.12 (uv venv with every module's requirements, plus Python Playwright + Chromium) and Node 22:
- **The branch alone** (a clean `git worktree` of `w4/docs` at f8ad184, without anyone's uncommitted changes):
  `make test` exit 0. pytest **318 passed**, nothing skipped (the browser test ran). Worker: 39/39. Web: 61/61.
- **The shared working tree** (with the uncommitted v2-integration changes another flow left in it): pytest 363
  passed and 1 failed. The failure was `deploy/tests/test_aws_deploy.py::test_up_creates_everything_once_and_is_idempotent`
  with `tar: .: file changed as we read it`: the test tars the repo while other processes were writing to it. Run
  again by itself, that file passes 23/23. It has nothing to do with the docs.
- Note: `make test` with `PY=<venv python>` fails `deploy/tests/test_artifacts.py::test_makefile_dry_runs`, which
  expects the literal `python3` in the dry run. Put the venv first on `PATH` instead.

## What the integrator must know

1. **The docs describe the final, merged state, written before the other branches landed.** I wrote them from the
   briefs (`.reference/v3-frankenstein.yaml`, `.reference/w4.yaml`, `.reference/rag-papers.yaml`,
   `.reference/bff-ideas.yaml`) and the v1/v2 code. After merging, please check these claims against the merged code
   and fix the wording (not the features) where they differ:
   - routes: `POST /api/chat` (streamed), `GET /api/runs/{id}/trace.zip` **(v3 specified `GET /api/runs/{id}/trace`,
     w4 traces_download specified `trace.zip`; TRACES.md and the architecture page use `trace.zip`)**, the HTML
     report's route (I only name the **Open report** button, no route), `GET /api/runs/{id}/manuscript`,
     `GET /api/runs/{id}/artifacts/<path>`, `GET /api/capabilities`, `POST /api/benchmark/{dusk|dawn}`,
     `GET /api/dawn-report`;
   - the zip layout in `docs/TRACES.md` section 3 (`runs/<id>/…`, `jobs/<id>/<step>-a<n>/<trial>/…`,
     `macrae/events/<id>.jsonl`) — that is the on-disk layout; adjust if traces_download prefixes or renames folders;
   - that the zip **excludes `runs/<id>/live/token`** (TRACES.md section 7 says so; if it doesn't, that is a bug in
     the zip, not in the docs: the token lets anyone post live lines to that run);
   - UI labels used in DEMO.md and README: tabs **Tasks, Run, Manuscript, What it learned, Capabilities**, the
     **Dusk → Dawn** view, buttons **Download trace** and **Open report**, "queued, position N", the 🧭 plan card;
   - voice tools `get_costs` and `get_learnings` (in the diagram as "get_costs · learnings"; voice_e2e adds them
     only if the server has them);
   - the BFF task titles in README (task ids are not mentioned anywhere), "97 PDFs" (from the RAG brief);
   - `proof/run_proof.py` (DEMO.md's evening-before checklist).
2. **Possible real gap, not a docs issue: lessons and capabilities may not survive a container restart.**
   `deploy/r2sync.py` syncs only `STATE_PARTS = ("macrae", "runs", "jobs")`. `evolve` keeps lessons, tools and (v3)
   the capability registry under `$AGENT_RUNNER_HOME/evolve/`, which is not in that list, and v3's backend brief did
   not own `deploy/`. Unless someone added `"evolve"` (R2 `state/evolve/…`), every sleep or deploy of the container
   resets what Macrae has learned, which would break the dusk → dawn story on the live site. The docs (architecture
   diagram: `state/macrae/ · state/evolve/`) describe the intended state; please check and fix r2sync if needed.
3. `README.md` was rewritten wholesale (it was the old "iocb-chat" README). If another branch edited README.md,
   prefer this version and re-add their lines into the matching section. `agent_runner/retro.py` copies README.md
   into retro inputs; nothing parses it.
4. `tests/test_docs.py` is a new, unique basename. Its browser test uses **Python** Playwright and skips cleanly when
   it's missing; the qa_e2e branch's Playwright (likely Node) is not required. If the integrator edits
   `docs/architecture.html`, run it with a browser available: it catches labels that overflow their boxes.
5. The architecture page must stay free of account names: `test_architecture_has_no_account_names` fails if the
   live URL (which contains the Cloudflare account name) is pasted into it. README and DEMO.md do contain the live
   URL on purpose (the presenter needs it).
