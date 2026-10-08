"""Embedders: fastembed (BAAI/bge-small-en-v1.5, the default) and a deterministic hashing embedder for tests or
offline machines. `MACRAE_EMBEDDER` = fastembed (default) | hash | none.

If fastembed can't load (not installed, model download fails), search degrades to BM25 only instead of failing.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time

import numpy as np

from .config import EMBED_MODEL
from .text import tokenize

log = logging.getLogger("rag")

# bge-small reads at most 512 word pieces; longer chunks are embedded in windows and averaged.
_WINDOW_WORDS = 320


class FastEmbedder:
    def __init__(self, model: str = EMBED_MODEL):
        from fastembed import TextEmbedding

        cache = os.environ.get("FASTEMBED_CACHE_PATH") or None
        self.model = TextEmbedding(model_name=model, cache_dir=cache)
        self.name = f"fastembed:{model}"
        self._lock = threading.Lock()
        self.dim = len(self.embed_query("probe"))

    def embed_documents(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        windows, owner = [], []
        for i, text in enumerate(texts):
            words = text.split() or [""]
            for start in range(0, len(words), _WINDOW_WORDS):
                windows.append(" ".join(words[start : start + _WINDOW_WORDS]))
                owner.append(i)
        if not windows:
            return np.zeros((0, getattr(self, "dim", 0)), dtype=np.float32)
        with self._lock:
            vecs = np.asarray(list(self.model.embed(windows, batch_size=batch_size)), dtype=np.float32)
        out = np.zeros((len(texts), vecs.shape[1]), dtype=np.float32)
        np.add.at(out, np.asarray(owner), vecs)
        return _normalize(out)

    def embed_query(self, text: str) -> np.ndarray:
        with self._lock:
            vec = np.asarray(next(iter(self.model.query_embed(text))), dtype=np.float32)
        return _normalize(vec[None, :])[0]


class HashEmbedder:
    """Feature hashing of word and word-bigram tokens. Deterministic, dependency-free, weaker than a real model."""

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.name = f"hash:{dim}"

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        toks = tokenize(text)
        for feat in toks + [a + "_" + b for a, b in zip(toks, toks[1:])]:
            h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "little")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        return v

    def embed_documents(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _normalize(np.stack([self._vec(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize(self._vec(text)[None, :])[0]


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


_cache: dict[str, tuple[object, float]] = {}
_cache_lock = threading.Lock()
_RETRY_AFTER_S = 300.0  # a failed model load (e.g. no network yet) is retried after this long


def get_embedder(kind: str | None = None):
    """The configured embedder (cached per process), or None when embeddings are disabled/unavailable."""
    kind = (kind or os.environ.get("MACRAE_EMBEDDER") or "fastembed").strip().lower()
    with _cache_lock:
        if kind in _cache:
            emb, when = _cache[kind]
            if emb is not None or kind not in ("fastembed", "bge") or time.monotonic() - when < _RETRY_AFTER_S:
                return emb
        emb = None
        if kind == "hash":
            emb = HashEmbedder()
        elif kind in ("fastembed", "bge"):
            try:
                emb = FastEmbedder()
            except Exception as e:  # import error, download failure, corrupt cache …
                log.warning("embeddings unavailable, using BM25 only (%s: %s)", type(e).__name__, e)
        elif kind not in ("none", "off", "bm25"):
            log.warning("unknown MACRAE_EMBEDDER=%r, using BM25 only", kind)
        _cache[kind] = (emb, time.monotonic())
        return emb
