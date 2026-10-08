"""Okapi BM25 over chunk texts, with an inverted index (no external dependency)."""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from .text import tokenize


class BM25:
    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.n = len(texts)
        self.doc_len: list[int] = []
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, text in enumerate(texts):
            counts = Counter(tokenize(text))
            self.doc_len.append(sum(counts.values()))
            for term, tf in counts.items():
                self.postings[term].append((i, tf))
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.idf = {t: math.log(1 + (self.n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}

    def term_weights(self, query: str) -> dict[str, float]:
        """idf of each query term present in the corpus (used to pick the best quote sentence)."""
        return {t: self.idf[t] for t in set(tokenize(query)) if t in self.idf}

    def scores(self, query: str, allowed: set[int] | None = None) -> dict[int, float]:
        """doc index -> score, only for docs containing at least one query term."""
        out: dict[int, float] = defaultdict(float)
        if not self.n:
            return out
        for term, qtf in Counter(tokenize(query)).items():
            idf = self.idf.get(term)
            if idf is None:
                continue
            for doc, tf in self.postings[term]:
                if allowed is not None and doc not in allowed:
                    continue
                norm = tf + self.k1 * (1 - self.b + self.b * self.doc_len[doc] / (self.avgdl or 1))
                out[doc] += qtf * idf * tf * (self.k1 + 1) / norm
        return out

    def top(self, query: str, n: int, allowed: set[int] | None = None) -> list[tuple[int, float]]:
        ranked = sorted(self.scores(query, allowed).items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[:n]
