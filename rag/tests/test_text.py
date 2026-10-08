from rag.text import approx_tokens, clean_doi, find_dois, fold, split_sentences, tokenize, truncate


def test_find_dois_cleans_and_dedupes():
    text = ("DOI: 10.1021/acs.jctc.5c02051. See https://doi.org/10.1039/D6SM00560H, and (10.1093/glycob/cwag064) "
            "again 10.1021/acs.jctc.5c02051")
    assert find_dois(text) == ["10.1021/acs.jctc.5c02051", "10.1039/d6sm00560h", "10.1093/glycob/cwag064"]


def test_clean_doi_prefixes_and_parens():
    assert clean_doi("https://dx.doi.org/10.1002/(SICI)1097-0282(1998)46:3<155::AID>3.0.CO;2-#") .startswith(
        "10.1002/(sici)1097-0282(1998)")
    assert clean_doi("doi:10.1063/5.0012345).") == "10.1063/5.0012345"
    assert clean_doi("10.1063/5.0(12)") == "10.1063/5.0(12)"


def test_fold_strips_accents_and_punctuation():
    assert fold("Košťál, V.: Ion–Water (H₂O) interactions!") == "kostal v ion water h2o interactions"


def test_tokenize_drops_stopwords_and_plurals():
    assert tokenize("The ions and the surfaces of water") == ["ion", "surface", "water"]
    assert tokenize("et al. used it") == []


def test_split_sentences_keeps_abbreviations():
    s = split_sentences("As shown by Jungwirth et al. Ions adsorb. See Fig. 3 for details. Done!")
    assert s == ["As shown by Jungwirth et al. Ions adsorb.", "See Fig. 3 for details.", "Done!"]


def test_truncate_and_tokens():
    assert truncate("short", 10) == "short"
    t = truncate("word " * 100, 50)
    assert len(t) <= 50 and t.endswith("…")
    assert 100 <= approx_tokens("word " * 100) <= 130
    assert approx_tokens("H2O 1.25 kcal/mol") > approx_tokens("water is wet")


def test_reference_list_is_dropped_but_an_early_mention_is_kept():
    from rag.pdf import _drop_back_matter

    body = [f"Body line {i} about ions at the air/water interface and charge scaling." for i in range(60)]
    refs = ["References"] + [f"({n}) A. Author; B. Author. J. Phys. Chem. B 20{n:02d}, 1{n}, 100-110." for n in range(1, 30)]
    pages = [body[:30], body[30:], refs]
    out, notes = _drop_back_matter(pages)
    assert out[0] == body[:30] and out[1] == body[30:]
    assert out[2] == [] and any("references removed" in n for n in notes)

    early = [["References to earlier work are discussed below.", *body[:20]], body[20:]]
    kept, notes2 = _drop_back_matter(early)
    assert kept == early and notes2 == []
