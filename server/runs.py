"""Runs, read from agent_runner's state: $AGENT_RUNNER_HOME/runs/<id>/state.json (+ engine.log, logs/).

We never write under runs/ or jobs/ (they are the runner's record). Which task a run belongs to is kept in our own
sidecar, $MACRAE_SERVER_DATA/runs/<id>.json, written when the server starts a task.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from . import catalog, config

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
TERMINAL = {"ok", "failed", "cancelled"}
SCAN_MAX = 300  # run folders read per listing
STARTUP_GRACE = 120  # seconds a run dir may exist without state.json before we call it crashed
PLANNING_GRACE = 300  # …longer while the planner is still deciding (plan.json status "planning")


def valid_run_id(run_id: str) -> bool:
    return bool(RUN_ID_RE.match(run_id or "")) and ".." not in run_id


def run_dir(run_id: str) -> Path:
    return config.runs_dir() / run_id


# ── sidecar: run → task ──────────────────────────────────────────────────────


def _sidecar_path(run_id: str) -> Path:
    return config.server_data_dir() / "runs" / f"{run_id}.json"


def save_sidecar(run_id: str, task: dict, inputs: dict) -> None:
    p = _sidecar_path(run_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"run_id": run_id, "task_id": task["id"], "title": task.get("title") or task["id"],
                                   "inputs": inputs, "created": time.time()}, default=str))
        tmp.replace(p)
    except OSError:
        pass  # bookkeeping only; we can still match the run to its task by flow file


def load_sidecar(run_id: str) -> dict:
    d = config.read_json(_sidecar_path(run_id))
    return d if isinstance(d, dict) else {}


# ── state ───────────────────────────────────────────────────────────────────


def pid_alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, ValueError, TypeError, OverflowError):
        return False
    except PermissionError:
        return True


def _tail(path: Path, n: int = 800) -> str:
    try:
        return path.read_bytes()[-n:].decode(errors="replace")
    except OSError:
        return ""


def _planning(d: Path) -> bool:
    """The server is still planning this run (or just finished), so no state.json yet is fine."""
    p = config.read_json(d / "plan.json")
    if not isinstance(p, dict):
        return False
    now = time.time()
    if p.get("status") == "planning":
        return now - float(p.get("started") or 0) < PLANNING_GRACE
    return now - float(p.get("finished") or 0) < STARTUP_GRACE


def read_state(run_id: str) -> Optional[dict]:
    """state.json with liveness fixed up, like agent_runner.flows.list_runs. None if the run doesn't exist."""
    if not valid_run_id(run_id):
        return None
    d = run_dir(run_id)
    if not d.is_dir():
        return None
    st = config.read_json(d / "state.json")
    if not isinstance(st, dict):
        try:
            mtime = d.stat().st_mtime
        except OSError:
            return None
        st = {"id": run_id, "name": run_id, "status": "starting", "started": mtime, "finished": None,
              "steps": {}, "order": [], "_synthetic": True}
        if time.time() - mtime > STARTUP_GRACE and not _planning(d):
            st["status"] = "crashed"
            st["error"] = _tail(d / "engine.log")
    if st.get("status") == "running" and st.get("pid") is not None and not pid_alive(st.get("pid")):
        st["status"] = "crashed"
        st.setdefault("error", "engine process is gone: " + _tail(d / "engine.log", 400))
    st.setdefault("id", run_id)
    st["_dir"] = str(d)
    return st


def public_status(status: str) -> str:
    """running|ok|failed|cancelled, as the contract says."""
    s = (status or "").lower()
    if s in TERMINAL:
        return s
    if s in ("crashed", "error"):
        return "failed"
    return "running"  # running, starting, unknown


def step_list(state: dict) -> list[dict]:
    steps = state.get("steps") or {}
    order = [k for k in state.get("order") or [] if k in steps] + [k for k in steps if k not in (state.get("order") or [])]
    out = []
    for k in order:
        s = steps[k] or {}
        out.append({
            "key": k, "kind": s.get("kind") or "", "status": s.get("status") or "",
            "account": s.get("account") or "", "reward": s.get("reward"), "attempt": s.get("attempt") or 0,
            "error": s.get("error") or "", "started": s.get("started"), "finished": s.get("finished"),
            "note": s.get("note") or "", "fanout": s.get("fanout"),
        })
    return out


def summarize(state: dict, with_extra: bool = False) -> dict:
    run_id = str(state.get("id"))
    side = load_sidecar(run_id)
    task = catalog.find_task(side["task_id"]) if side.get("task_id") else catalog.task_for_run(state)
    task_id = side.get("task_id") or (task["id"] if task else "")
    title = (task or {}).get("title") or side.get("title") or str(state.get("name") or run_id)
    out = {"run_id": run_id, "task_id": task_id, "title": title,
           "status": public_status(str(state.get("status") or "")),
           "started": state.get("started"), "finished": state.get("finished"), "steps": step_list(state)}
    if with_extra:
        out.update({"flow": state.get("name") or "", "inputs": side.get("inputs") or state.get("vars") or {},
                    "error": str(state.get("error") or "")[:1000], "engine_status": state.get("status") or ""})
    return out


def list_runs(limit: int = 50) -> list[dict]:
    root = config.runs_dir()
    if not root.is_dir():
        return []
    entries = []
    for d in root.iterdir():
        if not d.is_dir() or d.name.startswith("_") or not valid_run_id(d.name):
            continue
        entries.append(d)
    if len(entries) > SCAN_MAX:  # many old runs: only look at the most recently touched ones
        entries = sorted(entries, key=lambda d: d.stat().st_mtime if d.exists() else 0, reverse=True)[:SCAN_MAX]
    states = [s for s in (read_state(d.name) for d in entries) if s]
    states.sort(key=lambda s: float(s.get("started") or 0), reverse=True)
    return [summarize(s) for s in states[:limit]]


def get_run(run_id: str) -> Optional[dict]:
    st = read_state(run_id)
    return summarize(st, with_extra=True) if st else None


def latest_run_id() -> Optional[str]:
    runs = list_runs(limit=1)
    return runs[0]["run_id"] if runs else None
