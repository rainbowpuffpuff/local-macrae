import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def rag_env(tmp_path, monkeypatch):
    """Every test gets its own papers/ and index/ and the offline hashing embedder (no model download)."""
    from rag import embed, retrieval as search

    papers, index = tmp_path / "papers", tmp_path / "index"
    papers.mkdir()
    monkeypatch.setenv("MACRAE_PAPERS_DIR", str(papers))
    monkeypatch.setenv("MACRAE_INDEX_DIR", str(index))
    monkeypatch.setenv("MACRAE_EMBEDDER", "hash")
    search.clear_cache()
    embed._cache.pop("hash", None)
    yield {"papers": papers, "index": index}
    search.clear_cache()


@pytest.fixture
def papers_dir(rag_env):
    """papers/ with the two synthetic fixture PDFs."""
    for pdf in FIXTURES.glob("*.pdf"):
        shutil.copy(pdf, rag_env["papers"] / pdf.name)
    return rag_env["papers"]


@pytest.fixture
def make_pdf():
    from .make_fixtures import write_pdf

    return write_pdf
