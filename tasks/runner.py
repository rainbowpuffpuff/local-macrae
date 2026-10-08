"""Start predefined tasks (tasks/tasks.json) as agent_runner flows, in the background.

    from tasks import runner
    runner.list_tasks()                                   # [Task]
    run_id = runner.start("small-calc", {"ion": "K+"})    # returns at once; the flow runs in its own process
    runner.run_meta(run_id)                               # {"run_id", "task_id", "title", "inputs", ...}

CLI: python -m tasks.runner list | check | start TASK_ID [--input name=value ...]

Runs live where agent_runner keeps them ($AGENT_RUNNER_HOME/runs/<run_id>/state.json). Next to state.json this
module writes macrae.json with the task id, title and inputs; the same values are in state.json "vars"
(task_id, task_title, plus the inputs), so either file tells which task a run belongs to.

Errors: TaskNotFound (unknown id, a KeyError), TaskInputError (bad inputs, a ValueError), both TaskError.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TASKS_FILE = "tasks/tasks.json"
MAX_INPUT_LEN = 300
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class TaskError(Exception):
    pass


class TaskNotFound(TaskError, KeyError):
    def __str__(self) -> str:  # KeyError would print the repr
        return str(self.args[0]) if self.args else "task not found"


class TaskInputError(TaskError, ValueError):
    pass


# ── tasks.json ──────────────────────────────────────────────────────────────


def _resolve(p: str | Path) -> Path:
    p = Path(os.path.expanduser(str(p)))
    if p.is_absolute():
        return p
    return p.resolve() if p.exists() else REPO_ROOT / p


def tasks_file() -> Path:
    return _resolve(os.environ.get("MACRAE_TASKS_FILE") or DEFAULT_TASKS_FILE)


def list_tasks() -> list[dict]:
    """Tasks in file order. The file is {"tasks": [...]} (a bare list works too). Missing/broken file → []."""
    try:
        data = json.loads(tasks_file().read_text())
    except (OSError, ValueError):
        return []
    items = data.get("tasks") if isinstance(data, dict) else data
    return [dict(t) for t in items or [] if isinstance(t, dict) and t.get("id")]


def get_task(task_id: str) -> dict:
    for t in list_tasks():
        if t["id"] == task_id:
            return t
    raise TaskNotFound(f"no task {task_id!r}; known: {', '.join(t['id'] for t in list_tasks()) or 'none'}")


def flow_path(task: dict) -> Path:
    if not task.get("flow"):
        raise TaskError(f"task {task.get('id')!r} has no flow")
    p = _resolve(task["flow"])
    if not p.is_file():
        raise TaskError(f"flow file for task {task['id']!r} not found: {p}")
    return p


def resolve_inputs(task: dict, inputs: Optional[dict] = None) -> dict[str, str]:
    """Defaults + given values, validated: known names only, short single-line strings, `options` / `pattern`."""
    if inputs is not None and not isinstance(inputs, dict):
        raise TaskInputError("inputs must be an object")
    spec = {i["name"]: i for i in task.get("inputs") or [] if isinstance(i, dict) and i.get("name")}
    unknown = sorted(set(inputs or {}) - set(spec))
    if unknown:
        raise TaskInputError(f"unknown input(s) {unknown} for task {task['id']!r}; expected {sorted(spec)}")
    out: dict[str, str] = {}
    for name, s in spec.items():
        v = (inputs or {}).get(name)
        if v is None or (isinstance(v, str) and not v.strip()):
            v = s.get("default")
        if v is None or v == "":
            raise TaskInputError(f"input {name!r} is required")
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            raise TaskInputError(f"input {name!r} must be text")
        v = str(v).strip()
        if len(v) > MAX_INPUT_LEN or CONTROL_RE.search(v):
            raise TaskInputError(f"input {name!r} must be one line of at most {MAX_INPUT_LEN} characters")
        opts = [str(o) for o in s.get("options") or []]
        if opts:
            match = next((o for o in opts if o.lower() == v.lower()), None)
            if match is None:
                raise TaskInputError(f"input {name!r} must be one of {opts}, got {v!r}")
            v = match
        if s.get("pattern") and not re.fullmatch(s["pattern"], v):
            raise TaskInputError(f"input {name!r} has an invalid value {v!r}")
        out[name] = v
    return out


# ── starting runs ───────────────────────────────────────────────────────────


def _flows():
    try:
        from agent_runner import flows
    except ImportError:  # running from a checkout without `pip install -e .`
        sys.path.insert(0, str(REPO_ROOT))
        from agent_runner import flows
    return flows


def _cli_value(v: Any) -> str:
    """agent_runner passes vars as `--var k=v` and JSON-decodes v; keep strings strings ("5" stays "5")."""
    if not isinstance(v, str):
        return json.dumps(v)
    try:
        json.loads(v)
    except ValueError:
        return v
    return json.dumps(v)


def build_vars(task: dict, inputs: Optional[dict] = None) -> dict[str, str]:
    vars_ = resolve_inputs(task, inputs)
    vars_.update(task_id=task["id"], task_title=str(task.get("title") or task["id"]), python=sys.executable)
    env = os.environ.get("MACRAE_HARBOR_ENV", "").strip().lower()
    if env:  # e.g. docker for local development; flows default to modal
        vars_["environment"] = env
    return vars_


def start(task_id: str, inputs: Optional[dict] = None) -> str:
    """Validate, then start the task's flow detached (agent_runner.flows.start_detached). Returns the run id."""
    task = get_task(task_id)
    path = flow_path(task)
    vars_ = build_vars(task, inputs)
    flows = _flows()
    try:
        run_id = flows.start_detached(path, {k: _cli_value(v) for k, v in vars_.items()})
    except flows.FlowError as e:
        raise TaskError(f"flow for task {task['id']!r} is invalid: {e}") from e
    meta = {"run_id": run_id, "task_id": task["id"], "title": task.get("title") or task["id"],
            "inputs": {k: vars_[k] for k in resolve_inputs(task, inputs)}, "flow": str(path), "started": time.time()}
    try:
        (flows.RUNS_DIR / run_id / "macrae.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    except OSError:
        pass  # the same facts are in state.json vars
    return run_id


def run_meta(run_id: str) -> Optional[dict]:
    """Task id, title and inputs of a run started here (macrae.json, else state.json vars). None if unknown."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id or ""):
        return None
    d = _flows().RUNS_DIR / run_id
    for name in ("macrae.json", "state.json"):
        try:
            data = json.loads((d / name).read_text())
        except (OSError, ValueError):
            continue
        if name == "macrae.json":
            return data
        v = data.get("vars") or {}
        if v.get("task_id"):
            return {"run_id": run_id, "task_id": v["task_id"], "title": v.get("task_title") or v["task_id"],
                    "inputs": {k: x for k, x in v.items() if k not in ("task_id", "task_title", "python",
                                                                        "environment")},
                    "flow": data.get("file", ""), "started": data.get("started")}
    return None


# ── CLI ─────────────────────────────────────────────────────────────────────


def check_all() -> list[str]:
    """Problems with tasks.json and every task's flow (empty = all good)."""
    flows = _flows()
    problems = []
    tasks = list_tasks()
    if not tasks:
        problems.append(f"no tasks in {tasks_file()}")
    seen = set()
    for t in tasks:
        tid = t["id"]
        if tid in seen:
            problems.append(f"duplicate task id {tid}")
        seen.add(tid)
        for k in ("title", "subtitle", "icon", "prompt", "flow"):
            if not isinstance(t.get(k), str) or not t[k].strip():
                problems.append(f"{tid}: missing {k}")
        try:
            resolve_inputs(t, {})
            spec, _ = flows.load_flow(flow_path(t))
            _, _, ps = flows.analyze(spec)
            problems += [f"{tid}: {p}" for p in ps]
            declared = set(spec.get("vars") or {})
            missing = [i["name"] for i in t.get("inputs") or [] if i.get("name") not in declared]
            if missing:
                problems.append(f"{tid}: inputs {missing} are not vars of {t['flow']}")
        except (TaskError, flows.FlowError) as e:
            problems.append(f"{tid}: {e}")
    return problems


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m tasks.runner", description="macrae tasks")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("check")
    s = sub.add_parser("start")
    s.add_argument("task_id")
    s.add_argument("--input", action="append", default=[], help="name=value")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        print(json.dumps({"tasks": list_tasks()}, indent=1, ensure_ascii=False))
        return 0
    if a.cmd == "check":
        problems = check_all()
        for p in problems:
            print("✗", p)
        if not problems:
            print(f"ok: {len(list_tasks())} tasks in {tasks_file()}")
        return 1 if problems else 0
    try:
        print(start(a.task_id, dict(x.split("=", 1) for x in a.input if "=" in x)))
    except TaskError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
