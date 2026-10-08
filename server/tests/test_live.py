"""v2 live events: POST /api/live/{run}/{step} (the claude wrapper's endpoint), and live lines becoming TraceEvents
that are not repeated when the trial's trajectory arrives."""

import json

from conftest import T0, iso, make_agent_run

from server import live

SESSION = "sess-live-1"
USAGE = {"input_tokens": 1200, "output_tokens": 300, "cache_read_input_tokens": 5000,
         "cache_creation_input_tokens": 800}


def stream_lines(session=SESSION):
    """What `claude --output-format stream-json` prints for a short agent turn (one line per content block)."""
    def asst(mid, block, usage=USAGE):
        return {"type": "assistant", "session_id": session, "parent_tool_use_id": None,
                "message": {"id": mid, "model": "claude-opus-5-5", "role": "assistant", "content": [block],
                            "usage": usage}}

    def result(tid, content):
        return {"type": "user", "session_id": session,
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                         "content": content}]}}

    return [
        {"type": "system", "subtype": "init", "session_id": session, "model": "claude-opus-5-5", "tools": []},
        asst("msg_1", {"type": "text", "text": "I'll read the paper record first."}),
        asst("msg_1", {"type": "tool_use", "id": "toolu_live_1", "name": "Read",
                       "input": {"file_path": "/app/paper/paper.json"}}),
        result("toolu_live_1", '{"doi": "10.1093/glycob/cwag064"}'),
        asst("msg_2", {"type": "tool_use", "id": "toolu_live_2", "name": "Bash",
                       "input": {"command": "python3 run_calc.py", "description": "Run ion-water scan"}},
             {"input_tokens": 50, "output_tokens": 120, "cache_read_input_tokens": 7000,
              "cache_creation_input_tokens": 0}),
        result("toolu_live_2", "r_min 2.2 A  E_int -24.123456 kcal/mol"),
        asst("msg_3", {"type": "text", "text": "Done: the binding energy is -24.1 kcal/mol."},
             {"input_tokens": 10, "output_tokens": 40, "cache_read_input_tokens": 7300,
              "cache_creation_input_tokens": 0}),
        {"type": "result", "subtype": "success", "session_id": session, "is_error": False,
         "total_cost_usd": 0.0421, "num_turns": 3,
         "usage": {"input_tokens": 1260, "output_tokens": 460, "cache_read_input_tokens": 19300,
                   "cache_creation_input_tokens": 800}},
    ]


def post(client, run_id, step, token, lines, stream="s1", offset=0, **kw):
    body = {"stream": stream, "offset": offset, "lines": [json.dumps(x) if not isinstance(x, str) else x
                                                          for x in lines]}
    body.update(kw)
    return client.post(f"/api/live/{run_id}/{step.replace('[', '%5B').replace(']', '%5D')}", json=body,
                       headers={"X-Macrae-Live": token, "X-Macrae-Secret": ""})


def test_ingest_needs_the_runs_token_not_the_secret(env, client):
    run_dir, _, _ = make_agent_run(env, trajectory=False, result_json=False)
    rid = run_dir.name
    assert post(client, rid, "card[0]", "x", ["{}"]).status_code == 404  # live is off for this run (no token)
    token = live.setup_run(run_dir)
    assert (run_dir / "live" / "token").stat().st_mode & 0o777 == 0o600
    assert post(client, rid, "card[0]", "wrong", ["{}"]).status_code == 401
    r = client.post(f"/api/live/{rid}/card", json={"lines": ["{}"]})  # the secret alone is not enough
    assert r.status_code == 401
    assert post(client, "../etc", "card", token, ["{}"]).status_code == 404
    assert post(client, "nope-run", "card", token, ["{}"]).status_code == 404
    r = post(client, rid, "card[0]", token, ['{"type":"system"}'])
    assert r.status_code == 200 and r.json() == {"ok": True, "accepted": 1, "next": 1}
    assert (run_dir / "live" / "card-0.jsonl").read_text() == '{"type":"system"}\n'


def test_ingest_is_idempotent_by_stream_and_offset(env, client):
    run_dir, _, _ = make_agent_run(env, trajectory=False, result_json=False)
    token = live.setup_run(run_dir)
    rid = run_dir.name
    a, b, c = '{"n":1}', '{"n":2}', '{"n":3}'
    assert post(client, rid, "card[0]", token, [a, b]).json()["accepted"] == 2
    # the wrapper didn't see our answer and resends with the next line: only the new one is kept
    assert post(client, rid, "card[0]", token, [a, b, c]).json() == {"ok": True, "accepted": 1, "next": 3}
    assert post(client, rid, "card[0]", token, [b, c], offset=1).json()["accepted"] == 0
    # a second claude invocation (retry attempt) is its own stream, starting at 0
    assert post(client, rid, "card[0]", token, [a], stream="s2").json()["accepted"] == 1
    # NDJSON works too (curl-friendly), appended to the default stream
    r = client.post(f"/api/live/{rid}/card%5B0%5D", content=b'{"n":9}\n\n{"n":10}\n',
                    headers={"X-Macrae-Live": token, "content-type": "application/x-ndjson"})
    assert r.json()["accepted"] == 2
    stored = (run_dir / "live" / "card-0.jsonl").read_text().splitlines()
    assert stored == [a, b, c, a, '{"n":9}', '{"n":10}']
    got = live.streams(run_dir, "card[0]")
    assert [d["n"] for _, d in got["s1"]] == [1, 2, 3] and [d["n"] for _, d in got["default"]] == [9, 10]


def test_ingest_limits_and_clock_skew(env, client, monkeypatch):
    run_dir, _, _ = make_agent_run(env, trajectory=False, result_json=False)
    token = live.setup_run(run_dir)
    rid = run_dir.name
    # the sandbox clock is 100 s behind: times are shifted by (receive − sent)
    import time
    now = time.time()
    r = post(client, rid, "card[0]", token, ['{"a":1}'], times=[now - 101], sent=now - 100)
    assert r.status_code == 200
    (t, _), = live.streams(run_dir, "card[0]")["s1"]
    assert abs(t - (now - 1)) < 3
    # a line with U+2028 inside stays one line (str.splitlines would cut it)
    post(client, rid, "card[0]", token, ['{"text":"a\u2028b"}'], stream="u")
    assert live.streams(run_dir, "card[0]")["u"][0][1]["text"] == "a\u2028b"
    monkeypatch.setattr(live, "MAX_BODY", 100)
    assert post(client, rid, "card[0]", token, ['{"x":"' + "y" * 200 + '"}'], stream="big").status_code == 413
    # long after the run ended: refused, so a stray wrapper stops
    st = json.loads((run_dir / "state.json").read_text())
    st.update(status="ok", finished=now - 3600)
    (run_dir / "state.json").write_text(json.dumps(st))
    monkeypatch.setattr(live, "MAX_BODY", 4 * 1024 * 1024)
    assert post(client, rid, "card[0]", token, ['{"late":1}'], stream="late").status_code == 410


def _types(evs):
    return [(e["type"], e["title"]) for e in evs]


def test_live_lines_become_events_and_the_trajectory_does_not_repeat_them(env, client):
    run_dir, st, trial = make_agent_run(env, trajectory=False, result_json=False)
    rid = run_dir.name
    token = live.setup_run(run_dir)
    lines = stream_lines()
    # first batch: init, text, Read call (+ its result), Bash call without its output yet
    post(client, rid, "card[0]", token, lines[:5], times=[T0 + 20 + i for i in range(5)])
    evs = client.get(f"/api/runs/{rid}/events").json()["events"]
    tt = _types(evs)
    assert ("think", "I'll read the paper record first.") in tt
    assert ("read", "Read paper/paper.json") in tt
    assert not any(t == "calc" for t, _ in tt)  # Bash waits for its output (it decides calc vs status)
    think = next(e for e in evs if e["type"] == "think")
    assert think["cost"]["tokens"] == {"in": 1200, "out": 300, "cache": 5800}
    assert think["cost"]["usd"] > 0 and "elapsed_s" in think
    read = next(e for e in evs if e["type"] == "read")
    assert read["citation"]["doi"] == "10.1093/glycob/cwag064"
    last = evs[-1]["seq"]

    post(client, rid, "card[0]", token, lines[5:], offset=5, times=[T0 + 30 + i for i in range(3)])
    new = client.get(f"/api/runs/{rid}/events", params={"after": last}).json()["events"]
    tt = _types(new)
    calc = next(e for e in new if e["type"] == "calc")
    assert calc["title"].startswith("Ran ion-water scan (python")
    assert ("think", "Done: the binding energy is -24.1 kcal/mol.") in tt
    last = new[-1]["seq"]

    # the trial ends: Harbor writes the trajectory (same session, same tool ids) and the step finishes
    atif = {"schema_version": "ATIF-v1.7", "session_id": SESSION,
            "agent": {"name": "claude-code", "version": "2.1", "model_name": "claude-opus-5-5"},
            "final_metrics": {"total_prompt_tokens": 21360, "total_completion_tokens": 460,
                              "total_cached_tokens": 19300, "total_cost_usd": 0.0421,
                              "extra": {"total_cache_creation_input_tokens": 800}},
            "steps": [
                {"step_id": 1, "timestamp": iso(T0 + 19), "source": "user", "message": "Do it."},
                {"step_id": 2, "timestamp": iso(T0 + 21), "source": "agent",
                 "message": "I'll read the paper record first.",
                 "tool_calls": [{"tool_call_id": "toolu_live_1", "function_name": "Read",
                                 "arguments": {"file_path": "/app/paper/paper.json"}}],
                 "observation": {"results": [{"source_call_id": "toolu_live_1", "content": "{}"}]}},
                {"step_id": 3, "timestamp": iso(T0 + 24), "source": "agent", "message": "",
                 "tool_calls": [{"tool_call_id": "toolu_live_2", "function_name": "Bash",
                                 "arguments": {"command": "python3 run_calc.py",
                                               "description": "Run ion-water scan"}}],
                 "observation": {"results": [{"source_call_id": "toolu_live_2",
                                              "content": "r_min 2.2 A  E_int -24.123456 kcal/mol"}]}},
                {"step_id": 4, "timestamp": iso(T0 + 31), "source": "agent",
                 "message": "Done: the binding energy is -24.1 kcal/mol."}]}
    (trial / "agent" / "trajectory.json").write_text(json.dumps(atif))
    (trial / "result.json").write_text(json.dumps({
        "started_at": iso(T0 + 9), "finished_at": iso(T0 + 40),
        "environment_setup": {"started_at": iso(T0 + 9), "finished_at": iso(T0 + 17)},
        "agent_execution": {"started_at": iso(T0 + 18), "finished_at": iso(T0 + 38)},
        "agent_result": {"n_input_tokens": 21360, "n_cache_tokens": 19300, "n_output_tokens": 460,
                         "cost_usd": 0.0421},
        "agent_info": {"name": "claude-code", "model_info": {"name": "claude-opus-5-5"}},
        "verifier_result": {"rewards": {"reward": 1.0}}}))
    st["steps"]["card[0]"].update(status="ok", finished=T0 + 41, reward=1.0, output="Done.", environment="modal")
    st["steps"]["card"]["status"] = "ok"
    st.update(status="ok", finished=T0 + 42)
    (run_dir / "state.json").write_text(json.dumps(st))
    body = client.get(f"/api/runs/{rid}/events", params={"after": last}).json()
    tt = _types(body["events"])
    assert body["done"]
    assert not any(t in ("think", "read", "calc") for t, _ in tt), tt  # all seen live already
    done = next(e for e in body["events"] if e["title"].startswith("Finished card[0]"))
    assert done["cost"]["llm_usd"] == 0.0421 and done["cost"]["compute_usd"] > 0
    end = body["events"][-1]
    assert end["title"] == "Run finished" and end["cost"]["usd"] >= 0.0421
    allev = client.get(f"/api/runs/{rid}/events").json()["events"]
    assert [e["seq"] for e in allev] == list(range(1, len(allev) + 1))
    assert sum(1 for e in allev if e["type"] == "calc") == 1

    run = client.get(f"/api/runs/{rid}").json()
    c = run["costs"]
    assert c["by_step"]["card[0]"]["llm_usd"] == 0.0421
    assert c["by_step"]["card[0]"]["seconds"] == 31.0  # the trial's own start → end
    assert c["phases"]["setup"] == 8.0 and c["phases"]["work"] >= 20.0
    assert c["total_usd"] == round(c["llm_usd"] + c["compute_usd"], 6)
    assert run["live"] is True
    assert json.loads((run_dir / "costs.json").read_text())["total_usd"] == c["total_usd"]


def test_running_step_costs_grow_from_live_lines(env, client):
    import time
    run_dir, st, _ = make_agent_run(env, trajectory=False, result_json=False)
    now = time.time()
    st["steps"]["card[0]"].update(started=now - 60, environment="modal")
    st["started"] = now - 70
    (run_dir / "state.json").write_text(json.dumps(st))
    token = live.setup_run(run_dir)
    post(client, run_dir.name, "card[0]", token, stream_lines()[:5], times=[now - 30] * 5, sent=now)
    c1 = client.get(f"/api/runs/{run_dir.name}").json()["costs"]
    step = c1["by_step"]["card[0]"]
    assert step["llm_usd"] > 0 and step["compute_usd"] > 0 and step["seconds"] >= 59
    assert step["tokens"]["out"] == 300 + 120  # msg_1 counted once although it came on two lines
    assert c1["phases"]["setup"] >= 29 and c1["phases"]["work"] >= 29  # first live line splits setup / work
    assert c1["estimated"] is True  # no Claude Code total yet
    post(client, run_dir.name, "card[0]", token, stream_lines()[5:], offset=5, sent=time.time())
    c2 = client.get(f"/api/runs/{run_dir.name}").json()["costs"]
    assert c2["by_step"]["card[0]"]["llm_usd"] == 0.0421  # the result line's total_cost_usd wins
    assert c2["estimated"] is False
