"""`rag.ingest(papers_dir, index_dir, metadata)`: PDFs -> pages -> chunks -> BM25-ready text + embeddings on disk.

Incremental: a PDF whose sha256 is already in the index keeps its chunks (and embeddings, if the embedder is the
same); citation metadata is re-matched every time, so edits to group_publications.json or sidecars apply at once.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path

from . import config
from .chunking import CHUNKER_VERSION, chunk_pages
from .meta import PAPER_FIELDS, Publications, match_paper
from .store import read_index, write_index

log = logging.getLogger("rag")

_HEAD_CHARS = 6000


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _list_pdfs(papers_dir: Path) -> list[Path]:
    if not papers_dir.is_dir():
        return []
    return sorted(p for p in papers_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"
                  and not any(part.startswith(".") for part in p.relative_to(papers_dir).parts))


def _prefix(record: dict, rel: str) -> str:
    if record.get("doi"):
        return f"doi:{record['doi']}"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", rel.rsplit(".", 1)[0]).strip("-") or "paper"
    return f"file:{slug}"


def ingest(papers_dir=None, index_dir=None, metadata=config.DEFAULT_METADATA, *, rebuild: bool = False,
           embedder="default") -> dict:
    """Build/refresh the index. Returns a summary dict (counts, skipped files, unmatched files).

    `embedder`: "default" = from MACRAE_EMBEDDER (fastembed), None = BM25 only, or an embedder object.
    Never raises for bad/missing PDFs or zero papers; those end up in `skipped` / an empty index.
    """
    import numpy as np

    from .embed import get_embedder

    t0 = time.time()
    papers_path = config.papers_dir(papers_dir)
    index_path = config.index_dir(index_dir)
    meta_path = config.metadata_path(metadata)
    if meta_path is None:
        log.warning("publications list %s not found; citations will come from the PDFs only", metadata)
    pubs = Publications.load(meta_path)
    if not papers_path.is_dir():
        log.warning("papers folder %s does not exist; writing an empty index", papers_path)
    pdfs = _list_pdfs(papers_path)

    emb = get_embedder() if embedder == "default" else embedder
    emb_name = getattr(emb, "name", None) if emb is not None else None

    # Cache from the previous index, keyed by file hash (only if chunked the same way).
    chunker = f"{CHUNKER_VERSION}/{config.CHUNK_TOKENS}/{config.CHUNK_OVERLAP_TOKENS}"
    prev_by_sha: dict[str, tuple[dict, list[dict], object]] = {}
    if not rebuild:
        prev = read_index(index_path)
        if prev.manifest.get("chunker") == chunker:
            rows: dict[int, list[int]] = {}
            for i, c in enumerate(prev.chunks):
                rows.setdefault(c["paper"], []).append(i)
            same_emb = prev.embeddings is not None and prev.embedder == emb_name and emb_name is not None
            for pi, p in enumerate(prev.papers):
                idx = rows.get(pi, [])
                vecs = prev.embeddings[idx] if same_emb and idx else None
                prev_by_sha[p["sha256"]] = (p, [prev.chunks[i] for i in idx], vecs)

    papers: list[dict] = []
    chunks: list[dict] = []
    vec_blocks: list[tuple[int, object]] = []  # (first chunk row, vectors) for reused embeddings
    to_embed: list[int] = []
    skipped: list[dict] = []
    seen_sha: set[str] = set()
    seen_doi: dict[str, str] = {}
    reused = 0

    for n, pdf in enumerate(pdfs, 1):
        rel = pdf.relative_to(papers_path).as_posix()
        try:
            sha = _sha256(pdf)
        except OSError as e:
            skipped.append({"file": rel, "reason": f"unreadable: {e}"})
            continue
        if sha in seen_sha:
            skipped.append({"file": rel, "reason": "duplicate file"})
            continue

        cached = prev_by_sha.get(sha)
        if cached:
            old, old_chunks, old_vecs = cached
            head, pdf_meta, n_pages = old.get("head", ""), old.get("pdf_meta", {}), old.get("n_pages", 0)
            page_chunks = [{"local": c["id"].rsplit("#", 1)[-1], "text": c["text"], "page": c["page"],
                            "page_end": c.get("page_end", c["page"])} for c in old_chunks]
        else:
            old_vecs = None
            try:
                from .pdf import read_pdf

                doc = read_pdf(pdf)
            except Exception as e:  # corrupt, encrypted, not really a PDF …
                log.warning("skipping %s: %s", rel, e)
                skipped.append({"file": rel, "reason": f"{type(e).__name__}: {e}"})
                continue
            head, pdf_meta, n_pages = doc.raw_head[:_HEAD_CHARS], doc.meta, doc.n_pages
            if not any(p.strip() for p in doc.pages):
                log.warning("skipping %s: no extractable text (scanned PDF? needs OCR)", rel)
                skipped.append({"file": rel, "reason": "no extractable text (scanned PDF?)"})
                continue
            page_chunks = [{"local": c["id"].rsplit("#", 1)[-1], "text": c["text"], "page": c["page"],
                            "page_end": c["page_end"]} for c in chunk_pages(doc.pages, "x")]

        record = match_paper(pdf, head, pdf_meta, pubs)
        if record["doi"] and record["doi"] in seen_doi:
            log.warning("skipping %s: same DOI %s as %s", rel, record["doi"], seen_doi[record["doi"]])
            skipped.append({"file": rel, "reason": f"duplicate DOI {record['doi']} (also {seen_doi[record['doi']]})"})
            continue
        if not page_chunks:
            skipped.append({"file": rel, "reason": "no text after cleaning"})
            continue
        seen_sha.add(sha)
        if record["doi"]:
            seen_doi[record["doi"]] = rel

        paper_idx = len(papers)
        prefix = _prefix(record, rel)
        first_row = len(chunks)
        for c in page_chunks:
            chunks.append({"id": f"{prefix}#{c['local']}", "paper": paper_idx, "text": c["text"],
                           "page": c["page"], "page_end": c["page_end"]})
        if old_vecs is not None and len(old_vecs) == len(page_chunks):
            vec_blocks.append((first_row, old_vecs))
        else:
            to_embed.extend(range(first_row, len(chunks)))
        if cached:
            reused += 1
        papers.append({**{k: record.get(k) for k in PAPER_FIELDS}, "matched_by": record["matched_by"],
                       "file": rel, "sha256": sha, "n_pages": n_pages, "n_chunks": len(page_chunks),
                       "head": head, "pdf_meta": pdf_meta})
        log.info("[%d/%d] %s -> %s (%s, %d chunks%s)", n, len(pdfs), rel, record.get("doi") or record["title"][:50],
                 record["matched_by"], len(page_chunks), ", cached" if cached else "")

    embeddings = None
    if emb is not None and chunks:
        try:
            embeddings = np.zeros((len(chunks), emb.dim), dtype=np.float32)
            for first_row, vecs in vec_blocks:
                embeddings[first_row : first_row + len(vecs)] = vecs
            batch = 256
            for start in range(0, len(to_embed), batch):
                rows = to_embed[start : start + batch]
                embeddings[rows] = emb.embed_documents([chunks[r]["text"] for r in rows])
                log.info("embedded %d/%d new chunks", min(start + batch, len(to_embed)), len(to_embed))
        except Exception as e:
            log.warning("embedding failed (%s: %s); index will be BM25 only", type(e).__name__, e)
            embeddings = None
    elif emb is not None:
        embeddings = np.zeros((0, emb.dim), dtype=np.float32)

    unmatched = [p["file"] for p in papers if p["matched_by"] in ("none", "doi-unlisted")]
    manifest = write_index(index_path, papers, chunks, embeddings, emb_name, extra={
        "chunker": chunker,
        "papers_dir": str(papers_path),
        "metadata": str(meta_path) if meta_path else None,
        "skipped": skipped,
    })
    summary = {
        "papers": manifest["papers"],
        "chunks": manifest["chunks"],
        "embedder": manifest["embedder"],
        "reused": reused,
        "embedded": len(to_embed) if embeddings is not None else 0,
        "skipped": skipped,
        "unmatched": unmatched,
        "index_dir": str(index_path),
        "papers_dir": str(papers_path),
        "seconds": round(time.time() - t0, 2),
    }
    log.info("index: %d papers, %d chunks, embedder=%s, %d skipped, %.1f s", summary["papers"], summary["chunks"],
             summary["embedder"], len(skipped), summary["seconds"])
    return summary
