"""PDF -> cleaned text per page (pymupdf), with the reference list cut off."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .text import normalize_unicode

_REFERENCES_HEADING = re.compile(
    r"^\s*(?:\d+\.?\s*)?(references(?: and notes)?|notes and references|bibliography|literature cited|"
    r"works cited|cited literature)\s*:?\s*$",
    re.IGNORECASE,
)


@dataclass
class PdfDoc:
    path: Path
    pages: list[str]                      # cleaned running text, index 0 = page 1
    raw_head: str                         # raw text of pages 1-2 (line breaks kept, pages split by \f)
    meta: dict = field(default_factory=dict)  # PDF metadata: title, author, subject, keywords
    n_pages: int = 0


def _clean_page(raw: str) -> str:
    text = normalize_unicode(raw)
    text = re.sub(r"(\w)-\n(?=[a-z])", r"\1", text)   # re-join words hyphenated across lines
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _line_key(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip().lower())


def _strip_running_lines(raw_pages: list[str]) -> list[str]:
    """Remove running headers/footers (journal name, title, 'Page 3 of 12') and bare page numbers: lines among
    the first/last 3 of a page that recur (digits ignored) on at least 40% of pages."""
    counts: dict[str, int] = {}
    for page in raw_pages if len(raw_pages) >= 3 else []:
        lines = [l for l in page.split("\n") if l.strip()]
        for key in {_line_key(l) for l in lines[:3] + lines[-3:]}:
            counts[key] = counts.get(key, 0) + 1
    threshold = max(3, int(0.4 * len(raw_pages) + 0.999))
    running = {k for k, n in counts.items() if n >= threshold}
    out = []
    for page in raw_pages:
        lines = page.split("\n")
        nonblank = [i for i, l in enumerate(lines) if l.strip()]
        edge = set(nonblank[:3] + nonblank[-3:])
        out.append("\n".join(l for i, l in enumerate(lines)
                             if not (i in edge and (_line_key(l) in running or re.fullmatch(r"\s*\d{1,4}\s*", l)))))
    return out


def _cut_references(raw_pages: list[str]) -> list[str]:
    """Drop everything from a 'References' heading on, if it appears in the second half of the document."""
    n = len(raw_pages)
    start = n // 2 if n > 2 else min(1, n - 1)
    for i in range(start, n):
        lines = raw_pages[i].split("\n")
        for j, line in enumerate(lines):
            if _REFERENCES_HEADING.match(line):
                return raw_pages[:i] + ["\n".join(lines[:j])]
    return raw_pages


def read_pdf(path: str | Path) -> PdfDoc:
    """Read a PDF. Raises on unreadable/encrypted files; the caller decides whether to skip."""
    import pymupdf  # imported lazily so `import rag` works without pymupdf installed

    path = Path(path)
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("encrypted PDF")
        raw_pages = [page.get_text("text") for page in doc]
        meta = {k: (v or "").strip() for k, v in (doc.metadata or {}).items()
                if k in ("title", "author", "subject", "keywords")}
    head = normalize_unicode("\f".join(raw_pages[:2]))
    pages = [_clean_page(p) for p in _cut_references(_strip_running_lines(raw_pages))]
    return PdfDoc(path=path, pages=pages, raw_head=head, meta=meta, n_pages=len(raw_pages))
