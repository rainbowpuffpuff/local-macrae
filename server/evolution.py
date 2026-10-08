"""The bridge to `evolve/` (owned by the evolve module), imported lazily so the server runs without it.

    lessons(task_id) -> str        evolve.context(task_id, k=8): injected into the planner and into flow var `lessons`
    metrics() -> dict              evolve.metrics() for GET /api/evolution
    on_run_end(run_id)             once per finished task run: write <run>/costs.json, then evolve.distill(run_dir)

on_run_end is triggered by the first events poll that sees the run finished, and by a watcher thread (started with
the app) for runs nobody is watching. A marker file per run ($MACRAE_SERVER_DATA/evolve/<run>.json) makes it once.
MACRAE_EVOLVE=off skips distilling (lessons and metrics still work).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

from . import bridges, config, costs, planner, runs, safety

log = logging.getLogger("macrae.server")

WATCH_INTERVAL = 20.0
_pending: set[str] = set()
_pending_lock = threading.Lock()


def module() -> Any:
    return bridges._import("evolve")


def lessons(task_id: str, k: int = 8) -> str:
    try:
        ctx = module().context(task_id, k=k)
    except bridges.Unavailable:
        return ""
    except Exception as e:
        log.warning("evolve.context(%s) failed: %s", task_id, e)
        return ""
    return str(ctx or "").strip()[:8000]


def metrics() -> dict:
    """evolve.metrics(); raises bridges.Unavailable when evolve isn't installed."""
    data = bridges.to_plain(module().metrics())
    if not isinstance(data, dict):
        raise ValueError("evolve.metrics() did not return an object")
    data.setdefault("by_task", {})
    return data


def _marker(run_id: str):
    return config.server_data_dir() / "evolve" / f"{run_id}.json"


def distilled(run_id: str) -> bool:
    return _marker(run_id).is_file()


def write_costs(run_id: str) -> dict:
    st = runs.read_state(run_id)
    if not st:
        return {}
    run_dir = runs.run_dir(run_id)
    c = costs.run_costs(st, planner.load(run_dir))
    costs.save(run_dir, c)
    return c


def _distill(run_id: str) -> None:
    rec: dict[str, Any] = {"run_id": run_id, "started": time.time()}
    try:
        try:
            write_costs(run_id)
        except Exception as e:
            log.warning("costs for %s failed: %s", run_id, e)
        if os.environ.get("MACRAE_EVOLVE", "").strip().lower() in ("off", "0", "false", "no"):
            rec["skipped"] = "MACRAE_EVOLVE=off"
        elif blocked := safety.llm_block_reason():
            # no marker: the watcher distills it later, when the kill switch is off or a new day's budget starts
            log.info("not distilling %s now: %s", run_id, blocked)
            return
        else:
            try:
                out = module().distill(runs.run_dir(run_id))
                rec["lessons"] = len(out) if isinstance(out, (list, tuple)) else bridges.to_plain(out)
            except bridges.Unavailable as e:
                rec["skipped"] = str(e)[:300]
            except Exception as e:
                log.warning("evolve.distill(%s) failed: %s", run_id, e)
                rec["error"] = f"{type(e).__name__}: {e}"[:500]
        rec["finished"] = time.time()
        try:
            m = _marker(run_id)
            m.parent.mkdir(parents=True, exist_ok=True)
            m.write_text(json.dumps(rec, default=str))
        except OSError:
            pass
    finally:
        with _pending_lock:
            _pending.discard(run_id)


def on_run_end(run_id: str, wait: bool = False) -> bool:
    """Distill a finished task run once (in a background thread unless wait). False if already done/going."""
    if distilled(run_id) or not runs.load_sidecar(run_id).get("task_id"):
        return False
    with _pending_lock:
        if run_id in _pending:
            return False
        _pending.add(run_id)
    if wait:
        _distill(run_id)
    else:
        threading.Thread(target=_distill, args=(run_id,), name=f"distill-{run_id}", daemon=True).start()
    return True


def sweep() -> int:
    """Distill finished task runs that haven't been (the watcher's tick). Returns how many were started."""
    n = 0
    for r in runs.list_runs(limit=50):
        if r["status"] in runs.TERMINAL and r.get("task_id") and on_run_end(r["run_id"]):
            n += 1
    return n


_watcher: dict[str, Any] = {"thread": None, "stop": None}


def start_watcher() -> None:
    if _watcher["thread"] is not None:
        return
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(WATCH_INTERVAL):
            try:
                sweep()
            except Exception as e:
                log.warning("evolve watcher: %s", e)

    t = threading.Thread(target=loop, name="evolve-watcher", daemon=True)
    _watcher.update(thread=t, stop=stop)
    t.start()


def stop_watcher() -> None:
    if _watcher["stop"] is not None:
        _watcher["stop"].set()
    _watcher.update(thread=None, stop=None)
