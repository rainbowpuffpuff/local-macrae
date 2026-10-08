"""Lazy imports of the other modules, so the server starts (and /api/health answers) even if they are missing.

rag:          rag.search(query, k=6) -> list[Passage]; rag.format_context(passages) -> (str, citations)
tasks.runner: start(task_id, inputs) -> run_id
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from . import classify, config

log = logging.getLogger("macrae.server")


class Unavailable(RuntimeError):
    """A module the request needs is missing or failed to import."""


def _ensure_repo_on_path() -> None:
    root = str(config.REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


_import_lock = threading.Lock()


def _import(name: str) -> Any:
    _ensure_repo_on_path()
    with _import_lock:
        try:
            return importlib.import_module(name)
        except Exception as e:  # ImportError, or the module's own import-time error
            raise Unavailable(f"{name} is not available: {type(e).__name__}: {e}") from e


def rag_module() -> Any:
    return _import("rag")


def runner_module() -> Any:
    return _import("tasks.runner")


# ── rag ─────────────────────────────────────────────────────────────────────


def to_plain(obj: Any) -> Any:
    """Passage/Citation as plain JSON data, whether rag returns dicts, dataclasses or pydantic models."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    if hasattr(obj, "model_dump"):
        return to_plain(obj.model_dump())
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return to_plain(dataclasses.asdict(obj))
    for meth in ("to_dict", "dict", "_asdict"):
        f = getattr(obj, meth, None)
        if callable(f):
            try:
                return to_plain(f())
            except Exception:
                pass
    if hasattr(obj, "__dict__"):
        return to_plain({k: v for k, v in vars(obj).items() if not k.startswith("_")})
    return str(obj)


def normalize_passage(p: Any, i: int) -> Optional[dict]:
    d = to_plain(p)
    if not isinstance(d, dict):
        return None
    text = str(d.get("text") or "")
    cit = classify.normalize_citation(d.get("citation") or {}, text) or {
        "key": f"[{i + 1}]", "title": "", "authors": "", "year": None, "journal": "", "doi": "", "page": None,
        "url": "", "quote": classify.clip(text, 240)}
    if not cit.get("key"):
        cit["key"] = f"[{i + 1}]"
    try:
        score = float(d.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return {"id": str(d.get("id") or f"p{i + 1}"), "text": text, "score": score, "citation": cit}


def search(query: str, k: int = 6) -> list[dict]:
    rag = rag_module()
    raw = rag.search(query, k=k)
    out = [normalize_passage(p, i) for i, p in enumerate(raw or [])]
    return [p for p in out if p]


def search_with_context(query: str, k: int = 6) -> tuple[str, list[dict]]:
    """(answer_context, citations) for the voice agent's search_papers tool."""
    rag = rag_module()
    raw = list(rag.search(query, k=k) or [])
    if not raw:
        return "", []
    fmt = getattr(rag, "format_context", None)
    if callable(fmt):
        ctx, cits = fmt(raw)
        cits = [c for c in (classify.normalize_citation(to_plain(c)) for c in (cits or [])) if c]
        return str(ctx or ""), cits
    passages = [p for p in (normalize_passage(p, i) for i, p in enumerate(raw)) if p]
    parts, cits = [], []
    for i, p in enumerate(passages):
        c = dict(p["citation"], key=f"[{i + 1}]")
        cits.append(c)
        head = ", ".join(str(x) for x in (c["authors"].split(";")[0].strip(), c["year"], c["title"]) if x)
        parts.append(f"[{i + 1}] {head}" + (f" (p. {c['page']})" if c.get("page") else "") + f"\n{p['text']}")
    return "\n\n".join(parts), cits


_stats_cache: dict[str, Any] = {"key": None, "val": (0, 0)}


def index_stats() -> tuple[int, int]:
    """(papers, chunks) for /api/health. Asks rag if it offers stats(); otherwise reads the index folder."""
    try:
        rag = sys.modules.get("rag") or (rag_module() if (config.REPO_ROOT / "rag").is_dir() else None)
    except Unavailable:
        rag = None
    for name in ("stats", "index_stats", "info"):
        f = getattr(rag, name, None) if rag else None
        if callable(f):
            try:
                d = to_plain(f())
                if isinstance(d, dict):
                    return int(d.get("papers") or d.get("n_papers") or 0), int(d.get("chunks") or d.get("n_chunks")
                                                                               or 0)
            except Exception as e:
                log.warning("rag.%s() failed: %s", name, e)
    return _stats_from_disk(config.index_dir(), config.papers_dir())


def _stats_from_disk(index: Path, papers: Path) -> tuple[int, int]:
    try:
        key = (str(index), max((p.stat().st_mtime_ns for p in index.iterdir()), default=0), str(papers))
    except OSError:
        key = (str(index), 0, str(papers))
    if key == _stats_cache["key"]:
        return _stats_cache["val"]
    n_papers, n_chunks = 0, 0
    for name in ("manifest.json", "meta.json", "stats.json", "index.json", "info.json"):
        d = config.read_json(index / name)
        if isinstance(d, dict):
            n_papers = _count(d, ("papers", "n_papers", "num_papers", "documents"))
            n_chunks = _count(d, ("chunks", "n_chunks", "num_chunks", "passages"))
            if n_papers or n_chunks:
                break
    if not n_chunks:
        for name in ("chunks.jsonl", "passages.jsonl"):
            p = index / name
            if p.is_file():
                papers_seen: set[str] = set()
                try:
                    with open(p, errors="replace") as f:
                        for ln in f:
                            if not ln.strip():
                                continue
                            n_chunks += 1
                            try:
                                r = json.loads(ln)
                                c = r.get("citation") or r
                                papers_seen.add(str(c.get("doi") or c.get("title") or r.get("paper") or ""))
                            except (ValueError, AttributeError):
                                pass
                except OSError:
                    pass
                n_papers = n_papers or len(papers_seen - {""})
                break
    if not n_chunks:
        for name in ("chunks.json", "passages.json"):
            d = config.read_json(index / name)
            if isinstance(d, list):
                n_chunks = len(d)
                n_papers = n_papers or len({str((x.get("citation") or x).get("doi") or "") for x in d
                                            if isinstance(x, dict)} - {""})
                break
    if not n_papers:
        try:
            n_papers = sum(1 for p in papers.rglob("*.pdf"))
        except OSError:
            n_papers = 0
    _stats_cache.update(key=key, val=(n_papers, n_chunks))
    return n_papers, n_chunks


def _count(d: dict, names: tuple[str, ...]) -> int:
    for n in names:
        v = d.get(n)
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            return v
        if isinstance(v, (list, dict)):
            return len(v)
    return 0


# ── tasks.runner ────────────────────────────────────────────────────────────


def start_task(task_id: str, inputs: dict) -> str:
    runner = runner_module()
    run_id = runner.start(task_id, inputs)
    if not run_id or not isinstance(run_id, str):
        raise RuntimeError(f"tasks.runner.start returned {run_id!r}, expected a run id")
    return run_id
