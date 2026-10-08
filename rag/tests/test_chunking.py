from rag.chunking import chunk_pages
from rag.text import approx_tokens


def _page(tag, n):
    return " ".join(f"Sentence {i} on page {tag} talks about ion hydration and water structure." for i in range(n))


def test_chunks_respect_budget_and_pages():
    pages = [_page(1, 60), _page(2, 60), _page(3, 10)]
    chunks = chunk_pages(pages, "doi:10.1/x", budget=200, overlap=40)
    assert len(chunks) > 5
    for c in chunks:
        assert c["tokens"] <= 200 * 1.05
        assert c["id"].startswith("doi:10.1/x#p")
        assert f"page {c['page']} " in c["text"].split(".")[0] + " "
        assert c["page"] <= c["page_end"]
    assert len({c["id"] for c in chunks}) == len(chunks)
    assert chunks[0]["id"] == "doi:10.1/x#p1c1" and chunks[1]["id"] == "doi:10.1/x#p1c2"
    # every sentence ends up somewhere
    joined = " ".join(c["text"] for c in chunks)
    for tag, n in ((1, 60), (2, 60), (3, 10)):
        for i in range(n):
            assert f"Sentence {i} on page {tag} " in joined


def test_overlap_between_consecutive_chunks():
    chunks = chunk_pages([_page(1, 80)], "p", budget=150, overlap=40)
    for a, b in zip(chunks, chunks[1:]):
        last_sentence = a["text"].rsplit(". ", 1)[-1]
        assert last_sentence.rstrip(".") in b["text"]


def test_default_budget_is_about_800_tokens():
    chunks = chunk_pages([_page(1, 400)], "p")
    assert all(approx_tokens(c["text"]) <= 840 for c in chunks)
    assert approx_tokens(chunks[0]["text"]) >= 700


def test_overlong_sentence_is_split_and_tiny_tail_merged():
    table = " ".join(f"x{i}" for i in range(3000))  # no punctuation at all
    chunks = chunk_pages([table, "Short tail."], "p", budget=300, overlap=0)
    assert all(c["tokens"] <= 330 for c in chunks)
    assert "Short tail." in chunks[-1]["text"]
    assert chunks[-1]["page_end"] == 2


def test_empty_pages():
    assert chunk_pages([], "p") == []
    assert chunk_pages(["", "  "], "p") == []
