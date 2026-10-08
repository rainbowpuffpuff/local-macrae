"""Where task flows get their sources: the group's publication list, the RAG index, OpenAlex abstracts.

Everything here degrades gracefully: no RAG package or an empty index gives no passages (not a crash), no network
gives no abstract. Sources are numbered [1], [2], … and carry a Citation shaped like the contract's.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLICATIONS = REPO_ROOT / "data" / "group_publications.json"
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
QUOTE_MAX = 240


def log(msg: str) -> None:
    """Progress for the step log (stderr; stdout is the step's JSON output)."""
    print(msg, file=sys.stderr, flush=True)


# ── publications ────────────────────────────────────────────────────────────


def norm_doi(doi: Any) -> str:
    s = str(doi or "").strip()
    s = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", s, flags=re.I)
    return s.lower()


def load_publications(path: Path = PUBLICATIONS) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    return [p for p in data if isinstance(p, dict)] if isinstance(data, list) else []


def find_publication(doi: str, pubs: Optional[list[dict]] = None) -> Optional[dict]:
    want = norm_doi(doi)
    for p in pubs if pubs is not None else load_publications():
        if want and norm_doi(p.get("doi")) == want:
            return p
    return None


def citation_from_publication(p: dict, quote: str = "", page: Optional[int] = None) -> dict:
    doi = str(p.get("doi") or "")
    return {
        "key": "", "title": p.get("title") or "", "authors": p.get("authors") or "", "year": p.get("year"),
        "journal": p.get("journal") or "", "doi": doi, "page": page,
        "url": f"https://doi.org/{doi}" if doi else (p.get("link") or ""), "quote": shorten(quote, QUOTE_MAX),
    }


def first_author(authors: str) -> str:
    a = (authors or "").split(";")[0].strip()
    return a.split()[-1] if a else ""


def shorten(text: Any, n: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ── RAG ─────────────────────────────────────────────────────────────────────


def _as_dict(x: Any) -> dict:
    if isinstance(x, dict):
        return x
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return dataclasses.asdict(x)
    for m in ("model_dump", "dict", "to_dict"):
        if callable(getattr(x, m, None)):
            try:
                return dict(getattr(x, m)())
            except Exception:
                pass
    return dict(getattr(x, "__dict__", {}) or {})


def normalize_passage(x: Any) -> Optional[dict]:
    d = _as_dict(x)
    text = str(d.get("text") or "").strip()
    if not text:
        return None
    cit = _as_dict(d.get("citation") or {})
    return {"id": str(d.get("id") or ""), "text": text, "score": d.get("score"), "citation": cit}


def rag_search(query: str, k: int = 6) -> list[dict]:
    """Passages from the rag package (contract: rag.search(query, k) -> list[Passage]).

    Falls back to the CLI (`python -m rag search …`, JSON on stdout) when the package can't be imported here.
    Any failure means "no passages": tasks must work before papers are ingested.
    """
    try:
        import rag  # noqa: PLC0415  (owned by the rag module; optional at import time)
        found = rag.search(query, k=k)
        out = [p for p in (normalize_passage(x) for x in found or []) if p]
        log(f"search: {query!r} → {len(out)} passages")
        return out
    except ImportError:
        pass
    except Exception as e:  # index missing/corrupt: no passages, but say why in the log
        log(f"search: {query!r} failed: {type(e).__name__}: {e}")
        return []
    try:
        r = subprocess.run([sys.executable, "-m", "rag", "search", query, "--k", str(k), "--json"],
                           capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT))
        data = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []
        if isinstance(data, dict):
            data = data.get("passages") or []
        out = [p for p in (normalize_passage(x) for x in data) if p]
        log(f"search (cli): {query!r} → {len(out)} passages")
        return out
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        log(f"search: rag unavailable ({type(e).__name__}); continuing without passages")
        return []


def dedupe(passages: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for p in passages:
        key = p.get("id") or p["text"][:200]
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


# ── OpenAlex ────────────────────────────────────────────────────────────────


def openalex_abstract(doi: str, timeout: float = 20) -> Optional[str]:
    if os.environ.get("MACRAE_OFFLINE"):
        return None
    url = "https://api.openalex.org/works/" + urllib.parse.quote(f"https://doi.org/{doi}", safe="")
    req = urllib.request.Request(url, headers={"User-Agent": "local-macrae tasks (mailto:none@example.org)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            w = json.load(r)
    except Exception as e:
        log(f"openalex: no abstract for {doi} ({type(e).__name__})")
        return None
    inv = w.get("abstract_inverted_index") or {}
    words = sorted((i, word) for word, idx in inv.items() for i in idx)
    text = " ".join(word for _, word in words).strip()
    log(f"openalex: abstract for {doi}: {len(text.split())} words")
    return text or None


# ── numbered sources ────────────────────────────────────────────────────────


def number_sources(items: list[dict]) -> list[dict]:
    """items: {"text", "citation", "origin"} → adds n and key "[n]" (citation.key too)."""
    out = []
    for i, it in enumerate(items, 1):
        cit = dict(it.get("citation") or {})
        cit["key"] = f"[{i}]"
        if not cit.get("quote"):
            cit["quote"] = shorten(it.get("text", ""), QUOTE_MAX)
        out.append({"n": i, "key": f"[{i}]", "origin": it.get("origin", "rag"), "text": it.get("text", ""),
                    "citation": cit})
    return out


def context_markdown(sources: list[dict], header: str = "") -> str:
    parts = [header.strip() + "\n"] if header else []
    for s in sources:
        c = s["citation"]
        where = ", ".join(x for x in (
            f"{c.get('authors') or '?'}", str(c.get("year") or ""), c.get("journal") or "",
            f"DOI {c['doi']}" if c.get("doi") else "", f"p. {c['page']}" if c.get("page") else "") if x)
        parts.append(f"## {s['key']} {c.get('title') or 'untitled'}\n_{where}_ — source: {s['origin']}\n\n"
                     f"{s['text'].strip()}\n")
    return "\n".join(parts)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False))
