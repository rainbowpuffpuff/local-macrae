"""Both task flows end to end through the real engine, with a fake harbor standing in for Modal + Claude."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

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
    assert res["e_int_kcal_mol"] == -25.46
    summary = (run_dir / "result" / "summary.txt").read_text()
    assert "-25.5 kcal/mol" in summary and "2.20 Å" in summary
    argv = json.loads(log.read_text().splitlines()[0])["argv"]
    assert "Na+" in argv[argv.index("-i") + 1]
    assert argv[argv.index("--agent-timeout") + 1] == "2400"


def test_small_calc_is_a_research_run_with_notebook_and_manuscript(fake_harbor):
    env, log = fake_harbor
    r, state, run_dir = run(dict(env, FAKE_HARBOR_MODE="calc"), "tasks/flows/small-calc.yaml", "ion=Na+")
    assert r.stdout.strip().endswith("ok"), r.stdout + r.stderr
    # the agent got the protocol and the research question
    argv = json.loads(log.read_text().splitlines()[0])["argv"]
    instruction = argv[argv.index("-i") + 1]
    for needle in ("NOTES_TO_SELF.md", "results/manuscript.md", "ONLY with the Write and Edit tools", "**TBD**",
                   "$$...$$", "fig1.png", "never sleep longer than 15 s", "CAPABILITY_GAP: calc-image",
                   "/opt/calc/bin/python -c \"import pyscf, numpy, matplotlib\"", "ensurepip"):
        assert needle in instruction, needle
    assert instruction.rstrip().endswith("Use only numbers that exist in references.json.")  # evolve appends here
    sent = Path(argv[argv.index("-p") + 1])
    assert (sent / "PROTOCOL.md").is_file() and (sent / "results").is_dir()
    # the check ran the research protocol and passed, and said what it saw
    log_text = (run_dir / "logs" / "calc.log").read_text()
    assert "--research" in log_text and "edit stream: 1 Write + 9 Edit" in log_text
    # collect kept the manuscript's layout, so its relative figure link still resolves
    result = run_dir / "result"
    for rel in ("results/manuscript.md", "results/fig1.png", "NOTES_TO_SELF.md", "run_calc.py", "output.log"):
        assert (result / rel).is_file(), rel
    assert "](fig1.png)" in (result / "results" / "manuscript.md").read_text()
    out = json.loads((run_dir / "outputs" / "collect-a1.txt").read_text())
    assert out["manuscript"]["title"].startswith("How strongly does Na⁺ bind")
    assert out["manuscript"]["figures"] == ["fig1.png"] and out["manuscript"]["citations"] == [1, 2, 3, 4, 5]
    assert out["notes_to_self"].endswith("result/NOTES_TO_SELF.md")


def test_installed_calc_image_is_used_by_later_runs(fake_harbor):
    env, log = fake_harbor
    r, state, _ = run(dict(env, FAKE_HARBOR_MODE="calc"), "tasks/flows/small-calc.yaml",
                      "calc_image=ghcr.io/acalincarol/macrae-calc:2026-10-08")
    assert r.stdout.strip().endswith("ok"), r.stdout + r.stderr
    argv = json.loads(log.read_text().splitlines()[0])["argv"]
    assert argv[argv.index("--image") + 1] == "ghcr.io/acalincarol/macrae-calc:2026-10-08"
    assert "--task-template" not in argv  # the pinned image replaces Modal's Dockerfile build


def test_no_calc_image_means_the_default_environment(fake_harbor):
    env, log = fake_harbor
    run(dict(env, FAKE_HARBOR_MODE="calc"), "tasks/flows/small-calc.yaml")
    argv = json.loads(log.read_text().splitlines()[0])["argv"]
    assert "--image" not in argv and "--task-template" in argv


@pytest.mark.parametrize("mode,feedback", [
    ("calc-no-math", "no display equation"),
    ("calc-shell", "the manuscript was written from the shell"),
    ("calc-once", "written once and never revised"),
    ("calc-legacy", "results/manuscript.md is missing"),
])
def test_research_rules_are_enforced_with_feedback(fake_harbor, mode, feedback):
    env, log = fake_harbor
    r, state, _ = run(dict(env, FAKE_HARBOR_MODE=mode), "tasks/flows/small-calc.yaml")
    assert r.stdout.strip().endswith("failed")
    assert state["steps"]["calc"]["attempt"] == 2 and state["steps"]["collect"]["status"] == "skipped"
    second = json.loads(log.read_text().splitlines()[1])["argv"]
    assert feedback in second[second.index("-i") + 1]  # the check's output is the retry's feedback


def test_methods_card_keeps_a_notebook(fake_harbor):
    env, log = fake_harbor
    r, state, run_dir = run(env, "tasks/flows/methods-card.yaml")
    assert r.stdout.strip().endswith("ok"), r.stdout + r.stderr
    argv = json.loads(log.read_text().splitlines()[0])["argv"]
    assert "paper/NOTES_TO_SELF.md" in argv[argv.index("-i") + 1]
    assert (run_dir / "result" / "NOTES_TO_SELF.md").is_file()


BFF_LIKE_FLOW = """
name: bff-like
workdir: {root}
environment: modal
vars: {{python: python3, task_id: bff-demo, lessons: "", tools_dir: ""}}
steps:
  - id: prepare
    run: '"{{{{ vars.python }}}}" -m tasks.prepare_calc'
    outputs: auto
  - id: research
    description: Open the lab notebook and the manuscript (research protocol)
    run: >-
      "{{{{ vars.python }}}}" -m tasks.research prepare --dir "{{{{ prepare.output.dir }}}}"
      --references "{{{{ prepare.output.references_file }}}}" --task-id "{{{{ vars.task_id }}}}"
      --python /opt/bff/bin/python --packages bff
    outputs: auto
  - id: fit
    paths: ["{{{{ prepare.output.dir }}}}", "{{{{ vars.tools_dir }}}}"]
    instruction: >
      Do the BFF work.

      {{{{ research.output.protocol }}}}

      {{{{ vars.lessons }}}}
    check: >-
      python3 "{{{{ research.output.checker }}}}" research . --references "{{{{ prepare.output.references_file }}}}"
      --numbers e_int_kcal_mol
"""


def test_the_generic_research_step_hooks_any_flow(fake_harbor, tmp_path):
    """The three edits a BFF flow needs (tasks/V3_NOTES.md), through the real engine."""
    env, log = fake_harbor
    flow = tmp_path / "bff-like.yaml"
    flow.write_text(BFF_LIKE_FLOW.format(root=ROOT))
    r, _, run_dir = run(dict(env, FAKE_HARBOR_MODE="calc"), str(flow))
    assert r.stdout.strip().endswith("ok"), r.stdout + r.stderr + (run_dir / "logs" / "fit.log").read_text()
    argv = json.loads(log.read_text().splitlines()[0])["argv"]
    instruction = argv[argv.index("-i") + 1]
    assert "/opt/bff/bin/python -c \"import bff\"" in instruction and "results/manuscript.md" in instruction
    assert "Research protocol (bff-demo)" in (Path(argv[argv.index("-p") + 1]) / "PROTOCOL.md").read_text()
