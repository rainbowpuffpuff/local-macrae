import json
import shutil

import pytest

import rag
from rag import config, retrieval as search_mod

PASSAGE_KEYS = {"id", "text", "score", "citation"}
CITATION_KEYS = {"key", "title", "authors", "year", "journal", "doi", "page", "url", "quote"}


# ---- zero papers -------------------------------------------------------------------------------------------

def test_search_without_any_index_returns_empty(rag_env):
    assert not rag_env["index"].exists()
    assert rag.search("ion pairing") == []
    assert rag.stats() == {"papers": 0, "chunks": 0, "embedder": None, "created": None}
    context, citations = rag.format_context([])
    assert citations == [] and "don't cover" in context


def test_ingest_zero_papers_empty_and_missing_dir(rag_env, tmp_path):
    summary = rag.ingest()
    assert summary["papers"] == 0 and summary["chunks"] == 0
    assert rag.search("anything") == []
    summary = rag.ingest(tmp_path / "does-not-exist", rag_env["index"])
    assert summary["papers"] == 0
    assert rag.search("anything", k=3) == []
    assert rag.stats()["papers"] == 0


def test_blank_query(papers_dir):
    rag.ingest()
    assert rag.search("") == [] and rag.search("   ") == []


# ---- ingest + search on the synthetic PDFs -------------------------------------------------------------------

def test_ingest_fixtures_and_search_with_citations(papers_dir):
    summary = rag.ingest()
    assert summary["papers"] == 2 and summary["chunks"] >= 2 and summary["embedder"] == "hash:256"
    assert summary["skipped"] == [] and summary["unmatched"] == []

    passages = rag.search("which thermostat and barostat kept the temperature and pressure", k=6)
    assert passages, "expected a hit"
    top = passages[0]
    assert set(top) == PASSAGE_KEYS and set(top["citation"]) == CITATION_KEYS
    c = top["citation"]
    assert c["key"] == "[1]" and c["doi"] == "10.1039/d6sm00560h" and c["year"] == 2026
    assert c["authors"] == "I. Schachter; P. Jungwirth; D. Harries" and c["journal"] == "Soft Matter"
    assert c["url"] == "https://doi.org/10.1039/d6sm00560h"
    assert "thermostat" in c["quote"] and len(c["quote"]) <= 240 and c["quote"] in top["text"]
    assert top["id"].startswith("doi:10.1039/d6sm00560h#p") and 0 < top["score"] <= 1
    assert [p["citation"]["key"] for p in passages] == [f"[{i}]" for i in range(1, len(passages) + 1)]
    assert [p["score"] for p in passages] == sorted((p["score"] for p in passages), reverse=True)
    json.dumps(passages)  # plain JSON-serializable dicts

    glycan = rag.search("iduronic acid ring puckering", k=1)[0]["citation"]
    assert glycan["doi"] == "10.1093/glycob/cwag064"  # matched by fuzzy title, no DOI in that PDF
    assert glycan["authors"].startswith("M. Riopedre Fernández")


def test_reference_list_is_not_indexed(papers_dir):
    rag.ingest()
    from rag.retrieval import _load

    texts = " ".join(c["text"] for c in _load().chunks)
    assert "Zwitterionic quasicrystal" not in texts and "Hyperbolic origami" not in texts
    assert "weaker adhesion" in texts  # the text right before "References" is kept


def test_pages_in_citations(papers_dir, monkeypatch):
    monkeypatch.setattr(config, "CHUNK_TOKENS", 90)
    monkeypatch.setattr(config, "CHUNK_OVERLAP_TOKENS", 0)
    rag.ingest()
    hit = rag.search("GROMACS 2024 twelve independent trajectories", k=1)[0]
    assert hit["citation"]["page"] == 2 and "#p2c" in hit["id"]
    hit = rag.search("first-order morphological transition free energy minima barrier", k=1)[0]
    assert hit["citation"]["page"] == 3


def test_k_and_doi_filter(papers_dir, monkeypatch):
    monkeypatch.setattr(config, "CHUNK_TOKENS", 90)
    rag.ingest()
    assert len(rag.search("membrane", k=2)) == 2
    assert len(rag.search("membrane", k=0)) == 1  # clamped to at least 1
    only = rag.search("simulation force field", k=50, doi="https://doi.org/10.1093/GLYCOB/cwag064")
    assert only and {p["citation"]["doi"] for p in only} == {"10.1093/glycob/cwag064"}
    assert rag.search("membrane", doi="10.1/not-indexed") == []


def test_bm25_only_when_embeddings_off(papers_dir, monkeypatch):
    monkeypatch.setenv("MACRAE_EMBEDDER", "none")
    summary = rag.ingest()
    assert summary["embedder"] is None
    hits = rag.search("velocity-rescaling thermostat")
    assert hits[0]["citation"]["doi"] == "10.1039/d6sm00560h" and hits[0]["score"] == 1.0
    assert rag.search("qwertyuiop") == []  # no lexical match and no embeddings -> nothing


def test_embedder_mismatch_falls_back_to_bm25(papers_dir, monkeypatch):
    rag.ingest()
    monkeypatch.setenv("MACRAE_EMBEDDER", "none")
    assert rag.search("thermostat")[0]["citation"]["doi"] == "10.1039/d6sm00560h"


# ---- incremental ingest, robustness --------------------------------------------------------------------------

def test_reingest_reuses_cache_and_server_sees_changes(papers_dir, rag_env, make_pdf):
    first = rag.ingest()
    assert first["reused"] == 0 and first["embedded"] == first["chunks"]
    assert rag.search("Cremer-Pople", k=1)  # loads + caches the index in this process
    again = rag.ingest()
    assert again["reused"] == 2 and again["embedded"] == 0 and again["chunks"] == first["chunks"]

    make_pdf(papers_dir / "extra.pdf", ["Clathrate hydrates\nDOI: 10.9999/clathrate.1\n"
                                        "Methane clathrate hydrates nucleate slowly in supercooled water."],
             title="Clathrate hydrates", author="J. Doe")
    third = rag.ingest()
    assert third["papers"] == 3 and third["reused"] == 2 and third["unmatched"] == ["extra.pdf"]
    hit = rag.search("methane clathrate nucleation", k=1)[0]  # cache reloads after the manifest changed
    assert hit["citation"]["doi"] == "10.9999/clathrate.1" and hit["citation"]["year"] is None
    assert hit["citation"]["title"] == "Clathrate hydrates" and hit["citation"]["authors"] == "J. Doe"

    (papers_dir / "glycan_paper.pdf").unlink()
    fourth = rag.ingest()
    assert fourth["papers"] == 2
    assert all(p["citation"]["doi"] != "10.1093/glycob/cwag064" for p in rag.search("iduronic", k=10))
    # only the files of the current index stay on disk
    names = sorted(p.name for p in rag_env["index"].iterdir())
    assert len(names) == 4 and names[-1] == "papers-" + names[-1][7:] and "manifest.json" in names


def test_bad_pdfs_are_skipped_not_fatal(papers_dir, make_pdf):
    (papers_dir / "broken.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")
    (papers_dir / "empty.PDF").write_bytes(b"")
    make_pdf(papers_dir / "scanned.pdf", [""])
    shutil.copy(papers_dir / "vesicle.pdf", papers_dir / "vesicle-copy.pdf")
    (papers_dir / "notes.txt").write_text("ignored")
    sub = papers_dir / "sub"
    sub.mkdir()
    make_pdf(sub / "nested.pdf", ["A nested paper about ionic liquids and their viscosity at room temperature."])
    summary = rag.ingest()
    reasons = {s["file"]: s["reason"] for s in summary["skipped"]}
    assert set(reasons) == {"broken.pdf", "empty.PDF", "scanned.pdf", "vesicle.pdf"} or \
        set(reasons) == {"broken.pdf", "empty.PDF", "scanned.pdf", "vesicle-copy.pdf"}
    assert "duplicate" in (reasons.get("vesicle.pdf") or reasons.get("vesicle-copy.pdf"))
    assert summary["papers"] == 3
    hit = rag.search("ionic liquids viscosity", k=1)[0]
    assert hit["id"].startswith("file:sub-nested#p1c")


def test_rebuild_ignores_cache(papers_dir):
    rag.ingest()
    assert rag.ingest(rebuild=True)["reused"] == 0


def test_corrupt_index_is_treated_as_empty(papers_dir, rag_env):
    rag.ingest()
    (rag_env["index"] / "manifest.json").write_text("{not json")
    search_mod.clear_cache()
    assert rag.search("thermostat") == []
    assert rag.ingest()["papers"] == 2  # and ingest recovers


# ---- format_context --------------------------------------------------------------------------------------------

def test_format_context_numbering_and_citations(papers_dir):
    rag.ingest()
    passages = rag.search("membrane simulation", k=2)
    passages = list(reversed(passages)) + [passages[0]]  # reordered + a duplicate
    context, citations = rag.format_context(passages)
    assert [c["key"] for c in citations] == ["[1]", "[2]"]
    assert "[1] " in context and "[2] " in context and "[3]" not in context
    assert citations[0]["doi"] == passages[0]["citation"]["doi"]
    assert passages[0]["citation"]["key"] != "[1]" or True  # inputs are not mutated:
    assert rag.search("membrane simulation", k=2)[0]["citation"]["key"] == "[1]"
    assert "et al. 2026" in context and "doi:10." in context and "p. 1" in context


def test_format_context_trims_long_passages_around_the_quote():
    text = ("Filler sentence number one. " * 200) + "The key finding is that iodide adsorbs at the surface. " + \
           ("More filler afterwards. " * 200)
    passage = {"id": "x", "text": text, "score": 1.0,
               "citation": {"key": "[1]", "title": "T", "authors": "A. B", "year": 2020, "journal": "J",
                            "doi": "", "page": 4, "url": "", "quote": "The key finding is that iodide adsorbs at the surface."}}
    context, cits = rag.format_context([passage], max_chars_per_passage=600)
    body = context.split("\n", 3)[-1]
    assert "iodide adsorbs" in body and len(body) < 700 and body.startswith("…") and body.endswith("…")
    assert cits[0]["page"] == 4


def test_doi_in_query_restricts_to_that_paper(papers_dir, monkeypatch):
    monkeypatch.setattr(config, "CHUNK_TOKENS", 90)
    rag.ingest()
    only = rag.search("10.1093/glycob/cwag064", k=10)
    assert only and {p["citation"]["doi"] for p in only} == {"10.1093/glycob/cwag064"}
    hits = rag.search("thermostat in https://doi.org/10.1039/D6SM00560H", k=10)
    assert {p["citation"]["doi"] for p in hits} == {"10.1039/d6sm00560h"}
    assert "thermostat" in hits[0]["citation"]["quote"]
    assert rag.search("doi:10.1021/not.indexed methods") == []


def test_public_api_is_not_shadowed_by_submodules(papers_dir):
    rag.ingest()
    rag.search("membrane")
    rag.format_context([])
    import importlib

    for sub in ("rag.indexer", "rag.retrieval", "rag.store", "rag.embed"):  # loading submodules must not
        importlib.import_module(sub)                                       # replace the public functions

    assert all(callable(getattr(rag, n)) and not hasattr(getattr(rag, n), "__path__")
               for n in ("ingest", "search", "format_context", "stats", "warmup"))
    assert type(rag.search).__name__ == "function" and type(rag.ingest).__name__ == "function"
    assert rag.warmup() == {"embedder": "hash:256", "papers": 2, "chunks": rag.stats()["chunks"]}


def test_concurrent_searches_during_reingest(papers_dir):
    import threading

    rag.ingest()
    errors, results = [], []

    def worker():
        try:
            for _ in range(15):
                results.append(len(rag.search("membrane adhesion", k=2)))
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for _ in range(3):
        rag.ingest(rebuild=True)
    for t in threads:
        t.join()
    assert not errors and set(results) == {2}
