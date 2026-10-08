"""The lesson store ($MACRAE_EVOLVE_HOME/lessons.jsonl) and the distill registry (distilled.json).

A lesson line is the contract's record plus bookkeeping:

    {"id": "L3fa2c1", "task_id": "small-calc", "lesson": "…", "kind": "do|avoid|setting|tool",
     "evidence": ["<run_id>/<step>/<seq>", …], "created": 1760000000.0,
     "updated": …, "runs": [run_id, …], "hits": 2, "confidence": 0.8, "source": "claude|rules|manual",
     "key": "rule key or ''", "used": 3, "used_ok": 3, "status": "active|retired"}

`hits` = how many runs produced or confirmed it. `used`/`used_ok` = how many later runs had it in their instruction,
and how many of those passed. A lesson that keeps being used by failing runs is retired (left out of context).
Writes go through one lock (a thread lock plus flock on <home>/.lock), so the server's background distills, the CLI
and tests can't interleave.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Optional

from . import config

KINDS = ("do", "avoid", "setting", "tool")
LESSON_ID_RE = re.compile(r"\[(L[0-9a-f]{6})\]")
SIMILAR = 0.6           # word-set Jaccard above which two lessons are "the same lesson"
MAX_EVIDENCE = 12
HALF_LIFE_DAYS = 21.0
RETIRE_AFTER_USES = 3
RETIRE_BELOW = 0.34     # success rate of runs that used it
STOP = set("a an the of to in on for and or is are was were be been it its this that with as at by from into "
           "than then so do does did not no use used using run runs ran when if before after your you".split())

_tlock = threading.RLock()
_held = threading.local()


@contextlib.contextmanager
def locked() -> Iterator[None]:
    """Exclusive access to the store, across threads and processes. Re-entrant within a thread."""
    if getattr(_held, "depth", 0):
        _held.depth += 1
        try:
            yield
        finally:
            _held.depth -= 1
        return
    home = config.home()
    home.mkdir(parents=True, exist_ok=True)
    with _tlock:
        _held.depth = 1
        try:
            with _flock(home / ".lock"):
                yield
        finally:
            _held.depth = 0


@contextlib.contextmanager
def _flock(path: Path) -> Iterator[None]:
    try:
        import fcntl
    except ImportError:  # no flock (Windows): the thread lock still serializes this process
        fcntl = None
    with open(path, "a+") as fh:
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


# ── lessons ─────────────────────────────────────────────────────────────────


def load() -> list[dict]:
    out = []
    try:
        lines = config.lessons_file().read_text().splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            d = json.loads(ln)
        except ValueError:
            continue  # a torn line after a crash
        if isinstance(d, dict) and d.get("id") and d.get("lesson"):
            out.append(d)
    return out


def save(lessons: list[dict]) -> None:
    _write_atomic(config.lessons_file(), "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lessons))


def new_id(task_id: str, text: str) -> str:
    return "L" + hashlib.sha1(f"{task_id}\n{text}\n{time.time()}".encode()).hexdigest()[:6]


def words(text: str) -> set[str]:
    text = re.sub(r"\d+(\.\d+)?", " ", text.lower())
    return {w for w in re.findall(r"[a-z_][a-z0-9_+.-]*", text) if w not in STOP and len(w) > 1}


def similarity(a: str, b: str) -> float:
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def find_same(lessons: list[dict], task_id: str, text: str, key: str = "") -> Optional[dict]:
    best, best_sim = None, 0.0
    for x in lessons:
        if x.get("task_id") != task_id:
            continue
        if key and x.get("key") == key:
            return x
        s = similarity(text, x["lesson"])
        if s > best_sim:
            best, best_sim = x, s
    return best if best_sim >= SIMILAR else None


def merge(lessons: list[dict], cand: dict, now: Optional[float] = None) -> tuple[dict, bool]:
    """Add a candidate lesson, or reinforce the existing one it repeats. Returns (lesson, is_new). In place."""
    now = now or time.time()
    task_id, text = cand["task_id"], cand["lesson"].strip()
    run_id = cand.get("run_id") or ""
    same = find_same(lessons, task_id, text, cand.get("key") or "")
    if same is not None:
        if run_id and run_id not in same.setdefault("runs", []):
            same["runs"].append(run_id)
            same["hits"] = int(same.get("hits") or 1) + 1
        ev = same.setdefault("evidence", [])
        for e in cand.get("evidence") or []:
            if e not in ev:
                ev.append(e)
        del ev[:-MAX_EVIDENCE]
        same["confidence"] = round(max(float(same.get("confidence") or 0), float(cand.get("confidence") or 0)), 2)
        if cand.get("replace") or (cand.get("key") and cand.get("key") == same.get("key")):
            same["lesson"] = text  # keyed lessons (e.g. the performance baseline) keep the newest wording
        same["updated"] = now
        if same.get("status") == "retired" and cand.get("source") == "manual":
            same["status"] = "active"
        return same, False
    lesson = {
        "id": new_id(task_id, text), "task_id": task_id, "lesson": text,
        "kind": cand.get("kind") if cand.get("kind") in KINDS else "do",
        "evidence": list(dict.fromkeys(cand.get("evidence") or []))[:MAX_EVIDENCE],
        "created": now, "updated": now, "runs": [run_id] if run_id else [], "hits": 1,
        "confidence": round(float(cand.get("confidence") or 0.6), 2), "source": cand.get("source") or "rules",
        "key": cand.get("key") or "", "used": 0, "used_ok": 0, "status": "active",
    }
    if cand.get("model"):
        lesson["model"] = cand["model"]
    lessons.append(lesson)
    return lesson, True


def score(lesson: dict, now: Optional[float] = None) -> float:
    """Rank for context(): confident, repeated, recent lessons that runs did well with come first."""
    now = now or time.time()
    age_days = max(0.0, now - float(lesson.get("updated") or lesson.get("created") or now)) / 86400
    decay = 0.5 ** (age_days / HALF_LIFE_DAYS)
    hits = max(1, int(lesson.get("hits") or 1))
    used, ok = int(lesson.get("used") or 0), int(lesson.get("used_ok") or 0)
    outcome = 2.0 * (ok + 1) / (used + 2)  # 1.0 with no data; up to 2 when every run that used it passed
    kind_w = {"avoid": 1.1, "do": 1.0, "tool": 1.0, "setting": 0.9}.get(lesson.get("kind"), 1.0)
    return float(lesson.get("confidence") or 0.5) * (1 + 0.5 * math.log2(hits)) * decay * outcome * kind_w


def active(lessons: list[dict], task_id: str) -> list[dict]:
    """A task's active lessons, best first. task_id "*" lessons apply to every task."""
    now = time.time()
    xs = [x for x in lessons if x.get("task_id") in (task_id, "*") and x.get("status", "active") == "active"]
    return sorted(xs, key=lambda x: (-score(x, now), -float(x.get("updated") or 0)))


def record_use(lessons: list[dict], ids: list[str], ok: bool) -> list[str]:
    """A finished run had these lessons in its instruction. Returns ids retired by this outcome. In place."""
    retired = []
    by_id = {x["id"]: x for x in lessons}
    for i in dict.fromkeys(ids):
        x = by_id.get(i)
        if x is None:
            continue
        x["used"] = int(x.get("used") or 0) + 1
        x["used_ok"] = int(x.get("used_ok") or 0) + (1 if ok else 0)
        if (x["used"] >= RETIRE_AFTER_USES and x["used_ok"] / x["used"] < RETIRE_BELOW
                and x.get("status", "active") == "active"):
            x["status"] = "retired"
            retired.append(i)
    return retired


def ids_in(text: Any) -> list[str]:
    """Lesson ids in an injected instruction block ("- [L3fa2c1] …")."""
    return list(dict.fromkeys(LESSON_ID_RE.findall(text if isinstance(text, str) else "")))


# ── distill registry ────────────────────────────────────────────────────────


def registry_file() -> Path:
    return config.home() / "distilled.json"


def registry() -> dict:
    d = read_json(registry_file(), {})
    return d if isinstance(d, dict) else {}


def register(run_id: str, entry: dict) -> None:
    reg = registry()
    reg[run_id] = entry
    _write_atomic(registry_file(), json.dumps(reg, indent=1, ensure_ascii=False))


def write_distill_record(run_id: str, record: dict) -> Path:
    p = config.home() / "distill" / f"{run_id}.json"
    _write_atomic(p, json.dumps(record, indent=1, ensure_ascii=False, default=str))
    return p
