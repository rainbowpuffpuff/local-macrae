"""Where the runner keeps things, and its settings.

Everything can be moved with environment variables, so the runner works the same on a laptop, a server or CI:

    AGENT_RUNNER_HOME   state: runs/, leases/, cooldowns.json, events.jsonl, settings.json
                        (default ~/.local/share/agent-runner)
    AGENT_RUNNER_JOBS   Harbor job folders = the traces (default $AGENT_RUNNER_HOME/jobs)
    AGENT_RUNNER_FLOWS  your flow files (default $AGENT_RUNNER_HOME/flows; examples ship in agent_runner/examples)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

HOME = Path.home()
DATA_DIR = Path(os.environ.get("AGENT_RUNNER_HOME") or HOME / ".local/share/agent-runner").expanduser()
RUNS_DIR = DATA_DIR / "runs"            # one folder per flow run: state.json, flow.yaml, logs/
LEASES_DIR = DATA_DIR / "leases"        # leases/<account>/<id> = pid holding a slot
COOLDOWN_FILE = DATA_DIR / "cooldowns.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULTS: dict[str, Any] = {
    "jobs_dir": os.environ.get("AGENT_RUNNER_JOBS") or str(DATA_DIR / "jobs"),
    "flows_dir": os.environ.get("AGENT_RUNNER_FLOWS") or str(DATA_DIR / "flows"),
    "max_concurrent": 8,                         # steps running at once per flow (default)
    "per_account": 2,                            # agent runs at once per Claude account
    "default_agent": "claude-code",
    "default_model": "",
    "agent_image": "agent-runner/agent-base:latest",  # Claude Code preinstalled (agent-runner image build)
}


def load_settings() -> dict[str, Any]:
    s = dict(DEFAULTS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text()))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s: dict[str, Any]) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2))
    tmp.replace(SETTINGS_FILE)


def jobs_dir() -> Path:
    return Path(load_settings()["jobs_dir"]).expanduser()


def flows_dir() -> Path:
    d = Path(load_settings()["flows_dir"]).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def trace_dirs() -> list[Path]:
    return [jobs_dir()]
