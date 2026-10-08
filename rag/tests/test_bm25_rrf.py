from rag.bm25 import BM25
from rag.retrieval import best_quote, first_author, rrf, short_ref


def test_bm25_ranks_matching_docs():
    docs = ["sodium ions at the water surface", "protein folding kinetics", "ions ions ions water", "nothing here"]
    bm = BM25(docs)
    top = bm.top("sodium ions", 10)
    assert top[0][0] == 0
    assert {d for d, _ in top} == {0, 2}
    assert bm.top("sodium", 10, allowed={1, 2}) == []
    assert BM25([]).top("x", 5) == []


def test_rrf_fuses_rankings():
    fused = rrf([[1, 2, 3], [3, 1]])
    assert fused[1] == 1 / 61 + 1 / 62
    assert fused[3] == 1 / 63 + 1 / 61
    assert fused[2] == 1 / 62
    assert max(fused, key=fused.get) == 1


def test_best_quote_picks_matching_sentence_and_limits_length():
    text = "Intro sentence here. The barostat was Parrinello-Rahman at 1 bar. " + "Long " * 200 + "end."
    assert best_quote(text, {"barostat": 2.0}) == "The barostat was Parrinello-Rahman at 1 bar."
    assert len(best_quote(text, {"long": 1.0})) <= 240
    assert best_quote(text, {}) == "Intro sentence here."


def test_short_ref():
    assert short_ref({"authors": "V. Košťál; D. Biriukov; P. Jungwirth", "year": 2026}) == "Košťál et al. 2026"
    assert short_ref({"authors": "P. Jungwirth; D. J. Tobias", "year": 2006}) == "Jungwirth and Tobias 2006"
    assert short_ref({"authors": "P. Jungwirth", "year": None}) == "Jungwirth"
    assert short_ref({"authors": "", "title": "Water surface is acidic", "year": 2007}) == "Water surface is acidic 2007"
    assert first_author("M. Riopedre Fernández; D. Biriukov") == "Riopedre Fernández"
