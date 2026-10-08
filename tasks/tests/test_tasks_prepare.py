"""Script steps: sources for the methods card (with a fake rag package), the calc folder, collect."""
import json
import sys
import types

import pytest

from tasks import collect, prepare_calc, prepare_methods, sources

DOI = "10.1021/acs.jctc.5c02051"


def passage(doi, page, text, score=0.5):
    return {"id": f"doi:{doi}#p{page}", "text": text, "score": score,
            "citation": {"key": "[1]", "title": "T", "authors": "A. B", "year": 2026, "journal": "J", "doi": doi,
                         "page": page, "url": f"https://doi.org/{doi}", "quote": text[:50]}}


@pytest.fixture
def fake_rag(monkeypatch):
    calls = []
    hits = [passage(DOI.upper(), 4, "We used CHARMM36 with the ECC water model."),
            passage(DOI, 2, "Simulations ran for 1 microsecond in GROMACS 2023."),
            passage("10.1021/other", 1, "Related: calcium binding to membranes.", score=0.9),
            {"id": "", "text": "", "score": 0.1, "citation": {}}]
    mod = types.ModuleType("rag")
    mod.search = lambda q, k=6: calls.append((q, k)) or list(hits)
    monkeypatch.setitem(sys.modules, "rag", mod)
    monkeypatch.setenv("MACRAE_OFFLINE", "1")
    return calls


def test_methods_sources_prefer_the_papers_own_passages(tmp_path, fake_rag, capsys):
    assert prepare_methods.main(["--doi", f"https://doi.org/{DOI}", "--out", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n_from_paper"] == 2 and out["n_sources"] == 3 and out["doi"] == DOI
    srcs = json.loads((tmp_path / "paper" / "sources.json").read_text())
    assert [s["key"] for s in srcs] == ["[1]", "[2]", "[3]"]
    assert [s["citation"]["page"] for s in srcs[:2]] == [2, 4]  # paper order
    assert srcs[2]["origin"].startswith("related")
    assert all(s["citation"]["key"] == s["key"] for s in srcs)
    assert srcs[0]["citation"]["title"] == "T"  # the index's citation wins over the publication record
    ctx = (tmp_path / "paper" / "context.md").read_text()
    assert "## [1]" in ctx and "GROMACS" in ctx
    assert json.loads((tmp_path / "sources.json").read_text()) == srcs
    assert len(fake_rag) == len(prepare_methods.QUERIES)


def test_methods_sources_without_index_or_network(tmp_path, monkeypatch, capsys):
    mod = types.ModuleType("rag")
    mod.search = lambda q, k=6: (_ for _ in ()).throw(FileNotFoundError("index/ missing"))
    monkeypatch.setitem(sys.modules, "rag", mod)
    monkeypatch.setenv("MACRAE_OFFLINE", "1")
    assert prepare_methods.main(["--doi", DOI, "--out", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n_sources"] == 1 and out["n_from_paper"] == 0
    (src,) = json.loads((tmp_path / "paper" / "sources.json").read_text())
    assert src["origin"] == "bibliographic record only" and "Bayesian" in src["text"]


def test_methods_rejects_bad_or_foreign_dois(tmp_path, capsys):
    assert prepare_methods.main(["--doi", "rm -rf /", "--out", str(tmp_path)]) == 2
    assert prepare_methods.main(["--doi", "10.9999/zzz", "--out", str(tmp_path)]) == 2


def test_abstract_used_when_index_has_little(tmp_path, monkeypatch, fake_rag, capsys):
    monkeypatch.setattr(sources, "openalex_abstract", lambda doi: "An abstract.")
    monkeypatch.setattr(prepare_methods.S, "openalex_abstract", lambda doi: "An abstract.")
    mod = sys.modules["rag"]
    mod.search = lambda q, k=6: [passage(DOI, 3, "Only one passage.")]
    assert prepare_methods.main(["--doi", DOI, "--out", str(tmp_path)]) == 0
    srcs = json.loads((tmp_path / "paper" / "sources.json").read_text())
    assert [s["origin"] for s in srcs] == ["abstract (OpenAlex)", "paper (RAG index)"]


@pytest.mark.parametrize("raw,ion", [("Na+", "Na+"), ("na+", "Na+"), ("sodium", "Na+"), ("ca2+", "Ca2+"),
                                     ("Ca++", "Ca2+"), (" K + ", "K+")])
def test_parse_ion(raw, ion):
    assert prepare_calc.parse_ion(raw) == ion


def test_parse_ion_rejects_others():
    with pytest.raises(ValueError):
        prepare_calc.parse_ion("Cl-")


def test_calc_folder_and_references(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "rag", types.ModuleType("rag"))  # no search(): treated as no index
    sys.modules["rag"].search = lambda q, k=6: []
    assert prepare_calc.main(["--ion", "Ca2+", "--out", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ion"] == "Ca2+" and out["charge"] == 2 and out["n_references"] == prepare_calc.MAX_REFS
    task = json.loads((tmp_path / "calc" / "task.json").read_text())
    assert task["ecc_scaling"] == 0.75 and task["water_model_charges"]["O"] == -0.8476
    refs = json.loads((tmp_path / "calc" / "references.json").read_text())
    assert all(prepare_calc.ECC_RE.search(r["citation"]["title"]) for r in refs)
    assert "calcium" in refs[0]["citation"]["title"].lower()
    assert prepare_calc.main(["--ion", "Fe3+", "--out", str(tmp_path)]) == 2


def test_collect_copies_and_summarizes(tmp_path, monkeypatch, capsys):
    files = tmp_path / "artifacts" / "calc"
    files.mkdir(parents=True)
    from tasks.tests.fake_harbor import CALC_RESULT
    (files / "result.json").write_text(json.dumps(CALC_RESULT))
    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps({"calc": {"ok": True, "files": str(files)}}))
    monkeypatch.setenv("FLOW_CONTEXT", str(ctx))
    monkeypatch.setenv("FLOW_RUN_DIR", str(tmp_path / "run"))
    assert collect.main(["--step", "calc", "--files", "result.json", "missing.md"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"].startswith("Na+–water binding: -25.5 kcal/mol at 2.20 Å (B3LYP/def2-TZVP, PySCF 2.14.0)")
    assert "-30.4 → -22.8" in out["summary"]
    assert (tmp_path / "run" / "result" / "result.json").is_file()
    ctx.write_text(json.dumps({"calc": {"ok": False}}))
    assert collect.main(["--step", "calc", "--files", "result.json"]) == 1
