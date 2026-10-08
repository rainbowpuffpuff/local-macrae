"""PDF -> cleaned text per page (pymupdf), with running headers, boilerplate and back matter removed.

What `read_pdf` fixes, in order (what it did to a file is listed in `PdfDoc.issues`):
1. Layout. Lines come from pymupdf's "dict" output. Vertical text (margin stamps such as "Downloaded via …") is
   dropped. Pages where nearly every word is its own block (some OUP PDFs place kerned letter pairs separately:
   "Secondar y", "Chemistr y") are rebuilt from word positions, so touching words join again. Pages with images
   but no text layer are OCR'd when Tesseract is available (pymupdf's OCR), otherwise reported.
2. Characters: soft hyphens, drop caps ("W" + "ater" -> "Water"), spacing accents rejoined with their letter
   ("F´abi´an" -> "Fábián", "Pˇredota" -> "Předota"), Elsevier math-font glyphs in documents that use them
   ("ð1Þ" -> "(1)", "¼" -> "=", "þ" -> "+").
3. Running headers/footers: lines in the top/bottom margin (or the first/last lines of a page) that recur, digits
   ignored, on ≥30% of the pages; bare page numbers there; publisher boilerplate (ACS "Cite This / Read Online /
   Metrics & More", "Downloaded from …", licence and received/accepted lines) on page 1 and in margins.
4. Back matter: the reference list (heading "References", "■REFERENCES", "R E F E R E N C E S", "Notes and
   references", … or a "[1] …" list without a heading) is removed up to the end of the document or up to a section
   that follows it (Cell Press STAR★Methods, appendices, supplementary text); so are ACS "AUTHOR INFORMATION",
   author-contribution, competing-interest, ORCID and publisher-note blocks in the second half.
5. Per page: unicode normalization (ligatures), words hyphenated across lines re-joined, whitespace collapsed.

`detect_si` tells Supplementary/Supporting Information files apart from papers.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .text import normalize_unicode

TEXT_PIPELINE_VERSION = 3  # bump when extraction/cleaning changes (part of the index's chunker key)


@dataclass
class PdfDoc:
    path: Path
    pages: list[str]                      # cleaned running text, index 0 = page 1
    raw_head: str                         # text of pages 1-2 before cleaning (line breaks kept, pages split by \f)
    meta: dict = field(default_factory=dict)  # PDF metadata: title, author, subject, keywords
    n_pages: int = 0
    issues: list[str] = field(default_factory=list)   # what was fixed, or couldn't be (for the ingest report)
    no_text_pages: list[int] = field(default_factory=list)


@dataclass
class _Line:
    text: str
    y0: float
    y1: float


# ── 1. layout ───────────────────────────────────────────────────────────────────────────────────────────────────


def _dict_lines(page) -> tuple[list[_Line], int, int, int]:
    """Horizontal text lines in content order, plus (vertical lines dropped, text blocks, one-word blocks)."""
    import pymupdf

    lines, vertical, blocks, single = [], 0, 0, 0
    for b in page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT).get("blocks", []):
        if b.get("type") != 0:
            continue
        found = []
        for ln in b.get("lines", []):
            text = "".join(s.get("text", "") for s in ln.get("spans", []))
            if not text.strip():
                continue
            dx, dy = ln.get("dir", (1.0, 0.0))
            if abs(dy) > 0.2 or dx < 0:
                vertical += 1
                continue
            found.append(_Line(text, ln["bbox"][1], ln["bbox"][3]))
        if found:
            blocks += 1
            single += len(found) == 1 and len(found[0].text.split()) <= 1
            lines += found
    return lines, vertical, blocks, single


def _word_lines(page) -> list[_Line]:
    """Rebuild lines from word boxes (content order): words on the same baseline join, without a space when the
    gap is a kerning gap (< 0.1 em) rather than a word space."""
    import pymupdf

    out: list[_Line] = []
    cur, cur_x1 = None, 0.0
    for x0, y0, x1, y1, w, *_ in page.get_text("words", flags=pymupdf.TEXTFLAGS_WORDS):
        h = max(1.0, y1 - y0)
        if cur is not None and abs(y0 - cur.y0) < 0.4 * h and -0.5 * h <= x0 - cur_x1 < 3 * h:
            cur.text += ("" if x0 - cur_x1 < 0.1 * h else " ") + w
            cur.y1 = max(cur.y1, y1)
        else:
            if cur is not None:
                out.append(cur)
            cur = _Line(w, y0, y1)
        cur_x1 = x1
    if cur is not None:
        out.append(cur)
    return out


def _ocr_lines(page) -> list[_Line] | None:
    """OCR a page that has no text layer (pymupdf + Tesseract). None when Tesseract isn't installed."""
    try:
        tp = page.get_textpage_ocr(full=True, dpi=300)
    except Exception:  # RuntimeError when Tesseract/tessdata is missing
        return None
    lines = []
    for b in page.get_text("dict", textpage=tp).get("blocks", []):
        for ln in b.get("lines", []):
            text = "".join(s.get("text", "") for s in ln.get("spans", []))
            if text.strip():
                lines.append(_Line(text, ln["bbox"][1], ln["bbox"][3]))
    return lines


# ── 2. characters ───────────────────────────────────────────────────────────────────────────────────────────────


def _merge_split_lines(lines: list[_Line]) -> list[_Line]:
    """Soft hyphen at a line end -> join with the next line; a drop cap ("W") -> prefix of the next line ("ater")."""
    out: list[_Line] = []
    for ln in lines:
        if out and (out[-1].text.endswith("­") or
                    (re.fullmatch(r"[A-Z]", out[-1].text.strip()) and ln.text[:1].islower())):
            prev = out.pop()
            ln = _Line(prev.text.rstrip("­").strip() + ln.text, prev.y0, ln.y1)
        out.append(_Line(ln.text.replace("­", ""), ln.y0, ln.y1))
    return out


# spacing accent -> (combining mark, letters it can sit on)
_ACUTE = "aeiouyAEIOUYıcnszrlCNSZRL"
_ACCENTS = {
    "´": ("́", _ACUTE), "ˊ": ("́", _ACUTE), "`": ("̀", "aeiouAEIOU"), "ˋ": ("̀", "aeiouAEIOU"),
    "¨": ("̈", "aeiouyAEIOUY"), "ˇ": ("̌", "cszrndtelCSZRNDTEL"), "˘": ("̆", "agiuAGIU"),
    "˚": ("̊", "auAU"), "˝": ("̋", "ouOU"), "ˆ": ("̂", "aeiouAEIOU"), "˜": ("̃", "anoANO"),
    "¸": ("̧", "cstCST"), "˛": ("̨", "aeAE"),
}
_VOWELS = set("aeiouyAEIOUYı")
_ACCENT_RE = re.compile(r"(\w?)( ?)([" + re.escape("".join(_ACCENTS)) + r"])( ?)(\w?)")


def _compose(letter: str, mark: str) -> str:
    return unicodedata.normalize("NFC", ("i" if letter == "ı" else letter) + mark)


def _fix_accents(pages: list[str]) -> tuple[list[str], int]:
    """Rejoin spacing accents with their letter. Publishers put the accent before or after the letter, so: only a
    letter that can carry the accent qualifies; a letter touching the accent beats one across a space; for acute and
    diaeresis a vowel beats a consonant; remaining ties go to the direction this document uses unambiguously."""
    text = "\f".join(pages)

    def options(m):
        prev, sp1, acc, sp2, nxt = m.groups()
        ok = _ACCENTS[acc][1]
        p = prev if prev and prev in ok else ""
        n = nxt if nxt and nxt in ok else ""
        if p and n and bool(sp1) != bool(sp2):
            p, n = (p, "") if not sp1 else ("", n)
        if p and n and acc in "´ˊ¨" and (p in _VOWELS) != (n in _VOWELS):
            p, n = (p, "") if p in _VOWELS else ("", n)
        return p, n

    votes = Counter()
    for m in _ACCENT_RE.finditer(text):
        p, n = options(m)
        if bool(p) != bool(n):
            votes["prev" if p else "next"] += 1
    prefer_prev = votes["prev"] > votes["next"]
    hits = 0

    def repl(m):
        nonlocal hits
        prev, sp1, acc, sp2, nxt = m.groups()
        p, n = options(m)
        if p and n:
            p, n = (p, "") if prefer_prev else ("", n)
        if not (p or n):
            return m.group(0)  # a prime, a quote mark …: leave it
        hits += 1
        mark = _ACCENTS[acc][0]
        return _compose(p, mark) + sp2 + nxt if p else prev + sp1 + _compose(n, mark)

    return _ACCENT_RE.sub(repl, text).split("\f"), hits


_MATHPI = str.maketrans({"ð": "(", "Þ": ")", "¼": "=", "þ": "+"})


def _fix_glyphs(pages: list[str]) -> tuple[list[str], bool]:
    """Elsevier's math fonts put ð Þ ¼ þ in the text layer for ( ) = +. Only remapped in documents that show the
    pattern (equation numbers like 'ð1Þ', spaced '¼'), so names such as 'Þórður' elsewhere stay intact."""
    text = "\f".join(pages)
    if len(re.findall(r"ð[^Þ\n]{0,40}Þ", text)) < 2 and len(re.findall(r"\s¼\s", text)) < 3:
        return pages, False
    return text.translate(_MATHPI).split("\f"), True


# ── 3. running headers/footers and boilerplate ─────────────────────────────────────────────────────────────────

_MARGIN = 0.085      # top/bottom fraction of the page where running heads and feet live
_EDGE_LINES = 3      # …or the first/last lines in content order (some PDFs draw the header last)

_BOILERPLATE = re.compile(
    r"^\s*(?:cite\s+this:?|read\s+online|access|metrics\s*&\s*more|article\s+recommendations|\*|"
    r"\*?\s*s[ıi]\s*supporting\s+information|view|online|export|citation|view\s+online|export\s+citation|"
    r"crossmark|check\s+for\s+updates)\s*$"
    r"|^\s*(?:downloaded\s+(?:via|from)\b|see\s+https?://pubs\.acs\.org/sharingguidelines|"
    r"contents\s+lists\s+available\s+at|journal\s+homepage:|this\s+article\s+is\s+licensed\s+under|©|"
    r"\(c\)\s*(?:19|20)\d\d|copyright\s*©|published\s+under\s+an?\s+(?:exclusive\s+)?licen[cs]e|"
    r"(?:received|revised|accepted|published|available\s+online)\s*:?\s*(?:\d|[A-Z][a-z]+\.?\s+\d))",
    re.IGNORECASE,
)
_PAGE_NUMBER = re.compile(r"^\s*(?:page\s+)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$", re.IGNORECASE)


def _line_key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", text.strip().lower()))


def _strip_running(pages: list[list[_Line]], heights: list[float]) -> tuple[list[list[str]], int]:
    """Lines of each page without running headers/footers, margin page numbers and publisher boilerplate."""
    def at_edge(i: int, j: int, ln: _Line) -> bool:
        h = heights[i] or 1.0
        return ln.y1 <= _MARGIN * h or ln.y0 >= (1 - _MARGIN) * h or j < _EDGE_LINES or \
            j >= len(pages[i]) - _EDGE_LINES

    counts: Counter = Counter()
    for i, lines in enumerate(pages):
        counts.update({_line_key(ln.text) for j, ln in enumerate(lines) if at_edge(i, j, ln)})
    n = len(pages)
    threshold = max(2 if n < 5 else 3, int(0.3 * n + 0.999))
    running = {k for k, c in counts.items() if c >= threshold} if n >= 3 else set()
    out, dropped = [], 0
    for i, lines in enumerate(pages):
        keep = []
        for j, ln in enumerate(lines):
            edge = at_edge(i, j, ln)
            if (edge and (_line_key(ln.text) in running or _PAGE_NUMBER.match(ln.text))) or \
                    ((i == 0 or edge) and _BOILERPLATE.match(ln.text)):
                dropped += 1
            else:
                keep.append(ln.text)
        out.append(keep)
    return out, dropped


# ── 4. back matter ──────────────────────────────────────────────────────────────────────────────────────────────


def _heading_key(line: str) -> str:
    s = line.strip().lstrip("■▪•●◆□▶► \t")
    s = re.sub(r"^(?:\d+(?:\.\d+)*\.?|[IVX]+\.)\s*", "", s)
    return re.sub(r"[\s:.’']+", "", s).lower()


_REF_KEYS = {"references", "referencesandnotes", "notesandreferences", "bibliography", "literaturecited",
             "workscited", "citedliterature", "referencelist"}
_RESUME = re.compile(
    r"^\s*[■▪•]?\s*(?:STAR\s*[★+*✩☆]?\s*METHODS|KEY\s+RESOURCES\s+TABLE|RESOURCE\s+AVAILABILITY|METHOD\s+DETAILS|"
    r"EXPERIMENTAL\s+MODEL|QUANTIFICATION\s+AND\s+STATISTICAL|(?:Appendix|APPENDIX)(?:\s+[A-Z0-9]+)?\b|"
    r"(?:Electronic\s+)?(?:Supplementary|Supporting|SUPPLEMENTARY|SUPPORTING)\s+(?:Information|Material|Data|Methods|"
    r"Notes?|Text|INFORMATION|MATERIAL|DATA|METHODS)\b|Methods|METHODS|Online\s+Methods)"
    r"[\s:.A-Za-z0-9,()-]{0,60}$")
# blocks dropped when they start in the second half of a document: (heading keys, max lines)
_DROP_SECTIONS = [
    ({"authorinformation"}, 80),
    ({"authorcontributions", "authorcontribution", "creditauthorshipcontributionstatement",
      "authorscontributions"}, 20),
    ({"declarationofcompetinginterest", "declarationofcompetinginterests", "competinginterests",
      "competinginterest", "conflictsofinterest", "conflictofinterest", "conflictofintereststatement",
      "declarationofinterests", "declarationofinterest", "disclosures"}, 8),
    ({"orcid"}, 30),
    ({"additionalinformation", "publishersnote", "reprintsandpermissionsinformation"}, 12),
]
_ANY_HEADING = re.compile(r"^\s*■|^\s*(?:\d+(?:\.\d+)*\.?\s+)?(?:[A-Z][A-Z &-]{3,40}|Acknowledg(?:e)?ments?|"
                          r"Funding|Data availability|Code availability|Notes|Abbreviations)\s*$")
_REF_LINE = re.compile(r"\b(?:19|20)\d\d\b|\bet al\b|\bdoi\b|^\s*(?:\[\d+\]|\(\d+\)|\d+\.)\s|"
                       r"\b[A-Z]\.\s?(?:[A-Z]\.\s?)?[A-Z][a-z]|[A-Z][a-z]+,\s[A-Z]\.", re.IGNORECASE)


def _ref_like(lines: list[str]) -> float:
    lines = [l for l in lines if len(l.strip()) > 3]
    return sum(1 for l in lines if _REF_LINE.search(l)) / len(lines) if lines else 0.0


def _drop_back_matter(pages: list[list[str]]) -> tuple[list[list[str]], list[str]]:
    """Remove reference lists and author/competing-interest blocks; returns (pages' lines, notes)."""
    flat = [(p, line) for p, lines in enumerate(pages) for line in lines]
    texts = [line for _, line in flat]
    total = sum(len(t) for t in texts) or 1
    pos, run = [], 0
    for t in texts:
        pos.append(run / total)
        run += len(t)
    drop = [False] * len(flat)
    notes = []

    i = 0
    while i < len(flat):
        line, key = texts[i], _heading_key(texts[i])
        is_ref = (key in _REF_KEYS and len(line.strip()) <= 40) or (
            pos[i] > 0.5 and re.match(r"^\s*\[1\]\s+\S", line) is not None
            and any(re.match(r"^\s*\[2\]\s", t) for t in texts[i + 1 : i + 60]))
        if is_ref and pos[i] > 0.2 and _ref_like(texts[i + 1 : i + 25]) >= 0.35:
            j = i + 1
            while j < len(flat) and not (_RESUME.match(texts[j]) and _ref_like(texts[j + 1 : j + 15]) < 0.3):
                j += 1
            drop[i:j] = [True] * (j - i)
            notes.append(f"references removed (p. {flat[i][0] + 1}–{flat[j - 1][0] + 1})")
            i = j
            continue
        if pos[i] > 0.5 and len(line.strip()) <= 60:
            for keys, limit in _DROP_SECTIONS:
                if key in keys:
                    j = i + 1
                    while j < len(flat) and j - i <= limit and not (
                            _ANY_HEADING.match(texts[j]) or _heading_key(texts[j]) in _REF_KEYS
                            or _RESUME.match(texts[j])):
                        j += 1
                    drop[i:j] = [True] * (j - i)
                    i = j - 1
                    break
        i += 1

    out: list[list[str]] = [[] for _ in pages]
    for (p, line), d in zip(flat, drop):
        if not d:
            out[p].append(line)
    return out, notes


# ── 5. per page ─────────────────────────────────────────────────────────────────────────────────────────────────


def _clean_page(raw: str) -> str:
    text = normalize_unicode(raw)
    text = re.sub(r"(\w)-\n(?=[a-z])", r"\1", text)             # re-join words hyphenated across lines
    text = re.sub(r"(\w)([-−–])\n(?=[A-Z0-9])", r"\1\2", text)   # "Martinez-\nSeara": keep the dash, no space
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def read_pdf(path: str | Path) -> PdfDoc:
    """Read a PDF. Raises on unreadable/encrypted files; the caller decides whether to skip."""
    import pymupdf  # imported lazily so `import rag` works without pymupdf installed

    path = Path(path)
    issues: list[str] = []
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("encrypted PDF")
        meta = {k: (v or "").strip() for k, v in (doc.metadata or {}).items()
                if k in ("title", "author", "subject", "keywords")}
        pages_lines, heights, no_text, ocr_done, rebuilt, vertical = [], [], [], [], 0, 0
        for n, page in enumerate(doc, start=1):
            lines, vert, blocks, single = _dict_lines(page)
            vertical += vert
            if blocks >= 60 and single >= 0.6 * blocks:
                lines = _word_lines(page)
                rebuilt += 1
            if not lines and page.get_images():
                lines = _ocr_lines(page) or []
                if lines:
                    ocr_done.append(n)
            if not lines:
                no_text.append(n)
            pages_lines.append(_merge_split_lines(lines))
            heights.append(page.rect.height)
    if rebuilt:
        issues.append(f"words re-joined from their positions on {rebuilt} page(s) (one block per word)")
    if vertical:
        issues.append(f"{vertical} vertical margin line(s) dropped")
    if ocr_done:
        issues.append(f"OCR on page(s) {ocr_done}")
    if no_text:
        issues.append(f"no text on page(s) {no_text}" + ("" if ocr_done else " (OCR needs Tesseract)"))

    texts = ["\n".join(ln.text for ln in lines) for lines in pages_lines]
    texts, accents = _fix_accents(texts)
    if accents:
        issues.append(f"{accents} spacing accent(s) rejoined")
    texts, glyphs = _fix_glyphs(texts)
    if glyphs:
        issues.append("math-font glyphs remapped (ð Þ ¼ þ -> ( ) = +)")
    for lines, text in zip(pages_lines, texts):  # the fixes never add or remove a line break
        for ln, t in zip(lines, text.split("\n")):
            ln.text = t
    head = normalize_unicode("\f".join(texts[:2]))

    kept, n_running = _strip_running(pages_lines, heights)
    if n_running:
        issues.append(f"{n_running} header/footer/boilerplate line(s) removed")
    body, notes = _drop_back_matter(kept)
    issues += notes
    pages = [_clean_page("\n".join(lines)) for lines in body]
    return PdfDoc(path=path, pages=pages, raw_head=head, meta=meta, n_pages=len(pages_lines), issues=issues,
                  no_text_pages=no_text)


# ── supplementary information ───────────────────────────────────────────────────────────────────────────────────

_SI_TITLE = re.compile(
    r"^\s*(?:electronic\s+)?(?:supplementary|supporting|supplemental)\s+(?:information|materials?|data|text|"
    r"appendix|figures|online\s+material)(?:\s*\(\w+\))?\s*(?:for|to|of)?\s*[:\-–—.]?\s*(.*)$", re.IGNORECASE)
_SI_NAME = re.compile(r"(?:^|[^a-z])(?:si|esi|supp|suppl|supplement|supplementary|supporting)(?:[^a-z]|$)",
                      re.IGNORECASE)


def detect_si(raw_head: str, file_name: str = "") -> tuple[bool, str]:
    """(is_si, text after the SI label). An SI file announces itself in its first lines ("Supplementary
    Information: <paper title>", "Supporting Information for …"), or its name says so (paper_SI.pdf, esi.pdf) and
    its first pages have S-numbered figures/tables."""
    first = raw_head.split("\f")[0]
    lines = [l.strip() for l in first.split("\n") if l.strip()][:8]
    for k, line in enumerate(lines):
        m = _SI_TITLE.match(line)
        if m:
            return True, " ".join([m.group(1).strip()] + lines[k + 1 : k + 4]).strip()
    if _SI_NAME.search(re.sub(r"[_\-.]", " ", Path(file_name).stem)):
        if len(re.findall(r"\b(?:Figure|Fig\.|Table)\s+S\d+", raw_head)) >= 2:
            return True, " ".join(lines[:4])
    return False, ""
