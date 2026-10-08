"""Starting a task run (v2): make the run id, the live token and the plan, then start the flow engine.

    run_id = launch.start(task, inputs, origin)       # returns at once

1. Validate with tasks.runner (get_task, flow_path, build_vars: same rules as runner.start) and agent_runner.
2. Make the run id the way agent_runner does, create <run>/, write <run>/live/token (+ url) and macrae.json.
3. In a background thread: the planner (planner.py, with evolve's lessons) writes <run>/plan.json, then the engine
   starts exactly like agent_runner.flows.start_detached (`python -m agent_runner flow run FLOW --run-id ID --var …`),
   with the task's vars plus `lessons`, `plan`, `hardware`, `budget_usd`, `plan_why` and the planner's params.

We launch the engine ourselves instead of calling tasks.runner.start because that accepts only the task's declared
inputs and makes its own run id, and the plan, lessons and token have to exist before the engine reads them.
A tasks.runner without build_vars (an older or stub module) gets the v1 path: plan synchronously, runner.start.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import bridges, config, evolution, live, planner

log = logging.getLogger("macrae.server")


def _cli_value(v: Any) -> str:
    """agent_runner JSON-decodes `--var k=v`; keep strings strings (same as tasks.runner._cli_value)."""
    if not isinstance(v, str):
        return json.dumps(v)
    try:
        json.loads(v)
    except ValueError:
        return v
    return json.dumps(v)


def _flows() -> Any:
    return bridges._import("agent_runner.flows")


def _sanitize(s: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")[:60] or "step"


def new_run_id(flow_name: str) -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{_sanitize(flow_name)}-{uuid.uuid4().hex[:4]}"


def engine_command(flow_file: Path, run_id: str, vars_: dict[str, Any]) -> list[str]:
    cmd = [sys.executable, "-m", "agent_runner", "flow", "run", str(flow_file), "--run-id", run_id]
    for k, v in vars_.items():
        cmd += ["--var", f"{k}={_cli_value(v)}"]
    return cmd


def spawn_engine(flow_file: Path, run_id: str, vars_: dict[str, Any]) -> subprocess.Popen:
    """Like agent_runner.flows.start_detached, for a run id we already made."""
    run_dir = config.runs_dir() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["PYTHONPATH"] = os.pathsep.join(x for x in (str(config.REPO_ROOT), env.get("PYTHONPATH", "")) if x)
    with open(run_dir / "engine.log", "ab") as logf:
        return subprocess.Popen(engine_command(flow_file, run_id, vars_), stdout=logf, stderr=logf,
                                stdin=subprocess.DEVNULL, env=env, start_new_session=True, cwd=str(flow_file.parent))


def _fail_run(run_dir: Path, run_id: str, name: str, error: str) -> None:
    """The engine never started: leave a failed state.json so the page says why at once."""
    now = time.time()
    st = {"id": run_id, "name": name, "status": "failed", "started": now, "finished": now, "error": error,
          "steps": {}, "order": [], "vars": {}}
    try:
        (run_dir / "state.json").write_text(json.dumps(st))
        with open(run_dir / "engine.log", "a") as f:
            f.write(error + "\n")
    except OSError:
        pass


def _plan_and_start(run_dir: Path, run_id: str, task: dict, inputs: dict, flow_file: Path, base_vars: dict,
                    flow_name: str) -> None:
    try:
        lessons = evolution.lessons(task["id"])
        rec = planner.make_plan(task, inputs, flow_file, lessons, run_dir=run_dir)
        vars_ = dict(base_vars)
        vars_["lessons"] = lessons
        vars_.update(planner.flow_vars(rec))
        spawn_engine(flow_file, run_id, vars_)
        log.info("run %s: plan %s (%s), engine started", run_id, rec.get("status"), rec.get("hardware"))
    except Exception as e:
        log.exception("run %s could not start", run_id)
        _fail_run(run_dir, run_id, flow_name, f"could not start the flow engine: {type(e).__name__}: {e}")


def start(task: dict, inputs: dict, origin: Optional[str] = None, wait: bool = False) -> str:
    """Start the task (inputs already checked by app._resolve_inputs). Raises bridges.Unavailable, KeyError,
    ValueError or FileNotFoundError like tasks.runner.start."""
    runner = bridges.runner_module()
    if not all(hasattr(runner, f) for f in ("get_task", "flow_path", "build_vars")):
        return _start_v1(runner, task, inputs)
    rtask = runner.get_task(task["id"])
    flow_file = Path(runner.flow_path(rtask))
    base_vars = dict(runner.build_vars(rtask, inputs))
    flows = _flows()
    spec, _ = flows.load_flow(flow_file)
    _, _, problems = flows.analyze(spec)
    if problems:
        raise ValueError(f"flow for task {task['id']!r} is invalid: {'; '.join(problems)}")
    name = str(spec.get("name") or flow_file.stem)
    run_id = new_run_id(name)
    run_dir = config.runs_dir() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    url = live.public_url(origin)
    live.setup_run(run_dir, url)
    meta = {"run_id": run_id, "task_id": task["id"], "title": task.get("title") or task["id"],
            "inputs": {k: base_vars.get(k, v) for k, v in inputs.items()}, "flow": str(flow_file),
            "started": time.time(), "live_url": url}
    try:
        (run_dir / "macrae.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    except OSError:
        pass
    args = (run_dir, run_id, dict(rtask, **{k: task[k] for k in ("hardware", "budget_usd") if k in task}),
            inputs, flow_file, base_vars, name)
    if wait:
        _plan_and_start(*args)
    else:
        threading.Thread(target=_plan_and_start, args=args, name=f"plan-{run_id}", daemon=True).start()
    return run_id


def _start_v1(runner: Any, task: dict, inputs: dict) -> str:
    """tasks.runner without build_vars: no extra vars possible. Plan first (for the record), then runner.start."""
    t_flow = None
    try:
        from . import catalog
        t_flow = catalog.task_flow_path(task)
    except Exception:
        pass
    rec = planner.make_plan(task, inputs, t_flow if t_flow and t_flow.is_file() else None,
                            evolution.lessons(task["id"]))
    run_id = runner.start(task["id"], inputs)
    if isinstance(run_id, str) and run_id:
        run_dir = config.runs_dir() / run_id
        if run_dir.is_dir():
            planner.save(run_dir, rec)
    return run_id
