"""v2 planner (one Claude call before the flow; defaults when it can't) and the launcher (run id, live token, plan
and lessons as flow vars, then the real agent_runner engine)."""

import json
import sys
import textwrap
import time
import types

import pytest

from server import costs, launch, planner

pytest.importorskip("yaml")

FLOW = """
name: v2-demo
vars:
  doi: 10.1/x
  n_points: "5"
  environment: modal
  python: python3
  task_id: v2-demo
  task_title: V2 demo
steps:
  - id: prep
    description: Show the chosen settings
    run: python3 -c "print('{{ vars.hardware }} {{ vars.n_points }} {{ vars.budget_usd }}')"
    outputs: text
"""

TASK = {"id": "v2-demo", "title": "V2 demo", "subtitle": "s", "icon": "atom", "prompt": "Model a thing on Modal.",
        "flow": "", "hardware": "cpu-4", "budget_usd": 1.5,
        "inputs": [{"name": "doi", "label": "DOI", "default": "10.1/x"}]}

USAGE = {"input_tokens": 900, "output_tokens": 250, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def fake_call(decision, model="claude-sonnet-5-5"):
    seen = {}

    def call(system, prompt):
        seen.update(system=system, prompt=prompt)
        return decision, USAGE, model
    return call, seen


@pytest.fixture
def flow_file(tmp_path, env):  # env: an empty runs folder, so the daily spend cap sees no spend
    p = tmp_path / "v2-demo.yaml"
    p.write_text(textwrap.dedent(FLOW))
    return p


def test_plan_from_claude_is_cleaned(flow_file):
    call, seen = fake_call({
        "plan": [{"name": "Prepare", "detail": "Pull inputs", "where": "backend"},
                 {"name": "Fit", "detail": "Fit the BFF model", "where": "modal"}],
        "hardware": "gpu-a10g", "budget_usd": 2.5, "why": "The fit is GPU-bound; a lesson says CPU took 40 min.",
        "params": [{"name": "n_points", "value": "9"}, {"name": "doi", "value": "10.9/evil"},
                   {"name": "python", "value": "/bin/sh"}, {"name": "n_points", "value": "1; rm -rf /"}]})
    rec = planner.make_plan(TASK, {"doi": "10.1/x"}, flow_file, "- avoid CPU for the fit (run r1)", call=call)
    assert rec["status"] == "ok" and rec["hardware"] == "gpu-a10g" and rec["budget_usd"] == 2.5
    assert rec["params"] == {"n_points": "9"}  # not the user's input, not plumbing, nothing shell-ish
    assert [s["where"] for s in rec["plan"]] == ["backend", "modal"]
    assert rec["cost_usd"] == round((900 * 2 + 250 * 10) / 1e6, 6)  # Sonnet 5.5 prices
    assert "n_points = 5" in seen["prompt"] and "gpu-a10g" in seen["prompt"]
    assert "avoid CPU for the fit" in seen["prompt"] and "Model a thing on Modal." in seen["prompt"]
    assert "doi = " not in seen["prompt"].split("Tunable parameters")[1].split("Hardware")[0]
    v = planner.flow_vars(rec)
    assert v["hardware"] == "gpu-a10g" and v["n_points"] == "9" and json.loads(v["plan"])[1]["name"] == "Fit"
    assert planner.decision_title(rec) == "Decided: GPU A10G, 2 stages, budget $2.50"
    assert "Used 1 lesson from earlier runs." in planner.decision_detail(rec)


def test_plan_falls_back_to_task_defaults(flow_file, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rec = planner.make_plan(TASK, {}, flow_file)
    assert rec["status"] == "default" and rec["hardware"] == "cpu-4" and rec["budget_usd"] == 1.5
    assert "no ANTHROPIC_API_KEY" in rec["why"] and rec["plan"][0]["where"] == "backend"
    assert planner.decision_title(rec).startswith("Using the task's defaults: 4 CPU cores, 1 stage")

    def boom(system, prompt):
        raise TimeoutError("read timed out")
    rec = planner.make_plan(TASK, {}, flow_file, call=boom)
    assert rec["status"] == "default" and "TimeoutError" in rec["error"]

    def refused(system, prompt):
        raise planner.PlannerRefused("the model declined to plan this run", USAGE, "claude-sonnet-5-5")
    rec = planner.make_plan(TASK, {}, flow_file, call=refused)
    assert rec["status"] == "default" and "declined" in rec["why"] and rec["cost_usd"] > 0  # refusals cost too
    rec = planner.make_plan(TASK, {}, flow_file, call=fake_call({"hardware": "quantum-9", "plan": []})[0])
    assert rec["status"] == "ok" and rec["hardware"] == "cpu-4"  # invalid choice → the default
    monkeypatch.setenv("MACRAE_PLANNER", "off")
    assert "switched off" in planner.make_plan(TASK, {}, flow_file, call=boom)["why"]


def test_call_claude_request_shape(monkeypatch):
    """The real call path with a stub SDK: model from env, structured output, refusal fallback, refusals handled."""
    sent = {}

    class Resp:
        def __init__(self, stop="end_turn"):
            self.stop_reason = stop
            self.model = "claude-sonnet-5-5"
            self.usage = types.SimpleNamespace(to_dict=lambda: dict(USAGE))
            self.content = [types.SimpleNamespace(type="text", text=json.dumps({"hardware": "cpu-8"}))]

    stop = {"v": "end_turn"}

    class Client:
        def __init__(self, **kw):
            sent["client"] = kw
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.create))

        def create(self, **kw):
            sent["req"] = kw
            return Resp(stop["v"])

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Client))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    decision, usage, model = planner.call_claude("sys", "prompt")
    req = sent["req"]
    assert req["model"] == "claude-sonnet-5-5" and decision == {"hardware": "cpu-8"} and usage == USAGE
    assert req["output_config"]["format"]["schema"]["properties"]["hardware"]["enum"] == list(costs.HARDWARE)
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in req and "temperature" not in req
    assert sent["client"]["api_key"] == "sk-test"
    monkeypatch.setenv("MACRAE_PLANNER_MODEL", "claude-haiku-4-5")
    planner.call_claude("sys", "prompt")
    assert "fallbacks" not in sent["req"] and sent["req"]["model"] == "claude-haiku-4-5"
    stop["v"] = "refusal"
    with pytest.raises(planner.PlannerRefused):
        planner.call_claude("sys", "prompt")


@pytest.fixture
def real_runner(env, flow_file, monkeypatch):
    """The real tasks.runner + catalog, reading a tasks.json whose one task uses the script-only flow above."""
    task = dict(TASK, flow=str(flow_file))
    env.tasks_file.write_text(json.dumps({"tasks": [task]}))
    from server import catalog
    catalog._tasks_cache.update(key=None)
    for m in [m for m in sys.modules if m == "tasks" or m.startswith("tasks.")]:
        monkeypatch.delitem(sys.modules, m)
    return task


def _wait_events(client, run_id, timeout=60):
    seen, after, done = [], 0, False
    deadline = time.time() + timeout
    while time.time() < deadline and not done:
        body = client.get(f"/api/runs/{run_id}/events", params={"after": after}).json()
        for e in body["events"]:
            assert e["seq"] == after + 1
            after = e["seq"]
            seen.append(e)
        done = body["done"]
        if not done:
            time.sleep(0.2)
    return seen, done


def test_start_plans_then_runs_the_engine_with_the_plan_as_vars(env, client, real_runner, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    call, seen_call = fake_call({"plan": [{"name": "Prep", "detail": "print settings", "where": "backend"}],
                                 "hardware": "gpu-l4", "budget_usd": 0.8, "why": "Small job.",
                                 "params": [{"name": "n_points", "value": "7"}]})
    monkeypatch.setattr(planner, "call_claude", call)
    evolve = types.ModuleType("evolve")
    evolve.context = lambda task_id, k=8: f"- lesson for {task_id} (run r0)"
    monkeypatch.setitem(sys.modules, "evolve", evolve)
    r = client.post("/api/tasks/v2-demo/start", json={"inputs": {"doi": "10.2/y"}},
                    headers={"X-Macrae-Origin": "https://macrae.example.workers.dev"})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    assert run_id.split("-")[2:4] == ["v2", "demo"]
    run_dir = env.runs / run_id
    assert (run_dir / "live" / "token").read_text() and \
        (run_dir / "live" / "url").read_text() == "https://macrae.example.workers.dev"
    seen, done = _wait_events(client, run_id)
    assert done, [e["title"] for e in seen]
    titles = [(e["type"], e["title"]) for e in seen]
    assert titles[0] == ("status", "Planning the run: stages, hardware and budget")
    assert titles[1] == ("plan", "Decided: GPU L4, 1 stage, budget $0.80")
    assert titles[2][1] == "Started V2 demo"
    dec = seen[1]
    assert dec["plan"]["hardware"] == "gpu-l4" and dec["cost"]["tokens"]["out"] == 250 and dec["elapsed_s"] >= 0
    assert "lesson for v2-demo" in seen_call["prompt"]
    state = json.loads((run_dir / "state.json").read_text())
    assert state["vars"]["hardware"] == "gpu-l4" and state["vars"]["n_points"] == "7"
    assert state["vars"]["lessons"] == "- lesson for v2-demo (run r0)" and state["vars"]["doi"] == "10.2/y"
    assert state["vars"]["budget_usd"] == 0.8 and json.loads(state["vars"]["plan"])[0]["name"] == "Prep"
    out = (run_dir / "outputs" / "prep-a1.txt").read_text().strip()
    assert out == "gpu-l4 7 0.8"
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "ok" and run["task_id"] == "v2-demo" and run["plan"]["hardware"] == "gpu-l4"
    c = run["costs"]
    assert c["by_step"]["plan"]["llm_usd"] == dec["cost"]["usd"] and c["llm_usd"] == dec["cost"]["usd"]
    assert c["compute_usd"] == 0.0 and c["phases"]["plan"] >= 0 and c["hardware"] == "gpu-l4"


def test_start_without_api_key_uses_defaults_and_says_so(env, client, real_runner):
    r = client.post("/api/tools/start_task", json={"task_id": "v2-demo"})
    run_id = r.json()["run_id"]
    assert run_id, r.json()
    seen, done = _wait_events(client, run_id)
    assert done
    dec = next(e for e in seen if e["type"] == "plan")
    assert dec["title"] == "Using the task's defaults: 4 CPU cores, 1 stage, budget $1.50"
    assert "no ANTHROPIC_API_KEY" in dec["detail"]
    assert not (env.runs / run_id / "live" / "url").exists()  # no public URL known: the agent gets no live env
    assert json.loads((env.runs / run_id / "state.json").read_text())["vars"]["hardware"] == "cpu-4"


def test_bad_input_and_engine_failure(env, client, real_runner, monkeypatch):
    assert client.post("/api/tasks/v2-demo/start", json={"inputs": {"doi": "a;b"}}).status_code == 400

    def no_engine(*a, **k):
        raise OSError("fork failed")
    monkeypatch.setattr(launch, "spawn_engine", no_engine)
    run_id = client.post("/api/tasks/v2-demo/start").json()["run_id"]
    seen, done = _wait_events(client, run_id, timeout=20)
    assert done and seen[-1]["type"] == "error" and "fork failed" in seen[-1]["detail"]


def test_planning_run_is_not_called_crashed(env, monkeypatch):
    from server import runs
    d = env.runs / "20261008-000000-x-0001"
    d.mkdir()
    old = time.time() - 200
    import os
    os.utime(d, (old, old))
    planner.save(d, {"status": "planning", "started": time.time() - 100})
    os.utime(d, (old, old))
    assert runs.read_state(d.name)["status"] == "starting"
    planner.save(d, {"status": "planning", "started": time.time() - 1000})
    os.utime(d, (old, old))
    assert runs.read_state(d.name)["status"] == "crashed"
