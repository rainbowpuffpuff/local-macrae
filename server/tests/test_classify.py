from server import catalog
from server.classify import citations_in_output, classify_command, classify_tool, past_tense


def test_read_of_a_paper_is_read_with_citation():
    c = classify_tool("Read", {"file_path": "/app/papers/10.1093_glycob_cwag064.pdf"}, "text")
    assert c.type == "read"
    assert c.title == "Read papers/10.1093_glycob_cwag064.pdf"
    assert c.citation and c.citation["doi"] == "10.1093/glycob/cwag064"
    assert c.citation["url"] == "https://doi.org/10.1093/glycob/cwag064"


def test_read_of_paper_json_title_matches_contract_example():
    c = classify_tool("Read", {"file_path": "/app/paper/paper.json"}, '{"doi": "10.1093/glycob/cwag064"}')
    assert (c.type, c.title) == ("read", "Read paper/paper.json")
    assert c.citation["title"].startswith("Quantitative mapping")


def test_read_of_other_file_is_not_a_paper_read():
    assert classify_tool("Read", {"file_path": "/app/src/main.py"}, "x").type == "status"


def test_grep_glob_webfetch_of_papers_are_reads():
    assert classify_tool("Grep", {"pattern": "hydration", "path": "/app/papers"}, "").type == "read"
    assert classify_tool("Glob", {"pattern": "papers/*.pdf"}, "").type == "read"
    assert classify_tool("WebFetch", {"url": "https://doi.org/10.1021/acs.jctc.5c02051"}, "").type == "read"
    assert classify_tool("WebFetch", {"url": "https://example.com/weather"}, "").type == "status"


def test_rag_and_search_papers_are_search():
    assert classify_tool("mcp__macrae__search_papers", {"query": "ion pairing"}, "").type == "search"
    c = classify_tool("Bash", {"command": 'python -m rag search "ion pairing" --k 6'}, None)
    assert c.type == "search" and "ion pairing" in c.title and not c.needs_output


def test_bash_calc_by_program_and_title_with_duration():
    c = classify_tool("Bash", {"command": "xtb dimer.xyz --gfn 2", "description": "Run water dimer energy"},
                      "TOTAL ENERGY -10.1234 Eh", seconds=0.4)
    assert c.type == "calc"
    assert c.title == "Ran water dimer energy (xtb, 0.4 s)"
    assert "-10.1234" in c.detail


def test_bash_calc_by_output_numbers():
    c = classify_tool("Bash", {"command": "./run.sh"}, "E = -76.026765 Eh\nE = -76.0277 Eh\nd = 2.91234")
    assert c.type == "calc"
    assert classify_tool("Bash", {"command": "ls -la"}, "total 0\nfoo bar").type == "status"


def test_bash_waits_for_output_before_deciding():
    assert classify_tool("Bash", {"command": "./run.sh"}, None).needs_output
    assert classify_tool("Bash", {"command": "python e.py"}, None).needs_output  # duration/output to come


def test_installs_and_version_checks_are_not_calcs():
    assert classify_command("pip install numpy xtb", "ok").type == "status"
    assert classify_command("python --version", "Python 3.11.9").type == "status"


def test_write_edit_think():
    assert classify_tool("Write", {"file_path": "/app/out/result.json", "content": "{}"}, "").type == "write"
    assert classify_tool("Edit", {"file_path": "/app/card.md", "old_string": "a", "new_string": "b"}, "").title \
        == "Edited card.md"
    assert classify_tool("TodoWrite", {"todos": [{"content": "read", "status": "pending"}]}, "").type == "think"


def test_unknown_tool_is_status():
    c = classify_tool("SomethingNew", {"x": 1}, "")
    assert c.type == "status" and c.title == "Used SomethingNew"


def test_past_tense():
    assert past_tense("Run water dimer energy") == "Ran water dimer energy"
    assert past_tense("Compute RDF") == "Computed RDF"
    assert past_tense("water box equilibration") == "Ran water box equilibration"


def test_citations_in_output_accepts_passages_and_bare_citations():
    text = 'log line\n{"passages": [{"text": "abc", "citation": {"title": "T", "doi": "10.1/x", "page": "4"}}]}'
    cits = citations_in_output(text)
    assert cits == [{"key": "", "title": "T", "authors": "", "year": None, "journal": "", "doi": "10.1/x",
                     "page": 4, "url": "https://doi.org/10.1/x", "quote": "abc"}]
    assert citations_in_output('[{"title": "A", "doi": "10.2/y"}, {"title": "A", "doi": "10.2/y"}]')[0]["doi"] \
        == "10.2/y"
    assert len(citations_in_output('[{"title": "A", "doi": "10.2/y"}, {"title": "A", "doi": "10.2/y"}]')) == 1
    assert citations_in_output("plain text, no json") == []


def test_find_doi_variants():
    assert catalog.find_doi("https://doi.org/10.1021/acs.jctc.5c02051") == "10.1021/acs.jctc.5c02051"
    assert catalog.find_doi("papers/10.1021_acs.jctc.5c02051.pdf") == "10.1021/acs.jctc.5c02051"
    assert catalog.find_doi("no doi here") is None


def test_command_label():
    from server.classify import command_label
    assert command_label("python3 /app/run_md.py --ps 10") == "run_md.py"
    assert command_label("cd x && python -m openmm.testInstallation") == "openmm.testInstallation"
    assert command_label('python3 -c "print(1)"') == "a Python snippet"
    assert classify_command("python3 calc.py", "E = -1.2345 kcal").title == "Ran calc.py (python)"
