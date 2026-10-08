"""Split a paper's pages into ~800-token chunks of whole sentences, each tagged with the page it starts on."""
from __future__ import annotations

from . import config
from .text import approx_tokens, split_sentences

CHUNKER_VERSION = 2  # bump when text extraction or chunking changes, so ingest re-chunks cached papers


def _units(pages: list[str], budget: int) -> list[tuple[str, int, int]]:
    """(sentence, page number, token estimate); over-long 'sentences' (tables, formulas) are split by words."""
    out = []
    for page_no, text in enumerate(pages, start=1):
        for sent in split_sentences(text):
            n = approx_tokens(sent)
            if n <= budget:
                out.append((sent, page_no, n))
                continue
            words = sent.split()
            step = max(1, int(len(words) * budget / n))
            for i in range(0, len(words), step):
                piece = " ".join(words[i : i + step])
                out.append((piece, page_no, approx_tokens(piece)))
    return out


def chunk_pages(pages: list[str], prefix: str, budget: int | None = None, overlap: int | None = None) -> list[dict]:
    """Chunks as dicts: id ('<prefix>#p<page>c<n>'), text, page (start), page_end, tokens.
    Defaults: config.CHUNK_TOKENS (~800) and config.CHUNK_OVERLAP_TOKENS."""
    budget = budget or config.CHUNK_TOKENS
    overlap = config.CHUNK_OVERLAP_TOKENS if overlap is None else overlap
    units = _units(pages, budget)
    groups: list[list[tuple[str, int, int]]] = []
    cur: list[tuple[str, int, int]] = []
    cur_tokens = 0
    fresh = 0  # sentences in `cur` that are not overlap from the previous chunk
    for unit in units:
        if cur and fresh and cur_tokens + unit[2] > budget:
            groups.append(cur)
            tail, tail_tokens = [], 0
            for u in reversed(cur):
                if tail_tokens + u[2] > overlap or len(tail) + 1 >= len(cur):
                    break
                tail.insert(0, u)
                tail_tokens += u[2]
            cur, cur_tokens, fresh = tail, tail_tokens, 0
        cur.append(unit)
        cur_tokens += unit[2]
        fresh += 1
    if cur and fresh:
        last_tokens = sum(u[2] for u in cur)
        if groups and last_tokens < budget // 5:
            prev = groups[-1]
            overlap_n = len(cur) - fresh
            groups[-1] = prev + cur[overlap_n:]
        else:
            groups.append(cur)

    chunks, per_page = [], {}
    for g in groups:
        page = g[0][1]
        per_page[page] = per_page.get(page, 0) + 1
        text = " ".join(u[0] for u in g)
        chunks.append({
            "id": f"{prefix}#p{page}c{per_page[page]}",
            "text": text,
            "page": page,
            "page_end": g[-1][1],
            "tokens": approx_tokens(text),
        })
    return chunks
