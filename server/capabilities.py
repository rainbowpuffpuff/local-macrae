"""The backend's side of the capability lifecycle: record what runs show, start forges, serve the ledger.

    on_events(run_id, summary, new_events)   (events.py, for every event appended to a run's log)
        gap → ledger `gap` and a forge run (forge.start), unless the run is itself a forge
        use → ledger `use` (the registry counts uses and last_used)
    on_run_end(run_id)                        (evolution.py, once per finished task run)
        gaps the agent did not say but its trace shows (slow installs, version breakage: evolve.gaps) → the same
    payload(window_h)                         GET /api/capabilities
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from . import bridges, forge, policy, runs

log = logging.getLogger("macrae.server")


def _caps() -> Any:
    return bridges._import("evolve.capabilities")


def on_events(run_id: str, summary: dict, new_events: list[dict]) -> None:
    caps = _caps()
    task_id = str(summary.get("task_id") or "")
    is_forge = task_id == forge.TASK["id"]
    for ev in new_events:
        typ = ev.get("type")
        cap = ev.get("capability") if isinstance(ev.get("capability"), dict) else {}
        name = str(cap.get("name") or "")
        if not name:
            continue
        keys = [k for k in ev.get("_keys") or [] if not str(k).startswith("ledger|")]
        if typ == "gap" and keys:
            if is_forge:
                continue  # a forge building one thing and wishing for another: not a loop we start
            why = str(cap.get("why") or ev.get("detail") or "")
            entry = caps.record("gap", name, run_id=run_id, step=str(ev.get("step") or ""),
                                key=f"gap|{run_id}|{keys[0]}", event_key=keys[0], t=ev.get("t"), why=why[:500],
                                kind=cap.get("kind") or None, task_id=task_id, source="agent")
            if entry is not None:
                res = forge.start(name, why, kind=str(cap.get("kind") or ""), gap_run=run_id,
                                  gap_step=str(ev.get("step") or ""), task_id=task_id)
                log.info("gap %s in %s → forge %s", name, run_id, res.get("status"))
        elif typ == "use" and keys:
            caps.record("use", name, run_id=run_id, step=str(ev.get("step") or ""), key=f"use|{run_id}|{keys[0]}",
                        event_key=keys[0], t=ev.get("t"), version=cap.get("version"), task_id=task_id,
                        kind=cap.get("kind") or None)


def on_run_end(run_id: str) -> list[dict]:
    """Gaps found in the finished run's trace (evolve.gaps): ledger + forge. Returns what was started."""
    side = runs.load_sidecar(run_id)
    task_id = str(side.get("task_id") or "")
    if not task_id or task_id == forge.TASK["id"]:
        return []
    try:
        gaps_mod = bridges._import("evolve.gaps")
        found = gaps_mod.trace_gaps(runs.run_dir(run_id))
    except bridges.Unavailable:
        return []
    caps = _caps()
    out = []
    for g in found:
        entry = caps.record("gap", g["name"], run_id=run_id, step=g.get("step") or "", key=f"gap|{run_id}|trace|{g['name']}",
                            why=g["why"][:500], kind=g.get("kind"), task_id=task_id, source="trace",
                            evidence=g.get("evidence", [])[:8], install_s=g.get("install_s"))
        if entry is None:
            continue
        res = forge.start(g["name"], g["why"], kind=g.get("kind") or "", gap_run=run_id, gap_step=g.get("step") or "",
                          task_id=task_id, evidence=g.get("evidence"))
        out.append(dict(g, forge=res))
    return out


def _iso(t: Optional[float]) -> Optional[str]:
    if not t:
        return None
    return datetime.fromtimestamp(float(t), timezone.utc).isoformat().replace("+00:00", "Z")


def session_start(window_h: Optional[float] = None) -> float:
    """The start of the session window: the latest dusk benchmark if one ran within the window, else now − window
    (MACRAE_SESSION_HOURS, default 24)."""
    if window_h is None:
        try:
            window_h = float(os.environ.get("MACRAE_SESSION_HOURS", "") or 24)
        except ValueError:
            window_h = 24.0
    floor = time.time() - window_h * 3600
    try:
        bench = bridges._import("evolve.benchmark")
        dusk = bench.latest("dusk")
        if dusk and float(dusk.get("started") or 0) >= floor:
            return float(dusk["started"]) - 1
    except bridges.Unavailable:
        pass
    except Exception as e:
        log.warning("benchmark lookup failed: %s", e)
    return floor


def payload(window_h: Optional[float] = None, all_events: bool = False) -> dict:
    """GET /api/capabilities: {"capabilities", "ledger", "dusk", "dawn", "authority", "forges", "window"}."""
    caps = _caps()
    since = None if all_events else session_start(window_h)
    ledger = caps.ledger(since=since)
    now = time.time()
    dusk_t = float(ledger[0]["t"]) if ledger else (since or now)
    keep = ("t", "event", "name", "run_id", "step", "version", "kind", "sha256", "why", "reason", "reasons",
            "passed", "n_tests", "n_passed", "task_id", "source", "seconds", "setup_s", "job", "purpose")
    out_ledger = [dict({k: e[k] for k in keep if k in e}, iso=_iso(e.get("t"))) for e in ledger]
    forges = []
    for name, entries in caps.forges().items():
        for f in entries[-3:]:
            st = runs.read_state(str(f.get("run_id") or ""))
            forges.append({"name": name, "run_id": f.get("run_id"), "t": f.get("t"), "kind": f.get("kind"),
                           "gap_run": f.get("gap_run"),
                           "status": runs.public_status(str(st.get("status"))) if st else "unknown"})
    forges.sort(key=lambda f: float(f.get("t") or 0), reverse=True)
    return {
        "capabilities": [caps.public(c) for c in reversed(caps.installed())],
        "ledger": out_ledger,
        "dusk": _iso(dusk_t), "dawn": _iso(now),
        "window": {"since": _iso(since) if since else None, "events": len(out_ledger)},
        "authority": policy.describe(),
        "forges": forges[:20],
        "counts": {ev: sum(1 for e in ledger if e["event"] == ev) for ev in caps.EVENTS},
    }
