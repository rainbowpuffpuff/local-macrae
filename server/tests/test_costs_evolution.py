"""v2 cost tables, the evolve bridge (/api/evolution, distill once per finished run), and the agent_runner env
pass-through that gives the agent its live URL and token (Harbor --ae)."""

import json
import os
import site
import subprocess
import sys
import textwrap
import time
import types
from pathlib import Path

import pytest
from conftest import ROOT, write_state

from server import costs, evolution, live


def test_price_lookup_and_llm_cost():
    assert costs.price_key("claude-opus-5-5") == ("claude-opus-5-5", True)
    assert costs.price_key("claude-sonnet-4-5-20250929") == ("claude-sonnet-4-5", True)
    assert costs.price_key("anthropic.claude-haiku-4-5") == ("claude-haiku-4-5", True)
    assert costs.price_key("claude-opus-5-5[1m]") == ("claude-opus-5-5", True)
    assert costs.price_key("some-future-opus") == ("claude-opus-5-5", False)
    assert costs.price_key("") == (costs.UNKNOWN_MODEL, False)
    tk = costs.norm_usage({"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                           "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 2_000_000,
                           "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000}})
    # Opus 5.5: $4 in, $20 out, $0.20 cache read, $5 5-min write, 1-hour write = 2 × input
    assert costs.llm_usd("claude-opus-5-5", tk) == pytest.approx(4 + 20 + 0.2 + 5 + 8)
    assert costs.event_cost(1.5, tk) == {"usd": 1.5, "tokens": {"in": 1_000_000, "out": 1_000_000,
                                                                "cache": 3_000_000}}


def test_sessions_count_each_message_once_and_prefer_claude_codes_total():
    u = {"input_tokens": 100, "output_tokens": 10}
    lines = [(1.0, {"type": "system", "subtype": "init", "session_id": "a", "model": "claude-sonnet-5-5"}),
             (2.0, {"type": "assistant", "session_id": "a", "message": {"id": "m1", "usage": u, "content": []}}),
             (2.5, {"type": "assistant", "session_id": "a", "message": {"id": "m1", "usage": u, "content": []}}),
             (3.0, {"type": "assistant", "session_id": "b", "message": {"id": "m9", "usage": u, "content": [],
                                                                         "model": "claude-haiku-5-5"}})]
    s = costs.sessions_from_lines(lines)
    assert set(s) == {"a", "b"} and s["a"].tokens()["in"] == 100 and s["a"].first_t == 1.0
    assert s["a"].usd() == (pytest.approx((100 * 2 + 10 * 10) / 1e6), False)
    s["a"].feed({"type": "result", "total_cost_usd": 0.5, "usage": {"input_tokens": 7}})
    assert s["a"].usd() == (0.5, True) and s["a"].tokens()["in"] == 7


def test_compute_rates_and_override(monkeypatch):
    # Modal Sandbox rates (modal.com/pricing, 2026-10-08): $0.00003942/core/s, $0.00000667/GiB/s; A10G $0.000306/s
    cpu2 = 2 * 0.00003942 + 4 * 0.00000667
    assert costs.hardware_rate("cpu-2") == pytest.approx(cpu2)
    assert costs.hardware_rate("gpu-a10g") == pytest.approx(4 * 0.00003942 + 16 * 0.00000667 + 0.000306)
    assert costs.hardware_rate("nonsense") == pytest.approx(cpu2)
    monkeypatch.setenv("MACRAE_COMPUTE_RATES", '{"cpu_core_s": 0.001, "gpu_s": {"a10g": 1}}')
    assert costs.hardware_rate("cpu-2") == pytest.approx(0.002 + 4 * 0.00000667)
    assert costs.hardware_rate("gpu-a10g") > 1
    opts = {o["id"]: o for o in costs.hardware_options()}
    assert opts["gpu-h100"]["gpu"] == "h100" and opts["cpu-8"]["usd_per_hour"] > 0


def test_script_steps_and_local_docker_cost_no_compute(env):
    run_dir = env.runs / "r-costs"
    now = time.time()
    st = write_state(run_dir, status="ok", started=now - 100, finished=now, steps={
        "prep": {"kind": "run", "status": "ok", "started": now - 100, "finished": now - 90},
        "agent": {"kind": "agent", "status": "ok", "started": now - 90, "finished": now - 10,
                  "environment": "docker"}}, order=["prep", "agent"])
    st["_dir"] = str(run_dir)
    c = costs.run_costs(st, {"started": now - 110, "finished": now - 100, "model": "claude-sonnet-5-5",
                             "usage": {"input_tokens": 1000, "output_tokens": 100}, "cost_usd": 0.003,
                             "hardware": "gpu-h100"})
    assert c["compute_usd"] == 0.0 and c["by_step"]["agent"]["hardware"] == "local"
    assert c["by_step"]["prep"]["seconds"] == 10.0 and c["by_step"]["agent"]["seconds"] == 80.0
    assert c["llm_usd"] == 0.003 and c["phases"]["plan"] == 10.0 and c["wall_s"] == 110.0
    assert c["tokens"]["in"] == 1000 and c["hardware"] == "gpu-h100"


# ── evolution ───────────────────────────────────────────────────────────────


def test_evolution_route_without_and_with_evolve(env, client, monkeypatch):
    monkeypatch.setitem(sys.modules, "evolve", None)
    r = client.get("/api/evolution")
    assert r.status_code == 200 and r.json()["by_task"] == {} and r.json()["available"] is False
    evolve = types.ModuleType("evolve")
    evolve.metrics = lambda: {"by_task": {"small-calc": [{"run_id": "r1", "ok": True, "reward": 1.0, "wall_s": 300,
                                                          "total_usd": 0.42, "lessons_used": 2}]}}
    monkeypatch.setitem(sys.modules, "evolve", evolve)
    body = client.get("/api/evolution").json()
    assert body["available"] is True and body["by_task"]["small-calc"][0]["total_usd"] == 0.42
    assert client.get("/api/evolution", headers={"X-Macrae-Secret": "no"}).status_code == 401


def test_finished_run_is_distilled_once_with_costs_on_disk(env, client, fake_modules, monkeypatch):
    calls = []
    evolve = types.ModuleType("evolve")

    def distill(run_dir):
        assert (Path(run_dir) / "costs.json").is_file()  # costs are there for the distiller to read
        calls.append(Path(run_dir).name)
        return [{"lesson": "x"}]
    evolve.distill = distill
    monkeypatch.setitem(sys.modules, "evolve", evolve)
    run_id = client.post("/api/tasks/small-calc/start").json()["run_id"]
    write_state(env.runs / run_id, status="ok", finished=time.time(), steps={}, order=[])
    client.get(f"/api/runs/{run_id}/events")
    deadline = time.time() + 5
    while not evolution.distilled(run_id) and time.time() < deadline:
        time.sleep(0.05)
    assert calls == [run_id]
    client.get(f"/api/runs/{run_id}/events")
    assert evolution.sweep() == 0 and not evolution.on_run_end(run_id)
    assert calls == [run_id]
    marker = json.loads((env.tmp / "server-data" / "evolve" / f"{run_id}.json").read_text())
    assert marker["lessons"] == 1


def test_lessons_never_break_a_start(monkeypatch):
    evolve = types.ModuleType("evolve")

    def bad(task_id, k=8):
        raise RuntimeError("lessons.jsonl is corrupt")
    evolve.context = bad
    monkeypatch.setitem(sys.modules, "evolve", evolve)
    assert evolution.lessons("small-calc") == ""
    monkeypatch.setitem(sys.modules, "evolve", None)
    assert evolution.lessons("small-calc") == ""


# ── agent_runner: live env reaches Harbor as --ae, the token never reaches the log ────────────

FAKE_HARBOR = """#!{python}
import json, os, sys
with open(os.environ["FAKE_HARBOR_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(1)
"""

AGENT_FLOW = """
name: live-env
environment: modal
defaults: {{account: none}}
steps:
  - id: card
    instruction: Write it.
    path: {path}
"""


def test_engine_passes_live_env_to_harbor_and_masks_the_token(env, tmp_path, monkeypatch):
    from agent_runner import harbor
    run_dir = tmp_path / "run"
    assert harbor.live_agent_env(run_dir, "r", "s") == {}
    token = live.setup_run(run_dir, "https://macrae.example.dev/")
    assert harbor.live_agent_env(run_dir, "r1", "card[0]") == {
        "MACRAE_LIVE_URL": "https://macrae.example.dev", "MACRAE_LIVE_TOKEN": token, "MACRAE_RUN_ID": "r1",
        "MACRAE_STEP": "card[0]"}
    monkeypatch.setenv("MACRAE_LIVE_URL", "https://override.dev")
    assert harbor.live_agent_env(run_dir, "r1", "s")["MACRAE_LIVE_URL"] == "https://override.dev"
    monkeypatch.delenv("MACRAE_LIVE_URL")
    cmd = harbor.exec_cmd(paths=["/x"], instruction="i", agent="claude-code", jobs_dir=tmp_path, job_name="j",
                          agent_env={"A": "1", "MACRAE_LIVE_TOKEN": "s3cret"})
    assert cmd[-4:] == ["--ae", "A=1", "--ae", "MACRAE_LIVE_TOKEN=s3cret"]
    assert "s3cret" not in " ".join(harbor.mask_agent_env(cmd))
    assert harbor.run_cmd(agent="a", jobs_dir=tmp_path, job_name="j", agent_env={"B": "2"})[-2:] == ["--ae", "B=2"]
    assert "--ae" not in harbor.exec_cmd(paths=[], instruction="i", agent="a", jobs_dir=tmp_path, job_name="j")

    # the real engine, a fake harbor on PATH that records its argv
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "harbor").write_text(FAKE_HARBOR.format(python=sys.executable))
    (bin_dir / "harbor").chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    flow = tmp_path / "flow.yaml"
    flow.write_text(textwrap.dedent(AGENT_FLOW.format(path=work)))
    rid = "20261008-120000-live-env-abcd"
    rdir = env.runs / rid
    tok = live.setup_run(rdir, "https://macrae.example.dev")
    penv = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", PYTHONPATH=str(ROOT),
                FAKE_HARBOR_LOG=str(tmp_path / "harbor.log"), HOME=str(tmp_path),
                PYTHONUSERBASE=site.getuserbase())  # a new HOME must not hide pip --user packages (pyyaml)
    r = subprocess.run([sys.executable, "-m", "agent_runner", "flow", "run", str(flow), "--run-id", rid],
                       env=penv, capture_output=True, text=True, timeout=60, cwd=str(tmp_path))
    argv = json.loads((tmp_path / "harbor.log").read_text().splitlines()[0])
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "--ae"]
    assert pairs == ["MACRAE_LIVE_URL=https://macrae.example.dev", f"MACRAE_LIVE_TOKEN={tok}",
                     f"MACRAE_RUN_ID={rid}", "MACRAE_STEP=card"], (r.stdout, r.stderr)
    log = (rdir / "logs" / "card.log").read_text()
    assert "MACRAE_LIVE_TOKEN=***" in log and tok not in log


# ── the Worker forwards /api/live/* (cloudflare/worker.js, run on node) ──────

WORKER_HARNESS = r"""
import { proxy, containerEnv } from %(worker)s;
const calls = [];
globalThis.fetch = async (url, init) => {
  calls.push({ url: String(url), method: init.method, headers: Object.fromEntries(new Headers(init.headers)),
               body: init.body ? new TextDecoder().decode(init.body) : null });
  return new Response('{"ok":true}', { status: 200, headers: { "content-type": "application/json" } });
};
const env = { BACKEND_URL: "http://backend.test", MACRAE_TOOL_SECRET: "sek" };
const out = {};
const live = (init, path = "/api/live/r1/card%%5B0%%5D") =>
  proxy(new Request("https://macrae.dev" + path, init), env, new URL("https://macrae.dev" + path));
let r = await live({ method: "POST", headers: { "x-macrae-live": "tok", "content-type": "application/json",
                     "x-macrae-secret": "forged" }, body: '{"lines":[]}' });
out.ok = { status: r.status, call: calls.at(-1) };
r = await live({ method: "POST", body: "{}" });
out.noToken = r.status;
r = await live({ method: "GET" });
out.get = r.status;
r = await live({ method: "POST", headers: { "x-macrae-live": "t" }, body: "{}" }, "/api/live/r1");
out.badPath = r.status;
r = await live({ method: "POST", headers: { "x-macrae-live": "t" }, body: "x".repeat(4 * 1024 * 1024 + 1) });
out.big = r.status;
const nLive = calls.length;
r = await proxy(new Request("https://macrae.dev/api/runs"), env, new URL("https://macrae.dev/api/runs"));
out.runs = calls.at(-1);
out.nLive = nLive;
out.env = containerEnv({ MACRAE_LIVE_URL: "https://x.dev", MACRAE_PLANNER_MODEL: "claude-opus-5-5", OTHER: "no" });
console.log(JSON.stringify(out));
"""


def test_worker_forwards_live_posts_without_the_secret(tmp_path):
    import shutil
    if not shutil.which("node"):
        pytest.skip("node not installed")
    worker = ROOT / "cloudflare" / "worker.js"
    harness = tmp_path / "harness.mjs"
    harness.write_text(WORKER_HARNESS % {"worker": json.dumps(worker.as_uri())})
    r = subprocess.run(["node", str(harness)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    ok = out["ok"]
    assert ok["status"] == 200 and ok["call"]["url"] == "http://backend.test/api/live/r1/card%5B0%5D"
    h = ok["call"]["headers"]
    assert h["x-macrae-live"] == "tok" and "x-macrae-secret" not in h and ok["call"]["body"] == '{"lines":[]}'
    assert out["noToken"] == 401 and out["get"] == 405 and out["badPath"] == 404 and out["big"] == 413
    assert out["nLive"] == 1  # refused requests never reached the backend
    assert out["runs"]["headers"]["x-macrae-secret"] == "sek"
    assert out["runs"]["headers"]["x-macrae-origin"] == "https://macrae.dev"
    assert out["env"]["MACRAE_LIVE_URL"] == "https://x.dev" and out["env"]["MACRAE_PLANNER_MODEL"] == \
        "claude-opus-5-5" and "OTHER" not in out["env"]
