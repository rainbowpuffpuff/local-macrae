import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent
ENV_KEYS = ("AGENT_RUNNER_HOME", "MACRAE_EVOLVE_HOME", "MACRAE_SERVER_DATA", "MACRAE_EVOLVE_LLM", "MACRAE_EVOLVE_TOOLS",
            "MACRAE_EVOLVE_MODEL", "ANTHROPIC_API_KEY")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An empty AGENT_RUNNER_HOME; no Claude key, so distill uses the rules unless a test sets one."""
    h = tmp_path / "home"
    (h / "runs").mkdir(parents=True)
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AGENT_RUNNER_HOME", str(h))
    return h


@pytest.fixture
def traces(home):
    """home with the four fixture runs installed; returns (home, {run_id: start})."""
    from evolve.tests.trace_fixtures import install
    return home, install(home)


@pytest.fixture
def fake_harbor(tmp_path, home):
    """A `harbor` on PATH that plays an agent (see fake_harbor_evolve.py); returns (env, call log)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "harbor"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fake_harbor_evolve.py"}" "$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "harbor-calls.jsonl"
    (home / "settings.json").write_text('{"agent_image": ""}')
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("MODAL_", "AGENT_RUNNER_", "CLAUDE", "MACRAE_")) and k != "ANTHROPIC_API_KEY"}
    env.update(PATH=f"{bindir}{os.pathsep}{env.get('PATH', '')}", FAKE_HARBOR_LOG=str(log),
               AGENT_RUNNER_HOME=str(home), ANTHROPIC_API_KEY="sk-ant-api03-test", MACRAE_OFFLINE="1",
               PYTHONPATH=str(ROOT))
    return env, log
