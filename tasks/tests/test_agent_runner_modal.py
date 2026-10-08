"""agent_runner Modal support: the harbor commands it builds, the env it exports, a full flow through a fake harbor."""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

from agent_runner import flows, harbor

ROOT = Path(__file__).resolve().parents[2]


def _flag(cmd, name):
    return cmd[cmd.index(name) + 1] if name in cmd else None


def test_exec_cmd_modal_adds_env_and_template():
    cmd = harbor.exec_cmd(paths=["/data/paper"], instruction="go", agent="claude-code", jobs_dir=Path("/j"),
                          job_name="card-a1", environment="modal", task_template="/run/modal-template")
    assert cmd[1] == "exec"
    assert _flag(cmd, "-e") == "modal"
    assert _flag(cmd, "--task-template") == "/run/modal-template"
    assert "--image" not in cmd
    assert _flag(cmd, "-p") == "/data/paper" and _flag(cmd, "-i") == "go"


def test_exec_and_run_cmd_default_to_docker_without_flags():
    before = harbor.exec_cmd(paths=["/p"], instruction="go", agent="claude-code", jobs_dir=Path("/j"),
                             job_name="x", image="agent-runner/agent-base:latest")
    assert "-e" not in before and "--task-template" not in before
    assert _flag(before, "--image") == "agent-runner/agent-base:latest"
    assert harbor.exec_cmd(paths=["/p"], instruction="go", agent="a", jobs_dir=Path("/j"), job_name="x",
                           environment="docker") == harbor.exec_cmd(paths=["/p"], instruction="go", agent="a",
                                                                    jobs_dir=Path("/j"), job_name="x")
    run = harbor.run_cmd(task_path="/t", agent="claude-code", jobs_dir=Path("/j"), job_name="b")
    assert "-e" not in run
    run = harbor.run_cmd(task_path="/t", agent="claude-code", jobs_dir=Path("/j"), job_name="b", environment="modal")
    assert run[1] == "run" and _flag(run, "-e") == "modal" and _flag(run, "-p") == "/t"


def test_modal_env_profile_defaults_and_token_login():
    assert harbor.modal_env({})["MODAL_PROFILE"] == "acalincarol"
    assert harbor.modal_env({"MODAL_PROFILE": "lab"})["MODAL_PROFILE"] == "lab"
    tok = harbor.modal_env({"MODAL_TOKEN_ID": "ak-1", "MODAL_TOKEN_SECRET": "as-1"})
    assert "MODAL_PROFILE" not in tok


def test_modal_template_copies_the_agent_dockerfile(tmp_path):
    t = harbor.modal_template(tmp_path / "tpl")
    df = t / "environment" / "Dockerfile"
    assert df.read_text() == (ROOT / "agent_runner" / "image" / "Dockerfile").read_text()
    assert harbor.modal_template(tmp_path / "tpl") == t  # idempotent
    assert harbor.IMAGE_DIR == ROOT / "agent_runner" / "image"


def test_environment_key_is_valid_at_step_and_flow_level():
    spec, _ = flows.load_flow(textwrap.dedent("""
        name: t
        environment: modal
        steps:
          - {id: a, instruction: hi, environment: docker}
    """))
    assert flows.analyze(spec)[2] == []


def _run_flow(env, flow: Path, *vars_):
    cmd = [sys.executable, "-m", "agent_runner", "flow", "run", str(flow)]
    for v in vars_:
        cmd += ["--var", v]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180, cwd=str(ROOT))
    state = json.loads(next((Path(env["AGENT_RUNNER_HOME"]) / "runs").glob("*/state.json")).read_text())
    return r, state


def _calls(log: Path):
    return [json.loads(ln) for ln in log.read_text().splitlines()]


def test_flow_level_modal_runs_harbor_with_modal_flags(tmp_path, fake_harbor):
    env, log = fake_harbor
    (tmp_path / "in").mkdir()
    flow = tmp_path / "f.yaml"
    flow.write_text(textwrap.dedent(f"""
        name: modal-test
        environment: modal
        steps:
          - id: a
            path: {tmp_path / 'in'}
            instruction: write things
          - id: b
            environment: docker
            path: {tmp_path / 'in'}
            instruction: write things
    """))
    r, state = _run_flow(env, flow)
    assert r.stdout.strip().endswith("ok"), r.stdout + r.stderr
    calls = {c["argv"][_i(c["argv"], "--job-name")]: c for c in _calls(log)}
    modal, docker = calls["a-a1"], calls["b-a1"]
    assert _flag(modal["argv"], "-e") == "modal"
    tpl = _flag(modal["argv"], "--task-template")
    assert tpl == str(Path(env["AGENT_RUNNER_HOME"]) / "runs" / state["id"] / "modal-template")
    assert modal["template_dockerfile"] == (ROOT / "agent_runner/image/Dockerfile").read_text()
    assert "--image" not in modal["argv"]
    assert modal["env"]["MODAL_PROFILE"] == "acalincarol"
    assert "-e" not in docker["argv"] and "--task-template" not in docker["argv"]
    assert docker["env"]["MODAL_PROFILE"] is None
    assert state["steps"]["a"]["environment"] == "modal" and state["steps"]["b"]["environment"] == "docker"


def test_registry_image_replaces_the_template(tmp_path, fake_harbor):
    env, log = fake_harbor
    env = dict(env, AGENT_RUNNER_MODAL_IMAGE="ghcr.io/lab/agent-base:latest", MODAL_PROFILE="lab")
    (tmp_path / "in").mkdir()
    flow = tmp_path / "f.yaml"
    flow.write_text(f"name: t\nsteps:\n  - {{id: a, environment: modal, path: {tmp_path / 'in'}, instruction: x}}\n")
    r, _ = _run_flow(env, flow)
    assert r.returncode == 0, r.stdout + r.stderr
    (call,) = _calls(log)
    assert _flag(call["argv"], "--image") == "ghcr.io/lab/agent-base:latest"
    assert "--task-template" not in call["argv"]
    assert call["env"]["MODAL_PROFILE"] == "lab"


def _i(argv, name):
    return argv.index(name) + 1


def test_a_rate_limited_attempt_never_passes_its_until(tmp_path, fake_harbor):
    """The w4 bug: an agent that hit the session limit left partial work that scored reward 1, and `until: reward >= 1`
    marked the step ok. An attempt that errored must stay failed, whatever `until` says."""
    env, log = fake_harbor
    env = dict(env, FAKE_HARBOR_MODE="limit")
    (tmp_path / "in").mkdir()
    flow = tmp_path / "f.yaml"
    flow.write_text(textwrap.dedent(f"""
        name: limit-test
        steps:
          - id: build
            path: {tmp_path / 'in'}
            instruction: write things
            retry: {{max: 1, until: "reward >= 1"}}
    """))
    r, state = _run_flow(env, flow)
    assert r.stdout.strip().endswith("failed"), r.stdout + r.stderr
    st = state["steps"]["build"]
    assert st["status"] == "failed" and st["attempt"] == 2 and "ApiRateLimitError" in st["error"]
