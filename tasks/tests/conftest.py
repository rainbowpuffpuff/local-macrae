import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent


@pytest.fixture
def fake_harbor(tmp_path):
    """A `harbor` on PATH that records its calls; returns (env for subprocesses, path of the call log)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "harbor"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fake_harbor.py"}" "$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "harbor-calls.jsonl"
    home = tmp_path / "home"
    home.mkdir()
    (home / "settings.json").write_text('{"agent_image": ""}')  # no `docker image inspect` in tests
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("MODAL_", "AGENT_RUNNER_", "CLAUDE")) and k != "ANTHROPIC_API_KEY"}
    env.update(PATH=f"{bindir}{os.pathsep}{env.get('PATH', '')}", FAKE_HARBOR_LOG=str(log),
               AGENT_RUNNER_HOME=str(home), ANTHROPIC_API_KEY="sk-ant-api03-test", MACRAE_OFFLINE="1",
               PYTHONPATH=str(ROOT))
    return env, log
