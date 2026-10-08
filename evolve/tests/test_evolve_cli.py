"""python -m evolve …, as the owner and the integrator will use it."""
import json
import os
import subprocess
import sys
from pathlib import Path

from evolve.tests.trace_fixtures import A, C

ROOT = Path(__file__).resolve().parents[2]


def cli(home, *args, check=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MACRAE_", "AGENT_RUNNER_"))
           and k != "ANTHROPIC_API_KEY"}
    env.update(AGENT_RUNNER_HOME=str(home), PYTHONPATH=str(ROOT))
    r = subprocess.run([sys.executable, "-m", "evolve", *args], env=env, capture_output=True, text=True,
                       timeout=120, cwd=str(ROOT))
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def test_cli_end_to_end(traces, tmp_path):
    home, _ = traces
    out = json.loads(cli(home, "distill", A).stdout)
    assert out[A] and out[A][0]["task_id"] == "small-calc"
    pending = json.loads(cli(home, "distill").stdout)
    assert A not in pending and C in pending
    assert cli(home, "context", "small-calc").stdout.startswith("LESSONS FROM EARLIER RUNS")
    v = json.loads(cli(home, "context", "small-calc", "--vars").stdout)
    assert v["tools_dir"].endswith("tools/small-calc")
    assert "(no lessons for nope yet)" in cli(home, "context", "nope").stdout
    m = json.loads(cli(home, "metrics").stdout)
    assert len(m["by_task"]["small-calc"]) == 2
    assert "trajectories" in cli(home, "export", str(tmp_path / "ds")).stdout
    assert (tmp_path / "ds" / "sft.jsonl").is_file()
    added = json.loads(cli(home, "add", "bff-charges", "Use 20k MCMC steps; 100k does not fit in 30 min.",
                           "--kind", "setting").stdout)
    assert added["source"] == "manual"
    listing = cli(home, "lessons", "bff-charges").stdout
    assert added["id"] in listing and "setting" in listing
    assert json.loads(cli(home, "hints", "bff-charges").stdout)["suggested_hardware"] == "cpu-8"
    assert cli(home, "check-flows").stdout.startswith("ok: 2 flow(s)")
    r = cli(home, "distill", "no-such-run", check=False)
    assert r.returncode == 2 and "no run" in r.stderr


def test_cli_installs_sample_runs_for_a_demo(home, tmp_path):
    demo = tmp_path / "demo"
    r = cli(home, "fixtures", str(demo))
    assert len(r.stdout.splitlines()) == 4 and (demo / "runs" / A / "state.json").is_file()
    assert len(json.loads(cli(demo, "distill").stdout)) == 4
