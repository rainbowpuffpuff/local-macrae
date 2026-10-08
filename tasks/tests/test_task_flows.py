"""Both task flows end to end through the real engine, with a fake harbor standing in for Modal + Claude."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(env, flow, *vars_):
    cmd = [sys.executable, "-m", "agent_runner", "flow", "run", str(ROOT / flow), "--var", f"python={sys.executable}"]
    for v in vars_:
        cmd += ["--var", v]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=240, cwd=str(ROOT))
    st = next((Path(env["AGENT_RUNNER_HOME"]) / "runs").glob("*/state.json"))
    return r, json.loads(st.read_text()), st.parent


def test_methods_card_flow_runs_on_modal_and_publishes_the_card(fake_harbor):
    env, log = fake_harbor
    r, state, run_dir = run(env, "tasks/flows/methods-card.yaml")
    assert r.stdout.strip().endswith("ok"), r.stdout + r.stderr + (run_dir / "logs" / "card.log").read_text()
    steps = state["steps"]
    assert [steps[k]["status"] for k in ("sources", "card", "collect")] == ["ok", "ok", "ok"]
    assert steps["card"]["reward"] == 1.0 and steps["card"]["environment"] == "modal"
    assert state["vars"]["task_id"] == "methods-card"
    card = run_dir / "result" / "methods-card.md"
    assert card.is_file() and "[1]" in card.read_text()
    out = json.loads((run_dir / "outputs" / "collect-a1.txt").read_text())
    assert out["summary"].startswith("Wrote 'Methods card")
    call = json.loads(log.read_text().splitlines()[0])
    assert call["argv"][call["argv"].index("-e") + 1] == "modal"
    assert "--task-template" in call["argv"]
    instruction = call["argv"][call["argv"].index("-i") + 1]
    assert "Bayesian Learning for Accurate and Robust Biomolecular Force Fields" in instruction


def test_methods_card_retries_when_claims_are_uncited(fake_harbor):
    env, log = fake_harbor
    env = dict(env, FAKE_HARBOR_MODE="bad-card")
    r, state, _ = run(env, "tasks/flows/methods-card.yaml")
    assert r.stdout.strip().endswith("failed")
    assert state["steps"]["card"]["status"] == "failed" and state["steps"]["card"]["attempt"] == 2
    assert state["steps"]["collect"]["status"] == "skipped"
    second = json.loads(log.read_text().splitlines()[1])["argv"]
    retry_instruction = second[second.index("-i") + 1]
    assert "without an [n] citation" in retry_instruction  # check output fed back to the agent


def test_unknown_doi_fails_in_the_script_step(fake_harbor):
    env, log = fake_harbor
    r, state, _ = run(env, "tasks/flows/methods-card.yaml", "doi=10.9999/not.a.group.paper")
    assert state["steps"]["sources"]["status"] == "failed"
    assert "not in data/group_publications.json" in state["steps"]["sources"]["error"]
    assert not log.exists()  # no agent started


def test_small_calc_flow_checks_numbers_and_summarizes(fake_harbor):
    env, log = fake_harbor
    env = dict(env, FAKE_HARBOR_MODE="calc")
    r, state, run_dir = run(env, "tasks/flows/small-calc.yaml", "ion=Na+")
    assert r.stdout.strip().endswith("ok"), r.stdout + r.stderr + (run_dir / "logs" / "calc.log").read_text()
    assert state["steps"]["calc"]["reward"] == 1.0 and state["steps"]["calc"]["environment"] == "modal"
    res = json.loads((run_dir / "result" / "result.json").read_text())
    assert res["e_int_kcal_mol"] == -23.4
    summary = (run_dir / "result" / "summary.txt").read_text()
    assert "-23.4 kcal/mol" in summary and "2.23 Å" in summary
    argv = json.loads(log.read_text().splitlines()[0])["argv"]
    assert "Na+" in argv[argv.index("-i") + 1]
    assert argv[argv.index("--agent-timeout") + 1] == "2400"
