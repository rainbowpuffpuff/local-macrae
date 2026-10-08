import dataclasses
import json
import sys

from conftest import SECRET, make_agent_run, write_state


def test_health_needs_no_secret_and_works_without_rag(env, client, monkeypatch):
    monkeypatch.setitem(sys.modules, "rag", None)  # import rag → ImportError
    client.headers.pop("X-Macrae-Secret")
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "papers": 0, "chunks": 0, "modal": False}


def test_health_counts_from_rag_and_modal_env(env, client, fake_modules, monkeypatch):
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-1")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "as-1")
    assert client.get("/api/health").json() == {"ok": True, "papers": 3, "chunks": 42, "modal": True}


def test_health_counts_from_index_folder(env, client, monkeypatch):
    monkeypatch.setitem(sys.modules, "rag", None)
    idx = env.tmp / "index"
    idx.mkdir()
    (idx / "chunks.jsonl").write_text("\n".join(json.dumps({"citation": {"doi": d}}) for d in ("a", "a", "b")))
    assert client.get("/api/health").json()["chunks"] == 3
    assert client.get("/api/health").json()["papers"] == 2


def test_modal_profile_in_modal_toml(env, client, monkeypatch, tmp_path):
    cfg = tmp_path / "modal.toml"
    cfg.write_text('[acalincarol]\ntoken_id = "ak"\ntoken_secret = "as"\nactive = true\n')
    monkeypatch.setenv("MODAL_CONFIG_PATH", str(cfg))
    assert client.get("/api/health").json()["modal"] is True
    monkeypatch.setenv("MODAL_PROFILE", "other")
    assert client.get("/api/health").json()["modal"] is False


def test_secret_is_required(env, client):
    assert client.get("/api/tasks", headers={"X-Macrae-Secret": "nope"}).status_code == 401
    client.headers.pop("X-Macrae-Secret")
    for method, path in (("get", "/api/tasks"), ("get", "/api/runs"), ("post", "/api/search"),
                         ("post", "/api/tools/search_papers"), ("post", "/api/tools/start_task"),
                         ("post", "/api/tools/run_status"), ("post", "/api/tasks/methods-card/start"),
                         ("get", "/api/runs/x"), ("get", "/api/runs/x/events")):
        assert getattr(client, method)(path).status_code == 401, path


def test_no_secret_configured_fails_closed(env, client, monkeypatch):
    monkeypatch.delenv("MACRAE_TOOL_SECRET")
    assert client.get("/api/tasks").status_code == 503
    monkeypatch.setenv("MACRAE_AUTH_DISABLED", "1")
    assert client.get("/api/tasks").status_code == 200


def test_tasks(env, client):
    tasks = client.get("/api/tasks").json()["tasks"]
    assert [t["id"] for t in tasks] == ["methods-card", "small-calc"]
    t = tasks[0]
    assert set(t) >= {"id", "title", "subtitle", "icon", "prompt", "flow", "inputs"}
    assert t["inputs"][0] == {"name": "doi", "label": "DOI", "default": "10.1093/glycob/cwag064"}


def test_tasks_file_as_plain_list_and_missing_file(env, client):
    env.tasks_file.write_text(json.dumps([{"id": "x", "title": "X"}, {"no": "id"}]))
    assert [t["id"] for t in client.get("/api/tasks").json()["tasks"]] == ["x"]
    env.tasks_file.unlink()
    assert client.get("/api/tasks").json() == {"tasks": []}


def test_start_task_fills_defaults_and_records_task(env, client, fake_modules):
    r = client.post("/api/tasks/methods-card/start", json={"inputs": {}})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    assert fake_modules.started == [("methods-card", {"doi": "10.1093/glycob/cwag064"}, run_id)]
    r2 = client.post("/api/tasks/methods-card/start", json={"inputs": {"doi": "10.1021/acs.jctc.5c02051"}})
    assert fake_modules.started[-1][1] == {"doi": "10.1021/acs.jctc.5c02051"}
    assert client.post("/api/tasks/small-calc/start").status_code == 200  # no body at all
    runs = client.get("/api/runs").json()["runs"]
    assert {x["run_id"] for x in runs} == {run_id, r2.json()["run_id"], fake_modules.started[-1][2]}
    by_id = {x["run_id"]: x for x in runs}
    assert by_id[run_id]["task_id"] == "methods-card" and by_id[run_id]["title"] == "Methods card"
    assert by_id[run_id]["status"] == "running"


def test_start_task_rejects_bad_input(env, client, fake_modules):
    assert client.post("/api/tasks/nope/start").status_code == 404
    assert client.post("/api/tasks/methods-card/start", json={"inputs": {"x": "1"}}).status_code == 400
    for bad in ('10.1/x"; rm -rf /', "$(id)", "a`b`", "a\nb", "a|b", "x" * 600):
        r = client.post("/api/tasks/methods-card/start", json={"inputs": {"doi": bad}})
        assert r.status_code == 400, bad
    assert client.post("/api/tasks/methods-card/start", json={"inputs": {"doi": ["a"]}}).status_code == 400
    assert fake_modules.started == []


def test_start_task_without_runner_is_503(env, client, monkeypatch):
    monkeypatch.setitem(sys.modules, "tasks.runner", None)
    r = client.post("/api/tasks/methods-card/start")
    assert r.status_code == 503
    assert client.post("/api/tools/start_task", json={"task_id": "methods-card"}).json()["run_id"] == ""


def test_runner_error_is_400(env, client, fake_modules):
    def boom(task_id, inputs):
        raise ValueError("flow file missing")
    fake_modules.runner.start = boom
    r = client.post("/api/tasks/methods-card/start")
    assert r.status_code == 400 and "flow file missing" in r.json()["detail"]


def test_capacity_limit(env, client, fake_modules, monkeypatch):
    monkeypatch.setenv("MACRAE_MAX_ACTIVE_RUNS", "1")
    assert client.post("/api/tasks/small-calc/start").status_code == 200
    assert client.post("/api/tasks/small-calc/start").status_code == 429


def test_draining_refuses_new_starts(env, client, fake_modules, monkeypatch, tmp_path):
    flag = tmp_path / "draining"
    monkeypatch.setenv("MACRAE_DRAIN_FILE", str(flag))
    assert client.post("/api/tasks/small-calc/start").status_code == 200  # flag file absent: normal
    flag.touch()  # deploy/start.py: the container is stopping
    r = client.post("/api/tasks/small-calc/start")
    assert r.status_code == 503 and "restarting" in r.json()["detail"]
    said = client.post("/api/tools/start_task", json={"task_id": "small-calc"}).json()
    assert said["run_id"] == "" and "restarting" in said["message"]
    assert client.get("/api/runs").status_code == 200  # everything else keeps answering


def test_run_and_events_endpoints(env, client):
    run_dir, *_ = make_agent_run(env, status="ok", step_status="ok")
    rid = run_dir.name
    run = client.get(f"/api/runs/{rid}").json()
    assert run["run_id"] == rid and run["status"] == "ok"
    assert run["task_id"] == "methods-card"  # matched by the flow file, no sidecar
    assert [s["key"] for s in run["steps"]] == ["passages", "card", "card[0]"]
    s = run["steps"][2]
    assert {k: s[k] for k in ("key", "kind", "status", "account", "reward", "attempt", "error")} == {
        "key": "card[0]", "kind": "agent", "status": "ok", "account": "alice", "reward": 1.0, "attempt": 1,
        "error": ""}
    ev = client.get(f"/api/runs/{rid}/events").json()
    assert ev["done"] is True and len(ev["events"]) > 5
    last = ev["events"][-1]["seq"]
    assert client.get(f"/api/runs/{rid}/events", params={"after": last}).json() == {"events": [], "done": True}
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/nope/events").status_code == 404
    assert client.get("/api/runs/..%2F..%2Fetc").status_code == 404


def test_runs_are_newest_first_and_capped(env, client):
    for i in range(55):
        write_state(env.runs / f"r{i:03d}", started=1000.0 + i, status="ok")
    runs = client.get("/api/runs").json()["runs"]
    assert len(runs) == 50
    assert runs[0]["run_id"] == "r054" and runs[-1]["run_id"] == "r005"
    assert runs[0]["title"] == "methods-card" or runs[0]["task_id"] == "methods-card"


def test_search(env, client, fake_modules):
    ps = client.post("/api/search", json={"query": "ion pairing", "k": 2}).json()["passages"]
    assert len(ps) == 2
    p = ps[0]
    assert set(p) == {"id", "text", "score", "citation"}
    assert set(p["citation"]) == {"key", "title", "authors", "year", "journal", "doi", "page", "url", "quote"}
    assert p["citation"]["key"] == "[1]"
    empty = client.post("/api/search", json={"query": "  "}).json()
    assert empty["passages"] == [] and set(empty) == {"passages", "cost"}  # cost: server/cost_api.py
    assert client.post("/api/search", json={"query": "x", "k": 0}).status_code == 422


def test_search_accepts_dataclass_passages(env, client, fake_modules):
    @dataclasses.dataclass
    class Cit:
        key: str
        title: str
        doi: str

    @dataclasses.dataclass
    class Passage:
        id: str
        text: str
        score: float
        citation: Cit

    fake_modules.rag.search = lambda q, k=6: [Passage("a", "text", 0.5, Cit("[1]", "T", "10.1/x"))]
    p = client.post("/api/search", json={"query": "q"}).json()["passages"][0]
    assert p["citation"]["doi"] == "10.1/x" and p["citation"]["url"] == "https://doi.org/10.1/x"


def test_search_without_rag(env, client, monkeypatch):
    monkeypatch.setitem(sys.modules, "rag", None)
    assert client.post("/api/search", json={"query": "q"}).status_code == 503
    r = client.post("/api/tools/search_papers", json={"query": "q"})
    assert r.status_code == 200 and r.json()["citations"] == [] and "offline" in r.json()["answer_context"]


def test_tool_search_papers(env, client, fake_modules):
    r = client.post("/api/tools/search_papers", json={"query": "ion pairing"}).json()
    # the passages come framed as quoted data (server/safety.py fence), numbered as rag gave them
    assert "<untrusted-papers>\n[1] Passage 0" in r["answer_context"]
    assert [c["key"] for c in r["citations"]] == ["[1]", "[2]"]
    empty = client.post("/api/tools/search_papers", json={"query": "nothing at all"}).json()
    assert empty["citations"] == [] and "don't cover" in empty["answer_context"]


def test_tool_start_task_fuzzy_and_unknown(env, client, fake_modules):
    r = client.post("/api/tools/start_task", json={"task_id": "Methods Card"}).json()
    assert r["run_id"] and "Methods card" in r["message"]
    r = client.post("/api/tools/start_task", json={"task_id": "dance"}).json()
    assert r["run_id"] == "" and "methods-card" in r["message"]


def test_tool_run_status(env, client, fake_modules):
    run_dir, st, trial = make_agent_run(env, status="running", step_status="running")
    rid = run_dir.name
    r = client.post("/api/tools/run_status", json={"run_id": rid}).json()
    assert r["status"] == "running"
    assert "Methods card" in r["summary"] and "working on card[0]" in r["summary"]
    assert 0 < len(r["recent"]) <= 5 and all(isinstance(x, str) for x in r["recent"])
    assert client.post("/api/tools/run_status", json={"run_id": "latest"}).json()["status"] == "running"
    assert client.post("/api/tools/run_status", json={"run_id": "missing"}).json()["status"] == "unknown"


def test_tool_run_status_finished(env, client):
    run_dir, *_ = make_agent_run(env, status="ok", step_status="ok")
    r = client.post("/api/tools/run_status", json={"run_id": run_dir.name}).json()
    assert r["status"] == "ok"
    assert r["summary"].startswith("“Methods card” finished in")
    assert r["recent"][-1] == "Run finished"


def test_tool_run_status_no_runs(env, client):
    assert client.post("/api/tools/run_status", json={}).json()["status"] == "none"


def test_secret_value_is_compared_exactly(env, client):
    assert client.get("/api/tasks", headers={"X-Macrae-Secret": SECRET + " "}).status_code == 401
