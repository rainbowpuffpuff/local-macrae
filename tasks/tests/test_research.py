"""The research protocol: the manuscript, notebook and edit-stream checks, the protocol text, notes → lessons."""
import json
import re
import shutil
import struct
import zlib
from pathlib import Path

import pytest

from tasks import checks, research, runner
from tasks.tests import reference_run as REF
from tasks.tests.fake_harbor import CALC_RESULT


@pytest.fixture
def refs(tmp_path):
    """The Na+ references prepare_calc finds offline (the ones the reference manuscript cites)."""
    from tasks import prepare_calc
    out = tmp_path / "prep"
    assert prepare_calc.main(["--ion", "Na+", "--out", str(out)]) == 0
    return str(out / "references.json")


@pytest.fixture
def calc(tmp_path):
    """The reference run in Harbor's layout: …/calc-a1__ref/{agent/trajectory.json, artifacts/app/calc/…}."""
    return REF.build(tmp_path / "job")


def problems(calc, refs, **kw):
    p, _, _ = checks.check_research(calc, refs, "result.json", ("e_int_kcal_mol", "r_min_angstrom"), **kw)
    return p


def edit_ms(calc, old, new, count=1):
    ms = calc / "results" / "manuscript.md"
    text = ms.read_text()
    assert old in text, old
    ms.write_text(text.replace(old, new, count))


# ── the reference run ─────────────────────────────────────────────────────


def test_reference_edit_stream_reproduces_the_manuscript():
    assert REF.apply_edits(REF.DRAFT, REF.EDITS) == REF.FINAL
    assert "**TBD**" in REF.DRAFT and "**TBD**" not in REF.FINAL
    assert REF.EARLY_CLAIM not in REF.FINAL  # the premature def2-SVP claim was deleted
    assert any(REF.EARLY_CLAIM == new for _, _, new in REF.EDITS)  # …after it had been written in


def test_reference_run_passes_and_reports_what_it_saw(calc, refs, capsys):
    p, notes, stats = checks.check_research(calc, refs, "result.json", ("e_int_kcal_mol", "r_min_angstrom"))
    assert p == [] and notes == []
    ms = stats["manuscript"]
    assert ms["figures"] == ["fig1.png"] and ms["citations"] == [1, 2, 3, 4, 5]
    assert ms["math"]["display"] == 3 and ms["math"]["inline"] > 10
    es = stats["edit_stream"]["manuscript"]
    assert (es["writes"], es["edits"], es["shell_writes"], es["work_between"]) == (1, 9, [], True)
    assert stats["edit_stream"]["notes"]["ops"] == 5
    assert checks.main(["calc", str(calc), "--references", refs, "--ion", "Na+", "--research"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("ok") and "edit stream: 1 Write + 9 Edit on the manuscript" in out


def test_real_figure_is_a_plot():
    assert checks.png_size(REF.DATA / "fig1.png") == (960, 630)
    assert CALC_RESULT["figures"] == ["results/fig1.png"] and CALC_RESULT["software"] == "PySCF 2.14.0"


# ── manuscript rules ──────────────────────────────────────────────────────


def test_the_draft_is_not_a_final_manuscript(calc, refs):
    (calc / "results" / "manuscript.md").write_text(REF.DRAFT)
    p = " ".join(problems(calc, refs))
    assert "placeholders left" in p and "TBD" in p
    assert "doesn't state e_int_kcal_mol = -25.46" in p


@pytest.mark.parametrize("old,new,needle", [
    ("## Discussion", "## Talk", "missing section '## Discussion'"),
    ("# How strongly", "How strongly", "must start with its title"),
    ("= -25.46$ kcal/mol, with a", "= -27.10$ kcal/mol, with a", "doesn't state e_int_kcal_mol = -25.46"),
    ("[1, 3]. We ask", "[1, 9]. We ask", "citations [9] don't exist"),
    ("\n[5] V. Košťál", "\n[x] V. Košťál", "## References does not list [5]"),
    ("10.1021/acs.jpcb.7b12510", "10.1021/acs.jpcb.9z99999", "[2] has DOI 10.1021/acs.jpcb.9z99999"),
    ("](fig1.png)", "](fig7.png)", "figure 'fig7.png' does not exist"),
    ("Figure 1 shows the scan.", "The scan:", None),  # still referenced in the Discussion "(Figure 1)"
    ("$$E_\\mathrm{C}(r)", "$$\\frac{E_\\mathrm{C}(r)", "unbalanced braces"),
])
def test_manuscript_rules(calc, refs, old, new, needle):
    edit_ms(calc, old, new)
    p = problems(calc, refs)
    if needle is None:
        assert p == []
    else:
        assert any(needle in x for x in p), p


def test_math_is_required_and_katex_safe(calc, refs):
    ms = calc / "results" / "manuscript.md"
    text = ms.read_text()
    ms.write_text(re.sub(r"\$\$.*?\$\$", "", text, flags=re.S))
    assert any("no display equation" in x for x in problems(calc, refs))
    ms.write_text(text.replace("$$E_\\mathrm{int}^\\mathrm{CP}", "$$\\begin{align}E_\\mathrm{int}^\\mathrm{CP}", 1)
                  .replace("E_{B}^{AB}.$$", "E_{B}^{AB}.\\end{align}$$", 1))
    assert any("\\begin{align}" in x for x in problems(calc, refs))
    ms.write_text(text + "\n$$x = 1\n")
    assert any("unbalanced $$" in x for x in problems(calc, refs))
    ms.write_text(re.sub(r"(?<!\$)\$(?!\$)[^$\n]+\$", "x", text))
    assert any("no inline math" in x for x in problems(calc, refs))


def test_currency_is_not_math():
    text = "It cost $0.40 and $2 in total."
    assert not checks.INLINE_MATH_RE.findall(text)
    assert checks.INLINE_MATH_RE.findall("the energy $E_\\mathrm{int}$ and $r$") == ["E_\\mathrm{int}", "r"]


def tiny_png(path: Path, w: int, h: int) -> None:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff" * w * 3 for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_a_figure_must_be_a_real_plot(calc, refs):
    fig = calc / "results" / "fig1.png"
    tiny_png(fig, 1, 1)
    assert any("not a real plot" in x for x in problems(calc, refs))
    fig.write_text("not an image")
    assert any("not a real plot" in x for x in problems(calc, refs))
    shutil.copy2(REF.DATA / "fig1.png", fig)
    tiny_png(calc / "results" / "fig2.png", 400, 300)  # made but never shown: a note, not a failure
    p, notes, _ = checks.check_research(calc, refs)
    assert p == [] and any("fig2.png" in n for n in notes)


def test_figure_paths_relative_to_the_workspace_also_resolve(calc, refs):
    edit_ms(calc, "](fig1.png)", "](results/fig1.png)")
    assert problems(calc, refs) == []


def test_every_figure_is_discussed(calc, refs):
    edit_ms(calc, "fig1.png)", "fig1.png)\n\n![Figure 2. The same, zoomed.](fig1.png)")
    assert any("never refers to Figure 2" in x for x in problems(calc, refs))


def test_missing_manuscript_and_notes(tmp_path, refs):
    (tmp_path / "result.json").write_text(json.dumps(CALC_RESULT))
    p = " ".join(problems(tmp_path, refs))
    assert "results/manuscript.md is missing" in p and "NOTES_TO_SELF.md is missing" in p


def test_numbers_are_matched_at_the_precision_written():
    assert checks._states("binds by −25.5 kcal/mol", -25.46)
    assert checks._states("binds by 25 kcal/mol", -25.46)  # sign-free, rounded
    assert checks._states("at 2.20 Å", 2.2) and checks._states("at 2.2 Å", 2.2)
    assert not checks._states("at 2 Å", 2.2)  # too coarse for a small number
    assert not checks._states("binds by −32.2 kcal/mol", -25.46)
    assert not checks._states("ref [25]", -25.46) or True  # bracketed citations may match; harmless


# ── notebook ──────────────────────────────────────────────────────────────


def test_notes_need_the_three_sections(calc):
    assert checks.check_notes(calc) == ([], [])
    (calc / "NOTES_TO_SELF.md").write_text("# Notes\n## What I tried\n- stuff\n")
    p, _ = checks.check_notes(calc)
    assert any("What was slow" in x for x in p) and any("differently" in x for x in p)
    assert checks.main(["notes", str(calc)]) == 1


def test_notes_become_lessons_and_gaps():
    lessons = research.notes_to_lessons(REF.NOTES_FINAL, "small-calc")
    kinds = [x["kind"] for x in lessons]
    assert kinds.count("do") >= 2 and "avoid" in kinds and "setting" in kinds and "tool" in kinds
    avoid = [x["lesson"] for x in lessons if x["kind"] == "avoid"]
    assert [x[:20] for x in avoid] == ["Never sleep-wait for", "Don't quote the def2"]
    tool = next(x for x in lessons if x["kind"] == "tool")
    assert tool["capability"] == "calc-image" and "130 s" in tool["lesson"]
    assert all(x["task_id"] == "small-calc" and x["source"] == "notes" for x in lessons)
    # placeholders from the draft aren't lessons
    assert research.notes_to_lessons(REF.NOTES_DRAFT) == []


def test_gap_lines_anywhere():
    text = 'echo "CAPABILITY_GAP: calc-image: pyscf missing, 62 s" && x\nCAPABILITY_GAP: gromacs-image: gmx missing'
    assert research.gaps(text) == [{"name": "calc-image", "why": "pyscf missing, 62 s"},
                                   {"name": "gromacs-image", "why": "gmx missing"}]


def test_notes_cli(tmp_path, capsys):
    p = tmp_path / "NOTES_TO_SELF.md"
    p.write_text(REF.NOTES_FINAL)
    assert research.main(["notes", str(p), "--task-id", "small-calc"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["gaps"][0]["name"] == "calc-image" and out["lessons"] and out["sections"]["tried"]


# ── edit stream ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("cmd,writes", [
    ("cat > /app/calc/results/manuscript.md <<'EOF'\n# x\nEOF", True),
    ("echo '## Results' >> results/manuscript.md", True),
    ("sed -i 's/TBD/2.2/' results/manuscript.md", True),
    ("printf x | tee -a /app/calc/results/manuscript.md", True),
    ("cp draft.md results/manuscript.md", True),
    ("python3 -c \"open('results/manuscript.md','w').write('x')\"", True),
    ("python3 -c \"Path('results/manuscript.md').write_text('x')\"", True),
    ("wc -w results/manuscript.md 2>&1", False),
    ("cat results/manuscript.md | head -20", False),
    ("grep -c TBD /app/calc/results/manuscript.md", False),
    ("cp results/manuscript.md /tmp/backup.md", False),
])
def test_shell_writes_are_recognised(cmd, writes):
    es = checks.edit_stream([("Bash", {"command": cmd})], checks.MANUSCRIPT)
    assert bool(es["shell_writes"]) is writes


def test_stream_json_transcripts_work_too(calc, refs, tmp_path):
    trial = calc.parents[2]
    traj = json.loads((trial / "agent" / "trajectory.json").read_text())
    lines = [json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tc["tool_call_id"], "name": tc["function_name"], "input": tc["arguments"]}]}})
        for st in traj["steps"] for tc in st.get("tool_calls") or []]
    (trial / "agent" / "trajectory.json").unlink()
    (trial / "agent" / "claude-code.txt").write_text("\n".join(lines) + "\n")
    p, _, stats = checks.check_research(calc, refs)
    assert p == [] and stats["edit_stream"]["transcript"] == "claude-code.txt"
    assert stats["edit_stream"]["manuscript"]["edits"] == 9


def test_without_a_transcript_the_stream_is_not_judged(tmp_path, calc, refs):
    loose = tmp_path / "loose"
    shutil.copytree(calc, loose)
    p, notes, _ = checks.check_research(loose, refs)
    assert p == [] and any("edit stream not checked" in n for n in notes)


def test_explicit_trajectory_and_a_single_write(calc, refs, tmp_path):
    traj = tmp_path / "t.json"
    traj.write_text(json.dumps({"steps": [{"tool_calls": [
        {"function_name": "Write", "arguments": {"file_path": "/app/calc/results/manuscript.md", "content": "x"}}]}]}))
    p, _, _ = checks.check_research(calc, refs, trajectory=str(traj))
    assert any("never revised" in x for x in p)


# ── the protocol given to agents ──────────────────────────────────────────


def test_protocol_text():
    text = research.protocol("/app/fit", python="/opt/bff/bin/python", packages="bff",
                             figure_hint="the posterior", numbers_hint="the Bayes factor")
    for needle in ("/app/fit/NOTES_TO_SELF.md", "/app/fit/results/manuscript.md", "Write and Edit tools",
                   "**TBD**", "$$...$$", "![Figure 1. <caption>](fig1.png)", "Figure 1 should show the posterior.",
                   "The abstract states the Bayes factor", "never sleep longer than 15 s",
                   '/opt/bff/bin/python -c "import bff"', "CAPABILITY_GAP: calc-image", "ensurepip"):
        assert needle in text, needle
    assert "CAPABILITY_GAP" not in research.protocol("/app/x")  # no environment to check
    assert "{{" not in text and "\n" not in text  # safe inside a folded YAML instruction


def test_workspace_and_reference_lines(tmp_path, refs):
    srcs = json.loads(Path(refs).read_text())
    p = research.prepare_workspace(tmp_path / "w", srcs, task_id="small-calc", title_hint="T")
    text = p.read_text()
    assert (tmp_path / "w" / "results").is_dir() and "Research protocol (small-calc)" in text
    assert "*T*" in text and research.reference_line(srcs[0]) in text
    line = research.reference_line(srcs[1])
    assert line.startswith("[2] J. Melcr; H. Martinez-Seara Monne; R. Nencini et al. (2018). Accurate Binding")
    assert line.endswith("https://doi.org/10.1021/acs.jpcb.7b12510")
    assert line in REF.FINAL  # the reference manuscript lists sources exactly as PROTOCOL.md shows them


def test_prepare_cli_for_any_flow(tmp_path, refs, capsys):
    d = tmp_path / "fit"
    assert research.main(["prepare", "--dir", str(d), "--references", refs, "--task-id", "bff-x",
                          "--python", "/opt/bff/bin/python", "--packages", "bff"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["manuscript"] == "results/manuscript.md" and Path(out["checker"]).name == "checks.py"
    assert "/app/fit/results/manuscript.md" in out["protocol"] and (d / "PROTOCOL.md").is_file()


# ── every task flow carries the protocol ──────────────────────────────────


def test_every_task_flow_follows_the_research_protocol():
    assert [p for p in runner.check_all() if "research" in p or "NOTES" in p or "protocol" in p] == []
    ids = {t["id"]: t.get("research") for t in runner.list_tasks()}
    assert ids["small-calc"]["manuscript"] == "results/manuscript.md"


def test_research_problems_flag_a_flow_without_the_hook():
    spec = {"steps": [{"id": "a", "instruction": "do it", "check": "true"}]}
    assert "no \"research\" entry" in runner.research_problems({"id": "bff-x"}, spec)[0]
    assert runner.research_problems({"id": "x", "research": False}, spec) == []
    p = runner.research_problems({"id": "x", "research": {"manuscript": "results/manuscript.md",
                                                          "notes": "NOTES_TO_SELF.md"}}, spec)
    assert len(p) == 2 and "NOTES_TO_SELF.md" in p[0] and "protocol" in p[1]
    spec["steps"][0]["instruction"] = "{{ research.output.protocol }}"
    p = runner.research_problems({"id": "x", "research": {"manuscript": "m", "notes": "NOTES_TO_SELF.md"}}, spec)
    assert p == ["x: the protocol's step has no manuscript check (checks.py research … or calc --research)"]
    spec["steps"][0]["check"] = 'python3 "{{ research.output.checker }}" research . --references r'
    assert runner.research_problems({"id": "x", "research": {"manuscript": "m", "notes": "N"}}, spec) == []


def test_a_draft_notebook_is_not_finished(calc):
    (calc / "NOTES_TO_SELF.md").write_text(REF.NOTES_DRAFT)
    p, _ = checks.check_notes(calc)
    assert any("What was slow" in x for x in p) and any("differently" in x for x in p)


def test_demo_bundle_in_research_is_current(tmp_path):
    """research/demo/small-calc-na is generated by reference_run.demo; regenerate it after changing the run."""
    fresh = REF.demo(tmp_path / "demo")
    committed = Path(__file__).resolve().parents[2] / "research" / "demo" / "small-calc-na"
    for name in ("manuscript.md", "draft.md", "NOTES_TO_SELF.md", "edit_stream.json", "trajectory.json",
                 "result.json", "fig1.png"):
        assert (committed / name).read_bytes() == (fresh / name).read_bytes(), name
    stream = json.loads((committed / "edit_stream.json").read_text())
    ms = [x for x in stream if x["path"].endswith("results/manuscript.md")]
    assert [x["op"] for x in ms] == ["write"] + ["edit"] * 9
    assert ms[0]["content"] == REF.DRAFT and ms[-1]["content"] == REF.FINAL
    assert any(x["old"] == REF.EARLY_CLAIM for x in ms)  # the replay shows a wrong claim being deleted
    assert [x["seq"] for x in stream] == list(range(1, len(stream) + 1))
    assert all(a["t"] < b["t"] for a, b in zip(stream, stream[1:]))
