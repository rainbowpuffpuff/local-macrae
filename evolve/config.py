"""Where evolve keeps things. Read from the environment at call time, so tests and restarts see changes.

    AGENT_RUNNER_HOME      the runner's home (default ~/.local/share/agent-runner); runs are in runs/<id>/
    MACRAE_EVOLVE_HOME     evolve's own folder (default $AGENT_RUNNER_HOME/evolve):
                             lessons.jsonl           one lesson per line (the contract's record + bookkeeping)
                             tools/<task_id>/        scripts from passing runs + manifest.json
                             distilled.json          run_id → what distill produced (makes distill idempotent)
                             distill/<run_id>.json   what the distiller read and answered, with its token cost
    MACRAE_SERVER_DATA     the backend's bookkeeping (default $AGENT_RUNNER_HOME/macrae): events/<run_id>.jsonl
    MACRAE_EVOLVE_MODEL    model for distill (default claude-opus-5-5)
    MACRAE_EVOLVE_LLM      0/off = never call Claude (rule-based extractor only)
    MACRAE_EVOLVE_TOOLS    0/off = don't hand earlier scripts to new runs (tools_dir stays "")
    ANTHROPIC_API_KEY      enables the Claude call; without it distill uses the rules
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_MODEL = "claude-opus-5-5"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _off(name: str) -> bool:
    return _env(name).lower() in ("0", "off", "false", "no")


def runner_home() -> Path:
    """Same rule as agent_runner.config.DATA_DIR."""
    return Path(_env("AGENT_RUNNER_HOME") or Path.home() / ".local/share/agent-runner").expanduser()


def runs_dir() -> Path:
    return runner_home() / "runs"


def home() -> Path:
    return Path(_env("MACRAE_EVOLVE_HOME") or runner_home() / "evolve").expanduser()


def lessons_file() -> Path:
    return home() / "lessons.jsonl"


def tools_root() -> Path:
    return home() / "tools"


def server_data_dir() -> Path:
    return Path(_env("MACRAE_SERVER_DATA") or runner_home() / "macrae").expanduser()


def model() -> str:
    return _env("MACRAE_EVOLVE_MODEL", DEFAULT_MODEL)


def llm_enabled() -> bool:
    return not _off("MACRAE_EVOLVE_LLM") and bool(_env("ANTHROPIC_API_KEY"))


def tools_enabled() -> bool:
    return not _off("MACRAE_EVOLVE_TOOLS")
