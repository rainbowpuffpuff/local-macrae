import json

from conftest import T0, TRAJECTORY, make_agent_run, write_state

from server import events, runs, trace

STREAM = [
    {"type": "system", "subtype": "init", "session_id": "s1"},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "I'll start by reading the paper."}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Read",
                                                   "input": {"file_path": "/app/paper/paper.json"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                              "content": [{"type": "text", "text": "{\"doi\": \"x\"}"}]}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_2", "name": "Bash",
                                                   "input": {"command": "xtb dimer.xyz --gfn 2",
                                                             "description": "Run water dimer energy"}}]}},
]


def types_titles(evs):
    return [(e["type"], e["title"]) for e in evs]


def test_parse_atif():
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "trajectory.json"
        p.write_text(json.dumps(TRAJECTORY))
        tr = trace.parse_atif(p)
    kinds = [m.kind for m in tr.msgs]
    assert kinds == ["instruction", "text", "call", "call", "call", "text"]
    bash = tr.msgs[3].call
    assert bash.name == "Bash" and bash.output == "TOTAL ENERGY -10.123456 Eh"
    assert abs(bash.seconds - 0.4) < 1e-6


def test_atif_message_as_content_parts_and_unmatched_observation():
    from pathlib import Path
    import tempfile
    traj = {"steps": [{"source": "agent", "message": [{"type": "text", "text": "Thinking about ions."}],
                       "tool_calls": [{"tool_call_id": "a", "function_name": "Bash", "arguments": "{\"command\": \"ls\"}"}],
                       "observation": {"results": [{"content": "file.txt"}]}}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.json"
        p.write_text(json.dumps(traj))
        tr = trace.parse_atif(p)
    assert tr.msgs[0].text == "Thinking about ions."
    assert tr.msgs[1].call.output == "file.txt"  # paired by position


def test_full_run_events_follow_the_mapping(env):
    make_agent_run(env, status="ok", step_status="ok")
    res = events.poll("20261008-100000-methods-card-ab12")
    evs = res["events"]
    assert res["done"] is True
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    tt = types_titles(evs)
    assert tt[0] == ("status", "Started Methods card")
    assert ("search", "Searched papers for “glycosaminoglycan methods”") in tt
    assert ("read", "Read paper/paper.json") in tt
    assert ("calc", "Ran water dimer energy (xtb, 0.4 s)") in tt
    assert ("write", "Wrote paper/card.md") in tt
    assert ("think", "I'll start by reading the paper.") in tt
    assert ("result", "Finished card[0] (reward 1)") in tt
    assert tt[-1] == ("result", "Run finished")
    cites = [e for e in evs if e["type"] == "cite"]
    assert cites and cites[0]["citation"]["doi"] == "10.1093/glycob/cwag064"
    assert cites[0]["step"] == "passages"
    started = next(e for e in evs if e["title"].startswith("Started Claude Code"))
    assert started["title"] == "Started Claude Code on Modal, account alice"
    assert "Write a cited methods card" in started["detail"]
    read = next(e for e in evs if e["type"] == "read")
    assert read["citation"]["doi"] == "10.1093/glycob/cwag064"
    for e in evs:
        base = {"seq", "t", "step", "type", "title", "detail", "citation"}
        assert base <= set(e) <= base | {"cost", "elapsed_s", "plan"}  # v2 adds optional cost / elapsed_s
        assert len(e["detail"]) <= 600


def test_after_returns_only_new_events_and_seq_is_stable(env):
    run_dir, st, trial = make_agent_run(env, trajectory=False, stream_lines=STREAM)
    rid = run_dir.name
    first = events.poll(rid)["events"]
    assert not events.poll(rid)["done"]
    titles = [e["title"] for e in first]
    assert "Read paper/paper.json" in titles
    # the Bash call has no output yet: it is held back (its output decides calc vs status)
    assert not any(e["type"] == "calc" for e in first)
    n = first[-1]["seq"]
    assert events.poll(rid, after=n)["events"] == []

    # trial finishes: the trajectory appears, the step and run end
    (trial / "agent" / "trajectory.json").write_text(json.dumps(TRAJECTORY))
    st["steps"]["card[0]"].update(status="ok", finished=T0 + 15, reward=1.0, output="Done.")
    st["steps"]["card"]["status"] = "ok"
    st.update(status="ok", finished=T0 + 16)
    (run_dir / "state.json").write_text(json.dumps(st))
    second = events.poll(rid, after=n)
    new = second["events"]
    assert second["done"]
    assert [e["seq"] for e in new] == list(range(n + 1, n + 1 + len(new)))
    new_titles = [e["title"] for e in new]
    # nothing seen live is repeated; what's new is the calc, the write, the final message and the endings
    assert "Read paper/paper.json" not in new_titles
    assert "I'll start by reading the paper." not in new_titles
    assert "Ran water dimer energy (xtb, 0.4 s)" in new_titles
    assert "Wrote paper/card.md" in new_titles
    assert new_titles[-1] == "Run finished"
    # the full log is the union, in seq order
    allev = events.poll(rid)["events"]
    assert allev[:len(first)] == first and allev[len(first):] == new


def test_event_log_survives_restart(env):
    run_dir, *_ = make_agent_run(env, status="ok", step_status="ok")
    a = events.poll(run_dir.name)["events"]
    events.reset_cache()
    b = events.poll(run_dir.name)["events"]
    assert a == b


def test_failed_step_and_check_and_retry(env):
    run_dir = env.runs / "r-fail"
    write_state(run_dir, status="failed", finished=T0 + 50, error="",
                steps={"calc": {"key": "calc", "id": "calc", "kind": "agent", "status": "failed", "attempt": 2,
                                "started": T0, "finished": T0 + 50, "error": "until not met: reward >= 1"}},
                order=["calc"])
    (run_dir / "logs").mkdir()
    from conftest import hms
    (run_dir / "logs" / "calc.log").write_text(
        f"[{hms(T0 + 1)}] harbor (bob): harbor exec -a claude-code --job-name calc-a1 -i do it -p x --no-scan\n"
        f"[{hms(T0 + 20)}] check exit 1: result.json has no numbers\nmore detail\n"
        f"[{hms(T0 + 20)}] attempt 1: failed reward=0.0 passed=False\n")
    evs = events.poll("r-fail")["events"]
    tt = types_titles(evs)
    assert ("status", "Started Claude Code on Docker, account bob") in tt
    chk = next(e for e in evs if e["title"] == "Check failed (exit 1)")
    assert chk["type"] == "error" and "more detail" in chk["detail"]
    assert ("status", "Attempt 1 did not pass (failed, reward 0.0)") in tt
    assert ("error", "calc failed") in tt
    assert tt[-1] == ("error", "Run failed")


def test_crashed_engine_counts_as_failed(env):
    write_state(env.runs / "r-dead", status="running", pid=2 ** 22 + 12345)
    assert runs.get_run("r-dead")["status"] == "failed"
    assert events.poll("r-dead")["done"]


def test_starting_run_without_state_is_running(env):
    (env.runs / "r-new").mkdir()
    r = runs.get_run("r-new")
    assert r["status"] == "running" and r["steps"] == []
    assert events.poll("r-new") == {"events": [], "done": False}
    write_state(env.runs / "r-new", name="my-flow", file="")
    assert events.poll("r-new")["events"][0]["title"] == "Started my-flow"


def test_job_dirs_include_retries_and_reroutes(env):
    jobs = env.jobs / "r1"
    for n in ("card-0-a1", "card-0-a2-r1", "card-0-a2", "card-1-a1", "card-0-a10"):
        (jobs / n).mkdir(parents=True)
    st = {"jobs_dir": str(jobs)}
    got = [p.name for p in trace.job_dirs_for(st, "card[0]", {})]
    assert got == ["card-0-a1", "card-0-a2", "card-0-a2-r1", "card-0-a10"]


def test_sanitize_matches_agent_runner():
    from agent_runner.flows import _sanitize
    for k in ("card[0]", "fix[12]", "a b/c", "x" * 80, "[]"):
        assert trace.sanitize(k) == _sanitize(k)


def test_trial_exception_is_an_error_event(env):
    run_dir, st, trial = make_agent_run(env, status="failed", step_status="failed", trajectory=False)
    (trial / "result.json").write_text(json.dumps({"finished_at": "2026-10-08T10:00:00Z", "exception_info": {
        "exception_type": "AgentTimeoutError", "exception_message": "agent timed out after 600 s"}}))
    evs = events.poll(run_dir.name)["events"]
    err = next(e for e in evs if e["title"].startswith("Agent run failed"))
    assert err["type"] == "error" and "AgentTimeoutError" in err["title"]
