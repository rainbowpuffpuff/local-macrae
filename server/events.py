"""Append-only TraceEvent log per run, so `seq` is stable across polls and server restarts.

Every poll rebuilds the candidate events from disk (trace.build) and appends the ones whose keys we haven't seen.
New events are ordered by time within the batch, so the page sees an orderly stream, and an event that appears
late (a trajectory written at the end of a trial) gets a new seq instead of shifting earlier ones.
The log is kept in memory and mirrored to $MACRAE_SERVER_DATA/events/<run_id>.jsonl.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from . import config, runs, trace

MAX_EVENTS_PER_POLL = 500


class RunLog:
    def __init__(self, run_id: str, path: Optional[Path]):
        self.run_id = run_id
        self.path = path
        self.events: list[dict] = []
        self.keys: set[str] = set()
        self.final = False
        self.lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.is_file():
            return
        try:
            lines = self.path.read_text().splitlines()
        except OSError:
            return
        for ln in lines:
            try:
                rec = json.loads(ln)
            except ValueError:
                continue  # a torn last line after a crash
            if rec.get("final"):
                self.final = True
                continue
            ev = rec.get("event")
            if isinstance(ev, dict) and ev.get("seq") == len(self.events) + 1:
                self.events.append(ev)
                self.keys.update(rec.get("keys") or [])

    def _write(self, recs: list[dict]) -> None:
        if not self.path or not recs:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as f:
                f.write("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in recs))
        except OSError:
            self.path = None  # read-only disk: keep going in memory

    def merge(self, cands: list[trace.Cand], final: bool) -> None:
        new = [c for c in cands if not any(k in self.keys for k in c.keys)]
        # keep the builder's order for equal times; the run's final event goes last
        new = [c for _, c in sorted(enumerate(new), key=lambda ic: (ic[1].last, ic[1].t, ic[0]))]
        recs = []
        for c in new:
            if any(k in self.keys for k in c.keys):
                continue  # two candidates in one batch sharing a key
            ev = {"seq": len(self.events) + 1, **c.event()}
            self.events.append(ev)
            self.keys.update(c.keys)
            recs.append({"keys": c.keys, "event": ev})
        if final and not self.final:
            self.final = True
            recs.append({"final": True})
        self._write(recs)


_logs: dict[str, RunLog] = {}
_logs_lock = threading.Lock()


def _log_for(run_id: str) -> RunLog:
    with _logs_lock:
        lg = _logs.get(run_id)
        if lg is None:
            lg = RunLog(run_id, config.server_data_dir() / "events" / f"{run_id}.jsonl")
            _logs[run_id] = lg
        return lg


def reset_cache() -> None:
    """Forget in-memory logs (tests; or after MACRAE_SERVER_DATA changes)."""
    with _logs_lock:
        _logs.clear()


def _refreshed(run_id: str) -> Optional[tuple[RunLog, bool]]:
    """The run's log, brought up to date with what's on disk; and whether the run is over. None = no such run."""
    state = runs.read_state(run_id)
    if state is None:
        return None
    summary = runs.summarize(state)
    terminal = summary["status"] in runs.TERMINAL
    lg = _log_for(run_id)
    with lg.lock:
        if not lg.final:
            lg.merge(trace.build(state, summary["title"]), final=terminal)
    if terminal and summary.get("task_id"):
        try:
            from . import evolution
            evolution.on_run_end(run_id)  # once: costs.json + evolve.distill, in the background
        except Exception:
            pass
    return lg, terminal


def poll(run_id: str, after: int = 0, limit: int = MAX_EVENTS_PER_POLL) -> Optional[dict]:
    """{"events": [...seq > after], "done": bool}, or None if the run doesn't exist."""
    got = _refreshed(run_id)
    if got is None:
        return None
    lg, terminal = got
    after = max(0, int(after or 0))
    with lg.lock:
        evs = lg.events[after:after + limit]
        more = after + len(evs) < len(lg.events)
    return {"events": evs, "done": terminal and not more}


def tail(run_id: str, n: int = 5) -> Optional[list[dict]]:
    """The last n events (for the voice agent's run_status)."""
    got = _refreshed(run_id)
    if got is None:
        return None
    with got[0].lock:
        return list(got[0].events[-n:])
