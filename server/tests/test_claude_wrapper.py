"""The Modal image's claude wrapper (tasks/common/claude + claude_live.py) against a fake `claude` that prints
stream-json, and the real backend served by uvicorn: stdout passes through byte for byte, the exit code is kept,
lines reach /api/live while the CLI is still running, and forwarding failures never break the run."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import ROOT, make_agent_run
from test_live import stream_lines

from server import live

WRAPPER = ROOT / "tasks" / "common" / "claude_live.py"
SHIM = ROOT / "tasks" / "common" / "claude"

FAKE_CLAUDE = r'''#!{python}
import json, os, sys, time
args = sys.argv[1:]
if "--version" in args:
    print("2.1.0 (Claude Code)")
    sys.exit(0)
instruction = sys.stdin.read()
lines = json.loads(os.environ["FAKE_LINES"])
pause = float(os.environ.get("FAKE_PAUSE", "0"))
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({{"args": args, "stdin": instruction}}) + "\n")
for i, ln in enumerate(lines):
    sys.stdout.write(ln + "\n")
    sys.stdout.flush()
    if i == int(os.environ.get("FAKE_HOLD_AT", "-1")):
        # wait until the backend has the first lines: proves forwarding happens while we run
        deadline = time.time() + 15
        while time.time() < deadline and not os.path.exists(os.environ["FAKE_GO"]):
            time.sleep(0.05)
    time.sleep(pause)
sys.stderr.write("some stderr noise\n")
sys.exit(int(os.environ.get("FAKE_RC", "0")))
'''


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(env):
    """The real app on a real port (the wrapper speaks plain HTTP to it)."""
    import uvicorn

    from server.app import app
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    assert srv.started
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)


@pytest.fixture
def fake_claude(tmp_path):
    p = tmp_path / "bin" / "claude-real"
    p.parent.mkdir()
    p.write_text(FAKE_CLAUDE.format(python=sys.executable))
    p.chmod(0o755)
    return p


def wrapper_env(fake, tmp_path, url="", token="", run_id="", step="card[0]", **extra):
    lines = [json.dumps(x) for x in stream_lines()]
    env = {k: v for k, v in os.environ.items() if not k.startswith("MACRAE_")}
    env.update(MACRAE_CLAUDE_REAL=str(fake), FAKE_LINES=json.dumps(lines), FAKE_LOG=str(tmp_path / "fake.log"),
               FAKE_GO=str(tmp_path / "go"), MACRAE_LIVE_DEBUG=str(tmp_path / "live-debug.log"))
    if url:
        env.update(MACRAE_LIVE_URL=url, MACRAE_LIVE_TOKEN=token, MACRAE_RUN_ID=run_id, MACRAE_STEP=step)
    env.update({k: str(v) for k, v in extra.items()})
    return env, lines


ARGS = ["--verbose", "--output-format=stream-json", "--print"]


def run_wrapper(env, args=ARGS, stdin="Write the card.", timeout=60):
    return subprocess.run([sys.executable, "-I", str(WRAPPER)] + args, input=stdin.encode(), env=env,
                          capture_output=True, timeout=timeout)


def test_forwards_live_while_passing_stdout_through(env, server, fake_claude, tmp_path):
    run_dir, _, _ = make_agent_run(env, trajectory=False, result_json=False)
    token = live.setup_run(run_dir)
    wenv, lines = wrapper_env(fake_claude, tmp_path, server, token, run_dir.name, FAKE_HOLD_AT=2, FAKE_RC=3)
    proc = subprocess.Popen([sys.executable, "-I", str(WRAPPER)] + ARGS, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=wenv)
    proc.stdin.write(b"Write the card.")
    proc.stdin.close()
    proc.stdin = None  # communicate() below must not touch it
    # while the fake CLI holds after its 3rd line, the backend must already have them (≤ 1 s batching)
    deadline = time.time() + 10
    got = []
    while time.time() < deadline:
        got = [d for _, d in sum(live.streams(run_dir, "card[0]").values(), [])]
        if len(got) >= 3:
            break
        time.sleep(0.1)
    assert len(got) >= 3, (tmp_path / "live-debug.log").read_text() if (tmp_path / "live-debug.log").exists() else ""
    assert proc.poll() is None  # still running: this was live
    (tmp_path / "go").touch()
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 3  # the CLI's exit code, unchanged
    assert out == ("\n".join(lines) + "\n").encode()  # stdout byte for byte (Harbor tees it to claude-code.txt)
    assert err == b"some stderr noise\n"  # nothing of ours on stderr
    calls = [json.loads(x) for x in (tmp_path / "fake.log").read_text().splitlines()]
    assert calls == [{"args": ARGS, "stdin": "Write the card."}]  # args and stdin reach the real CLI
    stored = sum(live.streams(run_dir, "card[0]").values(), [])
    assert [d for _, d in stored] == [json.loads(x) for x in lines]  # all of it, once, in order


def test_events_show_up_from_the_wrapper(env, server, fake_claude, tmp_path, client):
    run_dir, _, _ = make_agent_run(env, trajectory=False, result_json=False)
    token = live.setup_run(run_dir)
    wenv, _ = wrapper_env(fake_claude, tmp_path, server, token, run_dir.name)
    r = run_wrapper(wenv)
    assert r.returncode == 0
    evs = client.get(f"/api/runs/{run_dir.name}/events").json()["events"]
    types = [e["type"] for e in evs if e["step"] == "card[0]"]
    assert "think" in types and "read" in types and "calc" in types


def test_backend_down_or_refusing_never_breaks_the_run(env, fake_claude, tmp_path):
    port = _free_port()  # nothing listens here
    wenv, lines = wrapper_env(fake_claude, tmp_path, f"http://127.0.0.1:{port}", "tok", "r1", FAKE_RC=0)
    t0 = time.time()
    r = run_wrapper(wenv)
    assert r.returncode == 0 and r.stdout == ("\n".join(lines) + "\n").encode()
    assert time.time() - t0 < 15  # the tail delivery gives up quickly
    # a backend that refuses (wrong token → 401): forwarding stops, the run goes on
    wenv, lines = wrapper_env(fake_claude, tmp_path, "http://127.0.0.1:9", "tok", "r1", FAKE_RC=1)
    r = run_wrapper(wenv)
    assert r.returncode == 1 and r.stdout.count(b"\n") == len(lines)


def test_refused_token_stops_forwarding(env, server, fake_claude, tmp_path):
    run_dir, _, _ = make_agent_run(env, trajectory=False, result_json=False)
    live.setup_run(run_dir)
    wenv, lines = wrapper_env(fake_claude, tmp_path, server, "not-the-token", run_dir.name, FAKE_PAUSE=0.05)
    r = run_wrapper(wenv)
    assert r.returncode == 0 and r.stdout.count(b"\n") == len(lines)
    assert live.streams(run_dir, "card[0]") == {}
    assert "post returned 401" in (tmp_path / "live-debug.log").read_text()


def test_non_stream_calls_and_missing_env_go_straight_to_the_real_cli(fake_claude, tmp_path):
    wenv, _ = wrapper_env(fake_claude, tmp_path, "http://127.0.0.1:9", "tok", "r1")
    r = run_wrapper(wenv, args=["--version"])
    assert r.returncode == 0 and r.stdout == b"2.1.0 (Claude Code)\n"
    wenv, lines = wrapper_env(fake_claude, tmp_path)  # no MACRAE_LIVE_*: plain pass-through
    r = run_wrapper(wenv)
    assert r.returncode == 0 and r.stdout.count(b"\n") == len(lines)
    assert not (tmp_path / "live-debug.log").exists()


def test_shell_shim_falls_back_without_python_or_env(fake_claude, tmp_path):
    wenv, lines = wrapper_env(fake_claude, tmp_path)
    r = subprocess.run(["sh", str(SHIM)] + ARGS, input=b"x", env=wenv, capture_output=True, timeout=30)
    assert r.returncode == 0 and r.stdout.count(b"\n") == len(lines)
    # env set but the forwarder isn't installed at /usr/local/lib/macrae (as on this test machine): still runs
    wenv, _ = wrapper_env(fake_claude, tmp_path, "http://127.0.0.1:9", "tok", "r1")
    r = subprocess.run(["sh", str(SHIM), "--version"], env=wenv, capture_output=True, timeout=30)
    assert r.returncode == 0 and r.stdout == b"2.1.0 (Claude Code)\n"


def test_wrapper_unit_pieces():
    sys.path.insert(0, str(WRAPPER.parent))
    try:
        import claude_live as cl
    finally:
        sys.path.pop(0)
    assert cl.wants_stream(["--output-format", "stream-json", "--print"])
    assert cl.wants_stream(["--output-format=stream-json"])
    assert not cl.wants_stream(["--output-format", "json"]) and not cl.wants_stream(["--version"])
    assert cl.live_config({"MACRAE_LIVE_URL": "https://x.dev/", "MACRAE_LIVE_TOKEN": "t", "MACRAE_RUN_ID": "r 1",
                           "MACRAE_STEP": "card[0]"}) == ("https://x.dev/api/live/r%201/card%5B0%5D", "t")
    assert cl.live_config({"MACRAE_LIVE_URL": "ftp://x", "MACRAE_LIVE_TOKEN": "t", "MACRAE_RUN_ID": "r",
                           "MACRAE_STEP": "s"}) is None
    stub = json.loads(cl._stub(json.dumps({"type": "user", "x": "y" * 50})))
    assert stub["type"] == "macrae_truncated" and stub["orig_type"] == "user"

    # batching, retry with the same offset, permanent stop
    posted, answers = [], [0, 500, 200, 200, 401]

    def fake_post(body):
        posted.append(json.loads(body))
        return answers.pop(0) if answers else 200

    cl.INTERVAL = 0.05
    f = cl.Forwarder("http://x", "t", post=fake_post)
    for i in range(3):
        f.add(json.dumps({"i": i}).encode() + b"\n")
    time.sleep(0.6)
    f.add(b'{"i": 3}\n')
    f.add(b"\n")  # blank lines are not forwarded
    time.sleep(0.6)
    f.close(2)
    offsets = [(p["offset"], len(p["lines"])) for p in posted]
    assert offsets[0][0] == 0 and offsets[1][0] == 0  # 0 = connection error, 500: same batch again
    assert all(p["stream"] == f.stream for p in posted)
    # delivered on the 3rd try; a retry carries the lines added meanwhile, still from offset 0
    assert posted[2]["offset"] == 0 and [json.loads(x)["i"] for x in posted[2]["lines"]][:3] == [0, 1, 2]
    delivered = []
    for q in posted[2:]:
        if q["offset"] == len(delivered):
            delivered += [json.loads(x)["i"] for x in q["lines"]]
    assert delivered == [0, 1, 2, 3] and f.sent >= 4
