# rag/ — notes

Search over the group's papers with citations, as specified in CONTRACT.md § RAG. Index on disk, no external DB,
works with zero papers.

## What's here
| file | role |
|---|---|
| `__init__.py` | public API: `ingest`, `search`, `format_context`, plus `stats`, `warmup`. Heavy deps are imported on first call, so `import rag` is cheap |
| `__main__.py` | CLI: `python -m rag ingest / search / stats / warmup` |
| `pdf.py` | pymupdf → cleaned text per page: ligatures, hyphenation, running headers/footers and page numbers removed, reference list cut |
| `meta.py` | matches a PDF to `data/group_publications.json` (DOI → fuzzy title), with a fallback record from the PDF itself |
| `chunking.py` | whole-sentence chunks of ~800 tokens (100 overlap), each tagged with its start page |
| `bm25.py` | Okapi BM25 with an inverted index (no dependency) |
| `embed.py` | fastembed `BAAI/bge-small-en-v1.5` (default), a deterministic hashing embedder (tests/offline), or none |
| `store.py` | the on-disk index: atomic writes, reload when it changes |
| `indexer.py` / `retrieval.py` | ingest pipeline / hybrid search + RRF + `format_context` (named so they don't shadow `rag.ingest`/`rag.search`) |
| `tests/` | 50 pytest tests; `tests/fixtures/*.pdf` are two tiny synthetic PDFs (8 KB), made by `tests/make_fixtures.py` |

## API (contract shapes, plain JSON-serializable dicts)
```python
import rag
rag.ingest(papers_dir=None, index_dir=None, metadata="data/group_publications.json", rebuild=False)
    # -> {"papers", "chunks", "embedder", "reused", "embedded", "skipped": [{"file","reason"}], "unmatched": [file], ...}
rag.search(query, k=6, index_dir=None, doi=None) -> [Passage]
    # Passage = {"id": "doi:10.1039/d6sm00560h#p2c1", "text", "score": 0..1, "citation": Citation}
    # Citation = {"key": "[1]", "title", "authors", "year", "journal", "doi", "page", "url", "quote" (≤240 chars)}
rag.format_context(passages, max_chars_per_passage=1800) -> (context_str, citations)
rag.stats(index_dir=None) -> {"papers": int, "chunks": int, "embedder": str|None, "created": float|None}
rag.warmup(index_dir=None)   # loads the model (downloading it if needed) and the index
```
- `search`: BM25 top-N and cosine top-N (N = max(50, 5k)), fused with reciprocal rank fusion (k=60). `score` is
  the fused score divided by the best possible score (1.0 = ranked first by both rankers). Citation keys are `[1]..[k]` in rank order.
  `quote` is the sentence that best covers the query terms. `page` is 1-based and is the page the chunk starts on.
- Paper filter: `doi=` or a DOI written in the query (`"methods of 10.1021/…"`) limits the search to that paper.
  A query that is only a DOI returns that paper's methods-like passages. A DOI that isn't indexed gives `[]`.
- `format_context` renumbers `[1]..[n]` (dedupes repeated passages; the input passages are not changed). Each block looks like
  `[1] Košťál et al. 2026, "Title", Journal, p. 3, doi:…`, followed by the passage text trimmed around the quote.
  With no passages it returns an instruction ("…papers here don't cover it; don't answer from memory") and `[]`.
- Zero papers: no `index/` → `search` returns `[]`, `stats` returns zeros. `ingest` on a missing or empty folder
  writes an empty index. Nothing raises.

## CLI
```bash
python -m rag ingest                    # $MACRAE_PAPERS_DIR (papers/) -> $MACRAE_INDEX_DIR (index/); JSON summary on stdout
python -m rag ingest --rebuild          # ignore cached chunks/embeddings;  --no-embed = BM25 only
python -m rag search "ion pairing at the water surface" -k 6          # text on a terminal, JSON when piped
python -m rag search "methods" --doi 10.1021/acs.jctc.5c02051 --format json     # {"passages": [...]}
python -m rag search "…" --format context   # numbered LLM context, then a line "CITATIONS_JSON [...]"
python -m rag stats                     # {"papers": …, "chunks": …}
python -m rag warmup                    # pre-download the model (Docker build) / preload
```
Progress logs go to stderr, so stdout stays clean JSON.

## Paths and environment
- `MACRAE_PAPERS_DIR` (default `papers/`) and `MACRAE_INDEX_DIR` (default `index/`). When the env var is unset,
  the default is relative to the **repo root**, not the cwd, so a flow step running in `tasks/flows/` finds the
  same index. Env or CLI paths that are given and relative are resolved against the cwd.
- `metadata`: tried relative to the cwd first, then the repo root. If it's missing, citations come from the PDFs only.
- `MACRAE_EMBEDDER` = `fastembed` (default) | `hash` | `none`. `MACRAE_EMBED_MODEL` overrides the model.
  `FASTEMBED_CACHE_PATH` sets the model cache. fastembed's default is `/tmp/fastembed_cache`, which is lost
  when the container restarts.
- If the model can't load (offline, not installed), search falls back to BM25 only, logs a warning, and retries
  loading after 5 minutes. If the index was built with a different embedder than the one configured, search uses
  BM25 only and asks you to re-ingest.

## Index layout (`index/`, gitignored)
`manifest.json` (counts, embedder, chunker version, skipped files, names of the current data files),
`papers-<stamp>.json`, `chunks-<stamp>.jsonl`, `emb-<stamp>.npy` (float32, L2-normalized, 384-d).
New files are written first, then `manifest.json` is swapped atomically and old files are deleted. A running
server reloads when the manifest's mtime changes, so re-ingesting while the server runs is safe (tested with
concurrent searches). Re-ingest is incremental by PDF sha256: unchanged PDFs keep their chunks and embeddings.
Citation metadata is matched again on every run, so edits to `group_publications.json` take effect without re-embedding.

## How PDFs get their citation
In order: sidecar `<name>.json` next to the PDF (any of doi/title/authors/year/journal/url; it overrides the
other sources) → DOI in the file name (`10.1021_acs.jctc.5c02051.pdf`) → DOI in the PDF metadata → a DOI on page 1
that is in the publications list → the title found on pages 1–2 (exact after normalization, or ≥88% of the
words in order; short titles like "Arginine" only count at the top of page 1) → no match: title from PDF metadata
or the first line, plus any DOI on page 1 (`matched_by: "doi-unlisted"`). DOIs in the reference list are never used.
`ingest` lists unmatched files in `unmatched`.
Checked on 4 real open-access preprints of group papers: 2 matched by title to their journal DOIs. The other 2
have different preprint titles and were kept under their bioRxiv DOI. Add a sidecar JSON if you want those cited
as the journal version.

## Run the tests
```bash
pip install -r rag/requirements.txt
python -m pytest rag/tests -q          # from the repo root; 50 passed on Python 3.11.17 and 3.12.3
RAG_SKIP_FASTEMBED=1 python -m pytest rag/tests -q    # skip the 2 tests that load the real model
```
The tests use temporary papers/index folders and the hashing embedder. `test_fastembed.py` uses the real
bge-small model, and skips itself if fastembed or the model can't load. Root `pytest` collects these tests
together with `tests/` (55 passed together with the agent_runner tests). `rag/tests/__init__.py` makes the
module names `rag.tests.*`, so they can't collide with other modules' tests.

## Performance (16-core CPU)
Ingesting 4 real papers (~100 pages, 68 chunks) took 15 s, almost all of it embedding. Expect about 25–30 min for
all ~480 papers on first ingest, then only new files are processed. A CLI search with model load takes about 0.8 s.
In a running process a search takes a few ms (BM25 + dense over ~9k chunks: about 1.5 ms + 9 ms) plus about
10 ms to embed the query. The first search after (re)loading builds BM25 in memory, a few seconds for the full
corpus, so call `rag.warmup()` at server startup.

## Assumptions about other modules
- **server**: calls `rag.search(query, k)` for `POST /api/search` and returns `{"passages": result}`. For
  `/api/tools/search_papers` it calls `context, citations = rag.format_context(rag.search(query, k=6))` and returns
  `{"answer_context": context, "citations": citations}`. For `/api/health` it uses `rag.stats()["papers"]` and
  `["chunks"]`. Calls are synchronous and thread-safe, so FastAPI's threadpool (plain `def` routes) is fine.
  Optionally call `rag.warmup()` once at startup, in a background thread.
- **tasks**: `python -m rag search "<query>" --doi <doi> --format json` (or put the DOI in the query). Piped
  output is JSON by default. Because the root `pyproject.toml` only packages `agent_runner`, `pip install -e .`
  does **not** install `rag`: run it with cwd = repo root or `PYTHONPATH=<repo root>`.
- **deploy**: install `rag/requirements.txt` (pymupdf, fastembed, numpy; fastembed pulls onnxruntime, about
  200 MB). In the Dockerfile set `FASTEMBED_CACHE_PATH` to a directory in the image and run `python -m rag warmup`,
  so the model (~70 MB) is baked in rather than downloaded at the first query. `papers/` and `index/` should be
  volumes or mounted paths (never committed). `make index` = `python -m rag ingest`.
- **voice**: the context starts with "Cite them as [n] (first author + year when speaking)". Each block header
  already gives the spoken form ("Košťál et al. 2026").

## Not done / limits
- No OCR: scanned PDFs are skipped with reason "no extractable text".
- The reading order of two-column layouts is whatever pymupdf extracts, which is usually right for ACS/RSC/AIP
  PDFs.
- Everything after the "References" heading, including appendices and SI that come after it, is dropped.
