"""Hybrid search (BM25 + embeddings, reciprocal rank fusion) and LLM context formatting with [n] citations."""
from __future__ import annotations

import logging
import re
import threading

from . import config
from .bm25 import BM25
from .store import IndexCache, IndexData
from .text import DOI_RE, clean_doi, find_dois, split_sentences, tokenize, truncate

log = logging.getLogger("rag")

RRF_K = 60          # standard reciprocal-rank-fusion constant
QUOTE_CHARS = 240   # CONTRACT: quote is ≤ 240 chars from the passage
MAX_K = 50
# Used when the query is only a DOI: the passages most useful for describing what the paper did.
METHODS_QUERY = ("methods computational details simulation model force field parameters system setup "
                 "molecular dynamics calculations results")

_cache = IndexCache()
_warned: set[str] = set()
_warn_lock = threading.Lock()


def _warn_once(msg: str, *args) -> None:
    key = msg % args
    with _warn_lock:
        if key in _warned:
            return
        _warned.add(key)
    log.warning(msg, *args)


class LoadedIndex:
    def __init__(self, data: IndexData):
        self.data = data
        self.papers = data.papers
        self.chunks = data.chunks
        self.bm25 = BM25([c["text"] for c in data.chunks])
        self.embeddings = data.embeddings
        self.by_doi: dict[str, set[int]] = {}
        for i, c in enumerate(self.chunks):
            doi = self.papers[c["paper"]].get("doi")
            if doi:
                self.by_doi.setdefault(doi, set()).add(i)


def _load(index_dir=None) -> LoadedIndex:
    return _cache.get(config.index_dir(index_dir), LoadedIndex)


def rrf(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal rank fusion: doc -> sum over rankings of 1 / (k + rank), rank starting at 1."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking, start=1):
            fused[doc] = fused.get(doc, 0.0) + 1.0 / (k + rank)
    return fused


def best_quote(text: str, query_weights: dict[str, float], limit: int = QUOTE_CHARS) -> str:
    """The sentence of `text` that best covers the query terms (idf-weighted), cut to `limit` chars."""
    sentences = split_sentences(text) or [text]
    best, best_score = sentences[0], 0.0
    for s in sentences:
        toks = set(tokenize(s))
        score = sum(w for t, w in query_weights.items() if t in toks)
        if score > best_score + 1e-9:
            best, best_score = s, score
    return truncate(best, limit)


def _citation(paper: dict, chunk: dict, quote: str, key: str) -> dict:
    return {
        "key": key,
        "title": paper.get("title") or "",
        "authors": paper.get("authors") or "",
        "year": paper.get("year"),
        "journal": paper.get("journal") or "",
        "doi": paper.get("doi") or "",
        "page": chunk.get("page"),
        "url": paper.get("url") or "",
        "quote": quote,
    }


def search(query: str, k: int = 6, *, index_dir=None, doi: str | None = None) -> list[dict]:
    """Top-k passages for `query` as CONTRACT Passage dicts:
    {"id", "text", "score" (0..1, fused), "citation": Citation with key "[1]".."[k]"}.

    `doi` restricts the search to one paper; so does a DOI written in the query (a query that is only a DOI
    returns that paper's methods-like passages). Empty index, empty query or a DOI not in the index -> [].
    """
    query = (query or "").strip()
    if not query:
        return []
    k = max(1, min(6 if k is None else int(k), MAX_K))
    idx = _load(index_dir)
    if not idx.chunks:
        return []

    if doi is None:
        # A DOI inside the query ("methods of 10.1021/…", or just the DOI) restricts the search to that paper.
        found = find_dois(query)
        if found:
            doi = found[0]
            query = DOI_RE.sub(" ", query)
            query = re.sub(r"(?:https?://)?(?:dx\.)?doi\.org/|\bdoi:?", " ", query, flags=re.I).strip(" ,;:")
            if not tokenize(query):
                query = METHODS_QUERY
    allowed = None
    if doi:
        allowed = idx.by_doi.get(clean_doi(doi))
        if not allowed:
            return []
    depth = max(50, k * 5)

    rankings = [[i for i, _ in idx.bm25.top(query, depth, allowed)]]
    n_rankers = 1
    emb_vector_ranking = _embedding_ranking(idx, query, depth, allowed)
    if emb_vector_ranking is not None:
        rankings.append(emb_vector_ranking)
        n_rankers = 2

    fused = rrf(rankings)
    if not fused:
        return []
    best_possible = n_rankers / (RRF_K + 1)
    order = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    weights = idx.bm25.term_weights(query)
    out = []
    for n, (i, score) in enumerate(order, start=1):
        chunk = idx.chunks[i]
        paper = idx.papers[chunk["paper"]]
        out.append({
            "id": chunk["id"],
            "text": chunk["text"],
            "score": round(min(1.0, score / best_possible), 4),
            "citation": _citation(paper, chunk, best_quote(chunk["text"], weights), f"[{n}]"),
        })
    return out


def _embedding_ranking(idx: LoadedIndex, query: str, depth: int, allowed: set[int] | None) -> list[int] | None:
    import numpy as np

    from .embed import get_embedder

    if idx.embeddings is None or len(idx.embeddings) == 0:
        return None
    emb = get_embedder()
    if emb is None:
        return None
    if emb.name != idx.data.embedder:
        _warn_once("index was embedded with %s but the configured embedder is %s; using BM25 only "
                   "(re-run `python -m rag ingest`)", idx.data.embedder, emb.name)
        return None
    try:
        q = emb.embed_query(query)
    except Exception as e:
        _warn_once("query embedding failed (%s); using BM25 only", e)
        return None
    sims = idx.embeddings @ q
    if allowed is not None:
        rows = np.fromiter(allowed, dtype=np.int64)
        sub = sims[rows]
        top = rows[np.argsort(-sub, kind="stable")[:depth]]
    else:
        n = min(depth, len(sims))
        part = np.argpartition(-sims, n - 1)[:n] if n < len(sims) else np.arange(len(sims))
        top = part[np.argsort(-sims[part], kind="stable")]
    return [int(i) for i in top]


def first_author(authors: str) -> str:
    """'V. Košťál; D. Biriukov' -> 'Košťál'; 'M. Riopedre Fernández; …' -> 'Riopedre Fernández'."""
    first = (authors or "").split(";")[0].split(" and ")[0].strip()
    if "," in first:  # 'Košťál, V.'
        return first.split(",")[0].strip()
    words = [w for w in first.split() if not _INITIAL.match(w)]
    return " ".join(words) if words else first


_INITIAL = re.compile(r"^(?:[A-Z][a-z]?\.)(?:-?[A-Z][a-z]?\.)*$|^[A-Z]$")


def short_ref(citation: dict) -> str:
    """'Košťál et al. 2026' style reference for speaking/reading."""
    authors = [a for a in (citation.get("authors") or "").split(";") if a.strip()]
    name = first_author(citation.get("authors") or "")
    if not name:
        name = truncate(citation.get("title") or "Unknown", 40)
    elif len(authors) == 2:
        name = f"{name} and {first_author(authors[1])}"
    elif len(authors) > 2:
        name = f"{name} et al."
    year = citation.get("year")
    return f"{name} {year}" if year else name


def _excerpt(text: str, quote: str, limit: int) -> str:
    """Up to `limit` chars of text, centered on the quote when the passage is longer."""
    if len(text) <= limit:
        return text
    anchor = text.find(quote.rstrip("…")[:80]) if quote else -1
    if anchor < 0:
        return truncate(text, limit)
    start = max(0, anchor - limit // 3)
    end = min(len(text), start + limit)
    start = max(0, end - limit)
    if start > 0:
        start = text.find(" ", start) + 1 or start
    piece = text[start:end]
    if end < len(text):
        cut = piece.rfind(" ")
        piece = (piece[:cut] if cut > len(piece) * 0.6 else piece) + "…"
    return ("…" if start > 0 else "") + piece


NO_RESULTS = ("No passages from the group's indexed papers match this question. Say that the papers available "
              "here don't cover it; don't answer from memory.")


def format_context(passages: list[dict], max_chars_per_passage: int = 1800) -> tuple[str, list[dict]]:
    """Numbered context for the LLM plus the matching citations (keys renumbered "[1]".."[n]").

    Each block: `[n] Košťál et al. 2026, "Title", Journal, p. 3, doi:…` then the passage text (trimmed around the
    quoted sentence to `max_chars_per_passage`). No passages -> an instruction to say the papers don't cover it.
    """
    if not passages:
        return NO_RESULTS, []
    blocks, citations, seen = [], [], set()
    for p in passages:
        if p.get("id") in seen:
            continue
        seen.add(p.get("id"))
        n = len(citations) + 1
        cit = dict(p.get("citation") or {})
        cit["key"] = f"[{n}]"
        citations.append(cit)
        head = [f"[{n}] {short_ref(cit)}"]
        if cit.get("title"):
            head.append(f"\"{cit['title']}\"")
        if cit.get("journal"):
            head.append(cit["journal"])
        if cit.get("page"):
            head.append(f"p. {cit['page']}")
        if cit.get("doi"):
            head.append(f"doi:{cit['doi']}")
        body = _excerpt(p.get("text") or "", cit.get("quote") or "", max_chars_per_passage)
        blocks.append(", ".join(head) + "\n" + body)
    preamble = ("Passages from the group's papers. Cite them as [n] (first author + year when speaking); "
                "use only what they say.\n\n")
    return preamble + "\n\n".join(blocks), citations


def stats(index_dir=None) -> dict:
    """{"papers", "chunks", "embedder", "created"} of the current index (zeros when there is none)."""
    from .store import read_manifest

    m = read_manifest(config.index_dir(index_dir)) or {}
    return {"papers": int(m.get("papers") or 0), "chunks": int(m.get("chunks") or 0),
            "embedder": m.get("embedder"), "created": m.get("created")}


def clear_cache() -> None:
    _cache.clear()
