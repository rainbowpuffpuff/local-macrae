"""Stands in for `harbor exec` in evolve's end-to-end tests: plays a small-calc agent and writes a Harbor job.

Records {argv} per call to $FAKE_HARBOR_LOG. The "agent" writes run_calc.py, then runs it with the system python
(ModuleNotFoundError: pyscf) before switching to the venv's python, unless its instruction carries lessons from
earlier runs: then it goes straight to the venv. The outputs pass tasks/checks.py calc.
"""

import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tasks" / "tests"))
from fake_harbor import CALC_RESULT, RESEARCH_CALLS, write_research_files  # noqa: E402  (the tasks module's passing
# result.json, and the reference run's manuscript + notebook with the Write/Edit calls that wrote them)

RUN_CALC = '"""Ion–water scan with PySCF (B3LYP/def2-SVP), CP-corrected def2-TZVP at the minimum."""\nprint("scan")\n'


def opt(argv, *names, many=False):
    out = [argv[i + 1] for i, a in enumerate(argv) if a in names and i + 1 < len(argv)]
    return out if many else (out[-1] if out else None)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def main():
    argv = sys.argv[1:]
    with open(os.environ["FAKE_HARBOR_LOG"], "a") as f:
        f.write(json.dumps({"argv": argv}) + "\n")
    instruction = opt(argv, "-i") or ""
    learned = "LESSONS FROM EARLIER RUNS" in instruction
    job = Path(opt(argv, "--jobs-dir")) / opt(argv, "--job-name")
    trial = job / f"{job.name}__t1"
    (trial / "agent").mkdir(parents=True)
    (job / "config.json").write_text("{}")
    (trial / "config.json").write_text("{}")
    paths = opt(argv, "-p", many=True)
    for p in paths:
        shutil.copytree(p, trial / "artifacts" / "app" / Path(p).name)
    calc = trial / "artifacts" / "app" / Path(paths[0]).name
    write_research_files(calc)  # results/manuscript.md, NOTES_TO_SELF.md, figure: the v3 research protocol
    (calc / "result.json").write_text(json.dumps(CALC_RESULT))
    (calc / "run_calc.py").write_text(RUN_CALC)
    (calc / "output.log").write_text("r=2.2 E=-23.4\n")
    (calc / "explanation.md").write_text(
        ("The sodium ion binds one water molecule by about 23 kcal/mol in the gas phase. " * 12)
        + "The group scales ionic charges by 0.75 to include electronic polarization [2].\n")
    t = datetime.now(timezone.utc) - timedelta(seconds=300)
    steps, n = [{"step_id": 1, "timestamp": iso(t), "source": "user", "message": instruction}], 0

    def call(name, args, out, secs, err=False):
        nonlocal t, n
        n += 1
        res = {"source_call_id": f"toolu_{n}", "content": out}
        if err:
            res["is_error"] = True
        steps.append({"step_id": len(steps) + 1, "timestamp": iso(t), "source": "agent", "message": "",
                      "tool_calls": [{"tool_call_id": f"toolu_{n}", "function_name": name, "arguments": args}],
                      "observation": {"results": [res]}})
        t += timedelta(seconds=secs)

    call("Bash", {"command": "python3 -m venv /opt/calc && /opt/calc/bin/pip install -q pyscf numpy",
                  "description": "Install PySCF in a venv"}, "", 40)
    call("Write", {"file_path": "/app/calc/run_calc.py", "content": RUN_CALC}, "File created successfully", 2)
    if not learned:
        call("Bash", {"command": "cd /app/calc && python3 run_calc.py | tee output.log", "description": "Run the scan"},
             "Traceback (most recent call last):\n  File \"run_calc.py\", line 2\nModuleNotFoundError: No module "
             "named 'pyscf'", 2, err=True)
    call("Bash", {"command": "cd /app/calc && /opt/calc/bin/python run_calc.py | tee output.log",
                  "description": "Run the scan with the venv's python"}, "r=2.2 E=-23.4\nwall 41.2 s", 45)
    for name, args, out in RESEARCH_CALLS:  # the notebook and the manuscript, drafted and revised
        call(name, args, out, 3)
    steps.append({"step_id": len(steps) + 1, "timestamp": iso(t), "source": "agent",
                  "message": "Done: result.json, explanation.md, run_calc.py and output.log are in /app/calc."})
    (trial / "agent" / "trajectory.json").write_text(json.dumps({"schema_version": "ATIF-v1.2", "steps": steps}))
    (trial / "agent" / "claude-code.txt").write_text(json.dumps(
        {"type": "result", "total_cost_usd": 0.31 if learned else 0.52,
         "usage": {"input_tokens": 9000, "output_tokens": 3000, "cache_read_input_tokens": 120000}}) + "\n")
    t0 = iso(datetime.now(timezone.utc) - timedelta(seconds=300))
    (trial / "result.json").write_text(json.dumps({
        "task_name": job.name, "started_at": t0, "finished_at": iso(t),
        "agent_info": {"name": "claude-code", "model_info": {"name": "claude-opus-5-5"}},
        "verifier_result": None}))
    (job / "result.json").write_text(json.dumps({"started_at": t0, "finished_at": iso(t), "n_total_trials": 1,
                                                 "stats": {"n_completed_trials": 1}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
