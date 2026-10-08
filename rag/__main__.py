"""CLI: python -m rag ingest | search "…" | stats | warmup."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import config


def _print_text(passages: list[dict]) -> None:
    from .retrieval import short_ref

    if not passages:
        print("No passages found (is the index empty? run `python -m rag ingest`).")
        return
    for p in passages:
        c = p["citation"]
        where = f"p. {c['page']}" if c.get("page") else ""
        print(f"{c['key']} {short_ref(c)} — {c.get('title', '')}")
        print(f"    {', '.join(x for x in (c.get('journal'), where, c.get('url')) if x)}   score {p['score']:.3f}")
        print(f"    \"{c.get('quote', '')}\"")
        print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rag", description="Search the group's papers with citations.")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="PDFs in the papers folder -> index on disk")
    p.add_argument("--papers", default=None, help="folder of PDFs (default $MACRAE_PAPERS_DIR or papers/)")
    p.add_argument("--index", default=None, help="index folder (default $MACRAE_INDEX_DIR or index/)")
    p.add_argument("--metadata", default=config.DEFAULT_METADATA, help="group_publications.json")
    p.add_argument("--rebuild", action="store_true", help="ignore cached chunks/embeddings")
    p.add_argument("--no-embed", action="store_true", help="BM25 only (no model download)")

    s = sub.add_parser("search", help="hybrid search; prints passages with citations")
    s.add_argument("query", nargs="+")
    s.add_argument("-k", "--k", type=int, default=6, help="number of passages (default 6)")
    s.add_argument("--doi", default=None, help="only search this paper")
    s.add_argument("--index", default=None)
    s.add_argument("--format", choices=["auto", "text", "json", "context"], default="auto",
                   help="text for people, json = {\"passages\": [...]}, context = numbered LLM context + "
                        "citations JSON; auto = text on a terminal, json otherwise")
    s.add_argument("--json", dest="format", action="store_const", const="json", help="same as --format json")

    st = sub.add_parser("stats", help="papers and chunks in the index (JSON)")
    st.add_argument("--index", default=None)
    w = sub.add_parser("warmup", help="download/load the embedding model and the index")
    w.add_argument("--index", default=None)

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, stream=sys.stderr,
                        format="rag: %(message)s")

    if a.cmd == "ingest":
        from .indexer import ingest

        kwargs = {"rebuild": a.rebuild}
        if a.no_embed:
            kwargs["embedder"] = None
        summary = ingest(a.papers, a.index, a.metadata, **kwargs)
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0

    if a.cmd == "search":
        from .retrieval import format_context, search

        logging.getLogger("rag").setLevel(logging.DEBUG if a.verbose else logging.WARNING)
        passages = search(" ".join(a.query), a.k, index_dir=a.index, doi=a.doi)
        fmt = a.format
        if fmt == "auto":
            fmt = "text" if sys.stdout.isatty() else "json"
        if fmt == "json":
            print(json.dumps({"passages": passages}, ensure_ascii=False, indent=1))
        elif fmt == "context":
            context, citations = format_context(passages)
            print(context)
            print("\nCITATIONS_JSON " + json.dumps(citations, ensure_ascii=False))
        else:
            _print_text(passages)
        return 0

    if a.cmd == "stats":
        from .retrieval import stats

        print(json.dumps(stats(a.index)))
        return 0

    if a.cmd == "warmup":
        from . import warmup

        print(json.dumps(warmup(a.index)))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
