#!/usr/bin/env python3
"""Checks for task outputs. Run by agent_runner `check:` inside the folder an agent returned; exit 0 = reward 1.
Whatever is printed goes back to the agent as feedback on a retry, so messages say exactly what to fix.

    python3 tasks/checks.py card methods-card.md --sources /path/sources.json [--notes]
    python3 tasks/checks.py calc . --references /path/references.json --ion Na+ [--research]
    python3 tasks/checks.py research . --references /path/references.json [--numbers key,key]   # any research task
    python3 tasks/checks.py notes .
    python3 tasks/checks.py image-capability tasks/capabilities/calc-image

`research` = the research protocol (tasks/research.py): results/manuscript.md (title, abstract, methods, results,
discussion, references; LaTeX math; at least one real figure results/fig*.png referenced in the text; valid [n]
citations listed under References; no TBD left; the abstract's numbers match result.json), NOTES_TO_SELF.md, and
the edit stream: the manuscript was written with the Write/Edit tools and revised at least once (read from the
Harbor trial's agent/trajectory.json or claude-code.txt next to the returned folder, when there is one).
Problems fail the check; "notes" are printed but don't (a retry re-runs the whole calculation, so only what
matters fails).

Standard library only: it runs with whatever python3 the backend has.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import sys
from pathlib import Path
from typing import Optional

CITE_RE = re.compile(r"\[(\d+(?:\s*(?:,|–|-|—)\s*\d+)*)\]")
UNCOVERED_RE = re.compile(r"not (stated|covered|reported|described|given|available|specified|mentioned)|"
                          r"no information|sources do not|the sources don't|unknown from", re.I)
CARD_SECTIONS = ["## System studied", "## Computational methods", "## Experimental methods", "## Analysis",
                 "## Limitations", "## References"]
MIN_SENTENCE = 40  # shorter fragments ("See below.") need no citation


def cited_numbers(text: str) -> set[int]:
    out: set[int] = set()
    for m in CITE_RE.finditer(text):
        for part in re.split(r"\s*,\s*", m.group(1)):
            rng = re.split(r"\s*(?:–|-|—)\s*", part)
            if len(rng) == 2 and rng[0].isdigit() and rng[1].isdigit() and int(rng[0]) <= int(rng[1]) <= 999:
                out.update(range(int(rng[0]), int(rng[1]) + 1))
            elif part.strip().isdigit():
                out.add(int(part))
    return out


def _load_list(path: str) -> list:
    if not path:
        return []
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _valid_numbers(srcs: list) -> set[int]:
    return {int(s.get("n")) for s in srcs if isinstance(s, dict) and str(s.get("n", "")).isdigit()}


def sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    # split after . ! ? (optionally followed by a citation) when the next sentence starts with a capital/number
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(*_])", text)
    return [p.strip() for p in parts if p.strip()]


def check_card(card: Path, sources_file: str) -> list[str]:
    problems: list[str] = []
    if not card.is_file() or not card.read_text().strip():
        return [f"{card.name} is missing or empty"]
    text = card.read_text()
    srcs = _load_list(sources_file)
    valid = _valid_numbers(srcs)
    lines = text.splitlines()
    heads = [ln.strip() for ln in lines if ln.startswith("## ")]
    for sec in CARD_SECTIONS:
        if not any(h.lower().startswith(sec.lower()) for h in heads):
            problems.append(f"missing section heading '{sec}'")
    used = cited_numbers(text)
    if not used:
        problems.append("no [n] citations at all; every claim needs one")
    bad = sorted(n for n in used if valid and n not in valid)
    if bad:
        problems.append(f"citations {bad} don't exist; valid numbers are {sorted(valid)}")
    # every claim (sentence) outside headings and the References section carries a citation
    in_refs = False
    uncited: list[str] = []
    block: list[str] = []

    def flush() -> None:
        para = " ".join(block)
        block.clear()
        for s in sentences(para):
            if len(s) >= MIN_SENTENCE and not CITE_RE.search(s) and not UNCOVERED_RE.search(s):
                uncited.append(s)

    for ln in lines:
        st = ln.strip()
        if st.startswith("#"):
            flush()
            in_refs = st.lower().startswith("## references")
            continue
        if in_refs:
            continue
        if not st or st.startswith(("- ", "* ", "|")) or re.match(r"^\d+\.\s", st) or st.startswith("**Paper"):
            flush()
            if st and not st.startswith("**Paper"):
                block.append(re.sub(r"^([-*]|\d+\.)\s+", "", st.strip("|")))
                flush()
            continue
        block.append(st)
    flush()
    if uncited:
        shown = "\n".join(f"  - {s[:160]}" for s in uncited[:8])
        problems.append(f"{len(uncited)} sentence(s) make a claim without an [n] citation (add the source number, "
                        f"or say the sources don't state it):\n{shown}")
    if "## references" in text.lower():
        refs = text.lower().split("## references", 1)[1]
        missing = sorted(n for n in used if f"[{n}]" not in refs)
        if missing:
            problems.append(f"References section does not list {', '.join(f'[{n}]' for n in missing)}")
    return problems


CALC_NUMBERS = {  # key: (min, max) or None for any finite number
    "r_min_angstrom": (1.4, 4.0),
    "e_int_kcal_mol": (-200.0, -1.0),
    "e_int_uncorrected_kcal_mol": (-250.0, 0.0),
    "e_coulomb_full_charge_kcal_mol": None,
    "e_coulomb_ecc_kcal_mol": None,
    "wall_time_s": (0.0, 1e6),
}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check_calc(folder: Path, references_file: str, ion: str = "", research: bool = False) -> list[str]:
    """small-calc's numbers and files. With `research`, the cited explanation is the manuscript (checked by
    check_research), so explanation.md is not required."""
    problems: list[str] = []
    rp = folder / "result.json"
    try:
        res = json.loads(rp.read_text())
    except OSError:
        return ["result.json is missing (write calc/result.json)"]
    except ValueError as e:
        return [f"result.json is not valid JSON: {e}"]
    if not isinstance(res, dict):
        return ["result.json must be a JSON object"]
    for k, rng in CALC_NUMBERS.items():
        v = res.get(k)
        if not _num(v):
            problems.append(f"result.json: '{k}' must be a finite number, got {v!r}")
        elif rng and not rng[0] <= v <= rng[1]:
            problems.append(f"result.json: '{k}' = {v} is outside the plausible range {rng}; check units and sign "
                            "(kcal/mol, Å; bound = negative)")
    for k in ("ion", "software", "method"):
        if not isinstance(res.get(k), str) or not res.get(k).strip():
            problems.append(f"result.json: '{k}' must be a non-empty string")
    if ion and isinstance(res.get("ion"), str) and res["ion"].replace(" ", "").lower() != ion.lower():
        problems.append(f"result.json: 'ion' is {res['ion']!r} but the task asked for {ion!r}")
    scan = res.get("scan")
    if not isinstance(scan, list) or len(scan) < 5:
        problems.append("result.json: 'scan' must list at least 5 points {r_angstrom, e_int_kcal_mol}")
    elif not all(isinstance(p, dict) and _num(p.get("r_angstrom")) and _num(p.get("e_int_kcal_mol")) for p in scan):
        problems.append("result.json: every 'scan' point needs numeric 'r_angstrom' and 'e_int_kcal_mol'")
    full, ecc = res.get("e_coulomb_full_charge_kcal_mol"), res.get("e_coulomb_ecc_kcal_mol")
    if _num(full) and _num(ecc) and abs(full) > 1e-6 and not 0.70 <= ecc / full <= 0.80:
        problems.append(f"e_coulomb_ecc_kcal_mol / e_coulomb_full_charge_kcal_mol = {ecc / full:.3f}; with only the "
                        "ion charge scaled by 0.75 it should be 0.75")
    if not (folder / "run_calc.py").is_file():
        problems.append("run_calc.py (the script that did the calculation) is missing")
    log = folder / "output.log"
    if not log.is_file() or not log.read_text(errors="replace").strip():
        problems.append("output.log (the calculation's printed output) is missing or empty")
    ex = folder / "explanation.md"
    if research:
        pass
    elif not ex.is_file():
        problems.append("explanation.md is missing")
    else:
        text = ex.read_text()
        if len(text.split()) < 120:
            problems.append(f"explanation.md has {len(text.split())} words; write at least 120")
        refs = _load_list(references_file)
        valid = _valid_numbers(refs)
        used = cited_numbers(text)
        if valid and not used:
            problems.append("explanation.md cites no group paper; cite references.json entries as [n]")
        bad = sorted(n for n in used if valid and n not in valid)
        if bad:
            problems.append(f"explanation.md cites {bad}, which don't exist; valid numbers are {sorted(valid)}")
    return problems


# ── the research protocol (tasks/research.py): manuscript, notebook, edit stream ─────────────────────────────

MANUSCRIPT = "results/manuscript.md"
NOTES = "NOTES_TO_SELF.md"
MS_SECTIONS = [("Abstract", ("abstract",)), ("Methods", ("method",)), ("Results", ("result",)),
               ("Discussion", ("discussion", "conclusion")), ("References", ("reference",))]
DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$", re.S)
INLINE_MATH_RE = re.compile(r"(?<![\\$])\$(?![\s$])([^$\n]+?)(?<![\s\\])\$(?!\$)")
IMG_RE = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
FIG_MENTION_RE = re.compile(r"\bFig(?:ure|s?\.)?\s*~?(\d+)", re.I)
PLACEHOLDER_RE = re.compile(r"\bTBD\b|\bTODO\b|\bFIXME\b|\bXXX\b|\?\?\?|lorem ipsum|\[citation needed\]", re.I)
NUM_RE = re.compile(r"(?<![\w.])[-−–]?\d+(?:\.\d+)?(?![\w.]*\d)")
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s)\]>,;]+")
MS_MIN_WORDS = 300
ABSTRACT_WORDS = (50, 260)
FIG_MIN = (200, 120)  # a real plot, not a 1×1 placeholder
WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}
# shell commands that write a file (not ones that merely read it, like `wc -w results/manuscript.md 2>&1`)
SHELL_WRITE_RE = r"(?:>>?\s*['\"]?\S*{f}|\btee\b[^|;&]*{f}|\bsed\s+-i[^|;&]*{f}|\bperl\s+-[a-z]*i[^|;&]*{f}|" \
                 r"\b(?:cp|mv|install|rsync)\b[^|;&]*\s\S*{f}\s*(?:$|[|;&])|open\([^)]*{f}[^)]*['\"][wa]|" \
                 r"{f}['\"]?\s*\)\.write_text|write_text\([^)]*{f})"


def _sections(lines: list[str]) -> list[tuple[str, int]]:
    """(lowercased ## heading, line index), in order."""
    return [(ln.lstrip("#").strip().lower(), i) for i, ln in enumerate(lines) if re.match(r"^\s*##\s+\S", ln)]


def _section_text(lines: list[str], words: tuple) -> Optional[str]:
    heads = _sections(lines)
    for j, (h, i) in enumerate(heads):
        if any(w in h for w in words):
            end = heads[j + 1][1] if j + 1 < len(heads) else len(lines)
            return "\n".join(lines[i + 1:end])
    return None


def _strip_math_and_code(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = DISPLAY_MATH_RE.sub(" ", text)
    return INLINE_MATH_RE.sub(" ", text)


def _braces_balanced(expr: str) -> bool:
    depth = 0
    for i, ch in enumerate(expr):
        if ch in "{}" and i and expr[i - 1] == "\\":
            continue
        depth += {"{": 1, "}": -1}.get(ch, 0)
        if depth < 0:
            return False
    return depth == 0


def png_size(path: Path) -> Optional[tuple[int, int]]:
    """(width, height) of a PNG file, None if it isn't one."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def _states(text: str, value: float) -> bool:
    """Does `text` state `value`, rounded to the precision written (sign optional: "binds by 25.5 kcal/mol")?"""
    for m in NUM_RE.finditer(text.replace("\u2212", "-").replace("\u2013", "-")):
        s = m.group(0).replace("−", "-").replace("–", "-")
        try:
            x = float(s)
        except ValueError:
            continue
        d = len(s.split(".")[1]) if "." in s else 0
        tol = 0.5 * 10 ** -d + 1e-9
        if abs(x - value) <= tol or abs(abs(x) - abs(value)) <= tol:
            if d > 0 or abs(value) >= 10:  # "−25" for −25.46 is fine; "2" for 2.2 Å is not
                return True
    return False


def check_manuscript(folder: Path, references_file: str = "", result_file: str = "",
                     numbers: tuple = ()) -> tuple[list[str], list[str], dict]:
    """(problems, notes, stats) for folder/results/manuscript.md."""
    problems: list[str] = []
    notes: list[str] = []
    ms = folder / MANUSCRIPT
    if not ms.is_file() or not ms.read_text(errors="replace").strip():
        return [f"{MANUSCRIPT} is missing or empty: draft it early with the Write tool and revise it with Edit"], [], {}
    text = ms.read_text(errors="replace")
    lines = text.splitlines()
    stats: dict = {"words": len(_strip_math_and_code(text).split())}
    title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), "")
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    if not title or not first.startswith("# "):
        problems.append("the manuscript must start with its title as '# <title>'")
    stats["title"] = title
    heads = [h for h, _ in _sections(lines)]
    for name, words in MS_SECTIONS:
        if not any(any(w in h for w in words) for h in heads):
            problems.append(f"missing section '## {name}'")
    if stats["words"] < MS_MIN_WORDS:
        problems.append(f"the manuscript has {stats['words']} words of prose; a real one needs at least {MS_MIN_WORDS}")
    abstract = _section_text(lines, ("abstract",))
    if abstract is not None:
        n = len(_strip_math_and_code(abstract).split())
        if not ABSTRACT_WORDS[0] <= n <= ABSTRACT_WORDS[1]:
            problems.append(f"the abstract has {n} words; write {ABSTRACT_WORDS[0]}–{ABSTRACT_WORDS[1]} "
                            "(aim for 80–200)")
    left = sorted({m.group(0) for m in PLACEHOLDER_RE.finditer(text)})
    if left:
        problems.append(f"placeholders left in the final manuscript ({', '.join(left)}): replace them with the "
                        "measured values or delete the claim")
    # math
    if text.count("$$") % 2:
        problems.append("unbalanced $$: every display equation needs an opening and a closing $$")
    display = [m.group(1) for m in DISPLAY_MATH_RE.finditer(text)]
    inline = [m.group(1) for m in INLINE_MATH_RE.finditer(DISPLAY_MATH_RE.sub(" ", text))]
    stats["math"] = {"display": len(display), "inline": len(inline)}
    if not display:
        problems.append("no display equation: put at least one key equation on its own lines as $$ ... $$")
    if not inline:
        problems.append("no inline math: write symbols and quantities in the text as $...$ (e.g. $E_\\mathrm{int}$)")
    bad = [e.strip()[:60] for e in display + inline if not _braces_balanced(e)]
    if bad:
        problems.append(f"unbalanced braces in math (KaTeX will not render it): {bad[:3]}")
    if re.search(r"\\begin\{(align|eqnarray|equation)\*?\}", text):
        problems.append("KaTeX can't render \\begin{align}/\\begin{equation} here: use $$ \\begin{aligned} … "
                        "\\end{aligned} $$")
    # figures
    figs = []
    for m in IMG_RE.finditer(text):
        path = m.group(2).split("#")[0]
        cands = [ms.parent / path, folder / path] if not path.startswith(("http:", "https:", "/")) else []
        hit = next((c for c in cands if c.is_file()), None)
        figs.append((m.group(1), path, hit))
    stats["figures"] = [p for _, p, _ in figs]
    if not figs:
        problems.append("no figure: save a matplotlib plot as results/fig1.png and embed it as "
                        "![Figure 1. <caption>](fig1.png)")
    for cap, path, hit in figs:
        if hit is None:
            problems.append(f"figure {path!r} does not exist (embed it relative to the manuscript, e.g. "
                            "![Figure 1. …](fig1.png) for results/fig1.png)")
            continue
        size = png_size(hit)
        if hit.suffix.lower() == ".png" and (size is None or size[0] < FIG_MIN[0] or size[1] < FIG_MIN[1]):
            problems.append(f"{path} is not a real plot (needs a PNG of at least {FIG_MIN[0]}×{FIG_MIN[1]} px; "
                            f"got {size or 'not a PNG'})")
        if not cap.strip():
            notes.append(f"{path} has no caption: write ![Figure n. <caption>]({path})")
    prose = "\n".join(ln for ln in lines if not IMG_RE.search(ln) and not ln.lstrip().startswith("#"))
    mentioned = {int(m.group(1)) for m in FIG_MENTION_RE.finditer(prose)}
    missing = [i for i in range(1, len(figs) + 1) if i not in mentioned]
    if figs and missing:
        problems.append(f"the text never refers to {', '.join(f'Figure {i}' for i in missing)}: discuss each figure "
                        "(\"Figure 1 shows …\")")
    on_disk = sorted(p.name for p in (folder / "results").glob("fig*.png")) if (folder / "results").is_dir() else []
    shown = {Path(p).name for _, p, _ in figs}
    if [f for f in on_disk if f not in shown]:
        notes.append(f"figures made but not in the manuscript: {[f for f in on_disk if f not in shown]}")
    # citations
    refs = _load_list(references_file)
    valid = _valid_numbers(refs)
    body = re.split(r"(?im)^\s*##\s+references\b", text, maxsplit=1)
    ref_part = body[1] if len(body) > 1 else ""
    used = cited_numbers(_strip_math_and_code(body[0]))
    stats["citations"] = sorted(used)
    need = 2 if len(valid) >= 2 else 1
    if len(used) < need:
        problems.append(f"the text cites {len(used)} source(s); cite the group's papers as [n] for every statement "
                        f"about prior work (at least {need} different ones)")
    bad = sorted(n for n in used if valid and n not in valid)
    if bad:
        problems.append(f"citations {bad} don't exist; valid numbers are {sorted(valid)}")
    listed = cited_numbers(ref_part)
    missing = sorted(n for n in used if n not in listed)
    if ref_part and missing:
        problems.append(f"## References does not list {', '.join(f'[{n}]' for n in missing)}")
    by_n = {int(s["n"]): s for s in refs if isinstance(s, dict) and str(s.get("n", "")).isdigit()}
    for ln in ref_part.splitlines():
        m = re.match(r"^\s*(?:[-*]\s*)?\[(\d+)\]", ln)
        if not m or int(m.group(1)) not in by_n:
            continue
        want = str((by_n[int(m.group(1))].get("citation") or {}).get("doi") or "").lower()
        got = [d.rstrip(".").lower() for d in DOI_RE.findall(ln)]
        if want and got and want not in got:
            problems.append(f"References: [{m.group(1)}] has DOI {got[0]}, but source [{m.group(1)}] is {want}")
    # the numbers the abstract must state
    res: dict = {}
    if result_file:
        try:
            res = json.loads((folder / result_file).read_text())
        except (OSError, ValueError):
            res = {}
    for key in numbers:
        v = res.get(key) if isinstance(res, dict) else None
        if _num(v) and abstract is not None and not _states(abstract, float(v)):
            problems.append(f"the abstract doesn't state {key} = {v} from {result_file} (revise it to the final "
                            "number; delete superseded values)")
    return problems, notes, stats


def check_notes(folder: Path) -> tuple[list[str], list[str]]:
    """NOTES_TO_SELF.md: the three sections, each with at least one entry."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from tasks.research import NOTE_SECTIONS, REQUIRED_NOTE_SECTIONS, parse_notes
    except ImportError:  # checks.py copied somewhere on its own
        return ([] if (folder / NOTES).is_file() else [f"{NOTES} is missing"]), []
    p = folder / NOTES
    if not p.is_file() or not p.read_text(errors="replace").strip():
        return [f"{NOTES} is missing: keep your lab notebook there (what you tried, what was slow, what to do "
                "differently next time), created early with Write and updated with Edit"], []
    text = p.read_text(errors="replace")
    parsed = parse_notes(text)
    problems = [f"{NOTES} needs a section '{NOTE_SECTIONS[k][0]}' with at least one entry"
                for k in REQUIRED_NOTE_SECTIONS if not parsed[k]]
    if len(text.split()) < 30:
        problems.append(f"{NOTES} has {len(text.split())} words; give the next run concrete, specific advice")
    return problems, []


def find_trajectory(folder: Path) -> Optional[Path]:
    """The Harbor trial's transcript for a returned folder <trial>/artifacts/app/<name>: agent/trajectory.json,
    else agent/claude-code.txt (stream-json)."""
    try:
        f = folder.resolve()
    except OSError:
        return None
    if len(f.parents) < 3 or f.parent.name != "app" or f.parent.parent.name != "artifacts":
        return None
    agent = f.parents[2] / "agent"
    for name in ("trajectory.json", "claude-code.txt"):
        if (agent / name).is_file():
            return agent / name
    return None


def tool_calls(path: Path) -> list[tuple[str, dict]]:
    """(tool name, arguments) in order, from an ATIF trajectory.json or a stream-json claude-code.txt."""
    out: list[tuple[str, dict]] = []
    try:
        raw = path.read_text(errors="replace")
    except OSError:
        return out
    if path.suffix == ".json":
        try:
            data = json.loads(raw)
        except ValueError:
            return out
        for st in data.get("steps") or [] if isinstance(data, dict) else []:
            for tc in st.get("tool_calls") or []:
                args = tc.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {"raw": args}
                out.append((str(tc.get("function_name") or ""), args if isinstance(args, dict) else {}))
        return out
    for ln in raw.splitlines():
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(ev, dict) or ev.get("type") != "assistant":
            continue
        for c in (ev.get("message") or {}).get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_use":
                out.append((str(c.get("name") or ""), c.get("input") if isinstance(c.get("input"), dict) else {}))
    return out


def edit_stream(calls: list[tuple[str, dict]], suffix: str) -> dict:
    """How a file was written: Write/Edit ops on it, shell commands that wrote it, and whether other work
    happened between its first and last edit (= it was revised as results arrived)."""
    pat = re.compile(SHELL_WRITE_RE.format(f=re.escape(suffix.split("/")[-1])))
    ops, shell, idx = [], [], []
    for i, (name, args) in enumerate(calls):
        if name in WRITE_TOOLS and str(args.get("file_path") or "").endswith(suffix):
            ops.append(name)
            idx.append(i)
        elif name == "Bash" and pat.search(str(args.get("command") or "")):
            shell.append(str(args.get("command"))[:120])
    between = bool(idx) and any(calls[i][0] not in WRITE_TOOLS and calls[i][0] != "Read"
                                for i in range(idx[0] + 1, idx[-1]))
    return {"writes": ops.count("Write"), "edits": len(ops) - ops.count("Write"), "ops": len(ops),
            "shell_writes": shell, "work_between": between}


def check_edit_stream(folder: Path, trajectory: str = "") -> tuple[list[str], list[str], dict]:
    path = Path(trajectory) if trajectory else find_trajectory(folder)
    if not path or not path.is_file():
        return [], ["edit stream not checked (no Harbor trajectory next to this folder)"], {}
    calls = tool_calls(path)
    if not calls:
        return [], [f"edit stream not checked ({path.name} has no tool calls)"], {}
    ms, nt = edit_stream(calls, MANUSCRIPT), edit_stream(calls, NOTES)
    problems, notes = [], []
    if ms["shell_writes"]:
        problems.append(f"the manuscript was written from the shell ({ms['shell_writes'][0]!r}); write and revise "
                        "it only with the Write and Edit tools so the page can replay every edit")
    if ms["ops"] == 0 and (folder / MANUSCRIPT).is_file():
        problems.append("the manuscript never went through the Write/Edit tools")
    elif ms["ops"] == 1:
        problems.append("the manuscript was written once and never revised: draft it early with Write, then revise "
                        "it with Edit as results arrive (replace TBD values, rewrite, delete wrong claims)")
    elif ms["ops"] > 1 and not ms["work_between"]:
        notes.append("all manuscript edits came back to back; draft before the calculation and revise after it")
    if nt["shell_writes"] or (nt["ops"] == 0 and (folder / NOTES).is_file()):
        notes.append(f"{NOTES} should be written with Write/Edit too, so its edits show on the page")
    return problems, notes, {"manuscript": ms, "notes": nt, "transcript": path.name}


def check_research(folder: Path, references_file: str = "", result_file: str = "result.json",
                   numbers: tuple = (), trajectory: str = "") -> tuple[list[str], list[str], dict]:
    """The whole research protocol: manuscript + notebook + edit stream."""
    p1, n1, s1 = check_manuscript(folder, references_file, result_file, numbers)
    p2, n2 = check_notes(folder)
    p3, n3, s3 = check_edit_stream(folder, trajectory)
    return p1 + p2 + p3, n1 + n2 + n3, {"manuscript": s1, "edit_stream": s3}


# ── capability specs (tasks/capabilities/<name>/): what the TEST stage verifies before INSTALL ──────────────

PIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?==[A-Za-z0-9][A-Za-z0-9.+!_-]*"
                    r"(\s*;\s*[^#]+)?(\s+--hash=sha256:[0-9a-f]{64})*\s*$")
SECRET_RE = re.compile(r"(?i)^\s*(ENV|ARG)\s+\S*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_image_capability(d: Path) -> list[str]:
    """An image capability: Dockerfile + requirements.lock (every package pinned) + smoke.py run at build time
    + manifest.json whose hashes match the files and which asks for no extra authority."""
    problems: list[str] = []
    files = {n: d / n for n in ("Dockerfile", "requirements.lock", "smoke.py", "manifest.json")}
    for n, p in files.items():
        if not p.is_file():
            problems.append(f"{n} is missing")
    if problems:
        return problems
    lock = [ln.strip() for ln in files["requirements.lock"].read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    if not lock:
        problems.append("requirements.lock pins nothing")
    for ln in lock:
        if not PIN_RE.match(ln):
            problems.append(f"requirements.lock: {ln!r} is not pinned as name==version (no URLs, -e, or index options)")
    df = re.sub(r"\\\s*\n\s*", " ", files["Dockerfile"].read_text())  # join `\`-continued lines
    froms = re.findall(r"(?im)^\s*FROM\s+(\S+)", df)
    if not froms or not re.fullmatch(r"\$\{?BASE_IMAGE\}?", froms[0]):
        problems.append("Dockerfile must build FROM ${BASE_IMAGE} (the macrae agent image), so the live-trace "
                        "wrapper and Claude Code stay in the image")
    if not re.search(r"(?m)^\s*RUN\b.*pip[^\n]*install[^\n]*-r\s+\S*requirements\.lock", df):
        problems.append("Dockerfile must install exactly requirements.lock (pip install -r requirements.lock)")
    if not re.search(r"(?m)^\s*RUN\b.*smoke\.py", df):
        problems.append("Dockerfile must run smoke.py at build time (an image that fails its smoke test never ships)")
    if re.search(r"(?i)(curl|wget)[^\n|]*\|\s*(ba|z)?sh", df):
        problems.append("Dockerfile pipes a download into a shell; install only pinned packages")
    if any(SECRET_RE.match(ln) for ln in df.splitlines()):
        problems.append("Dockerfile declares a secret-like ENV/ARG; capabilities never carry credentials")
    try:
        man = json.loads(files["manifest.json"].read_text())
    except ValueError as e:
        return problems + [f"manifest.json is not valid JSON: {e}"]
    for k in ("name", "kind", "version", "purpose", "provides", "smoke", "files"):
        if not man.get(k):
            problems.append(f"manifest.json: '{k}' is required")
    if man.get("kind") not in (None, "image"):
        problems.append(f"manifest.json: kind is {man.get('kind')!r}, expected 'image'")
    for name, digest in (man.get("files") or {}).items():
        p = d / name
        if not p.is_file():
            problems.append(f"manifest.json lists {name}, which doesn't exist")
        elif sha256_file(p) != digest:
            problems.append(f"manifest.json: sha256 of {name} doesn't match the file (re-hash after editing)")
    asks = man.get("requires") or {}
    for k in ("secrets", "network", "registry_write"):
        if asks.get(k):
            problems.append(f"manifest.json asks for {k}={asks[k]!r}; capabilities can't widen the agent's authority")
    return problems


def _report(problems: list[str], notes: list[str] = (), stats: Optional[dict] = None) -> int:
    if problems:
        print("FAILED:\n" + "\n".join(f"- {p}" for p in problems))
    else:
        print("ok")
    if notes:
        print("notes:\n" + "\n".join(f"- {n}" for n in notes))
    if stats:
        es = (stats.get("edit_stream") or {}).get("manuscript")
        ms = stats.get("manuscript") or {}
        if ms.get("words"):
            print(f"manuscript: {ms['words']} words, {ms['math']['display']} display + {ms['math']['inline']} inline "
                  f"equations, {len(ms['figures'])} figure(s), citations {ms['citations']}")
        if es:
            print(f"edit stream: {es['writes']} Write + {es['edits']} Edit on the manuscript")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="check task outputs")
    sub = ap.add_subparsers(dest="what", required=True)
    c = sub.add_parser("card")
    c.add_argument("file")
    c.add_argument("--sources", default="")
    c.add_argument("--notes", action="store_true", help=f"also require {NOTES} next to the card")
    k = sub.add_parser("calc")
    k.add_argument("folder", nargs="?", default=".")
    k.add_argument("--references", default="")
    k.add_argument("--ion", default="")
    k.add_argument("--research", action="store_true", help="also check the manuscript, notebook and edit stream")
    k.add_argument("--numbers", default="e_int_kcal_mol,r_min_angstrom",
                   help="result.json keys the abstract must state (with --research)")
    k.add_argument("--trajectory", default="")
    r = sub.add_parser("research")
    r.add_argument("folder", nargs="?", default=".")
    r.add_argument("--references", default="")
    r.add_argument("--result", default="result.json")
    r.add_argument("--numbers", default="", help="comma-separated result.json keys the abstract must state")
    r.add_argument("--trajectory", default="")
    n = sub.add_parser("notes")
    n.add_argument("folder", nargs="?", default=".")
    i = sub.add_parser("image-capability")
    i.add_argument("folder")
    a = ap.parse_args(argv)
    if a.what == "card":
        problems = check_card(Path(a.file), a.sources)
        if a.notes:
            problems += check_notes(Path(a.file).resolve().parent)[0]
        return _report(problems)
    if a.what == "notes":
        return _report(*check_notes(Path(a.folder)))
    if a.what == "image-capability":
        return _report(check_image_capability(Path(a.folder)))
    if a.what == "research":
        nums = tuple(x.strip() for x in a.numbers.split(",") if x.strip())
        return _report(*check_research(Path(a.folder), a.references, a.result, nums, a.trajectory))
    problems = check_calc(Path(a.folder), a.references, a.ion, research=a.research)
    if not a.research:
        return _report(problems)
    nums = tuple(x.strip() for x in a.numbers.split(",") if x.strip())
    more, notes, stats = check_research(Path(a.folder), a.references, "result.json", nums, a.trajectory)
    return _report(problems + more, notes, stats)


if __name__ == "__main__":
    sys.exit(main())
