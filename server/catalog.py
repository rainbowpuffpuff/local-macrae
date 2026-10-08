"""Task catalog (tasks/tasks.json) and paper metadata (data/group_publications.json)."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Optional

from . import config

_lock = threading.Lock()
_tasks_cache: dict[str, Any] = {"key": None, "tasks": []}
_pubs_cache: dict[str, Any] = {"key": None, "by_doi": {}}


def _mtime_key(p: Path) -> Optional[tuple]:
    try:
        st = p.stat()
        return (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _normalize_task(t: Any) -> Optional[dict]:
    if not isinstance(t, dict) or not str(t.get("id") or "").strip():
        return None
    inputs = []
    for i in t.get("inputs") or []:
        if isinstance(i, dict) and i.get("name"):
            inputs.append({"name": str(i["name"]), "label": str(i.get("label") or i["name"]),
                           "default": i.get("default", ""), **{k: v for k, v in i.items()
                                                                 if k not in ("name", "label", "default")}})
    out = dict(t)
    out.update({"id": str(t["id"]).strip(), "title": str(t.get("title") or t["id"]),
                "subtitle": str(t.get("subtitle") or ""), "icon": str(t.get("icon") or ""),
                "prompt": str(t.get("prompt") or ""), "flow": str(t.get("flow") or ""), "inputs": inputs})
    return out


def load_tasks() -> list[dict]:
    """Tasks from MACRAE_TASKS_FILE: a JSON list of Task, or {"tasks": [...]}. Missing/broken file → []."""
    p = config.tasks_file()
    key = _mtime_key(p)
    with _lock:
        if key is not None and key == _tasks_cache["key"]:
            return _tasks_cache["tasks"]
    data = config.read_json(p) if key else None
    raw = data.get("tasks") if isinstance(data, dict) else data
    tasks = [x for x in (_normalize_task(t) for t in (raw or [])) if x] if isinstance(raw, list) else []
    with _lock:
        _tasks_cache.update(key=key, tasks=tasks)
    return tasks


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def find_task(ref: str, fuzzy: bool = False) -> Optional[dict]:
    """By id; with fuzzy=True also by title/slug (the voice agent may say 'the methods card task')."""
    ref = (ref or "").strip()
    tasks = load_tasks()
    for t in tasks:
        if t["id"] == ref:
            return t
    if not fuzzy or not ref:
        return None
    s = _slug(ref)
    for t in tasks:
        if s in (_slug(t["id"]), _slug(t["title"])):
            return t
    hits = [t for t in tasks if s and (s in _slug(t["title"]) or _slug(t["id"]) in s or s in _slug(t["id"]))]
    return hits[0] if len(hits) == 1 else None


def task_flow_path(task: dict) -> Optional[Path]:
    f = task.get("flow")
    if not f:
        return None
    p = Path(f).expanduser()
    if not p.is_absolute():
        p = config.REPO_ROOT / p
    return p.resolve()


def task_for_run(state: dict) -> Optional[dict]:
    """Which task a run belongs to when we have no sidecar: same flow file, or the flow's name equals a task id."""
    file = str(state.get("file") or "")
    name = str(state.get("name") or "")
    tasks = load_tasks()
    if file:
        try:
            fp = Path(file).resolve()
        except OSError:
            fp = None
        for t in tasks:
            if fp is not None and task_flow_path(t) == fp:
                return t
    for t in tasks:
        if name and name in (t["id"], _slug(t["id"])):
            return t
    tid = (state.get("vars") or {}).get("task_id") if isinstance(state.get("vars"), dict) else None
    return find_task(str(tid)) if tid else None


# ── publications ────────────────────────────────────────────────────────────

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>()\[\]{}]+", re.I)


def _pubs() -> dict[str, dict]:
    p = config.publications_file()
    key = _mtime_key(p)
    with _lock:
        if key == _pubs_cache["key"]:
            return _pubs_cache["by_doi"]
    data = config.read_json(p) if key else None
    by_doi = {}
    for row in data if isinstance(data, list) else []:
        if isinstance(row, dict) and row.get("doi"):
            by_doi[str(row["doi"]).lower()] = row
    with _lock:
        _pubs_cache.update(key=key, by_doi=by_doi)
    return by_doi


def find_doi(text: str) -> Optional[str]:
    """First DOI in a path/url/text. File names often encode '/' as '_' or '-' (10.1021_acs.jctc.5c02051.pdf)."""
    if not text:
        return None
    m = DOI_RE.search(text) or _DOI_FILE_RE.search(text)
    if not m:
        return None
    doi = _EXT_RE.sub("", m.group(0).rstrip(".,;:"))
    if "/" in doi:
        return doi
    # 10.1093_glycob_cwag064: the suffix may itself contain '/', so prefer the variant that is a group paper
    variants = [doi.replace("_", "/", 1), doi.replace("_", "/")]
    known = _pubs()
    return next((v for v in variants if v.lower() in known), variants[0])


_DOI_FILE_RE = re.compile(r"10\.\d{4,9}_[A-Za-z0-9._\-]+")
_EXT_RE = re.compile(r"\.(pdf|json|txt|md|html|xml)$", re.I)


def citation_for_doi(doi: Optional[str]) -> Optional[dict]:
    """A Citation built from group_publications.json, or None if the DOI isn't a group paper."""
    if not doi:
        return None
    row = _pubs().get(doi.lower())
    if not row:
        return None
    year = row.get("year")
    return {"key": "", "title": row.get("title") or "", "authors": row.get("authors") or "",
            "year": year if isinstance(year, int) and year > 1970 else None, "journal": row.get("journal") or "",
            "doi": row["doi"], "page": None, "url": f"https://doi.org/{row['doi']}", "quote": ""}
