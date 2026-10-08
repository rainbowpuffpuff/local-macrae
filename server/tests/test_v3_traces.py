"""v3: the manuscript as an edit stream (trajectory + live, de-duplicated), NOTES_TO_SELF.md, Harbor job listing,
raw trajectories, the trace zip, and the images the agent made (rawtrace.router)."""

import io
import json
import os
import zipfile

import pytest
from conftest import SECRET, T0, atif, iso, write_state

from server import events, live, manuscript, rawtrace

RID = "20261008-120000-paper-x1y2"
MS = "/app/paper/results/manuscript.md"

C1 = "# Title\n\nIntro TODO.\n\n![Fig 1](fig1.png)\n"
C2 = C1.replace("TODO", "done")
C3 = C2.replace("# Title", "# Paper")
C4 = C3.replace("Intro done.", 'Intro done. <img src="figs/fig2.svg">')
C5 = ("# Paper v2\n\n![Fig 1](fig1.png)\n<img src='figs/fig2.svg' alt=x>\n![](/app/paper/results/fig3.png)\n"
      "![a](results/fig4.png \"title\")\n![remote](https://example.org/x.png)\n![](../fig5.png)\n![](fig1.png)\n")
C6 = C5.replace("v2", "v3")


def call(i, cid, name, args, out, dt):
    return {"step_id": i, "timestamp": iso(T0 + dt), "source": "agent", "message": "",
            "tool_calls": [{"tool_call_id": cid, "function_name": name, "arguments": args}],
            "observation": {"results": [{"source_call_id": cid, "content": out}]}}


TRAJ_A1 = atif([
    {"step_id": 1, "timestamp": iso(T0 + 10), "source": "user", "message": "Write the paper."},
    call(2, "toolu_r1", "Read", {"file_path": "/app/paper/data.csv"}, "a,b\n1,2", 11),
    call(3, "toolu_w1", "Write", {"file_path": MS, "content": C1}, "File created successfully at: " + MS, 12),
    call(4, "toolu_o1", "Write", {"file_path": "/app/paper/other.md", "content": "x"}, "File created", 13),
    call(5, "toolu_e1", "Edit", {"file_path": MS, "old_string": "TODO", "new_string": "done"},
         "The file " + MS + " has been updated.", 14),
    call(6, "toolu_e2", "Edit", {"file_path": MS, "old_string": "missing", "new_string": "x"},
         "<tool_use_error>String to replace not found in file.\nString: missing</tool_use_error>", 15),
    call(7, "toolu_m1", "MultiEdit", {"file_path": MS, "edits": [
        {"old_string": "# Title", "new_string": "# Paper"},
        {"old_string": "Intro done.", "new_string": 'Intro done. <img src="figs/fig2.svg">'}]},
         "Applied 2 edits to " + MS, 16),
    call(8, "toolu_n1", "Write", {"file_path": "/app/NOTES_TO_SELF.md", "content": "- try PME\n"}, "ok", 17),
    call(9, "toolu_n2", "Edit", {"file_path": "/app/NOTES_TO_SELF.md", "old_string": "PME",
                                 "new_string": "PME with 1 nm cutoff"}, "ok", 18),
])


def asst(cid, name, inp, session):
    return {"type": "assistant", "session_id": session,
            "message": {"id": "m_" + cid, "role": "assistant",
                        "content": [{"type": "tool_use", "id": cid, "name": name, "input": inp}]}}


def result(cid, content, session, err=False):
    return {"type": "user", "session_id": session,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": cid,
                                                     "content": content, "is_error": err}]}}


def post_live(run_dir, step, stream, lines, t0):
    token = live.setup_run(run_dir) if not (run_dir / "live" / "token").is_file() else \
        (run_dir / "live" / "token").read_text()
    res = live.ingest(RID, step, token, json.dumps({"stream": stream, "offset": 0, "lines": [json.dumps(x) for x
                                                                                            in lines],
                                                    "times": [t0 + i for i in range(len(lines))]}).encode())
    assert res["accepted"] == len(lines)


def make_run(env, status="running"):
    """write[0]: attempt 1 finished (trajectory), attempt 2 running (no trajectory yet, only live lines)."""
    run_dir = env.runs / RID
    jobs = env.jobs / RID
    t1 = jobs / "write-0-a1" / "write-0-a1__Aa1"
    t2 = jobs / "write-0-a2" / "write-0-a2__Bb2"
    for t in (t1, t2):
        (t / "agent").mkdir(parents=True)
        (t / "config.json").write_text(json.dumps({"trial_name": t.name, "agent": {
            "name": "claude-code", "env": {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-SECRETSECRET", "X": "1"}}}))
    (t1.parent / "config.json").write_text("{}")
    (t1 / "agent" / "trajectory.json").write_text(json.dumps(TRAJ_A1))
    (t1 / "result.json").write_text(json.dumps({"started_at": iso(T0 + 9), "finished_at": iso(T0 + 30),
                                                "verifier_result": {"rewards": {"reward": 0.0}}}))
    (t2 / "result.json").write_text(json.dumps({"started_at": iso(T0 + 100), "finished_at": None}))
    art = t1 / "artifacts" / "app" / "paper"
    (art / "results").mkdir(parents=True)
    (art / "results" / "fig1.png").write_bytes(b"\x89PNG\r\n\x1a\nA1")
    (art / "results" / "fig2.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>')
    (art / "results" / "run.py").write_text("print(1)")
    steps = {"write[0]": {"key": "write[0]", "id": "write", "kind": "agent", "status": "running", "attempt": 2,
                          "started": T0 + 5, "job_dir": str(t2.parent)}}
    st = write_state(run_dir, name="paper", status=status, steps=steps, order=["write[0]"],
                     finished=T0 + 200 if status != "running" else None)
    (run_dir / "logs").mkdir()
    (run_dir / "logs" / "write-0.log").write_text("[10:00:00] queued (agent)\n")
    (run_dir / "flow.yaml").write_text("name: paper\nsteps: []\n")
    sess1, sess2 = "sess-a1", "sess-a2"
    # attempt 1 as it was posted live: the same Write and Edit (same tool_use ids) the trajectory has
    post_live(run_dir, "write[0]", "s1", [
        {"type": "system", "subtype": "init", "session_id": sess1},
        asst("toolu_w1", "Write", {"file_path": MS, "content": C1}, sess1),
        result("toolu_w1", "File created", sess1),
        asst("toolu_e1", "Edit", {"file_path": MS, "old_string": "TODO", "new_string": "done"}, sess1),
    ], T0 + 12)
    # attempt 2, running: a fresh sandbox starts over with a Write, then a live-only Edit (no result yet)
    post_live(run_dir, "write[0]", "s2", [
        {"type": "system", "subtype": "init", "session_id": sess2},
        asst("toolu_w2", "Write", {"file_path": MS, "content": C5}, sess2),
        result("toolu_w2", "File created", sess2),
        asst("toolu_lx", "Edit", {"file_path": MS, "old_string": "v2", "new_string": "v3", "replace_all": True},
             sess2),
    ], T0 + 110)
    return run_dir, st, t1, t2


# ── manuscript reconstruction ───────────────────────────────────────────────


def test_edit_stream_reconstructs_the_manuscript(env):
    make_run(env)
    res = manuscript.edit_stream(RID)
    assert set(res) == {"run_id", "path", "edits", "content", "done", "figures", "updated"}
    assert res["run_id"] == RID and res["path"] == "results/manuscript.md" and res["done"] is False
    ed = res["edits"]
    # 1 write + 1 edit + 1 failed edit + 2 sub-edits (attempt 1), 1 write + 1 edit (attempt 2, live only)
    assert [(e["op"], e["attempt"], e["ok"]) for e in ed] == [
        ("write", 1, True), ("edit", 1, True), ("edit", 1, False), ("edit", 1, True), ("edit", 1, True),
        ("write", 2, True), ("edit", 2, True)]
    assert [e["seq"] for e in ed] == list(range(1, 8))
    assert [e["content"] for e in ed] == [C1, C2, C2, C3, C4, C5, C6]
    assert ed[0]["old"] is None and ed[0]["new"] == C1
    assert (ed[1]["old"], ed[1]["new"]) == ("TODO", "done")
    assert all(e["path"] == MS and e["step"] == "write[0]" for e in ed)
    assert [e["t"] for e in ed][:5] == [T0 + 12, T0 + 14, T0 + 15, T0 + 16, T0 + 16]
    assert ed[5]["t"] >= T0 + 110
    assert res["content"] == C6 and res["updated"] == max(e["t"] for e in ed)
    assert res["figures"] == ["results/fig1.png", "results/figs/fig2.svg", "results/fig3.png", "results/fig4.png",
                              "fig5.png"]


def test_edit_stream_dedupes_live_against_trajectory_by_hash_without_ids(env):
    """No tool_use ids (an old converter): the nth call with the same name + args in the step is the same call."""
    run_dir = env.runs / RID
    trial = env.jobs / RID / "calc-a1" / "calc-a1__Zz"
    (trial / "agent").mkdir(parents=True)
    w = {"file_path": MS, "content": "A B"}
    e = {"file_path": MS, "old_string": "B", "new_string": "C"}
    (trial / "agent" / "claude-code.txt").write_text("\n".join(json.dumps(x) for x in [
        asst("", "Write", w, "s"), asst("", "Edit", e, "s")]) + "\n")
    write_state(run_dir, steps={"calc": {"kind": "agent", "status": "running", "attempt": 1, "started": T0}},
                order=["calc"])
    post_live(run_dir, "calc", "x", [asst("", "Write", w, "s"), asst("", "Edit", e, "s"),
                                     asst("", "Edit", {"file_path": MS, "old_string": "C", "new_string": "D"}, "s")],
              T0 + 5)
    ed = manuscript.edit_stream(RID)["edits"]
    assert [x["content"] for x in ed] == ["A B", "A C", "A D"]


def test_edit_first_on_an_unknown_file_and_carry_over(env):
    run_dir = env.runs / RID
    write_state(run_dir, steps={"calc": {"kind": "agent", "status": "running", "attempt": 1, "started": T0}},
                order=["calc"])
    post_live(run_dir, "calc", "x", [
        asst("t1", "Edit", {"file_path": MS, "old_string": "a", "new_string": "b"}, "s"),
        result("t1", "updated", "s"),
        asst("t2", "Write", {"file_path": MS, "content": "one two"}, "s"),
        asst("t3", "Edit", {"file_path": MS, "old_string": "zzz", "new_string": "y"}, "s"),
        result("t3", "String to replace not found in file.", "s", err=True),
        asst("t4", "Edit", {"file_path": MS, "old_string": "two", "new_string": "2"}, "s"),
    ], T0 + 1)
    write_state(run_dir, steps={"calc": {"kind": "agent", "status": "ok", "attempt": 1, "started": T0}},
                order=["calc"], status="ok", finished=T0 + 50)
    res = manuscript.edit_stream(RID)
    assert [(x["ok"], x["content"]) for x in res["edits"]] == [(True, None), (True, "one two"), (False, "one two"),
                                                               (True, "one 2")]
    assert res["done"] is True and res["content"] == "one 2" and res["figures"] == []
    assert manuscript.edit_stream("no-such-run") is None and manuscript.edit_stream("../x") is None


def test_notes_reconstructed_then_from_artifacts(env):
    run_dir, _, t1, t2 = make_run(env)
    assert manuscript.notes(RID) == {"run_id": RID, "notes": "- try PME with 1 nm cutoff\n", "edits": 2}
    # a run whose agent never edited the notes in a way we saw: the newest trial's copy
    rid2 = RID + "b"
    write_state(env.runs / rid2, steps={"calc": {"kind": "agent", "status": "ok", "attempt": 1}}, order=["calc"])
    trial = env.jobs / rid2 / "calc-a1" / "calc-a1__Q"
    (trial / "artifacts" / "app" / "calc").mkdir(parents=True)
    (trial / "result.json").write_text("{}")
    (trial / "artifacts" / "app" / "calc" / "NOTES_TO_SELF.md").write_text("remember: tight SCF")
    assert manuscript.notes(rid2) == {"run_id": rid2, "notes": "remember: tight SCF", "edits": 0}
    assert manuscript.notes("nope") is None


def test_figures_normalization():
    f = manuscript.figures
    assert f("![x](fig.png) ![y](./sub/a.JPG) ![z](data:image/png;base64,xx) ![w](a.png#frag)",
             "results/manuscript.md") == ["results/fig.png", "results/sub/a.JPG", "results/a.png"]
    assert f("![](/etc/passwd.png) ![](../../up.png)", "results/manuscript.md", MS) == []
    assert f("![](img/a.png)", "NOTES_TO_SELF.md") == ["img/a.png"]


# ── jobs, trajectories, zip, artifacts ──────────────────────────────────────


@pytest.fixture
def api(env):
    """The router in a fresh app, behind a dependency like app.py's require_secret."""
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient

    def need_secret(x_macrae_secret: str = Header(default="")):
        if x_macrae_secret != SECRET:
            raise HTTPException(401, "missing or wrong X-Macrae-Secret")

    app = FastAPI()
    app.include_router(rawtrace.router, dependencies=[Depends(need_secret)])
    c = TestClient(app)
    c.headers.update({"X-Macrae-Secret": SECRET})
    return c


def test_jobs_listing(env, api):
    make_run(env)
    res = rawtrace.jobs(RID)
    assert api.get(f"/api/runs/{RID}/jobs").json() == res
    assert [(j["step"], j["attempt"], j["job"], j["path"]) for j in res["jobs"]] == [
        ("write[0]", 1, "write-0-a1", f"{RID}/write-0-a1"), ("write[0]", 2, "write-0-a2", f"{RID}/write-0-a2")]
    t1, = res["jobs"][0]["trials"]
    assert t1 == {"name": "write-0-a1__Aa1", "path": f"{RID}/write-0-a1/write-0-a1__Aa1",
                  "trajectory": f"{RID}/write-0-a1/write-0-a1__Aa1/agent/trajectory.json", "stream": None,
                  "reward": 0.0, "exception": None, "started": T0 + 9, "finished": T0 + 30,
                  "url": f"/api/runs/{RID}/jobs/write-0-a1/write-0-a1__Aa1/trajectory"}
    t2, = res["jobs"][1]["trials"]
    assert t2["trajectory"] is None and t2["url"] is None and t2["finished"] is None
    assert res["live"] == [{"step": "write[0]", "path": "live/write-0.jsonl",
                            "bytes": (env.runs / RID / "live" / "write-0.jsonl").stat().st_size}]
    assert api.get("/api/runs/nope/jobs").status_code == 404


def test_trajectory_route(env, api):
    _, _, t1, t2 = make_run(env)
    r = api.get(f"/api/runs/{RID}/jobs/write-0-a1/write-0-a1__Aa1/trajectory")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert r.headers["content-disposition"].startswith("inline")
    assert r.json()["steps"][2]["tool_calls"][0]["tool_call_id"] == "toolu_w1"
    assert api.get(f"/api/runs/{RID}/jobs/write-0-a2/write-0-a2__Bb2/trajectory").status_code == 404
    (t2 / "agent" / "claude-code.txt").write_text('{"type":"system"}\n')
    r = api.get(f"/api/runs/{RID}/jobs/write-0-a2/write-0-a2__Bb2/trajectory")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    for bad in ("write-0-a1/..%2F..%2Fx", "%2e%2e/write-0-a1__Aa1", "other-a1/write-0-a1__Aa1",
                "write-0-a1/nope"):
        assert api.get(f"/api/runs/{RID}/jobs/{bad}/trajectory").status_code == 404, bad
    assert rawtrace.trajectory_file(RID, "..", "x") is None
    # a job of another run is not this run's
    write_state(env.runs / "other", steps={}, order=[])
    assert rawtrace.trajectory_file("other", "write-0-a1", "write-0-a1__Aa1") is None


def test_trace_zip(env, api, monkeypatch, tmp_path):
    run_dir, _, t1, _ = make_run(env)
    events.poll(RID)  # writes the server's event log
    token = (run_dir / "live" / "token").read_text()
    (run_dir / "logs" / "write-0.log").write_text(f"[10:00:00] harbor --ae MACRAE_LIVE_TOKEN={token}\n")
    (run_dir / "outputs").mkdir()
    (run_dir / "outputs" / "big.txt").write_bytes(b"x" * 5000)
    (run_dir / "deploy.key").write_text("k")
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    os.symlink(outside, run_dir / "link.txt")
    monkeypatch.setattr(rawtrace, "MAX_FILE", 4000)
    r = api.get(f"/api/runs/{RID}/trace")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == f'attachment; filename="{RID}-trace.zip"'
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"run/state.json", "run/flow.yaml", "run/logs/write-0.log", "run/live/write-0.jsonl",
            "run/live/write-0.batches.jsonl", "events.jsonl", "MANIFEST.txt",
            "jobs/write-0-a1/write-0-a1__Aa1/agent/trajectory.json",
            "jobs/write-0-a1/write-0-a1__Aa1/artifacts/app/paper/results/fig1.png"} <= names
    assert "run/live/token" not in names and "run/deploy.key" not in names and "run/link.txt" not in names
    assert "run/outputs/big.txt" not in names
    manifest = z.read("MANIFEST.txt").decode()
    assert "run/outputs/big.txt" in manifest and "run/live/token" in manifest and "run/link.txt" in manifest
    blob = b"".join(z.read(n) for n in names)
    assert token.encode() not in blob and b"SECRETSECRET" not in blob
    cfg = json.loads(z.read("jobs/write-0-a1/write-0-a1__Aa1/config.json"))
    assert cfg["agent"]["env"] == {"CLAUDE_CODE_OAUTH_TOKEN": "***", "X": "1"}
    assert json.loads(z.read("events.jsonl").decode().splitlines()[0])["event"]["seq"] == 1
    assert api.get("/api/runs/nope/trace").status_code == 404


def test_artifacts(env, api):
    run_dir, _, t1, t2 = make_run(env)
    r = api.get(f"/api/runs/{RID}/artifacts/results/fig1.png")
    assert r.status_code == 200 and r.content.endswith(b"A1")
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=60"
    r = api.get(f"/api/runs/{RID}/artifacts/results/fig2.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["content-security-policy"] == "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    # the newest copy wins: attempt 2 left its own fig1.png
    a2 = t2 / "artifacts" / "app" / "paper" / "results"
    a2.mkdir(parents=True)
    (a2 / "fig1.png").write_bytes(b"\x89PNGA2")
    os.utime(a2 / "fig1.png", (T0 + 1000, T0 + 1000))
    os.utime(t1 / "artifacts" / "app" / "paper" / "results" / "fig1.png", (T0, T0))
    assert api.get(f"/api/runs/{RID}/artifacts/results/fig1.png").content == b"\x89PNGA2"
    # the run folder's results/ is searched too; app-level paths work
    (run_dir / "results").mkdir()
    (run_dir / "results" / "summary.jpg").write_bytes(b"jpg")
    assert api.get(f"/api/runs/{RID}/artifacts/summary.jpg").headers["content-type"] == "image/jpeg"
    assert api.get(f"/api/runs/{RID}/artifacts/paper/results/fig2.svg").status_code == 200
    assert api.get(f"/api/runs/{RID}/artifacts/results/run.py").status_code == 400
    assert api.get(f"/api/runs/{RID}/artifacts/%2e%2e/%2e%2e/secret.png").status_code == 400
    assert api.get(f"/api/runs/{RID}/artifacts/../x.png").status_code in (400, 404)
    assert api.get(f"/api/runs/{RID}/artifacts//etc/x.png").status_code == 400
    assert api.get(f"/api/runs/{RID}/artifacts/results%5Cfig1.png").status_code == 400
    assert api.get(f"/api/runs/{RID}/artifacts/" + "a" * 300 + ".png").status_code == 400
    assert api.get(f"/api/runs/{RID}/artifacts/results/none.png").status_code == 404
    assert api.get("/api/runs/nope/artifacts/results/fig1.png").status_code == 404
    # a symlink out of the artifacts is not followed
    os.symlink("/etc/hostname", a2 / "host.png")
    assert rawtrace.artifact_file(RID, "results/host.png") is None
    assert rawtrace.artifact_file(RID, "/abs.png") is None and rawtrace.artifact_file(RID, "a/../b.png") is None


def test_manuscript_and_notes_routes(env, api):
    make_run(env)
    r = api.get(f"/api/runs/{RID}/manuscript")
    assert r.status_code == 200 and r.json()["content"] == C6 and len(r.json()["edits"]) == 7
    r = api.get(f"/api/runs/{RID}/manuscript", params={"path": "NOTES_TO_SELF.md"})
    assert r.json()["content"] == "- try PME with 1 nm cutoff\n"
    for bad in ("../x.md", "results/manuscript.txt", "/app/x.md", "a\\b.md"):
        assert api.get(f"/api/runs/{RID}/manuscript", params={"path": bad}).status_code == 400, bad
    assert api.get(f"/api/runs/{RID}/notes").json() == {"run_id": RID, "notes": "- try PME with 1 nm cutoff\n",
                                                        "edits": 2}
    assert api.get("/api/runs/nope/notes").status_code == 404
    assert api.get("/api/runs/nope/manuscript").status_code == 404


def test_routes_need_the_secret(env, api):
    make_run(env)
    paths = [f"/api/runs/{RID}/jobs", f"/api/runs/{RID}/jobs/write-0-a1/write-0-a1__Aa1/trajectory",
             f"/api/runs/{RID}/trace", f"/api/runs/{RID}/manuscript", f"/api/runs/{RID}/notes",
             f"/api/runs/{RID}/artifacts/results/fig1.png"]
    for p in paths:
        assert api.get(p).status_code == 200, p
        assert api.get(p, headers={"X-Macrae-Secret": "wrong"}).status_code == 401, p
        assert api.get(p, headers={"X-Macrae-Secret": ""}).status_code == 401, p
    # once app.py mounts the router, the real app must guard it the same way
    from fastapi.testclient import TestClient

    from server.app import app
    if f"/api/runs/{{run_id}}/manuscript" in [getattr(r, "path", "") for r in app.routes]:
        c = TestClient(app)
        for p in paths:
            assert c.get(p).status_code == 401, p
            assert c.get(p, headers={"X-Macrae-Secret": SECRET}).status_code == 200, p
