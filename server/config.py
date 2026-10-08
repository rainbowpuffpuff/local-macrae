"""Backend settings, read from the environment at call time (so tests and restarts see changes).

Contract variables: MACRAE_TOOL_SECRET, MACRAE_PAPERS_DIR, MACRAE_INDEX_DIR, MACRAE_TASKS_FILE, MODAL_PROFILE,
MODAL_TOKEN_ID/MODAL_TOKEN_SECRET, AGENT_RUNNER_HOME. Server-only extras (documented in server/NOTES.md):
MACRAE_AUTH_DISABLED=1 (local dev without a secret), MACRAE_CORS_ORIGINS (comma list), MACRAE_SERVER_DATA.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _repo_path(value: str) -> Path:
    """A relative path is looked up in the working directory first, then the repo root."""
    p = Path(value).expanduser()
    if p.is_absolute():
        return p
    cwd = Path.cwd() / p
    return cwd if cwd.exists() else REPO_ROOT / p


def tool_secret() -> str:
    return _env("MACRAE_TOOL_SECRET")


def auth_disabled() -> bool:
    return _env("MACRAE_AUTH_DISABLED").lower() in ("1", "true", "yes")


def papers_dir() -> Path:
    return _repo_path(_env("MACRAE_PAPERS_DIR", "papers"))


def index_dir() -> Path:
    return _repo_path(_env("MACRAE_INDEX_DIR", "index"))


def tasks_file() -> Path:
    return _repo_path(_env("MACRAE_TASKS_FILE", "tasks/tasks.json"))


def publications_file() -> Path:
    return REPO_ROOT / "data" / "group_publications.json"


def runner_home() -> Path:
    """Same rule as agent_runner.config.DATA_DIR."""
    return Path(_env("AGENT_RUNNER_HOME") or Path.home() / ".local/share/agent-runner").expanduser()


def runs_dir() -> Path:
    return runner_home() / "runs"


def server_data_dir() -> Path:
    """Our own bookkeeping (run → task, trace event logs). Kept out of runs/ and jobs/, which are the runner's record."""
    return Path(_env("MACRAE_SERVER_DATA") or runner_home() / "macrae").expanduser()


def draining() -> bool:
    """deploy/start.py creates $MACRAE_DRAIN_FILE when the container is stopping (Cloudflare sleep/rollout/restart)."""
    path = _env("MACRAE_DRAIN_FILE")
    return bool(path) and Path(path).exists()


def cors_origins() -> list[str]:
    return [o.strip() for o in _env("MACRAE_CORS_ORIGINS").split(",") if o.strip()]


def modal_profile() -> str:
    return _env("MODAL_PROFILE", "acalincarol")


def modal_configured() -> bool:
    """Token pair in the environment, or the profile present in ~/.modal.toml (`modal token set --profile …`)."""
    if _env("MODAL_TOKEN_ID") and _env("MODAL_TOKEN_SECRET"):
        return True
    cfg = Path(_env("MODAL_CONFIG_PATH") or Path.home() / ".modal.toml").expanduser()
    try:
        text = cfg.read_text()
    except OSError:
        return False
    try:
        import tomllib  # py ≥ 3.11
        data = tomllib.loads(text)
        prof = data.get(modal_profile())
        return isinstance(prof, dict) and bool(prof.get("token_id")) and bool(prof.get("token_secret"))
    except Exception:
        return f"[{modal_profile()}]" in text


def read_json(path: Path) -> Optional[object]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None
