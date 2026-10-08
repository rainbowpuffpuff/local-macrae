"""context(), flow_vars(), hints(), metrics(), export_dataset()."""
import json

import evolve
from evolve import context as _ctx_mod  # noqa: F401  (module import; evolve.context is the function)
from evolve import store
from evolve.tests.trace_fixtures import A, B, C, D


def test_nothing_learned_yet_means_nothing_injected(home):
    assert evolve.context("small-calc") == ""
    assert evolve.flow_vars("small-calc") == {"lessons": "", "tools_dir": ""}


def test_context_lists_the_best_lessons_and_the_scripts(traces):
    evolve.distill(A)
    text = evolve.context("small-calc", k=3)
    lines = [ln for ln in text.splitlines() if ln.startswith("- [L")]
    assert text.startswith("LESSONS FROM EARLIER RUNS OF THIS TASK") and len(lines) == 3
    assert all(store.LESSON_ID_RE.search(ln) for ln in lines)
    assert "REUSABLE SCRIPTS" in text and "run_calc.py: Ion–water binding" in text
    assert "/app/" not in text  # the planner's view doesn't name a sandbox path
    assert evolve.context("bff-charges") == ""  # lessons are per task


def test_flow_vars_hand_the_tools_folder_to_the_sandbox(traces, monkeypatch):
    home, _ = traces
    evolve.distill(A)
    v = evolve.flow_vars("small-calc")
    assert v["tools_dir"] == str(home / "evolve" / "tools" / "small-calc")
    assert "are in /app/small-calc/" in v["lessons"]
    assert store.ids_in(v["lessons"])  # the run's state.json vars will carry the ids → lessons_used
    monkeypatch.setenv("MACRAE_EVOLVE_TOOLS", "0")
    v = evolve.flow_vars("small-calc")
    assert v["tools_dir"] == "" and "/app/small-calc" not in v["lessons"] and "LESSONS" in v["lessons"]


def test_retired_and_global_lessons(traces):
    evolve.distill(A)
    evolve.add_lesson("*", "Write every number you report into result.json from the program's own output.")
    assert "result.json from the program's own output" in evolve.context("bff-charges")
    with store.locked():
        xs = store.load()
        for x in xs:
            if "pyscf" in x["lesson"]:
                x["status"] = "retired"
        store.save(xs)
    assert "pyscf" not in evolve.context("small-calc", k=20)
    assert any("pyscf" in x["lesson"] for x in evolve.lessons("small-calc", include_retired=True))


def test_ranking_prefers_lessons_that_helped():
    now = 1_000_000.0
    base = {"task_id": "t", "kind": "do", "confidence": 0.7, "created": now, "updated": now, "hits": 1,
            "status": "active"}
    good = dict(base, id="L000001", lesson="a", used=3, used_ok=3)
    new = dict(base, id="L000002", lesson="b", used=0, used_ok=0)
    bad = dict(base, id="L000003", lesson="c", used=2, used_ok=0)
    assert store.score(good, now) > store.score(new, now) > store.score(bad, now)
    old = dict(new, id="L000004", updated=now - 60 * 86400)
    assert store.score(new, now) > store.score(old, now)


def test_metrics_shape_for_the_evolution_panel(traces):
    evolve.distill_pending()
    m = evolve.metrics()
    sc = m["by_task"]["small-calc"]
    assert [r["run_id"] for r in sc] == [A, B]  # oldest first: the sparkline's order
    for r in sc:
        assert set(r) >= {"run_id", "ok", "reward", "wall_s", "total_usd", "lessons_used"}
    a, b = sc
    assert a["ok"] and b["ok"] and a["reward"] == 1.0
    assert (a["lessons_used"], b["lessons_used"]) == (0, 3)
    assert b["wall_s"] < a["wall_s"] and b["total_usd"] < a["total_usd"]  # the run with lessons did better
    assert a["distilled"] and a["lessons_learned"] >= 3
    assert m["by_task"]["methods-card"][0]["ok"] is False and m["by_task"]["methods-card"][0]["reward"] == 0.0
    assert m["by_task"]["bff-charges"][0]["hardware"] == "cpu-8"
    lessons = m["lessons"]["small-calc"]
    assert lessons and all(x["links"] and x["links"][0]["run_id"] == A for x in lessons if A in x["runs"])
    assert lessons[0]["links"][0]["step"] in ("calc", "")
    assert {t["name"] for t in m["tools"]["bff-charges"]} == {"run_md.py", "fit_surrogate.py", "run_mcmc.py"}
    json.dumps(m)  # JSON-serializable for the route


def test_metrics_skip_running_runs(traces):
    home, _ = traces
    st = json.loads((home / "runs" / B / "state.json").read_text())
    st["status"], st["finished"] = "running", None
    (home / "runs" / B / "state.json").write_text(json.dumps(st))
    assert [r["run_id"] for r in evolve.metrics()["by_task"]["small-calc"]] == [A]


def test_hints_for_the_planner(traces):
    evolve.distill_pending()
    h = evolve.hints("bff-charges")
    assert h["runs"] == 1 and h["ok_rate"] == 1.0 and h["suggested_hardware"] == "cpu-8"
    assert h["best"]["run_id"] == C and h["suggested_budget_usd"] > h["median_usd"]
    assert any("time limit" in s for s in h["settings"])
    assert h["context"].startswith("LESSONS")
    assert evolve.hints("nope")["runs"] == 0


def test_export_dataset(traces, tmp_path):
    out = evolve.export_dataset(tmp_path / "ds")
    rows = [json.loads(ln) for ln in (out / "all.jsonl").read_text().splitlines()]
    sft = [json.loads(ln) for ln in (out / "sft.jsonl").read_text().splitlines()]
    # A: 2 attempts, B: 1, C: 2 (D never reached the agent)
    assert sorted((r["run_id"], r["attempt"]) for r in rows) == [(A, 1), (A, 2), (B, 1), (C, 1), (C, 2)]
    assert sorted((r["run_id"], r["attempt"]) for r in sft) == [(A, 2), (B, 1), (C, 2)]
    a1 = next(r for r in rows if r["run_id"] == A and r["attempt"] == 1)
    assert a1["reward"] == 0.0 and a1["check_passed"] is False and "cites [4]" in a1["check_output"]
    msgs = a1["messages"]
    assert msgs[0]["role"] == "user" and msgs[0]["content"].startswith("Run a small but real")
    calls = {c["id"] for m in msgs if m["role"] == "assistant" for c in m.get("tool_calls", [])}
    results = [m for m in msgs if m["role"] == "tool"]
    assert results and all(m["tool_call_id"] in calls for m in results)
    assert json.loads(next(m for m in msgs if m.get("tool_calls"))["tool_calls"][0]["function"]["arguments"])
    b1 = next(r for r in rows if r["run_id"] == B)
    assert b1["lessons_used"] == ["La1b2c3", "Ld4e5f6", "L0a9b8c"]
    c1 = next(r for r in rows if r["run_id"] == C and r["attempt"] == 1)
    assert c1["reward"] is None and c1["exception"].startswith("AgentTimeoutError")
    man = json.loads((out / "manifest.json").read_text())
    assert man["all"] == 5 and man["sft"] == 3 and man["by_task"]["small-calc"] == 3


def test_rule_plan_is_the_planners_fallback(traces):
    evolve.distill_pending()
    task = {"id": "small-calc", "flow": "tasks/flows/small-calc.yaml"}
    p = evolve.rule_plan(task, ["cpu-2", "cpu-8", "gpu-a10g"])
    assert set(p) >= {"plan", "hardware", "params", "budget_usd", "why"}
    assert p["plan"][0].startswith("Set up the calculation folder") and len(p["plan"]) == 3
    assert p["hardware"] == "cpu-2" and p["budget_usd"] and p["why"].startswith("Rule-based plan")
    assert "2 of 2 earlier runs passed" in p["why"]
    assert evolve.rule_plan("bff-charges", ["cpu-2", "cpu-8"])["hardware"] == "cpu-8"  # where it passed before
    fresh = evolve.rule_plan("never-run")
    assert fresh["hardware"] == "cpu-2" and fresh["budget_usd"] is None and "no earlier runs" in fresh["why"]
