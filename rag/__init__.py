"""rag: search the Jungwirth group's papers with citations.

    from rag import ingest, search, format_context
    ingest("papers/", "index/")                      # PDFs -> index/ (pymupdf, ~800-token chunks, bge-small)
    passages = search("ion pairing at the water surface", k=6)   # hybrid BM25 + embeddings, RRF
    context, citations = format_context(passages)    # "[1] Košťál et al. 2026, …" for the LLM

Every function works with zero papers: an empty index gives [] and a "papers don't cover this" context.
Heavy imports (numpy, pymupdf, fastembed) happen on first use, so `import rag` is cheap.
"""
from __future__ import annotations

from .config import DEFAULT_METADATA

__all__ = ["ingest", "search", "format_context", "stats", "warmup"]


def ingest(papers_dir=None, index_dir=None, metadata=DEFAULT_METADATA, **kwargs) -> dict:
    from .indexer import ingest as _ingest

    return _ingest(papers_dir, index_dir, metadata, **kwargs)


def search(query: str, k: int = 6, **kwargs) -> list[dict]:
    from .retrieval import search as _search

    return _search(query, k, **kwargs)


def format_context(passages: list[dict], **kwargs) -> tuple[str, list[dict]]:
    from .retrieval import format_context as _format_context

    return _format_context(passages, **kwargs)


def stats(index_dir=None) -> dict:
    from .retrieval import stats as _stats

    return _stats(index_dir)


def warmup(index_dir=None) -> dict:
    """Load the embedding model (downloading it if needed) and the index, so the first real query is fast."""
    from .embed import get_embedder
    from .retrieval import _load

    emb = get_embedder()
    idx = _load(index_dir)
    return {"embedder": getattr(emb, "name", None), "papers": len(idx.papers), "chunks": len(idx.chunks)}
