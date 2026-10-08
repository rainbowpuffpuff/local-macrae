"""Match a PDF to an entry of data/group_publications.json: by DOI (sidecar, file name, PDF metadata, first
page), then by fuzzy title. Unmatched PDFs still get a usable record from the PDF itself."""
from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path

from .text import clean_doi, find_dois, fold

log = logging.getLogger("rag")

# Fields of a paper record that end up in citations.
PAPER_FIELDS = ("doi", "title", "authors", "year", "journal", "url")


class Publications:
    """The group's publication list, indexed for DOI and title lookups."""

    def __init__(self, entries: list[dict]):
        self.entries = entries
        self.by_doi = {clean_doi(e["doi"]): e for e in entries if e.get("doi")}
        self._by_fname = {d.replace("/", "_"): d for d in self.by_doi}
        self._titles = []
        for e in entries:
            ft = fold(e.get("title") or "")
            if ft:
                words = ft.split()
                self._titles.append((e, ft, words, {w for w in words if len(w) > 2}))

    @classmethod
    def load(cls, path: str | Path | None) -> "Publications":
        if not path:
            return cls([])
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("could not read publications list %s: %s", path, e)
            return cls([])
        return cls([e for e in data if isinstance(e, dict)])

    def __len__(self):
        return len(self.entries)

    def doi_from_filename(self, stem: str) -> str | None:
        s = stem.strip().lower().replace("%2f", "/")
        if s.startswith("doi_") or s.startswith("doi:"):
            s = s[4:]
        if s in self.by_doi:
            return s
        if s in self._by_fname:
            return self._by_fname[s]
        if s.startswith("10.") and "_" in s:
            cand = s.replace("_", "/", 1)
            if cand in self.by_doi:
                return cand
        return None

    def match_title(self, head_text: str, pdf_title: str = "") -> tuple[dict, float] | None:
        """Best publication whose title appears in the first pages (or matches the PDF's title metadata)."""
        head = fold(re.sub(r"(\w)-\n(?=[a-z])", r"\1", head_text))  # undo line-break hyphenation
        head_words = head.split()
        head_set = set(head_words)
        top = head[:400]
        ptitle = fold(pdf_title)
        best: tuple[dict, float] | None = None
        for entry, ft, words, keyset in self._titles:
            score = 0.0
            if ft in head and (len(ft) >= 25 or ft in top):
                score = 1.0
            elif ptitle and len(ft) >= 15:
                score = max(score, SequenceMatcher(None, ft, ptitle).ratio())
            if score < 0.9 and len(keyset) >= 4 and len(keyset & head_set) / len(keyset) >= 0.8:
                score = max(score, _ordered_coverage(words, head_words))
            if score >= 0.88 and (best is None or score > best[1]):
                best = (entry, score)
        return best


def _ordered_coverage(title: list[str], head: list[str]) -> float:
    """Fraction of title words found in order inside a window of ~1.5x title length somewhere in head."""
    n = len(title)
    window = int(n * 1.5) + 3
    first = set(title[:3])
    best = 0
    for i, w in enumerate(head):
        if w not in first:
            continue
        j = 0
        for hw in head[i : i + window]:
            if j < n and hw == title[j]:
                j += 1
            elif j + 1 < n and hw == title[j + 1]:   # tolerate one word the PDF rendered differently
                j += 2
        best = max(best, j)
        if best >= n:
            break
    return min(best, n) / n


def _year(value) -> int | None:
    try:
        y = int(value)
    except (TypeError, ValueError):
        return None
    return y if 1900 < y < 2100 and y != 1970 else None  # the site shows 1970 for "no year yet"


def _record_from_entry(entry: dict) -> dict:
    doi = clean_doi(entry["doi"]) if entry.get("doi") else ""
    return {
        "doi": doi,
        "title": (entry.get("title") or "").strip(),
        "authors": (entry.get("authors") or "").strip(),
        "year": _year(entry.get("year")),
        "journal": (entry.get("journal") or "").strip(),
        "url": f"https://doi.org/{doi}" if doi else (entry.get("link") or entry.get("url") or ""),
    }


def _fallback_title(doc_meta: dict, raw_head: str, stem: str) -> str:
    title = (doc_meta.get("title") or "").strip()
    if len(title) >= 8 and not title.lower().endswith((".pdf", ".doc", ".docx", ".tex")):
        return title
    for line in raw_head.split("\n"):
        line = line.strip()
        if len(line) >= 15 and not line.lower().startswith(("doi", "http", "received", "cite this")):
            return line[:200]
    return stem


def match_paper(pdf_path: Path, raw_head: str, doc_meta: dict, pubs: Publications) -> dict:
    """Citation metadata for one PDF, plus `matched_by` (sidecar|filename|pdf-metadata|doi|title|none)."""
    sidecar = _read_sidecar(pdf_path)
    candidates: list[tuple[str, str]] = []
    if sidecar.get("doi"):
        candidates.append((clean_doi(sidecar["doi"]), "sidecar"))
    fname_doi = pubs.doi_from_filename(pdf_path.stem)
    if fname_doi:
        candidates.append((fname_doi, "filename"))
    for key in ("subject", "keywords", "title"):
        candidates += [(d, "pdf-metadata") for d in find_dois(doc_meta.get(key) or "")]
    candidates += [(d, "doi") for d in find_dois(raw_head.split("\f")[0])]

    record, matched_by = None, "none"
    for doi, how in candidates:
        if doi in pubs.by_doi:
            record, matched_by = _record_from_entry(pubs.by_doi[doi]), how
            break
    if record is None:
        hit = pubs.match_title(raw_head, doc_meta.get("title", ""))
        if hit:
            record, matched_by = _record_from_entry(hit[0]), "title"
    if record is None:
        # Not a group paper (e.g. a cited paper): build the citation from the PDF itself.
        doi = next((d for d, how in candidates if how in ("sidecar", "pdf-metadata", "doi")), "")
        record = {
            "doi": doi,
            "title": _fallback_title(doc_meta, raw_head, pdf_path.stem),
            "authors": (doc_meta.get("author") or "").strip(),
            "year": None,
            "journal": "",
            "url": f"https://doi.org/{doi}" if doi else "",
        }
        if doi:
            matched_by = "doi-unlisted"
    # A sidecar <name>.json next to the PDF overrides any field.
    for k in PAPER_FIELDS:
        if sidecar.get(k) not in (None, ""):
            record[k] = _year(sidecar[k]) if k == "year" else (clean_doi(sidecar[k]) if k == "doi" else sidecar[k])
    if sidecar.get("doi") and not sidecar.get("url"):
        record["url"] = f"https://doi.org/{record['doi']}"
    record["matched_by"] = matched_by
    return record


def _read_sidecar(pdf_path: Path) -> dict:
    side = pdf_path.with_suffix(".json")
    if not side.exists():
        return {}
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        log.warning("ignoring unreadable sidecar %s: %s", side, e)
        return {}
