"""The index on disk (no external DB).

    index/manifest.json          what's current: counts, embedder, file names (replaced atomically, last)
    index/papers-<stamp>.json    one record per PDF: citation fields, file, sha256, matched_by
    index/chunks-<stamp>.jsonl   one chunk per line: id, paper (index into papers), text, page, page_end
    index/emb-<stamp>.npy        float32 [n_chunks, dim], L2-normalized (absent when embeddings are off)

Writers put new data files next to the old ones and then swap manifest.json, so a reader never sees a half-written
index; files the manifest no longer names are removed afterwards.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("rag")

INDEX_VERSION = 1
MANIFEST = "manifest.json"


@dataclass
class IndexData:
    manifest: dict = field(default_factory=dict)
    papers: list[dict] = field(default_factory=list)
    chunks: list[dict] = field(default_factory=list)
    embeddings: object = None  # np.ndarray | None

    @property
    def embedder(self) -> str | None:
        return self.manifest.get("embedder")


def _atomic_write(path: Path, write) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_index(index_dir: Path, papers: list[dict], chunks: list[dict], embeddings, embedder: str | None,
                extra: dict | None = None) -> dict:
    import numpy as np

    index_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}-{time.time_ns() % 1_000_000:06d}"
    files = {"papers": f"papers-{stamp}.json", "chunks": f"chunks-{stamp}.jsonl", "embeddings": None}

    _atomic_write(index_dir / files["papers"],
                  lambda f: f.write(json.dumps(papers, ensure_ascii=False, indent=1).encode("utf-8")))

    def write_chunks(f):
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False).encode("utf-8") + b"\n")

    _atomic_write(index_dir / files["chunks"], write_chunks)
    dim = None
    if embeddings is not None:
        files["embeddings"] = f"emb-{stamp}.npy"
        arr = np.ascontiguousarray(embeddings, dtype=np.float32)
        dim = int(arr.shape[1]) if arr.ndim == 2 else None
        _atomic_write(index_dir / files["embeddings"], lambda f: np.save(f, arr, allow_pickle=False))

    manifest = {
        "version": INDEX_VERSION,
        "created": time.time(),
        "papers": len(papers),
        "chunks": len(chunks),
        "embedder": embedder if embeddings is not None else None,
        "dim": dim,
        "files": files,
        **(extra or {}),
    }
    _atomic_write(index_dir / MANIFEST,
                  lambda f: f.write(json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")))
    _remove_stale(index_dir, set(v for v in files.values() if v))
    return manifest


def _remove_stale(index_dir: Path, keep: set[str]) -> None:
    for p in index_dir.iterdir():
        if p.is_file() and p.name not in keep and p.name.startswith(("papers-", "chunks-", "emb-")):
            try:
                p.unlink()
            except OSError:
                pass


def read_manifest(index_dir: Path) -> dict | None:
    try:
        m = json.loads((index_dir / MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("unreadable index manifest in %s: %s", index_dir, e)
        return None
    if m.get("version") != INDEX_VERSION:
        log.warning("index in %s has version %s, expected %s: run `python -m rag ingest`",
                    index_dir, m.get("version"), INDEX_VERSION)
        return None
    return m


def read_index(index_dir: Path) -> IndexData:
    """The current index, or an empty one when there is none (zero papers is a valid state)."""
    import numpy as np

    for attempt in range(3):  # a concurrent ingest may swap the manifest and delete files under us
        manifest = read_manifest(index_dir)
        if manifest is None:
            return IndexData()
        files = manifest.get("files") or {}
        try:
            papers = json.loads((index_dir / files["papers"]).read_text(encoding="utf-8"))
            with open(index_dir / files["chunks"], encoding="utf-8") as f:
                chunks = [json.loads(line) for line in f if line.strip()]
            emb = None
            if files.get("embeddings"):
                emb = np.load(index_dir / files["embeddings"], allow_pickle=False)
                if emb.shape[0] != len(chunks):
                    log.warning("embeddings/chunks mismatch in %s; using BM25 only", index_dir)
                    emb = None
            return IndexData(manifest=manifest, papers=papers, chunks=chunks, embeddings=emb)
        except FileNotFoundError:
            if attempt == 2:
                raise
            time.sleep(0.2)
        except (KeyError, ValueError, OSError) as e:
            log.warning("unreadable index in %s (%s); run `python -m rag ingest`", index_dir, e)
            return IndexData()
    return IndexData()


class IndexCache:
    """Per-process cache of loaded indexes, reloaded when manifest.json changes (e.g. after a re-ingest)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: dict[Path, tuple[tuple, object]] = {}

    def get(self, index_dir: Path, build):
        try:
            st = (index_dir / MANIFEST).stat()
            key = (st.st_mtime_ns, st.st_size, st.st_ino)
        except FileNotFoundError:
            key = None
        with self._lock:
            hit = self._entries.get(index_dir)
            if hit and hit[0] == key:
                return hit[1]
            value = build(read_index(index_dir))
            self._entries[index_dir] = (key, value)
            return value

    def clear(self):
        with self._lock:
            self._entries.clear()
