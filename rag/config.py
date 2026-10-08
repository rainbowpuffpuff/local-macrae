"""Paths and settings, all from the environment (see CONTRACT.md).

Relative defaults resolve against the repo root (the folder that contains `rag/`), so `python -m rag search`
finds the same index whether it runs from the repo root, a flow's workdir or the server. A relative path given
explicitly (env var or argument) resolves against the current directory, like any CLI path.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PAPERS = "papers"
DEFAULT_INDEX = "index"
DEFAULT_METADATA = "data/group_publications.json"

# Default embedding model (CONTRACT: fastembed BAAI/bge-small-en-v1.5).
EMBED_MODEL = os.environ.get("MACRAE_EMBED_MODEL", "BAAI/bge-small-en-v1.5")

# Chunking target from the contract: ~800 tokens per chunk.
CHUNK_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 100


def _resolve(value: str | os.PathLike | None, env: str, default: str) -> Path:
    if value is not None and str(value) != "":
        return Path(value).expanduser().resolve()
    env_value = os.environ.get(env)
    if env_value:
        return Path(env_value).expanduser().resolve()
    return (REPO_ROOT / default).resolve()


def papers_dir(value=None) -> Path:
    return _resolve(value, "MACRAE_PAPERS_DIR", DEFAULT_PAPERS)


def index_dir(value=None) -> Path:
    return _resolve(value, "MACRAE_INDEX_DIR", DEFAULT_INDEX)


def metadata_path(value=None) -> Path | None:
    """The publications list. A relative path that doesn't exist from the cwd is tried from the repo root."""
    p = Path(value or DEFAULT_METADATA).expanduser()
    if p.is_absolute():
        return p if p.exists() else None
    for candidate in (Path.cwd() / p, REPO_ROOT / p):
        if candidate.exists():
            return candidate.resolve()
    return None
