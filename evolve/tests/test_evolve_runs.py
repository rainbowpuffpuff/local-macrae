"""Reading runs: state.json, step logs, Harbor trials (ATIF and stream-json), live files, events, costs."""
import json

import pytest

from evolve import costs, runs
from evolve.tests.trace_fixtures import A, B, C, D


def test_small_calc_run_with_a_retry(traces):
    r = runs.load(A)
    assert (r.task_id, r.status, r.title) == ("small-calc", "ok", "Ion–water binding, computed live")
    assert r.inputs == {"ion": "Na+"}
    assert r.wall_s == pytest.approx(1012.4)
    calc = r.step("calc")
    assert [s.key for s in r.steps] == ["prepare", "calc", "collect"]
    assert [(c.attempt, c.rc) for c in calc.checks] == [(1, 1), (2, 0)]
    assert "0.562" in calc.checks[0].text and "cites [4]" in calc.checks[0].text
    assert [(a["n"], a["passed"]) for a in calc.attempts] == [(1, False), (2, True)]
    assert [t.attempt for t in calc.trials] == [1, 2] and all(t.source == "atif" for t in calc.trials)
    t1 = calc.trials[0]
    assert t1.instruction.startswith("Run a small but real quantum-chemistry calculation")
    failed = [c for c in t1.calls if c.failed]
    assert len(failed) == 1 and "ModuleNotFoundError" in failed[0].output and failed[0].command.startswith("cd /app")
    scan = next(c for c in t1.calls if "/opt/calc/bin/python" in c.command)
    assert scan.seconds == pytest.approx(412, abs=1)
    assert t1.cost_usd == 1.07 and t1.tokens["in"] == 41234  # Claude Code's own result line
    assert r.lessons_used == []


def test_injected_lessons_are_read_back_from_the_vars(traces):
    r = runs.load(B)
    assert r.lessons_used == ["La1b2c3", "Ld4e5f6", "L0a9b8c"]
    assert "LESSONS FROM EARLIER RUNS" in r.step("calc").trials[0].instruction


def test_bff_run_timeout_and_unfinished_call(traces):
    r = runs.load(C)
    fit = r.step("fit")
    assert r.hardware == "cpu-8" and r.task_id == "bff-charges"
    t1, t2 = fit.trials
    assert t1.exception.startswith("AgentTimeoutError") and t1.cost_usd is None
    assert t1.calls[-1].output == "" and "--steps 100000" in t1.calls[-1].command
    assert t2.cost_usd == 1.62 and not t2.exception


def test_run_that_never_reached_the_agent(traces):
    r = runs.load(D)
    assert r.status == "failed" and r.step("card").trials == [] and "no Claude login" in r.step("card").error
    assert r.step("collect").status == "skipped"


def test_resolve_by_id_or_folder(traces):
    home, _ = traces
    assert runs.resolve(A) == home / "runs" / A
    assert runs.resolve(str(home / "runs" / A)) == (home / "runs" / A).resolve()
    with pytest.raises(FileNotFoundError):
        runs.resolve("no-such-run")
    with pytest.raises(FileNotFoundError):
        runs.resolve("../etc")


def test_events_come_from_the_backends_builder_with_contiguous_seq(traces):
    r = runs.load(A)
    seqs = [e["seq"] for e in r.events]
    assert seqs == list(range(1, len(seqs) + 1))
    assert r.events[0]["title"].startswith("Started")
    assert any(e["type"] == "error" and e["title"].startswith("Check failed") and e["step"] == "calc"
               for e in r.events)
    assert any(e["type"] == "calc" and e["step"] == "calc" for e in r.events)


def test_the_backends_event_log_wins_when_present(traces):
    home, _ = traces
    p = home / "macrae" / "events" / f"{A}.jsonl"
    p.parent.mkdir(parents=True)
    evs = [{"seq": 1, "t": 1.0, "step": "", "type": "plan", "title": "Decided: cpu-2", "detail": "", "citation": None},
           {"seq": 2, "t": 2.0, "step": "calc", "type": "calc", "title": "Ran scan", "detail": "", "citation": None}]
    p.write_text("".join(json.dumps({"keys": [str(e["seq"])], "event": e}) + "\n" for e in evs) + '{"final": true}\n')
    r = runs.load(A)
    assert r.events == evs
    assert runs.evidence(r, "calc", 2.0) == f"{A}/calc/2"


def test_own_events_when_the_backend_is_missing(traces, monkeypatch):
    monkeypatch.setattr(runs, "_server", lambda: None)
    r = runs.load(C)
    assert [e["seq"] for e in r.events] == list(range(1, len(r.events) + 1))
    assert any(e["type"] == "error" and e["step"] == "fit" for e in r.events)
    assert r.events[-1]["title"] == "Run finished"


def test_live_stream_is_used_when_no_trial_has_a_transcript(home):
    rid = "20261009-100000-small-calc-live"
    d = home / "runs" / rid
    (d / "live").mkdir(parents=True)
    (d / "state.json").write_text(json.dumps({
        "id": rid, "name": "small-calc", "status": "running", "started": 1000.0, "finished": None,
        "jobs_dir": str(home / "jobs" / rid), "vars": {"task_id": "small-calc"}, "order": ["calc"],
        "steps": {"calc": {"key": "calc", "kind": "agent", "status": "running", "attempt": 1, "started": 1001.0}}}))
    lines = [
        {"type": "assistant", "message": {"model": "claude-opus-5-5", "content": [
            {"type": "text", "text": "Installing PySCF."},
            {"type": "tool_use", "id": "toolu_9", "name": "Bash", "input": {"command": "pip install pyscf"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_9",
                                                  "content": "ERROR: No matching distribution", "is_error": True}]}},
    ]
    # the backend may wrap each line; both shapes are read
    (d / "live" / "calc.jsonl").write_text(json.dumps(lines[0]) + "\n" + json.dumps({"line": json.dumps(lines[1])}) + "\n")
    r = runs.load(rid)
    tr = r.step("calc").trials[0]
    assert tr.source == "live" and tr.model == "claude-opus-5-5"
    assert tr.calls[0].command == "pip install pyscf" and tr.calls[0].is_error
    assert not r.done


def test_cost_estimate_and_price_table(traces):
    r = runs.load(C)
    c = costs.estimate(r)
    assert c["llm_usd"] > 1.62  # attempt 2 from Claude Code + attempt 1 (killed) from its tokens
    assert c["compute_usd"] == pytest.approx(costs.compute_usd(sum(t.seconds for t in r.step("fit").trials), "cpu-8"),
                                             rel=1e-3)
    assert c["total_usd"] == pytest.approx(c["llm_usd"] + c["compute_usd"], abs=1e-3)
    assert costs.price("claude-opus-5-5-20260101")[0] == 4.0
    assert costs.price("unknown-model") == costs.PRICES["default"]
    assert costs.hardware_spec("gpu-a10g")["gpu"] == "a10g" and costs.hardware_spec("nonsense")["cores"] == 1
    assert costs.compute_usd(3600, "gpu-h100") > costs.compute_usd(3600, "cpu-8") > costs.compute_usd(3600, "cpu-1")


def test_server_costs_win_when_the_backend_reports_them(traces, monkeypatch):
    server_runs = pytest.importorskip("server.runs")
    monkeypatch.setattr(server_runs, "get_run", lambda rid: {"run_id": rid, "costs": {
        "llm_usd": 1.0, "compute_usd": 0.5, "total_usd": 1.5, "tokens": {}}})
    assert runs.load(A).costs["total_usd"] == 1.5 and runs.load(A).costs["source"] == "server"
