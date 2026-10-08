"""Text helpers shared by ingestion and search: cleaning, tokenizing, sentences, DOIs."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

STOPWORDS = frozenset(
    """a about above after again against all also am an and any are as at be because been before being below
    between both but by can could did do does doing down during each few for from further had has have having he
    her here hers him his how i if in into is it its itself just me more most my no nor not now of off on once
    only or other our ours out over own same she should so some such than that the their theirs them then there
    these they this those through to too under until up very was we were what when where which while who whom why
    will with would you your yours et al via using used use within without upon onto""".split()
)

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")
_ABBREV_END = re.compile(r"(?:\b(?:al|e\.g|i\.e|Fig|Figs|Eq|Eqs|Ref|Refs|ca|vs|cf|Tab|No|Sec)\.|\b[A-Z]\.)$")

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")


def normalize_unicode(text: str) -> str:
    """NFKC (ligatures like 'ﬁ' -> 'fi'), drop control chars except newline, tab and form feed."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if ch in "\n\t\f" or unicodedata.category(ch)[0] != "C")


def fold(text: str) -> str:
    """Lowercase, accents stripped, punctuation -> spaces. For fuzzy title matching."""
    if not text.isascii():
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(_WORD.findall(text.lower()))


@lru_cache(maxsize=200_000)
def _stem(token: str) -> str:
    if len(token) <= 3 or token.isdigit():
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("sses"):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Search tokens: folded words without stopwords, lightly stemmed (plurals)."""
    return [_stem(t) for t in fold(text).split() if (len(t) > 1 or t.isdigit()) and t not in STOPWORDS]


def approx_tokens(text: str) -> int:
    """Rough subword-token count (BPE/WordPiece average ~1.3 tokens per English word, more for numbers/formulas)."""
    words = text.split()
    return int(sum(1.0 + 0.25 * (len(w) > 7) + 0.5 * any(c.isdigit() for c in w) for w in words) * 1.15)


def split_sentences(text: str) -> list[str]:
    """Split running text into sentences, not breaking after common abbreviations ('et al.', 'Fig.')."""
    parts = _SENTENCE_END.split(text)
    out: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if out and _ABBREV_END.search(out[-1]):
            out[-1] = out[-1] + " " + part
        else:
            out.append(part)
    return out


def clean_doi(raw: str) -> str:
    """Normalize a DOI: lowercase, no URL prefix, trailing punctuation and unbalanced ')' removed."""
    doi = raw.strip()
    doi = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    doi = doi.rstrip(".,;:")
    while doi.endswith(")") and doi.count(")") > doi.count("("):
        doi = doi[:-1].rstrip(".,;:")
    return doi.lower()


def find_dois(text: str) -> list[str]:
    """All DOIs in text, in order of appearance, deduplicated."""
    seen: dict[str, None] = {}
    for m in DOI_RE.finditer(text):
        doi = clean_doi(m.group(1))
        if "/" in doi and len(doi.split("/", 1)[1]) >= 3:
            seen.setdefault(doi, None)
    return list(seen)


def truncate(text: str, limit: int) -> str:
    """Cut at a word boundary to at most `limit` chars, adding '…' when cut."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "…"
