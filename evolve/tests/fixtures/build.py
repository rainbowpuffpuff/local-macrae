#!/usr/bin/env python3
"""Writes the fixture runs under evolve/tests/fixtures/home/ (re-run after changing it; the output is committed).

No finished runs exist on the build machine (the owner's are on their laptop), so these are synthesized in exactly
the shapes the real writers produce:
  agent_runner.flows   runs/<id>/state.json, flow.yaml, macrae.json, logs/<step>.log ("[HH:MM:SS] msg"), outputs/
  Harbor 0.24          jobs/<id>/<step>-a<n>/{config.json,result.json}, <trial>/{config.json,result.json,
                       agent/trajectory.json (ATIF-v1.2), agent/claude-code.txt (stream-json), artifacts/app/...}
Placeholders, rendered by fixtures.install(): @HOME@ @REPO@, "@E+<s>@" (epoch number), @ISO+<s>@ (UTC ISO time),
@T+<s>@ (local HH:MM:SS, as the engine logs it). Offsets are seconds from the run's start.

Runs:
  A 20261008-201851-small-calc-71d4   small-calc Na+, ok on attempt 2: pyscf not importable with system python
                                      (twice), wrong ECC ratio + bad citation rejected by the check, 17 min, ~$2
  B 20261008-214406-small-calc-9c2e   small-calc K+ with 3 lessons injected, ok on attempt 1, 8 min, ~$0.7
  C 20261008-223015-bff-charges-e1f0  BFF acetate charges on cpu-8: gmx missing, MCMC hit the 30 min agent
                                      limit with 100k steps, ok on attempt 2 with 20k steps
  D 20261008-180522-methods-card-ab56 methods-card failed before the agent started: no Claude login (infra)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "home"
PY = "/usr/bin/python3"


def E(s: float) -> str:
    return f"@E+{s:g}@"


def ISO(s: float) -> str:
    return f"@ISO+{s:g}@"


def T(s: float) -> str:
    return f"@T+{s:g}@"


def write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def wjson(p: Path, obj) -> None:
    write(p, json.dumps(obj, indent=1, ensure_ascii=False))


class Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, s: float, msg: str) -> "Log":
        self.lines.append(f"[{T(s)}] {msg}")
        return self

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


# ── a Harbor trial ──────────────────────────────────────────────────────────


class Traj:
    """Builds an ATIF trajectory + the matching claude-code.txt stream lines."""

    def __init__(self, t0: float, instruction: str, model: str = "claude-opus-5-5"):
        self.t0, self.model, self.n = t0, model, 0
        self.steps = [{"step_id": 1, "timestamp": ISO(t0), "source": "user", "message": instruction}]
        self.t = t0 + 4
        self.texts: list[str] = []

    def _id(self) -> str:
        self.n += 1
        return f"toolu_01{int(self.t0):06d}{self.n:04d}"

    def say(self, text: str, dt: float = 6) -> "Traj":
        self.steps.append({"step_id": len(self.steps) + 1, "timestamp": ISO(self.t), "source": "agent",
                           "message": text})
        self.texts.append(text)
        self.t += dt
        return self

    def call(self, name: str, args: dict, output: str, dt: float, text: str = "", error: bool = False) -> "Traj":
        cid = self._id()
        res = {"source_call_id": cid, "content": output}
        if error:
            res["is_error"] = True
        st = {"step_id": len(self.steps) + 1, "timestamp": ISO(self.t), "source": "agent", "message": text,
              "tool_calls": [{"tool_call_id": cid, "function_name": name, "arguments": args}],
              "observation": {"results": [res]},
              "metrics": {"prompt_tokens": 2100 + 40 * self.n, "completion_tokens": 180 + 9 * self.n,
                          "cached_tokens": 18000 + 900 * self.n}}
        self.steps.append(st)
        self.t += dt
        return self

    def unfinished_call(self, name: str, args: dict, text: str = "") -> "Traj":
        """A call the agent was still waiting on when the trial was killed (no observation)."""
        cid = self._id()
        self.steps.append({"step_id": len(self.steps) + 1, "timestamp": ISO(self.t), "source": "agent",
                           "message": text, "tool_calls": [{"tool_call_id": cid, "function_name": name,
                                                            "arguments": args}]})
        return self

    def atif(self, cost: float | None) -> dict:
        calls = [s for s in self.steps if s.get("metrics")]
        fm = {"total_prompt_tokens": sum(s["metrics"]["prompt_tokens"] for s in calls),
              "total_completion_tokens": sum(s["metrics"]["completion_tokens"] for s in calls),
              "total_cached_tokens": sum(s["metrics"]["cached_tokens"] for s in calls),
              "total_steps": len(self.steps)}
        if cost is not None:
            fm["total_cost_usd"] = cost
        return {"schema_version": "ATIF-v1.2", "session_id": "5f0c8a52-6a51-4c39-9d1e-" + f"{self.n:012d}",
                "agent": {"name": "claude-code", "version": "2.1.31", "model_name": self.model},
                "steps": self.steps, "final_metrics": fm}


def trial(job: Path, trial_name: str, tr: Traj, t_end: float, *, cost: float | None, files: dict[str, str],
          exception: dict | None = None, tokens: tuple[int, int, int] = (0, 0, 0)) -> None:
    td = job / trial_name
    wjson(td / "config.json", {"trial_name": trial_name, "task": {"path": str(job / "task")},
                               "agent": {"name": "claude-code", "model_name": tr.model}, "environment": {"type": "modal"}})
    wjson(td / "agent" / "trajectory.json", tr.atif(cost))
    stream = [json.dumps({"type": "system", "subtype": "init", "model": tr.model, "tools": ["Bash", "Read", "Write",
                                                                                            "Edit", "Glob", "Grep"]})]
    if cost is not None:
        stream.append(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                  "duration_ms": int((t_end - tr.t0) * 1000), "num_turns": len(tr.steps),
                                  "result": tr.texts[-1] if tr.texts else "", "total_cost_usd": cost,
                                  "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1],
                                            "cache_read_input_tokens": tokens[2],
                                            "cache_creation_input_tokens": 0}}))
    write(td / "agent" / "claude-code.txt", "\n".join(stream) + "\n")
    for rel, content in files.items():
        write(td / "artifacts" / "app" / rel, content)
    res = {"task_name": job.name, "trial_name": trial_name, "started_at": ISO(tr.t0), "finished_at": ISO(t_end),
           "agent_info": {"name": "claude-code", "version": "2.1.31",
                          "model_info": {"name": tr.model, "provider": "anthropic"}},
           "agent_result": {"n_input_tokens": tokens[0], "n_cache_tokens": tokens[2], "n_output_tokens": tokens[1],
                            "cost_usd": cost},
           "verifier_result": None, "exception_info": exception}
    wjson(td / "result.json", res)
    wjson(job / "config.json", {"job_name": job.name, "n_attempts": 1, "orchestrator": {"type": "local"}})
    wjson(job / "result.json", {"started_at": ISO(tr.t0), "finished_at": ISO(t_end), "n_total_trials": 1,
                                "stats": {"n_completed_trials": 1, "n_errored_trials": 1 if exception else 0}})


def harbor_line(rid: str, key: str, attempt: int, instruction: str, path: str, timeout: int) -> str:
    cmd = ["/usr/local/bin/harbor", "exec", "-a", "claude-code", "--jobs-dir", f"@HOME@/jobs/{rid}", "--job-name",
           f"{key}-a{attempt}", "-k", "1", "-q", "-i", instruction, "-e", "modal", "--task-template",
           f"@HOME@/runs/{rid}/modal-template", "-p", path, "--no-scan", "-f", f"/app/{Path(path).name}",
           "--agent-timeout", str(timeout)]
    shown = [c if len(c) < 200 else c[:200] + "…" for c in cmd]
    return "harbor (main): " + " ".join(shown)


def step_state(key: str, kind: str, status: str, queued: float, started: float, finished: float, attempt: int = 1,
               **kw) -> dict:
    s = {"key": key, "id": key, "kind": kind, "status": status, "item": None, "queued": E(queued),
         "attempt": attempt, "started": E(started), "finished": E(finished), "error": kw.pop("error", ""),
         "reward": kw.pop("reward", None), "account": kw.pop("account", None), "job_dir": kw.pop("job_dir", None),
         "cost_usd": kw.pop("cost_usd", None), "output": kw.pop("output", None)}
    s.update(kw)
    return s


def state(rid: str, name: str, flow: str, status: str, finished: float, vars_: dict, steps: dict,
          levels: list) -> dict:
    return {"id": rid, "name": name, "file": f"@REPO@/{flow}", "status": status, "started": E(0),
            "finished": E(finished), "pid": 4242, "workdir": "@REPO@", "jobs_dir": f"@HOME@/jobs/{rid}",
            "vars": vars_, "levels": levels, "steps": steps, "order": list(steps)}


# ── small-calc ──────────────────────────────────────────────────────────────

CALC_INSTR = ("Run a small but real quantum-chemistry calculation for Pavel Jungwirth's group (IOCB Prague), who model "
              "ions in water and correct force fields for missing electronic polarization by scaling ionic charges "
              "by 0.75 (electronic continuum correction, ECC). The ion is {ion} (charge +1, element {el}). Work in "
              "/app/calc; task.json has the parameters, context.md and references.json list the group papers you "
              "may cite as [n].\n1. Install PySCF in a venv: python3 -m venv /opt/calc && /opt/calc/bin/pip install "
              "-q pyscf numpy (if that fails, use xtb or tblite instead and say so in result.json \"software\").\n"
              "2. Write calc/run_calc.py and run it with /opt/calc/bin/python, saving everything it prints to "
              "calc/output.log. …\n3. Write calc/result.json with numbers …\n4. Write calc/explanation.md …\n")

RUN_CALC = '''"""Ion–water binding: B3LYP/def2-SVP scan of the ion–O distance, counterpoise-corrected def2-TZVP at the
minimum, and the SPC/E point-charge Coulomb energy with the full and the ECC-scaled (x0.75) ion charge."""
import json, sys, time
import numpy as np
from pyscf import gto, dft
from pyscf.scf import addons

HARTREE_KCAL = 627.509
ION, CHARGE = sys.argv[1] if len(sys.argv) > 1 else "{ion}", 1


def water(r_ion):
    th = np.deg2rad(104.52 / 2)
    return [["O", (0, 0, 0)], ["H", (0.9572 * np.sin(th), 0, 0.9572 * np.cos(th))],
            ["H", (-0.9572 * np.sin(th), 0, 0.9572 * np.cos(th))], ["{el}", (0, 0, -r_ion)]]


def energy(atoms, basis, charge, ghost=()):
    mol = gto.M(atom=atoms, basis=basis, charge=charge, verbose=0)
    mf = dft.RKS(mol, xc="b3lyp")
    return mf.kernel()


t0 = time.time()
scan = []
for r in np.arange(1.7, 3.31, 0.1):
    e = (energy(water(r), "def2-svp", CHARGE) - energy(water(r)[3:], "def2-svp", CHARGE)
         - energy(water(r)[:3], "def2-svp", 0)) * HARTREE_KCAL
    scan.append({{"r_angstrom": round(float(r), 2), "e_int_kcal_mol": round(float(e), 3)}})
    print(f"r={{r:.2f}} E={{e:.2f}}", flush=True)
'''

EXPLANATION = ("We computed how strongly a {name} ion binds a single water molecule with density functional theory "
               "(B3LYP) ... The group scales ionic charges by 0.75 to account for electronic polarization of the "
               "solvent [1], which is supported by neutron scattering and simulations of aqueous salt solutions "
               "[2, 3]. " * 6)


def small_calc_run(rid: str, ion: str, el: str, name: str, *, lessons: str, attempts: list[dict], wall: float) -> None:
    run = OUT / "runs" / rid
    jobs = OUT / "jobs" / rid
    calc_dir = f"@HOME@/runs/{rid}/small-calc/calc"
    prep_out = {"dir": calc_dir, "ion": ion, "charge": 1, "element": el, "name": name, "n_references": 3,
                "references_file": f"@HOME@/runs/{rid}/small-calc/references.json",
                "checker": "@REPO@/tasks/checks.py",
                "citations": [{"key": "[1]", "title": "Electronic continuum correction for ions", "doi":
                               "10.1021/acs.jpcb.0c09009", "year": 2020}]}
    write(run / "outputs" / "prepare-a1.txt", json.dumps(prep_out) + "\n")
    write(run / "logs" / "prepare.log", Log().add(0.1, "queued (run)")
          .add(0.2, f'$ "{PY}" -m tasks.prepare_calc  (cwd @REPO@)')
          .add(2.9, f"stderr: references for {ion}: 3\nsearch: 'electronic continuum correction ion charge "
                    f"scaling water' → 4 passages\nsearch: '{name} ion hydration simulation' → 2 passages")
          .add(3.0, "attempt 1: ok reward=None passed=True").text())
    lg = Log().add(3.3, "queued (agent)")
    steps_calc = []
    for a in attempts:
        n, s0, s1 = a["n"], a["start"], a["end"]
        instr = CALC_INSTR.format(ion=ion, el=el) + (("\n" + lessons) if lessons else "") + a.get("feedback", "")
        lg.add(s0, harbor_line(rid, "calc", n, instr, calc_dir, 2400))
        tr = Traj(s0 + 25, instr)
        a["build"](tr)
        trial_name = f"calc-a{n}__{a['tid']}"
        app_dir = f"@HOME@/jobs/{rid}/calc-a{n}/{trial_name}/artifacts/app/calc"
        trial(jobs / f"calc-a{n}", trial_name, tr, s1 - 4, cost=a["cost"], files=a["files"],
              tokens=a["tokens"])
        lg.add(s1, f'check $ python3 "@REPO@/tasks/checks.py" calc . --references '
                   f'"@HOME@/runs/{rid}/small-calc/references.json" --ion "{ion}"  (in {app_dir})')
        lg.add(s1 + 1, f"check exit {a['check_rc']}: {a['check_text']}")
        passed = a["check_rc"] == 0
        lg.add(s1 + 1, f"attempt {n}: ok reward={1.0 if passed else 0.0} passed={passed}")
        steps_calc.append((n, s0, s1))
    write(run / "logs" / "calc.log", lg.text())
    last_n, last_s0, last_s1 = steps_calc[-1]
    write(run / "logs" / "collect.log", Log().add(last_s1 + 2, "queued (run)")
          .add(last_s1 + 2, f'$ "{PY}" -m tasks.collect --step calc --files result.json explanation.md run_calc.py '
                            f'output.log  (cwd @REPO@)')
          .add(last_s1 + 3, "attempt 1: ok reward=None passed=True").text())
    write(run / "outputs" / "collect-a1.txt", json.dumps({"result_dir": f"@HOME@/runs/{rid}/result",
                                                          "summary": f"{ion}–water: E_int −23.6 kcal/mol at 2.2 Å"}))
    vars_ = {"ion": ion, "environment": "modal", "python": PY, "task_id": "small-calc",
             "task_title": "Ion–water binding", "lessons": lessons, "tools_dir": ""}
    steps = {
        "prepare": step_state("prepare", "run", "ok", 0.1, 0.1, 3.0, output=json.dumps(prep_out)[:400] + "…"),
        "calc": step_state("calc", "agent", "ok", 3.3, last_s0, last_s1 + 1, attempt=last_n, reward=1.0,
                           account="main", job_dir=f"@HOME@/jobs/{rid}/calc-a{last_n}", environment="modal",
                           cost_usd=attempts[-1]["cost"], output="Done: result.json, explanation.md, run_calc.py, "
                                                                 "output.log are in /app/calc."),
        "collect": step_state("collect", "run", "ok", last_s1 + 2, last_s1 + 2, last_s1 + 3,
                              output=f'{{"summary": "{ion}–water: E_int −23.6 kcal/mol at 2.2 Å"}}'),
    }
    wjson(run / "state.json", state(rid, "small-calc", "tasks/flows/small-calc.yaml", "ok", wall, vars_, steps,
                                    [["prepare"], ["calc"], ["collect"]]))
    shutil.copy(HERE.parents[2] / "tasks" / "flows" / "small-calc.yaml", run / "flow.yaml")
    wjson(run / "macrae.json", {"run_id": rid, "task_id": "small-calc", "title": "Ion–water binding, computed live",
                                "inputs": {"ion": ion}, "flow": "@REPO@/tasks/flows/small-calc.yaml",
                                "started": E(0)})


def calc_files(ion: str, el: str, name: str, ratio: float, cite_bad: bool) -> dict[str, str]:
    full = -41.32
    res = {"ion": ion, "charge": 1, "software": "PySCF 2.8.0", "method": "B3LYP", "basis_scan": "def2-SVP",
           "basis_final": "def2-TZVP", "r_min_angstrom": 2.2, "e_int_kcal_mol": -23.61,
           "e_int_uncorrected_kcal_mol": -24.38, "bsse_kcal_mol": 0.77,
           "scan": [{"r_angstrom": round(1.7 + 0.1 * i, 2), "e_int_kcal_mol": round(-18.9 - 1.4 * i + 0.21 * i * i, 2)}
                    for i in range(17)],
           "e_coulomb_full_charge_kcal_mol": full, "e_coulomb_ecc_kcal_mol": round(full * ratio, 2),
           "ecc_scaling": 0.75, "wall_time_s": 405.2}
    expl = EXPLANATION.format(name=name) + (" A recent study confirms this [4]." if cite_bad else "")
    return {"calc/result.json": json.dumps(res, indent=1), "calc/run_calc.py": RUN_CALC.format(ion=ion, el=el),
            "calc/output.log": "r=1.70 E=-18.92\nr=2.20 E=-24.38\nr_min=2.20 E_int(CP,def2-TZVP)=-23.61\n",
            "calc/explanation.md": expl, "calc/task.json": json.dumps({"ion": ion, "element": el}),
            "calc/references.json": "[]", "calc/context.md": "# Group papers to cite\n"}


PIP = "python3 -m venv /opt/calc && /opt/calc/bin/pip install -q pyscf numpy"
NOMOD = ("Traceback (most recent call last):\n  File \"/app/calc/run_calc.py\", line 5, in <module>\n"
         "    from pyscf import gto, dft\nModuleNotFoundError: No module named 'pyscf'")


def run_a() -> None:
    rid = "20261008-201851-small-calc-71d4"

    def a1(tr: Traj) -> None:
        (tr.say("I'll read the task parameters and the references first.")
         .call("Read", {"file_path": "/app/calc/task.json"}, '{"ion": "Na+", "charge": 1, "ecc_scaling": 0.75}', 3)
         .call("Read", {"file_path": "/app/calc/context.md"}, "# Group papers to cite in calc/explanation.md\n[1] …", 4)
         .call("Bash", {"command": PIP, "description": "Install PySCF in a venv"}, "", 58)
         .call("Write", {"file_path": "/app/calc/run_calc.py", "content": "…"}, "File created successfully", 3)
         .call("Bash", {"command": "cd /app/calc && python3 run_calc.py 2>&1 | tee output.log",
                        "description": "Run the ion–water scan"}, NOMOD, 2, error=True)
         .say("The system python doesn't see the venv; I'll use /opt/calc/bin/python.")
         .call("Bash", {"command": "cd /app/calc && /opt/calc/bin/python run_calc.py 2>&1 | tee output.log",
                        "description": "Run the ion–water scan with the venv's python"},
               "r=1.70 E=-18.92\nr=1.80 E=-21.40\n…\nr=2.20 E=-24.38\n…\nr_min=2.20 Å E_int(CP, def2-TZVP)=-23.61 "
               "kcal/mol\nE_coul full=-41.32 ECC=-23.24\nwall 405.2 s", 412)
         .call("Write", {"file_path": "/app/calc/explanation.md", "content": "…"}, "File created successfully", 5)
         .call("Bash", {"command": "cat /app/calc/result.json | head -20", "description": "Check result.json"},
               '{"ion": "Na+", "r_min_angstrom": 2.2, …}', 2)
         .say("Done: result.json, explanation.md, run_calc.py and output.log are in /app/calc. Na+ binds water by "
              "−23.6 kcal/mol at 2.20 Å."))

    def a2(tr: Traj) -> None:
        (tr.say("The previous attempt scaled the water charges too and cited [4]. I'll redo it, scaling only the ion.")
         .call("Bash", {"command": PIP, "description": "Install PySCF in a venv"}, "", 61)
         .call("Write", {"file_path": "/app/calc/run_calc.py", "content": "…"}, "File created successfully", 3)
         .call("Bash", {"command": "cd /app/calc && python3 run_calc.py 2>&1 | tee output.log",
                        "description": "Run the ion–water scan"}, NOMOD, 2, error=True)
         .call("Bash", {"command": "cd /app/calc && /opt/calc/bin/python run_calc.py 2>&1 | tee output.log",
                        "description": "Run the ion–water scan with the venv's python"},
               "r=1.70 E=-18.92\n…\nr_min=2.20 Å E_int(CP, def2-TZVP)=-23.61 kcal/mol\nE_coul full=-41.32 "
               "ECC=-30.99\nwall 398.7 s", 405)
         .call("Write", {"file_path": "/app/calc/explanation.md", "content": "…"}, "File created successfully", 5)
         .say("Done: fixed the ECC comparison (only the ion charge scaled, ratio 0.75) and cited only [1]–[3]."))

    check1 = ("FAILED:\n- e_coulomb_ecc_kcal_mol / e_coulomb_full_charge_kcal_mol = 0.562; with only the ion charge "
              "scaled by 0.75 it should be 0.75\n- explanation.md cites [4], which don't exist; valid numbers are "
              "[1, 2, 3]")
    fb = ("\n\n---\nA previous attempt at this task did not pass (reward=0.0).\nError: until not met: (reward or 0) "
          ">= 1\nVerifier output (end):\ncheck `python3 …/checks.py calc .` exit 1\n" + check1 +
          "\nTake this into account and fix what failed.")
    small_calc_run(rid, "Na+", "Na", "sodium", lessons="", wall=1012.4, attempts=[
        {"n": 1, "start": 3.4, "end": 498.0, "tid": "Xk2pQ", "build": a1, "cost": 1.07,
         "tokens": (41234, 18234, 812345), "files": calc_files("Na+", "Na", "sodium", 0.5625, True),
         "check_rc": 1, "check_text": check1},
        {"n": 2, "start": 500.0, "end": 1005.0, "tid": "Ru7Lm", "build": a2, "cost": 0.96, "feedback": fb,
         "tokens": (38410, 16920, 790112), "files": calc_files("Na+", "Na", "sodium", 0.75, False),
         "check_rc": 0, "check_text": "ok"},
    ])


LESSONS_B = ("LESSONS FROM EARLIER RUNS OF THIS TASK (learned from their traces; follow them unless they contradict "
             "the instructions above):\n"
             "- [La1b2c3] SETTING: Python module `pyscf` is not importable in the sandbox with `cd /app/calc && "
             "python3 run_calc.py 2>&1 | tee output.log`; `cd /app/calc && /opt/calc/bin/python run_calc.py 2>&1 | tee "
             "output.log` worked (took 412 s). Do that first. (seen in 2 runs)\n"
             "- [Ld4e5f6] AVOID: Check before finishing: e_coulomb_ecc_kcal_mol / e_coulomb_full_charge_kcal_mol = "
             "0.562; with only the ion charge scaled by 0.75 it should be 0.75 (the `calc` check rejected attempt 1 "
             "for this; attempt 2 passed after fixing it).\n"
             "- [L0a9b8c] AVOID: Check before finishing: explanation.md cites [4], which don't exist; valid numbers "
             "are [1, 2, 3] (the `calc` check rejected attempt 1 for this; attempt 2 passed after fixing it).")


def run_b() -> None:
    rid = "20261008-214406-small-calc-9c2e"

    def a1(tr: Traj) -> None:
        (tr.say("Following the lessons: venv python for the scan, only the ion charge scaled, cite [1]–[3].")
         .call("Read", {"file_path": "/app/calc/task.json"}, '{"ion": "K+", "charge": 1}', 3)
         .call("Bash", {"command": PIP, "description": "Install PySCF in a venv"}, "", 57)
         .call("Write", {"file_path": "/app/calc/run_calc.py", "content": "…"}, "File created successfully", 3)
         .call("Bash", {"command": "cd /app/calc && /opt/calc/bin/python run_calc.py 2>&1 | tee output.log",
                        "description": "Run the ion–water scan with the venv's python"},
               "r=1.70 E=-9.81\n…\nr_min=2.70 Å E_int(CP, def2-TZVP)=-17.42 kcal/mol\nE_coul full=-33.10 "
               "ECC=-24.83\nwall 366.0 s", 372)
         .call("Write", {"file_path": "/app/calc/explanation.md", "content": "…"}, "File created successfully", 5)
         .say("Done: K+ binds water by −17.4 kcal/mol at 2.70 Å; result.json, explanation.md, run_calc.py, "
              "output.log are in /app/calc."))

    small_calc_run(rid, "K+", "K", "potassium", lessons=LESSONS_B, wall=478.9, attempts=[
        {"n": 1, "start": 3.4, "end": 470.0, "tid": "Pq3Vz", "build": a1, "cost": 0.66,
         "tokens": (30111, 12001, 601200), "files": calc_files("K+", "K", "potassium", 0.75, False),
         "check_rc": 0, "check_text": "ok"},
    ])


# ── BFF ─────────────────────────────────────────────────────────────────────

BFF_INSTR = ("Learn Bayesian partial charges for acetate with BayesicForceFields (bfflearn) on this Modal box, as in "
             "Košťál et al., JCTC 2026: classical MD of the solute in water for Latin-hypercube charge sets (GROMACS), "
             "a local Gaussian-process surrogate of the RDFs and hydrogen-bond counts, then emcee MCMC against the "
             "AIMD reference in /app/bff/reference/. Net charge −0.8 (ECC). Write /app/bff/result.json (posterior "
             "means and 95% intervals per atom type, tau, acceptance), posterior.png and explanation.md citing "
             "the group's papers as [n].")
MCMC = ('"""emcee StretchMove over the GP surrogate: walkers = 5 x dim, burn-in 2 tau_max, thin 0.5 tau_min."""\n'
        "import argparse, json\nimport emcee, numpy as np\nfrom bff import surrogate, likelihood\n\n"
        "ap = argparse.ArgumentParser()\nap.add_argument('--walkers', type=int, default=40)\n"
        "ap.add_argument('--steps', type=int, default=20000)\n")
RUN_MD = ('"""Classical MD (GROMACS, 128 TIP4P/2005 waters, 300 K) for Latin-hypercube charge sets of acetate."""\n'
          "import argparse, subprocess\nfrom bff.sampling import latin_hypercube\n")
FIT_GP = '"""Fit the local GP surrogate (one process per RDF bin), freeze hyperparameters after LOO."""\nimport bff\n'


def run_c() -> None:
    rid = "20261008-223015-bff-charges-e1f0"
    run = OUT / "runs" / rid
    jobs = OUT / "jobs" / rid
    bff_dir = f"@HOME@/runs/{rid}/bff-charges/bff"
    prep = {"dir": bff_dir, "molecule": "acetate", "n_charges": 4, "net_charge": -0.8,
            "checker": "@REPO@/tasks/checks.py"}
    write(run / "outputs" / "prepare-a1.txt", json.dumps(prep) + "\n")
    write(run / "logs" / "prepare.log", Log().add(0.1, "queued (run)")
          .add(0.2, f'$ "{PY}" -m tasks.prepare_bff  (cwd @REPO@)')
          .add(6.0, "stderr: search: 'Bayesian learning partial charges acetate' → 6 passages\n"
                    "openalex: abstract for 10.1021/acs.jctc.5c02051: 182 words")
          .add(6.1, "attempt 1: ok reward=None passed=True").text())
    gmx_missing = "Exit code 127\n/bin/bash: line 1: gmx: command not found"
    conda = "micromamba install -y -n base -c conda-forge gromacs=2024.4"

    def common(tr: Traj, md_s: float, gp_s: float) -> Traj:
        return (tr.call("Read", {"file_path": "/app/bff/task.json"},
                        '{"molecule": "acetate", "atom_types": ["C1", "C2", "O", "H"], "net_charge": -0.8}', 3)
                .call("Bash", {"command": "pip install -q bfflearn emcee", "description": "Install BFF and emcee"},
                      "Successfully installed bfflearn-0.4.2 emcee-3.1.6", 47)
                .call("Bash", {"command": "gmx --version", "description": "Check GROMACS"}, gmx_missing, 1,
                      error=True)
                .call("Bash", {"command": conda, "description": "Install GROMACS from conda-forge"},
                      "Transaction finished", 94)
                .call("Bash", {"command": "gmx --version | head -3", "description": "Check GROMACS"},
                      "GROMACS - gmx, 2024.4", 1)
                .call("Write", {"file_path": "/app/bff/run_md.py", "content": "…"}, "File created successfully", 3)
                .call("Bash", {"command": "cd /app/bff && python run_md.py --n-samples 64 --ns 0.1",
                               "description": "Run 64 short MD trajectories (LHS charge sets)"},
                      "64/64 trajectories done; RDF and H-bond observables in md/observables.npz", md_s)
                .call("Write", {"file_path": "/app/bff/fit_surrogate.py", "content": "…"}, "File created", 2)
                .call("Bash", {"command": "cd /app/bff && python fit_surrogate.py",
                               "description": "Fit the GP surrogate"},
                      "LOO R^2: rdf 0.97, hb 0.94; hyperparameters frozen", gp_s)
                .call("Write", {"file_path": "/app/bff/run_mcmc.py", "content": "…"}, "File created", 2))

    t1 = Traj(30, BFF_INSTR)
    common(t1.say("I'll set up BFF, GROMACS, the MD samples, the surrogate, then MCMC."), 618, 139)
    t1.unfinished_call("Bash", {"command": "cd /app/bff && python run_mcmc.py --walkers 40 --steps 100000",
                                "description": "Sample the posterior with emcee (100k steps)"})
    fb = ("\n\n---\nA previous attempt at this task did not pass (reward=None).\nError: AgentTimeoutError: Agent "
          "execution timed out after 1800.0 seconds\nTake this into account and fix what failed.")
    t2 = Traj(1870, BFF_INSTR + fb)
    common(t2.say("The last attempt ran out of time in MCMC. I'll cap it at 20k steps and check tau."), 602, 131)
    (t2.call("Bash", {"command": "cd /app/bff && python run_mcmc.py --walkers 40 --steps 20000 --check-tau",
                      "description": "Sample the posterior with emcee (20k steps, check autocorrelation)"},
             "tau_max=212 burn-in=424 thin=60 acceptance=0.31\nposterior means: C1 +0.58 C2 -0.21 O -0.61 H +0.01",
             412)
     .call("Bash", {"command": "cd /app/bff && python plot_posterior.py && ls", "description": "Plot the posterior"},
           "posterior.png result.json explanation.md", 18)
     .say("Done: posterior means O −0.61e (95% −0.66…−0.55), tau 212 < steps/50; result.json, posterior.png and "
          "explanation.md are in /app/bff."))
    files = {"bff/run_md.py": RUN_MD, "bff/fit_surrogate.py": FIT_GP, "bff/run_mcmc.py": MCMC,
             "bff/result.json": json.dumps({"means": {"O": -0.61}, "tau_max": 212, "acceptance": 0.31})}
    trial(jobs / "fit-a1", "fit-a1__Ab12C", t1, 1835, cost=None, files={},
          exception={"exception_type": "AgentTimeoutError",
                     "exception_message": "Agent execution timed out after 1800.0 seconds",
                     "exception_traceback": "Traceback …\nharbor.trial.errors.AgentTimeoutError"},
          tokens=(52000, 21000, 1302000))
    trial(jobs / "fit-a2", "fit-a2__Cd34E", t2, 3330, cost=1.62, files=files, tokens=(49800, 20500, 1250300))
    lg = (Log().add(6.3, "queued (agent)")
          .add(6.4, harbor_line(rid, "fit", 1, BFF_INSTR, bff_dir, 1800))
          .add(1838, "attempt 1: failed reward=None passed=False")
          .add(1840, harbor_line(rid, "fit", 2, BFF_INSTR + fb, bff_dir, 1800))
          .add(3334, 'check $ python3 "@REPO@/tasks/checks.py" bff .  (in …/artifacts/app/bff)')
          .add(3336, "check exit 0: ok")
          .add(3336, "attempt 2: ok reward=1.0 passed=True"))
    write(run / "logs" / "fit.log", lg.text())
    write(run / "logs" / "collect.log", Log().add(3337, "queued (run)")
          .add(3337, f'$ "{PY}" -m tasks.collect --step fit --files result.json posterior.png explanation.md  '
                     f'(cwd @REPO@)').add(3339, "attempt 1: ok reward=None passed=True").text())
    steps = {
        "prepare": step_state("prepare", "run", "ok", 0.1, 0.1, 6.1, output=json.dumps(prep)),
        "fit": step_state("fit", "agent", "ok", 6.3, 1840, 3336, attempt=2, reward=1.0, account="main",
                          job_dir=f"@HOME@/jobs/{rid}/fit-a2", environment="modal", cost_usd=1.62,
                          output="Done: posterior means O −0.61e …"),
        "collect": step_state("collect", "run", "ok", 3337, 3337, 3339, output='{"summary": "acetate O −0.61e"}'),
    }
    vars_ = {"molecule": "acetate", "environment": "modal", "python": PY, "task_id": "bff-charges",
             "task_title": "Bayesian charges (BFF)", "hardware": "cpu-8", "lessons": "", "tools_dir": ""}
    wjson(run / "state.json", state(rid, "bff-charges", "tasks/flows/bff-charges.yaml", "ok", 3340, vars_, steps,
                                    [["prepare"], ["fit"], ["collect"]]))
    write(run / "flow.yaml", "name: bff-charges\nsteps:\n- id: prepare\n  description: Pull the BFF paper's passages "
                             "and set up the acetate system\n  run: python3 -m tasks.prepare_bff\n- id: fit\n"
                             "  description: An agent runs MD, the surrogate and MCMC with BFF on Modal\n"
                             "  instruction: …\n- id: collect\n  run: python3 -m tasks.collect\n")
    wjson(run / "macrae.json", {"run_id": rid, "task_id": "bff-charges", "title": "Bayesian charges for acetate (BFF)",
                                "inputs": {"molecule": "acetate"}, "started": E(0)})


def run_d() -> None:
    rid = "20261008-180522-methods-card-ab56"
    run = OUT / "runs" / rid
    err = ("RuntimeError: no Claude login: `agent-runner accounts set NAME` (a token from `claude setup-token`) or "
           "ANTHROPIC_API_KEY")
    write(run / "logs" / "sources.log", Log().add(0.1, "queued (run)")
          .add(0.2, f'$ "{PY}" -m tasks.prepare_methods  (cwd @REPO@)')
          .add(3.5, "stderr: search: 'Bayesian Learning for Accurate and Robust Biomolecular Force Fields methods' "
                    "→ 0 passages\nopenalex: abstract for 10.1021/acs.jctc.5c02051: 182 words")
          .add(3.6, "attempt 1: ok reward=None passed=True").text())
    write(run / "logs" / "card.log", Log().add(3.7, "queued (agent)").text())
    steps = {
        "sources": step_state("sources", "run", "ok", 0.1, 0.1, 3.6, output='{"dir": "…/paper"}'),
        "card": step_state("card", "agent", "failed", 3.7, 3.7, 3.8, error=err),
        "collect": {"key": "collect", "id": "collect", "kind": "run", "status": "skipped", "note": "upstream: card"},
    }
    vars_ = {"doi": "10.1021/acs.jctc.5c02051", "environment": "modal", "python": PY, "task_id": "methods-card",
             "task_title": "Methods card"}
    wjson(run / "state.json", state(rid, "methods-card", "tasks/flows/methods-card.yaml", "failed", 4.0, vars_,
                                    steps, [["sources"], ["card"], ["collect"]]))
    shutil.copy(HERE.parents[2] / "tasks" / "flows" / "methods-card.yaml", run / "flow.yaml")


RUNS = {  # run id → start, seconds before "now" at install time (keeps their order and gaps)
    "20261008-180522-methods-card-ab56": 5 * 3600 + 20 * 60,
    "20261008-201851-small-calc-71d4": 3 * 3600 + 30 * 60,
    "20261008-214406-small-calc-9c2e": 2 * 3600 + 5 * 60,
    "20261008-223015-bff-charges-e1f0": 1 * 3600 + 15 * 60,
}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    run_a()
    run_b()
    run_c()
    run_d()
    wjson(HERE / "runs.json", RUNS)
    print(f"wrote {len(RUNS)} runs under {OUT}")


if __name__ == "__main__":
    main()
