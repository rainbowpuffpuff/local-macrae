"""metrics() → the Evolution panel's data (GET /api/evolution, wired by the backend).

    {"by_task": {task_id: [Row, …oldest first]},
     "lessons": {task_id: [Lesson summary, …best first]},   # extra: the lessons with links to their runs
     "tools":   {task_id: [{"name", "description", "runs"}]},
     "generated": epoch}
    Row = {"run_id", "ok", "reward", "wall_s", "total_usd", "lessons_used",          # the contract's fields
           "task_id", "title", "status", "started", "finished", "llm_usd", "compute_usd", "tokens",
           "hardware", "lesson_ids", "lessons_learned", "distilled"}

Only finished runs (ok / failed / cancelled / crashed). Rows are cached per run on state.json's mtime, so polling
the endpoint stays cheap.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from . import store, tools
from .runs import TERMINAL, list_run_dirs, load

MAX_PER_TASK = 100

_cache: dict[str, tuple[int, dict]] = {}
_cache_lock = threading.Lock()


def _row(run_dir) -> dict | None:
    try:
        mt = (run_dir / "state.json").stat().st_mtime_ns
    except OSError:
        return None
    with _cache_lock:
        hit = _cache.get(str(run_dir))
    if hit and hit[0] == mt:
        return hit[1]
    st = store.read_json(run_dir / "state.json", {})
    if not isinstance(st, dict) or st.get("status") not in TERMINAL:
        return None
    try:
        run = load(run_dir)
        c = run.costs
    except Exception:
        return None
    row = {
        "run_id": run.id, "task_id": run.task_id, "title": run.title, "status": run.status, "ok": run.ok,
        "reward": run.reward if run.reward is not None else (1.0 if run.ok else 0.0),
        "wall_s": run.wall_s, "total_usd": c.get("total_usd"), "llm_usd": c.get("llm_usd"),
        "compute_usd": c.get("compute_usd"), "tokens": c.get("tokens"), "hardware": run.hardware,
        "started": run.started, "finished": run.finished,
        "lessons_used": len(run.lessons_used), "lesson_ids": run.lessons_used,
    }
    with _cache_lock:
        _cache[str(run_dir)] = (mt, row)
    return row


def _rows() -> list[dict]:
    reg = store.registry()
    out = []
    for d in list_run_dirs():
        r = _row(d)
        if r is None:
            continue
        e = reg.get(r["run_id"]) or {}
        out.append(dict(r, distilled=bool(e), lessons_learned=len(e.get("lessons") or [])))
    out.sort(key=lambda r: r.get("started") or 0)
    return out


def task_rows(task_id: str) -> list[dict]:
    return [r for r in _rows() if r["task_id"] == task_id][-MAX_PER_TASK:]


def lesson_summary(x: dict) -> dict[str, Any]:
    links = []
    for e in x.get("evidence") or []:
        parts = str(e).split("/")
        if len(parts) >= 3:
            try:
                links.append({"run_id": "/".join(parts[:-2]), "step": "" if parts[-2] == "-" else parts[-2],
                              "seq": int(parts[-1])})
            except ValueError:
                continue
    return {k: x.get(k) for k in ("id", "task_id", "lesson", "kind", "evidence", "created", "updated", "hits",
                                  "used", "used_ok", "confidence", "source", "status", "runs")} | {"links": links}


def metrics() -> dict:
    rows = _rows()
    by_task: dict[str, list[dict]] = {}
    for r in rows:
        by_task.setdefault(r["task_id"], []).append(r)
    by_task = {k: v[-MAX_PER_TASK:] for k, v in by_task.items()}
    all_lessons = store.load()
    task_ids = sorted(set(by_task) | {x["task_id"] for x in all_lessons if x.get("task_id") != "*"})
    lessons = {t: [lesson_summary(x) for x in store.active(all_lessons, t)] for t in task_ids}
    tools_out = {t: [{"name": x["name"], "description": x.get("description", ""), "runs": x.get("runs", [])}
                     for x in tools.listing(t)] for t in task_ids}
    for t in task_ids:
        by_task.setdefault(t, [])
    return {"by_task": by_task, "lessons": {k: v for k, v in lessons.items() if v},
            "tools": {k: v for k, v in tools_out.items() if v}, "generated": time.time()}
