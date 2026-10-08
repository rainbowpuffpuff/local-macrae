"""Costs for people: the price table with its sources, estimates before a task starts, and a `cost` on every answer.

Routes (all behind X-Macrae-Secret, like the rest of /api):
    GET /api/costs/prices             → costs.price_table(): every rate the site uses, with links and the date
    GET /api/costs/estimates          → {"estimates": {task_id: Estimate}} for every task in the catalog
    GET /api/tasks/{task_id}/estimate → Estimate

Estimate (from the task's past finished runs; the successful ones when there are any, the newest RECENT of them):
    {"task_id", "basis": "past_runs"|"none", "n", "n_ok", "success_rate", "usd", "usd_low", "usd_high", "llm_usd",
     "compute_usd", "seconds", "seconds_low", "seconds_high", "hardware", "usd_per_hour", "runs": [run_id…]}
"usd"/"seconds" are medians; low/high the 25th/75th percentile (min/max under 4 runs). With no past run, basis is
"none" and only usd_per_hour (the default hardware's sandbox rate) is filled.

Answers: `install()` adds a middleware that puts `"cost"` (costs.answer_cost: LLM + backend compute, seconds) on the
JSON body of every successful POST to CHAT_PATHS, unless the route already set one. A route that calls Claude itself
can return its `"model"` and Anthropic `"usage"` in the body and the middleware prices them.
"""

from __future__ import annotations

import json
import logging
import statistics
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import Response

from . import catalog, config, costs, planner, runs

log = logging.getLogger("macrae.cost_api")

# POST routes whose JSON answers get a `cost`. Add a route here if it answers the user in the chat.
CHAT_PATHS = {"/api/search", "/api/chat", "/api/ask", "/api/answer"}
MAX_BODY = 2_000_000  # bigger answers are passed through untouched
RECENT = 10  # past runs an estimate looks at
CACHE_S = 15.0

_lock = threading.Lock()
_runs_cache: dict[str, tuple[float, dict]] = {}  # run_id → (costs.json / state.json mtime, record)
_est_cache: dict[str, Any] = {"key": None, "at": 0.0, "value": None}


def reset_cache() -> None:
    with _lock:
        _runs_cache.clear()
        _est_cache.update(key=None, at=0.0, value=None)


# ── estimates ───────────────────────────────────────────────────────────────


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _run_record(summary: dict) -> Optional[dict]:
    """What one finished run cost: <run>/costs.json, else computed now (and saved, like GET /api/runs/{id} does)."""
    run_id = summary["run_id"]
    d = runs.run_dir(run_id)
    key = max(_mtime(d / "costs.json"), _mtime(d / "state.json"))
    with _lock:
        hit = _runs_cache.get(run_id)
    if hit and hit[0] == key:
        return hit[1]
    c = config.read_json(d / "costs.json")
    if not isinstance(c, dict) or "total_usd" not in c:
        st = runs.read_state(run_id)
        if st is None:
            return None
        try:
            c = costs.run_costs(st, planner.load(d))
        except Exception as e:  # an odd trial file must not break the estimate for every task
            log.warning("costs for %s failed: %s", run_id, e)
            return None
        costs.save(d, c)
    started, finished = summary.get("started"), summary.get("finished")
    wall = c.get("wall_s")
    if not isinstance(wall, (int, float)) or wall <= 0:
        wall = (float(finished) - float(started)) if started and finished else 0.0
    rec = {"run_id": run_id, "ok": summary["status"] == "ok", "started": float(started or 0),
           "total_usd": float(c.get("total_usd") or 0), "llm_usd": float(c.get("llm_usd") or 0),
           "compute_usd": float(c.get("compute_usd") or 0), "wall_s": float(wall or 0),
           "hardware": str(c.get("hardware") or "")}
    with _lock:
        _runs_cache[run_id] = (key, rec)
    return rec


def _spread(xs: list[float]) -> tuple[float, float, float]:
    """(median, low, high): quartiles from 4 values up, else min/max."""
    xs = sorted(xs)
    med = statistics.median(xs)
    if len(xs) < 4:
        return med, xs[0], xs[-1]
    q = statistics.quantiles(xs, n=4, method="inclusive")
    return med, q[0], q[2]


def _no_history(task: dict) -> dict:
    hw = str(task.get("hardware") or costs.DEFAULT_HARDWARE)
    hw = hw if hw in costs.HARDWARE else costs.DEFAULT_HARDWARE
    out = {"task_id": task["id"], "basis": "none", "n": 0, "n_ok": 0, "success_rate": None, "usd": None,
           "usd_low": None, "usd_high": None, "llm_usd": None, "compute_usd": None, "seconds": None,
           "seconds_low": None, "seconds_high": None, "hardware": hw,
           "usd_per_hour": round(costs.hardware_rate(hw) * 3600, 3), "runs": []}
    if isinstance(task.get("budget_usd"), (int, float)):
        out["budget_usd"] = float(task["budget_usd"])
    return out


def estimate_from(task: dict, records: list[dict]) -> dict:
    """The estimate for one task from its finished runs' records (newest first)."""
    if not records:
        return _no_history(task)
    ok = [r for r in records if r["ok"]]
    use = (ok or records)[:RECENT]
    usd, usd_lo, usd_hi = _spread([r["total_usd"] for r in use])
    secs, secs_lo, secs_hi = _spread([r["wall_s"] for r in use])
    hws = [r["hardware"] for r in use if r["hardware"]]
    hw = max(set(hws), key=hws.count) if hws else str(task.get("hardware") or costs.DEFAULT_HARDWARE)
    recent = records[:RECENT]
    return {"task_id": task["id"], "basis": "past_runs", "n": len(use), "n_ok": len(ok),
            "success_rate": round(sum(r["ok"] for r in recent) / len(recent), 3),
            "usd": round(usd, 4), "usd_low": round(usd_lo, 4), "usd_high": round(usd_hi, 4),
            "llm_usd": round(statistics.median(r["llm_usd"] for r in use), 4),
            "compute_usd": round(statistics.median(r["compute_usd"] for r in use), 4),
            "seconds": round(secs, 1), "seconds_low": round(secs_lo, 1), "seconds_high": round(secs_hi, 1),
            "hardware": hw, "usd_per_hour": round(costs.hardware_rate(hw) * 3600, 3) if hw in costs.HARDWARE else None,
            "runs": [r["run_id"] for r in use]}


def estimates() -> dict[str, dict]:
    """Every catalog task's estimate. Cached for a few seconds: it reads every run folder."""
    key = str(config.runs_dir())
    now = time.time()
    with _lock:
        if _est_cache["key"] == key and now - _est_cache["at"] < CACHE_S and _est_cache["value"] is not None:
            return _est_cache["value"]
    by_task: dict[str, list[dict]] = {}
    for s in runs.list_runs(limit=runs.SCAN_MAX):  # newest first
        if s["status"] not in runs.TERMINAL or s["status"] == "cancelled" or not s.get("task_id"):
            continue
        rec = _run_record(s)
        if rec:
            by_task.setdefault(s["task_id"], []).append(rec)
    out = {t["id"]: estimate_from(t, by_task.get(t["id"], [])) for t in catalog.load_tasks()}
    with _lock:
        _est_cache.update(key=key, at=now, value=out)
    return out


# ── answers ─────────────────────────────────────────────────────────────────


async def _stamp_answer_cost(request: Request, call_next):
    if request.method != "POST" or request.url.path not in CHAT_PATHS:
        return await call_next(request)
    t0 = time.perf_counter()
    response = await call_next(request)
    if response.status_code != 200 or "application/json" not in response.headers.get("content-type", ""):
        return response
    chunks, size = [], 0
    async for chunk in response.body_iterator:
        chunks.append(chunk)
        size += len(chunk)
    raw = b"".join(chunks)
    headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
    seconds = time.perf_counter() - t0
    try:
        data = json.loads(raw) if size <= MAX_BODY else None
    except ValueError:
        data = None
    if isinstance(data, dict) and not isinstance(data.get("cost"), dict):
        try:
            data["cost"] = costs.answer_cost(seconds, str(data.get("model") or ""), data.get("usage"))
            raw = json.dumps(data, ensure_ascii=False).encode()
        except Exception as e:  # never lose the answer over its price
            log.warning("answer cost failed: %s", e)
    return Response(content=raw, status_code=response.status_code, headers=headers, media_type="application/json")


# ── wiring ──────────────────────────────────────────────────────────────────


def router(dependencies: list) -> APIRouter:
    r = APIRouter(dependencies=dependencies)

    @r.get("/api/costs/prices")
    def prices() -> dict:
        return costs.price_table()

    @r.get("/api/costs/estimates")
    def all_estimates() -> dict:
        return {"estimates": estimates(), "as_of": costs.PRICES_AS_OF}

    @r.get("/api/tasks/{task_id}/estimate")
    def task_estimate(task_id: str) -> dict:
        task = catalog.find_task(task_id)
        if not task:
            raise HTTPException(404, f"no task {task_id!r}")
        return estimates().get(task["id"]) or _no_history(task)

    return r


def install(app: FastAPI, dependencies: list) -> None:
    """Called once from server/app.py: the routes above (with the app's auth) and the answer-cost middleware."""
    app.include_router(router(dependencies))
    app.middleware("http")(_stamp_answer_cost)
