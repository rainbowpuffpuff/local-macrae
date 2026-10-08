import json
import sys
from pathlib import Path

import pytest

from agent_runner import cli, flows
from tasks import runner


def test_tasks_json_and_flows_are_consistent():
    assert runner.check_all() == []
    ids = [t["id"] for t in runner.list_tasks()]
    assert ids == ["methods-card", "small-calc"]
    for t in runner.list_tasks():
        assert {"id", "title", "subtitle", "icon", "prompt", "flow", "inputs"} <= set(t)
        for i in t["inputs"]:
            assert {"name", "label", "default"} <= set(i)


def test_tasks_file_env_and_bare_list(tmp_path, monkeypatch):
    f = tmp_path / "t.json"
    f.write_text(json.dumps([{"id": "x", "flow": "tasks/flows/small-calc.yaml"}]))
    monkeypatch.setenv("MACRAE_TASKS_FILE", str(f))
    assert [t["id"] for t in runner.list_tasks()] == ["x"]
    monkeypatch.setenv("MACRAE_TASKS_FILE", str(tmp_path / "missing.json"))
    assert runner.list_tasks() == []


def test_unknown_task():
    with pytest.raises(runner.TaskNotFound) as e:
        runner.get_task("nope")
    assert isinstance(e.value, KeyError) and "methods-card" in str(e.value)


@pytest.mark.parametrize("inputs,expected", [
    (None, {"doi": "10.1021/acs.jctc.5c02051"}),
    ({}, {"doi": "10.1021/acs.jctc.5c02051"}),
    ({"doi": "  "}, {"doi": "10.1021/acs.jctc.5c02051"}),
    ({"doi": " 10.1021/jacs.6b11760 "}, {"doi": "10.1021/jacs.6b11760"}),
])
def test_inputs_defaults_and_trimming(inputs, expected):
    assert runner.resolve_inputs(runner.get_task("methods-card"), inputs) == expected


@pytest.mark.parametrize("inputs", [
    {"doi": "10.1021/x; rm -rf /"}, {"doi": "$(id)"}, {"doi": "10.1/a\nb"}, {"doi": "x" * 400},
    {"doi": ["10.1021/x"]}, {"other": "1"}, "not a dict",
])
def test_bad_inputs_are_rejected(inputs):
    with pytest.raises(runner.TaskInputError):
        runner.resolve_inputs(runner.get_task("methods-card"), inputs)


def test_options_are_canonicalized():
    t = runner.get_task("small-calc")
    assert runner.resolve_inputs(t, {"ion": "ca2+"}) == {"ion": "Ca2+"}
    with pytest.raises(runner.TaskInputError):
        runner.resolve_inputs(t, {"ion": "Cl-"})


@pytest.mark.parametrize("value", ["Na+", "5", "true", "null", "10.1021/x", '{"a": 1}', "/usr/bin/python3"])
def test_vars_survive_the_cli_round_trip(value):
    k, _, v = f"x={runner._cli_value(value)}".partition("=")
    assert cli._vars([f"{k}={v}"]) == {"x": value}


def test_start_launches_the_flow_detached_and_records_the_task(tmp_path, monkeypatch):
    seen = {}

    def fake_start(path, vars_):
        seen.update(path=path, vars=vars_)
        (tmp_path / "rid").mkdir()
        return "rid"

    monkeypatch.setattr(flows, "start_detached", fake_start)
    monkeypatch.setattr(flows, "RUNS_DIR", tmp_path)
    monkeypatch.setenv("MACRAE_HARBOR_ENV", "docker")
    assert runner.start("small-calc", {"ion": "k+"}) == "rid"
    assert seen["path"] == Path(runner.REPO_ROOT / "tasks/flows/small-calc.yaml")
    assert seen["vars"] == {"ion": "K+", "task_id": "small-calc", "task_title": runner.get_task("small-calc")["title"],
                            "python": sys.executable, "environment": "docker"}
    meta = runner.run_meta("rid")
    assert meta["task_id"] == "small-calc" and meta["inputs"] == {"ion": "K+"}
    assert runner.run_meta("../etc") is None and runner.run_meta("missing") is None


def test_run_meta_falls_back_to_state_vars(tmp_path, monkeypatch):
    monkeypatch.setattr(flows, "RUNS_DIR", tmp_path)
    (tmp_path / "r2").mkdir()
    (tmp_path / "r2" / "state.json").write_text(json.dumps({"vars": {
        "task_id": "methods-card", "task_title": "Methods", "doi": "10.1/x", "python": "py", "environment": "modal"},
        "started": 5.0}))
    assert runner.run_meta("r2") == {"run_id": "r2", "task_id": "methods-card", "title": "Methods",
                                     "inputs": {"doi": "10.1/x"}, "flow": "", "started": 5.0}


def test_start_rejects_bad_input_before_launching(monkeypatch):
    monkeypatch.setattr(flows, "start_detached", lambda *a: pytest.fail("must not start"))
    with pytest.raises(runner.TaskInputError):
        runner.start("small-calc", {"ion": "U235"})
