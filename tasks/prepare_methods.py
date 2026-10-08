"""methods-card, step 1 (script, runs on the backend): gather numbered sources for one group paper.

    python -m tasks.prepare_methods --doi 10.1021/acs.jctc.5c02051      (DOI also from $TASK_DOI)

Writes $FLOW_RUN_DIR/methods-card/paper/{paper.json, sources.json, context.md} and prints JSON for the flow:
{"dir", "doi", "title", "citation", "n_sources", "n_from_paper", "sources_file", "checker"}.

Sources, in order: RAG passages from this paper (several method-oriented queries, filtered by DOI), the OpenAlex
abstract when the index has little or nothing from the paper, then up to 3 related passages from other group papers.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from tasks import sources as S

QUERIES = [
    "{title}",
    "computational methods simulation details {title}",
    "force field water model parameters {title}",
    "molecular dynamics setup system size simulation time {title}",
    "experimental methods measurements {title}",
    "analysis free energy calculation {title}",
]
MAX_FROM_PAPER = 12
MAX_RELATED = 3


def gather(doi: str, pub: dict, k: int = 12) -> list[dict]:
    title = pub.get("title") or doi
    want = S.norm_doi(doi)
    hits: list[dict] = []
    for q in QUERIES:
        hits += S.rag_search(q.format(title=title), k=k)
    hits = S.dedupe(hits)
    own = [h for h in hits if S.norm_doi(h["citation"].get("doi")) == want]
    other = [h for h in hits if S.norm_doi(h["citation"].get("doi")) != want and h["citation"].get("doi")]
    own.sort(key=lambda h: (h["citation"].get("page") or 0))  # read in paper order
    items = [{"text": h["text"], "citation": {**S.citation_from_publication(pub), **_clean(h["citation"])},
              "origin": "paper (RAG index)"} for h in own[:MAX_FROM_PAPER]]
    if len(items) < 3:
        abstract = S.openalex_abstract(doi)
        if abstract:
            items.insert(0, {"text": abstract, "citation": S.citation_from_publication(pub, quote=abstract),
                             "origin": "abstract (OpenAlex)"})
    if not items:
        # nothing indexed and no abstract: the bibliographic record is the only source; the card must say so
        items.append({"text": f"{title}. {pub.get('authors', '')}. {pub.get('citation', '')}.",
                      "citation": S.citation_from_publication(pub), "origin": "bibliographic record only"})
    other.sort(key=lambda h: -(h.get("score") or 0))
    items += [{"text": h["text"], "citation": _clean(h["citation"]), "origin": "related group paper (RAG index)"}
              for h in other[:MAX_RELATED]]
    return S.number_sources(items)


def _clean(c: dict) -> dict:
    keep = ("title", "authors", "year", "journal", "doi", "page", "url", "quote")
    return {k: c.get(k) for k in keep if c.get(k) not in (None, "")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--doi", default=os.environ.get("TASK_DOI", ""))
    ap.add_argument("--out", default="", help="folder (default $FLOW_RUN_DIR/methods-card)")
    a = ap.parse_args(argv)
    doi = S.norm_doi(a.doi)
    if not S.DOI_RE.match(doi):
        print(f"not a DOI: {a.doi!r}", file=sys.stderr)
        return 2
    pub = S.find_publication(doi)
    if not pub:
        print(f"DOI {doi} is not in data/group_publications.json", file=sys.stderr)
        return 2
    base = Path(a.out or Path(os.environ.get("FLOW_RUN_DIR") or ".") / "methods-card")
    d = base / "paper"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    srcs = gather(doi, pub)
    n_own = sum(1 for s in srcs if s["origin"] == "paper (RAG index)")
    S.log(f"sources for {doi}: {len(srcs)} ({n_own} passages from the paper itself)")
    S.write_json(d / "paper.json", pub)
    S.write_json(d / "sources.json", srcs)
    header = (f"# Sources for the methods card of \"{pub.get('title')}\" (DOI {pub.get('doi')})\n\n"
              "Cite these as [n]. Only these numbers exist. Sources marked 'related group paper' are other papers "
              "of the group, not the paper itself.")
    (d / "context.md").write_text(S.context_markdown(srcs, header))
    # keep an untouchable copy outside the folder the agent edits, for the check
    S.write_json(base / "sources.json", srcs)
    print(json.dumps({
        "dir": str(d), "doi": pub.get("doi"), "title": pub.get("title"),
        "citation": S.citation_from_publication(pub), "n_sources": len(srcs), "n_from_paper": n_own,
        "sources_file": str(base / "sources.json"), "checker": str(Path(__file__).resolve().parent / "checks.py"),
        "citations": [s["citation"] for s in srcs],  # the server turns these into `cite` trace events
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
