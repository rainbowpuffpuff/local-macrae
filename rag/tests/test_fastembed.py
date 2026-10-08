"""The real model (BAAI/bge-small-en-v1.5 via fastembed). Skipped when fastembed or the model isn't available
(e.g. offline CI); set RAG_SKIP_FASTEMBED=1 to skip explicitly."""
import os

import pytest

import rag
from rag import embed


@pytest.fixture
def real_embedder(monkeypatch):
    if os.environ.get("RAG_SKIP_FASTEMBED"):
        pytest.skip("RAG_SKIP_FASTEMBED set")
    pytest.importorskip("fastembed")
    monkeypatch.setenv("MACRAE_EMBEDDER", "fastembed")
    emb = embed.get_embedder("fastembed")
    if emb is None:
        pytest.skip("fastembed model could not be loaded (offline?)")
    return emb


def test_bge_small_hybrid_search(papers_dir, real_embedder):
    assert real_embedder.name == "fastembed:BAAI/bge-small-en-v1.5" and real_embedder.dim == 384
    summary = rag.ingest()
    assert summary["embedder"] == real_embedder.name
    # semantic match with little word overlap: "heat bath" ~ thermostat/temperature, "sugar chains" ~ glycans
    assert rag.search("how was the heat bath set up", k=1)[0]["citation"]["doi"] == "10.1039/d6sm00560h"
    assert rag.search("shape of charged sugar chains", k=1)[0]["citation"]["doi"] == "10.1093/glycob/cwag064"


def test_long_chunks_are_embedded_in_windows(real_embedder):
    import numpy as np

    long_text = "Ions adsorb at the air water interface. " * 120  # > 512 word pieces
    v = real_embedder.embed_documents([long_text, "short"])
    assert v.shape == (2, 384)
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-5)
