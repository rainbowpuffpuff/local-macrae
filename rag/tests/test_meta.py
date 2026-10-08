from pathlib import Path

from rag.meta import Publications, match_paper

PUBS = Publications([
    {"title": "Vesicle internalization proceeds via a morphological phase transition", "journal": "Soft Matter",
     "year": 2026, "doi": "10.1039/d6sm00560h", "authors": "I. Schachter; P. Jungwirth; D. Harries"},
    {"title": "Water surface is acidic", "journal": "PNAS", "year": 2007, "doi": "10.1073/pnas.0701234104",
     "authors": "V. Buch; A. Milet; R. Vácha; P. Jungwirth; J. P. Devlin"},
    {"title": "Arginine", "journal": "X", "year": 1970, "doi": "10.1/arg", "authors": "A. B"},
    {"title": "Specific ion effects at the air/water interface", "journal": "Chem. Rev.", "year": 2006,
     "doi": "10.1021/cr0403741", "authors": "P. Jungwirth; D. J. Tobias"},
])


def m(head, meta=None, name="paper.pdf", pubs=PUBS):
    return match_paper(Path("/nonexistent") / name, head, meta or {}, pubs)


def test_match_by_doi_on_first_page_not_references():
    rec = m("Some Journal\nA title\nDOI: 10.1039/D6SM00560H\nabstract\fpage two cites 10.1021/cr0403741")
    assert rec["doi"] == "10.1039/d6sm00560h" and rec["matched_by"] == "doi"
    assert rec["authors"] == "I. Schachter; P. Jungwirth; D. Harries" and rec["year"] == 2026
    assert rec["url"] == "https://doi.org/10.1039/d6sm00560h" and rec["journal"] == "Soft Matter"


def test_known_doi_preferred_over_unknown_one():
    rec = m("Funding: 10.13039/501100001824\nDOI 10.1073/pnas.0701234104")
    assert rec["doi"] == "10.1073/pnas.0701234104"


def test_match_by_filename_and_pdf_metadata():
    assert m("no doi", name="10.1021_cr0403741.pdf")["matched_by"] == "filename"
    rec = m("no doi", meta={"subject": "Chem. Rev. 2006, doi:10.1021/cr0403741"})
    assert rec["doi"] == "10.1021/cr0403741" and rec["matched_by"] == "pdf-metadata"


def test_match_by_fuzzy_title_with_line_breaks_and_hyphenation():
    head = "CHEMICAL REVIEWS\nSpe-\ncific Ion Effects at the\nAir/Water Interface\nPavel Jungwirth and Douglas Tobias"
    rec = m(head)
    assert rec["doi"] == "10.1021/cr0403741" and rec["matched_by"] == "title"


def test_fuzzy_title_tolerates_one_garbled_word():
    head = "Journal\nSpecific ion efects at the air/water interface\nauthors"
    assert m(head)["doi"] == "10.1021/cr0403741"


def test_short_title_only_matches_near_top():
    body = "Introduction. " + "Lots of text about proteins. " * 30 + "We studied arginine side chains."
    assert m(body)["matched_by"] == "none"
    rec = m("Arginine\nA. B\n" + body)
    assert rec["doi"] == "10.1/arg" and rec["year"] is None  # 1970 = "no year yet" on the group site


def test_common_words_do_not_false_match():
    head = "Ions at interfaces\nThe water surface is not acidic according to our new spectroscopy data."
    assert m(head)["matched_by"] == "none"


def test_unmatched_uses_pdf_metadata_and_unlisted_doi():
    rec = m("Header\nA Cited Paper About Clathrates\nDOI: 10.9999/zz.123", meta={"title": "A Cited Paper",
                                                                                 "author": "J. Doe"})
    assert rec["matched_by"] == "doi-unlisted" and rec["doi"] == "10.9999/zz.123"
    assert rec["title"] == "A Cited Paper" and rec["authors"] == "J. Doe"
    rec = m("Header line\nA Cited Paper About Clathrates in Ice\nbody", meta={"title": "Microsoft Word - x.docx"})
    assert rec["title"] == "A Cited Paper About Clathrates in Ice" and rec["doi"] == "" and rec["url"] == ""


def test_sidecar_overrides(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"")
    (tmp_path / "x.json").write_text('{"doi": "10.1073/PNAS.0701234104", "title": "Custom title"}')
    rec = match_paper(pdf, "nothing", {}, PUBS)
    assert rec["matched_by"] == "sidecar" and rec["title"] == "Custom title"
    assert rec["authors"].startswith("V. Buch") and rec["url"] == "https://doi.org/10.1073/pnas.0701234104"


def test_publications_load_missing_file(tmp_path):
    assert len(Publications.load(tmp_path / "nope.json")) == 0
    assert len(Publications.load(None)) == 0


def test_real_publications_list_loads():
    from rag.config import metadata_path

    pubs = Publications.load(metadata_path())
    assert len(pubs) > 400 and "10.1039/d6sm00560h" in pubs.by_doi
