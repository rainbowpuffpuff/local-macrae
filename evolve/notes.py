"""Notes-to-self: what the agent wrote down during the run (NOTES_TO_SELF.md: what it tried, what was slow, what to
do differently) becomes lessons for the next run of the task, and part of what the distiller reads.

    text(run) -> (notes, step key, time) | ("", "", None)
    lessons(run) -> candidate lessons (source "notes"), one per bullet, at most MAX_LESSONS

The file is read from the newest trial's artifacts (artifacts/app/**/NOTES_TO_SELF.md); while that isn't there
(a run that failed before returning its files), it is rebuilt from the agent's Write/Edit calls on it.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from .runs import Run, evidence

FILE = "NOTES_TO_SELF.md"
MAX_LESSONS = 5
MAX_CHARS = 6000
AVOID_RE = re.compile(r"\b(avoid|don'?t|do not|never|instead of|slow|wasted?|failed|broke|stuck|timed? out|"
                      r"too long|mistake|wrong)\b", re.I)
BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)\s*$")


def _from_calls(run: Run) -> tuple[str, str, Optional[float]]:
    content, where, when = "", "", None
    for s in run.agent_steps:
        for tr in s.trials:
            for c in tr.calls:
                if not c.path.endswith(FILE) or c.is_error:
                    continue
                a = c.args if isinstance(c.args, dict) else {}
                n = c.name.lower()
                if n == "write" and isinstance(a.get("content"), str):
                    content = a["content"]
                elif n == "edit" and isinstance(a.get("old_string"), str) and a["old_string"] in content:
                    content = content.replace(a["old_string"], str(a.get("new_string") or ""),
                                              -1 if a.get("replace_all") else 1)
                elif n == "multiedit":
                    for e in a.get("edits") or []:
                        if isinstance(e, dict) and isinstance(e.get("old_string"), str) and e["old_string"] in content:
                            content = content.replace(e["old_string"], str(e.get("new_string") or ""),
                                                      -1 if e.get("replace_all") else 1)
                else:
                    continue
                where, when = s.key, c.t
    return content, where, when


def text(run: Run) -> tuple[str, str, Optional[float]]:
    for s in reversed(run.agent_steps):
        for tr in sorted(s.trials, key=lambda t: (t.attempt, t.started or 0), reverse=True):
            app = tr.artifacts_app
            if not app.is_dir():
                continue
            hits = sorted(app.rglob(FILE), key=lambda p: p.stat().st_mtime, reverse=True)
            if hits:
                try:
                    return hits[0].read_text(errors="replace")[:MAX_CHARS], s.key, tr.finished
                except OSError:
                    continue
    content, where, when = _from_calls(run)
    return content[:MAX_CHARS], where, when


def lessons(run: Run) -> list[dict]:
    notes, step, t = text(run)
    if not notes.strip():
        return []
    out = []
    for ln in notes.splitlines():
        m = BULLET_RE.match(ln)
        if not m:
            continue
        item = re.sub(r"\s+", " ", m.group(1)).strip().strip("*_")
        if len(item) < 16 or item.endswith(":") or item.lower().startswith(("todo", "tbd")):
            continue
        kind = "avoid" if AVOID_RE.search(item) else "do"
        out.append({"task_id": run.task_id, "run_id": run.id,
                    "lesson": f"From the agent's notes-to-self: {item[:380]}", "kind": kind, "confidence": 0.55,
                    "key": "notes:" + hashlib.sha1(item.lower().encode()).hexdigest()[:10],
                    "evidence": [evidence(run, step, t, FILE, ("write",))], "source": "notes"})
        if len(out) >= MAX_LESSONS:
            break
    return out
