"""The lesson-injection hook in tasks/flows/*.yaml, end to end through the real agent_runner engine:
run 1 hits a problem → distill → run 2's instruction carries the lesson and its scripts → run 2 records the outcome."""
import json
import subprocess
import sys
from pathlib import Path

import yaml

import evolve
from evolve import store

ROOT = Path(__file__).resolve().parents[2]
FLOWS = sorted((ROOT / "tasks" / "flows").glob("*.yaml"))


def test_every_task_flow_injects_lessons_and_tools():
    assert FLOWS
    assert evolve.check_flows(FLOWS) == []


def test_check_flows_catches_a_flow_without_the_hook(tmp_path):
    p = tmp_path / "f.yaml"
    p.write_text(yaml.safe_dump({"name": "x", "vars": {}, "steps": [
        {"id": "a", "instruction": "do it", "path": "{{ vars.dir }}"}, {"id": "b", "run": "echo"}]}))
    problems = evolve.check_flows([p])
    assert len(problems) == 4  # two missing vars, no {{ vars.lessons }}, no tools_dir path


def test_the_hook_renders_into_the_instruction_and_paths():
    from agent_runner import flows
    spec, _ = flows.load_flow(ROOT / "tasks" / "flows" / "small-calc.yaml")
    step = next(s for s in spec["steps"] if s["id"] == "calc")
    ctx = {"vars": flows.AttrDict(dict(spec["vars"], lessons="LESSONS FROM EARLIER RUNS\n- [La1b2c3] DO: x",
                                       tools_dir="/h/evolve/tools/small-calc")),
           "prepare": flows.AttrDict(output=flows.AttrDict(dir="/r/calc", ion="Na+", charge=1, element="Na"))}
    text = flows.render(step["instruction"], ctx)
    assert text.rstrip().endswith("- [La1b2c3] DO: x") and "Use only numbers that exist in references.json." in text
    assert flows.render(step["paths"], ctx) == ["/r/calc", "/h/evolve/tools/small-calc"]
    ctx["vars"] = flows.AttrDict(spec["vars"])  # nothing learned yet: same instruction as before the hook
    assert flows.render(step["instruction"], ctx).rstrip().endswith("exist in references.json.")
    assert [p for p in flows.render(step["paths"], ctx) if p] == ["/r/calc"]


def run_flow(env, *vars_):
    cmd = [sys.executable, "-m", "agent_runner", "flow", "run", str(ROOT / "tasks/flows/small-calc.yaml"),
           "--var", f"python={sys.executable}", "--var", "ion=Na+"]
    for v in vars_:
        cmd += ["--var", v]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=240, cwd=str(ROOT))
    assert r.stdout.strip().endswith("ok"), r.stdout + r.stderr
    return r.stdout.split()[1]  # "run <id>  (<dir>)"


def test_the_next_run_learns_from_the_last(fake_harbor, home):
    env, log = fake_harbor
    run1 = run_flow(env)
    learned = evolve.distill(run1)  # what the server does when a run ends (no Claude key here → rules)
    mod = next(x for x in learned if "pyscf" in x["lesson"])
    assert "/opt/calc/bin/python" in mod["lesson"] and mod["evidence"][0].startswith(f"{run1}/calc/")
    v = evolve.flow_vars("small-calc")  # what the server merges into the next run's vars
    assert mod["id"] in v["lessons"] and v["tools_dir"]
    run2 = run_flow(env, f"lessons={v['lessons']}", f"tools_dir={v['tools_dir']}")

    calls = [json.loads(ln)["argv"] for ln in log.read_text().splitlines()]
    assert len(calls) == 2
    first, second = (c[c.index("-i") + 1] for c in calls)
    assert "LESSONS FROM EARLIER RUNS" not in first
    assert "LESSONS FROM EARLIER RUNS" in second and f"[{mod['id']}]" in second
    assert "run_calc.py" in second and "/app/small-calc/" in second
    paths = [calls[1][i + 1] for i, a in enumerate(calls[1]) if a == "-p"]
    assert paths[1] == v["tools_dir"] and Path(paths[1], "run_calc.py").is_file()

    evolve.distill(run2)
    x = next(x for x in store.load() if x["id"] == mod["id"])
    assert x["used"] == 1 and x["used_ok"] == 1
    rows = evolve.metrics()["by_task"]["small-calc"]
    assert [(r["run_id"], r["lessons_used"]) for r in rows] == [(run1, 0), (run2, len(store.ids_in(v["lessons"])))]
    assert rows[1]["total_usd"] < rows[0]["total_usd"]  # the fake agent skips the failed command with lessons
