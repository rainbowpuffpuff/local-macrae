"""Stands in for the `harbor` CLI in tests: records argv/env, writes a job folder shaped like Harbor's.

FAKE_HARBOR_LOG   JSON lines {argv, env, template_dockerfile} appended per call
FAKE_HARBOR_MODE  card | bad-card | calc   what the "agent" writes into the returned folder
"""

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

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

CALC_RESULT = {
    "ion": "Na+", "charge": 1, "software": "PySCF 2.8.0", "method": "B3LYP", "basis_scan": "def2-SVP",
    "basis_final": "def2-TZVP", "r_min_angstrom": 2.23, "e_int_kcal_mol": -23.4, "e_int_uncorrected_kcal_mol": -24.1,
    "bsse_kcal_mol": 0.7, "scan": [{"r_angstrom": 1.7 + 0.1 * i, "e_int_kcal_mol": -20.0 - i} for i in range(6)],
    "e_coulomb_full_charge_kcal_mol": -30.0, "e_coulomb_ecc_kcal_mol": -22.5, "ecc_scaling": 0.75,
    "wall_time_s": 41.2,
}


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
        elif mode == "calc":
            (dest / "result.json").write_text(json.dumps(CALC_RESULT))
            (dest / "run_calc.py").write_text("print('scan')\n")
            (dest / "output.log").write_text("r=2.2 E=-23.4\n")
            (dest / "explanation.md").write_text(
                ("The sodium ion binds one water molecule by about 23 kcal/mol in the gas phase. " * 12)
                + "The group scales ionic charges by 0.75 to include electronic polarization [2].\n")
    (trial / "agent" / "trajectory.json").write_text(json.dumps({"steps": [
        {"source": "user", "message": opt(argv, "-i")},
        {"source": "agent", "message": "", "tool_calls": [{"function_name": "Read",
                                                           "arguments": {"file_path": "/app/paper/context.md"}}]},
        {"source": "agent", "message": "Done: wrote the file."}]}))
    (trial / "result.json").write_text(json.dumps({
        "task_name": job.name, "started_at": t0, "finished_at": now(),
        "agent_info": {"name": opt(argv, "-a"), "model_info": {"name": "fake"}},
        "verifier_result": {"rewards": {"reward": 1.0}}}))
    (job / "result.json").write_text(json.dumps({"started_at": t0, "finished_at": now(), "n_total_trials": 1,
                                                  "stats": {"n_completed_trials": 1}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
