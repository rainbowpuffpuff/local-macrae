"""DUSK → DAWN: the proof that the agent learned. The same fixed suite of task runs, in two states:

    dusk   nothing learned: no lessons, no earlier scripts, no capabilities mounted or built (launch mode "dusk")
    dawn   everything installed since: capabilities + lessons (the normal mode)

    start(mode) -> benchmark record      POST /api/benchmark/{dusk|dawn}
    report(dusk_id=None, dawn_id=None)   GET /api/dawn-report: the latest dusk vs the latest dawn after it
    python -m evolve benchmark dusk|dawn|report|list

Per task it records ok, reward, wall time, setup time, $ LLM, $ compute, errors, capabilities used and the run ids.
The registry is snapshotted when each benchmark starts (capabilities/snapshots/<benchmark id>.json) and a finished
report is frozen under benchmark/report-<dusk>-<dawn>.json, so the numbers can be reproduced later.

The suite: small-calc with Na+ and K+, plus a short variant of each BFF task (tasks.json entries whose id starts
with "bff", or that declare "benchmark": {"inputs": {...}, "label": "…"} for their short variant). Override it with
$MACRAE_BENCHMARK_SUITE (a JSON list of {"label", "task_id", "inputs"}) or $MACRAE_EVOLVE_HOME/benchmark/suite.json.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from . import capabilities, config, store

MODES = ("dusk", "dawn")
BASE_SUITE = [
    {"label": "small-calc Na+", "task_id": "small-calc", "inputs": {"ion": "Na+"}},
    {"label": "small-calc K+", "task_id": "small-calc", "inputs": {"ion": "K+"}},
]
LOWER_IS_BETTER = ("wall_s", "setup_s", "llm_usd", "compute_usd", "total_usd", "errors")
HIGHER_IS_BETTER = ("ok", "ok_rate", "reward_mean", "capabilities_used")
INSTALL_RE = re.compile(r"\b(pip3?|uv pip|python3? -m pip|conda|mamba|micromamba|apt(-get)?)\s+install\b|"
                        r"\bpython3? -m venv\b", re.I)

Starter = Callable[[str, dict, str, dict], str]  # (task_id, inputs, mode, meta) -> run_id


def bench_dir() -> Path:
    return config.home() / "benchmark"


def _tasks() -> list[dict]:
    try:
        from server import catalog
        return catalog.load_tasks()
    except Exception:
        p = config.REPO_ROOT / "tasks" / "tasks.json"
        d = store.read_json(p, {})
        return d.get("tasks", d) if isinstance(d, (dict, list)) else []


def suite() -> list[dict]:
    env = os.environ.get("MACRAE_BENCHMARK_SUITE", "").strip()
    custom = None
    if env:
        try:
            custom = json.loads(env)
        except ValueError:
            custom = None
    if custom is None:
        custom = store.read_json(bench_dir() / "suite.json", None)
    if isinstance(custom, list) and custom:
        return [dict(x, inputs=dict(x.get("inputs") or {}), label=x.get("label") or x["task_id"])
                for x in custom if isinstance(x, dict) and x.get("task_id")]
    tasks = {t.get("id"): t for t in _tasks() if isinstance(t, dict)}
    out = [dict(x) for x in BASE_SUITE if x["task_id"] in tasks or not tasks]
    for tid, t in tasks.items():
        b = t.get("benchmark") if isinstance(t.get("benchmark"), dict) else None
        if tid == "small-calc" or not (str(tid).startswith("bff") or b):
            continue
        out.append({"label": (b or {}).get("label") or f"{tid} (short)", "task_id": tid,
                    "inputs": dict((b or {}).get("inputs") or {})})
    return out


# ── records ─────────────────────────────────────────────────────────────────


def _path(bench_id: str) -> Path:
    return bench_dir() / f"{re.sub(r'[^A-Za-z0-9._-]+', '-', bench_id)}.json"


def load(bench_id: str) -> Optional[dict]:
    d = store.read_json(_path(bench_id), None)
    return d if isinstance(d, dict) else None


def _save(rec: dict) -> None:
    store._write_atomic(_path(rec["id"]), json.dumps(rec, indent=1, ensure_ascii=False, default=str))


def list_benchmarks() -> list[dict]:
    d = bench_dir()
    if not d.is_dir():
        return []
    out = []
    for p in d.glob("*.json"):
        if p.name in ("suite.json",) or p.name.startswith("report-"):
            continue
        r = store.read_json(p, None)
        if isinstance(r, dict) and r.get("mode") in MODES:
            out.append(r)
    return sorted(out, key=lambda r: float(r.get("started") or 0), reverse=True)


def latest(mode: str, before: Optional[float] = None, after: Optional[float] = None) -> Optional[dict]:
    for r in list_benchmarks():
        t = float(r.get("started") or 0)
        if r.get("mode") != mode or (before is not None and t >= before) or (after is not None and t <= after):
            continue
        return r
    return None


def _server_start(task_id: str, inputs: dict, mode: str, meta: dict) -> str:
    """Start one benchmark run the way the page does (same input checks, sidecar, planner), in the given mode."""
    from fastapi import HTTPException

    from server import app as server_app, catalog, launch, runs
    task = catalog.find_task(task_id)
    if not task:
        raise KeyError(f"no task {task_id!r}")
    try:
        resolved = server_app._resolve_inputs(task, inputs)
    except HTTPException as e:
        raise ValueError(str(e.detail)) from e
    run_id = launch.start(task, resolved, None, mode=mode, meta_extra=meta)
    runs.save_sidecar(run_id, task, resolved)
    return run_id


def start(mode: str, items: Optional[list[dict]] = None, starter: Optional[Starter] = None,
          sequential: Optional[bool] = None) -> dict:
    """Start a benchmark suite in `mode`. Runs start at once, or one after another with sequential=True (or
    MACRAE_BENCHMARK_SEQUENTIAL=1) in a background thread."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    items = [dict(x) for x in (items or suite())]
    if not items:
        raise ValueError("the benchmark suite is empty")
    starter = starter or _server_start
    if sequential is None:
        sequential = os.environ.get("MACRAE_BENCHMARK_SEQUENTIAL", "").strip() in ("1", "true", "yes")
    bench_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{mode}-{uuid.uuid4().hex[:4]}"
    now = time.time()
    caps_now = [{"name": c["name"], "version": c.get("version"), "kind": c.get("kind"), "sha256": c.get("sha256")}
                for c in capabilities.installed()]
    active_lessons = [x for x in store.load() if x.get("status", "active") == "active"]
    snap = capabilities.snapshot(bench_id, {"mode": mode, "lessons_active": len(active_lessons),
                                            "lesson_ids": [x["id"] for x in active_lessons]})
    rec = {"id": bench_id, "mode": mode, "started": now, "finished": None, "sequential": bool(sequential),
           "snapshot": snap.name, "capabilities": caps_now if mode == "dawn" else [],
           "capabilities_installed": caps_now, "lessons_active": len(active_lessons),
           "runs": [{"label": x.get("label") or x["task_id"], "task_id": x["task_id"],
                     "inputs": dict(x.get("inputs") or {}), "run_id": "", "error": ""} for x in items]}
    _save(rec)

    def launch_one(i: int) -> str:
        r = rec["runs"][i]
        try:
            r["run_id"] = starter(r["task_id"], r["inputs"], mode,
                                  {"benchmark": bench_id, "benchmark_label": r["label"]})
            r["started"] = time.time()
        except Exception as e:
            r["error"] = f"{type(e).__name__}: {e}"[:300]
        _save(rec)
        return r["run_id"]

    if not sequential:
        for i in range(len(rec["runs"])):
            launch_one(i)
    else:
        def chain() -> None:
            for i in range(len(rec["runs"])):
                rid = launch_one(i)
                while rid and not _run_done(rid):
                    time.sleep(10)
        threading.Thread(target=chain, name=f"benchmark-{bench_id}", daemon=True).start()
    return summary(rec)


# ── measuring ───────────────────────────────────────────────────────────────


def _run_done(run_id: str) -> bool:
    st = store.read_json(config.runs_dir() / run_id / "state.json", {})
    return isinstance(st, dict) and st.get("status") in ("ok", "failed", "cancelled", "crashed")


def _server_costs(run_id: str) -> Optional[dict]:
    try:
        from server import costs, planner, runs
        st = runs.read_state(run_id)
        if not st:
            return None
        return costs.run_costs(st, planner.load(runs.run_dir(run_id)))
    except Exception:
        return None


def row(run_id: str, label: str = "", task_id: str = "") -> dict:
    """One benchmark run's numbers."""
    from .runs import load as load_run
    out: dict[str, Any] = {"label": label, "task_id": task_id, "run_id": run_id,
                           "status": "missing" if run_id else "pending", "ok": False,
                           "reward": None, "wall_s": None, "setup_s": None, "install_s": None, "llm_usd": None,
                           "compute_usd": None, "total_usd": None, "errors": 0, "error_titles": [],
                           "capabilities_used": [], "lessons_used": 0,
                           "links": {"run": f"/api/runs/{run_id}", "events": f"/api/runs/{run_id}/events",
                                     "trace": f"/api/runs/{run_id}/trace"} if run_id else {}}
    if not run_id or not (config.runs_dir() / run_id).is_dir():
        return out
    try:
        run = load_run(config.runs_dir() / run_id)
    except Exception as e:
        out["status"] = "unreadable"
        out["error_titles"] = [f"{type(e).__name__}: {e}"[:200]]
        return out
    c = _server_costs(run_id) or run.costs or {}
    install_s = 0.0
    for s in run.agent_steps:
        for tr in s.trials:
            for call in tr.calls:
                if call.command and INSTALL_RE.search(call.command):
                    install_s += float(call.seconds or 0)
    phases = c.get("phases") or {}
    errs = [e for e in run.events if e.get("type") == "error"]
    used = {e["name"] for e in capabilities.ledger(run_id=run_id) if e.get("event") == "use"}
    used |= {str((e.get("capability") or {}).get("name")) for e in run.events
             if e.get("type") == "use" and (e.get("capability") or {}).get("name")}
    out.update(task_id=task_id or run.task_id, status=run.status, ok=run.ok, reward=run.reward, wall_s=run.wall_s,
               install_s=round(install_s, 1), setup_s=round(float(phases.get("setup") or 0) + install_s, 1),
               llm_usd=c.get("llm_usd"), compute_usd=c.get("compute_usd"), total_usd=c.get("total_usd"),
               errors=len(errs), error_titles=[str(e.get("title"))[:120] for e in errs][:6],
               capabilities_used=sorted(used), lessons_used=len(run.lessons_used),
               started=run.started, finished=run.finished)
    return out


def _sum(rows: list[dict], k: str) -> Optional[float]:
    xs = [float(r[k]) for r in rows if isinstance(r.get(k), (int, float))]
    return round(sum(xs), 4) if xs else None


def totals(rows: list[dict]) -> dict:
    done = [r for r in rows if r["status"] in ("ok", "failed", "cancelled", "crashed")]
    rewards = [float(r["reward"]) for r in rows if isinstance(r.get("reward"), (int, float))]
    return {"n": len(rows), "done": len(done), "ok": sum(1 for r in rows if r.get("ok")),
            "ok_rate": round(sum(1 for r in rows if r.get("ok")) / len(rows), 3) if rows else None,
            "reward_mean": round(sum(rewards) / len(rewards), 3) if rewards else None,
            "wall_s": _sum(rows, "wall_s"), "setup_s": _sum(rows, "setup_s"), "install_s": _sum(rows, "install_s"),
            "llm_usd": _sum(rows, "llm_usd"), "compute_usd": _sum(rows, "compute_usd"),
            "total_usd": _sum(rows, "total_usd"), "errors": sum(int(r.get("errors") or 0) for r in rows),
            "capabilities_used": len({c for r in rows for c in r.get("capabilities_used") or []})}


def summary(rec: dict) -> dict:
    rows = [row(r.get("run_id") or "", r.get("label") or "", r.get("task_id") or "") | (
        {"error_titles": [r["error"]], "status": "not started"} if r.get("error") and not r.get("run_id") else {})
        for r in rec.get("runs") or []]
    tot = totals(rows)
    finished = rec.get("finished")
    if not finished and rows and tot["done"] == len(rows):
        finished = max(float(r.get("finished") or 0) for r in rows) or time.time()
        rec["finished"] = finished
        if rec.get("id") and load(rec["id"]):
            _save(rec)
    status = "done" if finished else "running"
    if rows and all(r["status"] == "not started" for r in rows):
        status = "failed"
    return {"id": rec.get("id"), "mode": rec.get("mode"), "status": status, "started": rec.get("started"),
            "finished": finished, "snapshot": rec.get("snapshot"), "capabilities": rec.get("capabilities") or [],
            "lessons_active": rec.get("lessons_active"), "tasks": rows, "totals": tot}


def _delta(a: Optional[float], b: Optional[float], lower_better: bool) -> Optional[dict]:
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return None
    d = round(b - a, 4)
    pct = round(100.0 * d / a, 1) if a else None
    better = (d < 0) if lower_better else (d > 0)
    return {"dusk": a, "dawn": b, "delta": d, "pct": pct, "better": better if d else None}


def report(dusk_id: Optional[str] = None, dawn_id: Optional[str] = None) -> dict:
    """The dusk vs dawn comparison. Without ids: the latest dawn, and the latest dusk before it."""
    dawn = load(dawn_id) if dawn_id else latest("dawn")
    dusk = load(dusk_id) if dusk_id else latest("dusk", before=float(dawn["started"]) if dawn else None)
    if dawn and dusk and float(dawn.get("started") or 0) < float(dusk.get("started") or 0) and not dawn_id:
        dawn = None  # the newest dawn is older than the dusk: no comparison yet
    frozen = bench_dir() / f"report-{(dusk or {}).get('id')}-{(dawn or {}).get('id')}.json"
    if dusk and dawn and frozen.is_file():
        cached = store.read_json(frozen, None)
        if isinstance(cached, dict):
            return cached
    ds = summary(dusk) if dusk else None
    dw = summary(dawn) if dawn else None
    out: dict[str, Any] = {"dusk": ds, "dawn": dw, "delta": None, "capabilities_installed_between": [], "runs": [],
                           "ready": bool(ds and dw and ds["status"] == "done" and dw["status"] == "done"),
                           "generated": time.time()}
    if ds and dw:
        td, tw = ds["totals"], dw["totals"]
        delta: dict[str, Any] = {k: _delta(td.get(k), tw.get(k), True) for k in LOWER_IS_BETTER}
        delta.update({k: _delta(td.get(k), tw.get(k), False) for k in HIGHER_IS_BETTER})
        by_label: dict[str, Any] = {}
        dusk_rows = {r["label"]: r for r in ds["tasks"]}
        for r in dw["tasks"]:
            a = dusk_rows.get(r["label"])
            if not a:
                continue
            by_label[r["label"]] = {k: _delta(a.get(k), r.get(k), True) for k in ("wall_s", "setup_s", "total_usd",
                                                                                 "errors")}
            by_label[r["label"]]["ok"] = {"dusk": a.get("ok"), "dawn": r.get("ok")}
        delta["by_task"] = by_label
        better = [k for k, v in delta.items() if isinstance(v, dict) and v.get("better") is True]
        worse = [k for k, v in delta.items() if isinstance(v, dict) and v.get("better") is False]
        delta["better"], delta["worse"] = better, worse
        out["delta"] = delta
    t0 = float((dusk or {}).get("started") or 0)
    t1 = float((dawn or {}).get("started") or time.time())
    if dusk:
        gaps = {e["name"]: e for e in capabilities.ledger(since=t0) if e["event"] == "gap"}
        for e in capabilities.ledger(since=t0):
            if e["event"] == "install" and float(e["t"]) <= t1:
                g = gaps.get(e["name"]) or {}
                out["capabilities_installed_between"].append(
                    {"name": e["name"], "version": e.get("version"), "kind": e.get("kind"), "t": e["t"],
                     "sha256": e.get("sha256"), "purpose": e.get("purpose"), "forge_run": e.get("run_id"),
                     "gap_run": g.get("run_id"), "why": g.get("why")})
    labels = list(dict.fromkeys([r["label"] for r in (ds or {}).get("tasks", [])] +
                                [r["label"] for r in (dw or {}).get("tasks", [])]))
    for lb in labels:
        a = next((r for r in (ds or {}).get("tasks", []) if r["label"] == lb), None)
        b = next((r for r in (dw or {}).get("tasks", []) if r["label"] == lb), None)
        out["runs"].append({"label": lb, "task_id": (a or b or {}).get("task_id"),
                            "dusk": (a or {}).get("run_id") or None, "dawn": (b or {}).get("run_id") or None})
    if out["ready"]:
        out["frozen"] = frozen.name
        store._write_atomic(frozen, json.dumps(out, indent=1, ensure_ascii=False, default=str))
    return out
