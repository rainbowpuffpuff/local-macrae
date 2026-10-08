"""Stands in for the `harbor` CLI in tests: records argv/env, writes a job folder shaped like Harbor's.

FAKE_HARBOR_LOG   JSON lines {argv, env, template_dockerfile} appended per call
FAKE_HARBOR_MODE  what the "agent" writes into the returned folder:
                  card | bad-card                methods card (+ its NOTES_TO_SELF.md)
                  calc                           the reference research run (tasks/tests/reference_run.py: real PySCF
                                                 numbers and figure, notebook, manuscript drafted then revised through
                                                 Write/Edit, and that edit stream in agent/trajectory.json)
                  calc-no-math | calc-shell | calc-once   the same with one research rule broken: no LaTeX math, the
                                                 manuscript written from the shell, or written once and never revised
                  calc-legacy                    v1 outputs only (result.json + explanation.md, no manuscript)

For other fakes (evolve's): `write_research_files(folder)` writes the reference run's outputs, and RESEARCH_CALLS
are the (tool, arguments, output) Write/Edit calls that wrote the manuscript and notebook, for the fake's trajectory
(interleave them with the fake's own work so the edit stream shows revisions after results).
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tasks.tests import reference_run as REF  # noqa: E402

GOOD_CARD = """# Methods card: Bayesian force fields

**Paper:** V. Košťál et al., Journal of Chemical Theory and Computation (2026), DOI 10.1021/acs.jctc.5c02051

## System studied
The framework is demonstrated on 18 biologically relevant molecular fragments [1].

## Computational methods
Parameters are learned from ab initio molecular dynamics data within a Bayesian framework [1].
Not stated in the available sources which software was used.

## Experimental methods
- Not stated in the available sources.

## Analysis
Uncertainty and transferability emerge from the probabilistic representation of parameters and data [1].

## Limitations
The available sources are limited to the abstract, so simulation lengths are not stated.

## References
[1] Košťál et al. (2026). Bayesian Learning for Accurate and Robust Biomolecular Force Fields. JCTC. 10.1021/acs.jctc.5c02051
"""

BAD_CARD = """# Methods card

## System studied
The framework is demonstrated on many biologically relevant molecular fragments of proteins.
"""

# the real Na+ result (PySCF 2.14.0, 2026-10-08): passes `checks.py calc` and matches the reference manuscript
CALC_RESULT = json.loads((REF.DATA / "result.json").read_text())

GOOD_NOTES = """# Notes to self: methods card
## What I tried
- Read context.md first, then sources.json to map each [n] to its origin (paper passage vs abstract).
## What was slow
- Nothing over 30 s: the sources were already prepared.
## What to do differently next time
- When fewer than 3 passages come from the paper itself, say so in Limitations before writing the other sections.
"""


def write_research_files(folder: Path, mode: str = "calc") -> None:
    """The reference research run's outputs (result.json, run_calc.py, output.log, figure, manuscript, notebook)."""
    calc = REF.build(Path(folder) / ".ref")
    for p in calc.rglob("*"):
        if p.is_file():
            dest = Path(folder) / p.relative_to(calc)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
    shutil.rmtree(Path(folder) / ".ref")
    ms = Path(folder) / "results" / "manuscript.md"
    if mode == "calc-no-math":
        ms.write_text(re.sub(r"\$\$.*?\$\$", "", re.sub(r"(?<!\$)\$(?!\$)[^$\n]+\$", "x", ms.read_text()), flags=re.S))


def research_calls(mode: str = "calc", instruction: str = "", writes_only: bool = False) -> list:
    """(tool, arguments, output) of the reference run in order; `mode` breaks one rule (see the module doc).
    `writes_only`: just the Write/Edit calls on the manuscript and the notebook (for fakes with their own story)."""
    calls = []
    for st in REF.trajectory(instruction)["steps"]:
        for tc in st.get("tool_calls") or []:
            calls.append((tc["function_name"], tc["arguments"], st["observation"]["results"][0]["content"]))
    if writes_only:
        calls = [c for c in calls if c[0] in ("Write", "Edit")
                 and str(c[1].get("file_path", "")).endswith(("manuscript.md", "NOTES_TO_SELF.md"))]
    if mode == "calc-shell":
        calls = [c for c in calls if not str(c[1].get("file_path", "")).endswith("manuscript.md")]
        calls.append(("Bash", {"command": f"cat > {REF.MS} <<'EOF'\n{REF.FINAL[:200]}…\nEOF"}, ""))
    elif mode == "calc-once":
        calls = [c for c in calls if not (c[0] == "Edit" and str(c[1].get("file_path", "")).endswith("manuscript.md"))]
    return calls


RESEARCH_CALLS = research_calls(writes_only=True)


def opt(argv, *names, many=False):
    out = []
    for i, a in enumerate(argv):
        if a in names and i + 1 < len(argv):
            out.append(argv[i + 1])
    return out if many else (out[-1] if out else None)


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    argv = sys.argv[1:]
    tpl = opt(argv, "--task-template")
    dockerfile = Path(tpl) / "environment" / "Dockerfile" if tpl else None
    with open(os.environ["FAKE_HARBOR_LOG"], "a") as f:
        f.write(json.dumps({
            "argv": argv,
            "env": {k: os.environ.get(k) for k in ("MODAL_PROFILE", "MODAL_TOKEN_ID", "ANTHROPIC_API_KEY")},
            "template_dockerfile": dockerfile.read_text() if dockerfile and dockerfile.is_file() else None,
        }) + "\n")
    jobs = Path(opt(argv, "--jobs-dir", "-o"))
    job = jobs / opt(argv, "--job-name")
    trial = job / f"{job.name}__abc"
    (trial / "agent").mkdir(parents=True)
    t0 = now()
    (job / "config.json").write_text("{}")
    (trial / "config.json").write_text("{}")
    mode = os.environ.get("FAKE_HARBOR_MODE", "card")
    for p in opt(argv, "-p", many=True):
        dest = trial / "artifacts" / "app" / Path(p).name
        shutil.copytree(p, dest)
        if mode == "card":
            (dest / "methods-card.md").write_text(GOOD_CARD)
        elif mode == "bad-card":
            (dest / "methods-card.md").write_text(BAD_CARD)
        if mode in ("card", "bad-card"):
            (dest / "NOTES_TO_SELF.md").write_text(GOOD_NOTES)
        elif mode.startswith("calc") and mode != "calc-legacy":
            write_research_files(dest, mode)
        elif mode == "calc-legacy":
            (dest / "result.json").write_text(json.dumps(CALC_RESULT))
            (dest / "run_calc.py").write_text("print('scan')\n")
            (dest / "output.log").write_text("r=2.2 E=-25.5\n")
            (dest / "explanation.md").write_text(
                ("The sodium ion binds one water molecule by about 25 kcal/mol in the gas phase. " * 12)
                + "The group scales ionic charges by 0.75 to include electronic polarization [2].\n")
    steps = [{"source": "user", "message": opt(argv, "-i")}]
    if mode.startswith("calc") and mode != "calc-legacy":
        steps += [{"source": "agent", "message": "", "tool_calls": [{"tool_call_id": f"toolu_{i}", "function_name": n,
                                                                     "arguments": a}],
                   "observation": {"results": [{"source_call_id": f"toolu_{i}", "content": out}]}}
                  for i, (n, a, out) in enumerate(research_calls(mode))]
    else:
        steps.append({"source": "agent", "message": "", "tool_calls": [
            {"function_name": "Read", "arguments": {"file_path": "/app/paper/context.md"}}]})
    steps.append({"source": "agent", "message": "Done: wrote the file."})
    (trial / "agent" / "trajectory.json").write_text(json.dumps({"steps": steps}))
    (trial / "result.json").write_text(json.dumps({
        "task_name": job.name, "started_at": t0, "finished_at": now(),
        "agent_info": {"name": opt(argv, "-a"), "model_info": {"name": "fake"}},
        "verifier_result": {"rewards": {"reward": 1.0}}}))
    (job / "result.json").write_text(json.dumps({"started_at": t0, "finished_at": now(), "n_total_trials": 1,
                                                  "stats": {"n_completed_trials": 1}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
