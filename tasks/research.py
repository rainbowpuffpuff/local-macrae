"""How a macrae research task's agent works: like a scientist in the open, with a lab notebook and a manuscript.

Every research flow (small-calc and the BFF tasks) gives its agent the same protocol:

1. NOTES_TO_SELF.md, its lab notebook, kept with the Write/Edit tools during the run: what it tried, what was
   slow (with seconds), what to do differently next time, and the capabilities it was missing. evolve ingests
   it after the run (`notes_to_lessons`), so the next run of the task starts from these notes.
2. results/manuscript.md, a real manuscript (title, abstract, methods, results, discussion, references) with
   LaTeX math, the figures it generated (results/fig*.png) and numbered [n] citations of the group's papers.
   It is drafted early, with **TBD** where numbers are still missing, and revised with Edit as results arrive.
   The page replays that edit stream, so the manuscript is only ever written through the Write and Edit tools.
3. No sleep-waiting: long commands run in the background and get polled, and the waiting time goes into
   the manuscript.
4. Missing capabilities are named in one structured line, `CAPABILITY_GAP: <name>: <why>`, which the backend
   records as a `gap` event (CONTRACT v3).

`tasks/checks.py research` verifies the outputs and the edit stream. Flows wire the protocol in with three edits:
the prep step calls `prepare_workspace` (or a flow adds the generic step below), the agent instruction includes
`{{ <step>.output.protocol }}`, and the check runs `checks.py research` (small-calc: `checks.py calc --research`).

    python -m tasks.research prepare --dir RUN/x/work --references refs.json --task-id bff-x \\
        [--python /opt/bff/bin/python --packages bff]
    python -m tasks.research notes NOTES_TO_SELF.md --task-id small-calc       # → candidate lessons (JSON)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

MANUSCRIPT = "results/manuscript.md"
NOTES = "NOTES_TO_SELF.md"
PROTOCOL_FILE = "PROTOCOL.md"
CHECKER = Path(__file__).resolve().parent / "checks.py"

# NOTES_TO_SELF.md sections, in order. The checker and the lesson parser match headings by these words.
NOTE_SECTIONS = {
    "tried": ("## What I tried", ("tried",)),
    "slow": ("## What was slow", ("slow",)),
    "next_time": ("## What to do differently next time", ("differently", "next time")),
    "gaps": ("## Capability gaps", ("capabilit", "gap")),
}
REQUIRED_NOTE_SECTIONS = ("tried", "slow", "next_time")

GAP_RE = re.compile(r"CAPABILITY_GAP:\s*([A-Za-z0-9][\w.-]{1,60})\s*:\s*([^\"\n\\\\]+)")


def _ws(workspace: str) -> str:
    return workspace.rstrip("/") or "/app"


def protocol(workspace: str, *, python: str = "", packages: str = "", figure_hint: str = "",
             numbers_hint: str = "") -> str:
    """The protocol block for an agent instruction. `workspace` is the agent's folder in the sandbox (/app/<dir>);
    `python`/`packages`: the interpreter and imports the calculation needs (to check before installing);
    `figure_hint`: what Figure 1 should show; `numbers_hint`: which result.json numbers the abstract must state."""
    ws = _ws(workspace)
    env = ""
    if python:
        env = (f"Before installing anything, check what is already there: `{python} -c \"import {packages or 'sys'}\"`."
               " If that works, the environment is preinstalled: use it and install nothing. If not, install what is"
               " missing, time it, and print one line `CAPABILITY_GAP: calc-image: <what you installed, seconds>`"
               " (if `python3 -m venv` fails with \"ensurepip is not available\", run `apt-get update -qq && apt-get"
               " install -y -qq python3-venv` first). ")
    fig = f" Figure 1 should show {figure_hint}." if figure_hint else ""
    nums = f" The abstract states {numbers_hint}, exactly as in result.json." if numbers_hint else ""
    return (
        f"WORK LIKE A RESEARCHER IN THE OPEN (the page replays every file edit you make; {ws}/{PROTOCOL_FILE} has the "
        "details and the reference list). "
        f"(a) Lab notebook: within your first few actions, create {ws}/{NOTES} with the Write tool, with the sections "
        "\"## What I tried\", \"## What was slow\", \"## What to do differently next time\" and "
        "\"## Capability gaps\"; "
        "keep it current with Edit: every failed command and what fixed it, every step over 30 s with its seconds "
        "(installs, builds, waits), concrete advice for the next run of this task. The next run reads it. "
        f"(b) Manuscript: write {ws}/{MANUSCRIPT} ONLY with the Write and Edit tools (never with shell redirection, "
        "tee, sed, cat <<EOF or Python, which would hide the edit from the page). Draft it early, before the main "
        "calculation finishes: title, abstract, methods with the equations, the planned figures, references; write "
        "**TBD** for every number you don't have yet. As results arrive, revise it with Edit: replace each **TBD**, "
        "rewrite sentences the data contradicts and delete claims you can't support. Final structure: \"# <title>\", "
        "\"## Abstract\" (80–200 words, the key numbers), \"## Methods\", \"## Results\", \"## Discussion\" (with "
        "limitations), \"## References\". Use LaTeX math: inline $...$ and at least one display equation $$...$$ "
        "(KaTeX: no \\begin{align}, no custom macros). Make figures with matplotlib (Agg backend, dpi 150, axis "
        "labels with units), save them as results/fig1.png, results/fig2.png, …, embed each as "
        "![Figure 1. <caption>](fig1.png) and refer to it in the text (\"Figure 1 shows …\")." + fig + " "
        "Cite the group's papers as [n] for every statement about prior work, using only the numbers in "
        f"{PROTOCOL_FILE}; list each cited [n] under \"## References\" in the form given there. Numbers from this "
        "run need no citation." + nums + " Final pass: no **TBD** left, every number agrees with result.json. "
        "(c) Time: never sleep longer than 15 s. Start anything that takes over a minute in the background (Bash "
        "run_in_background, or `nohup … > log 2>&1 &`), poll its log every 10–15 s, and spend the waiting time on "
        "the manuscript. " + env
    ).strip()


def reference_line(src: dict) -> str:
    """How a numbered source is listed under ## References: [n] Authors (year). Title. Journal. https://doi.org/…"""
    c = src.get("citation") or {}
    authors = "; ".join(a.strip() for a in str(c.get("authors") or "").split(";")[:3] if a.strip())
    if str(c.get("authors") or "").count(";") >= 3:
        authors += " et al."
    bits = [f"{src.get('key') or '[' + str(src.get('n')) + ']'} {authors or 'Anonymous'} ({c.get('year') or 'n.d.'})."]
    bits += [f"{str(c[k]).rstrip('.')}." for k in ("title", "journal") if c.get(k)]
    if c.get("doi"):
        bits.append(f"https://doi.org/{c['doi']}")
    return " ".join(bits)


def protocol_markdown(references: list[dict], *, task_id: str = "", title_hint: str = "") -> str:
    """The workspace's PROTOCOL.md: the manuscript and notebook rules plus the reference list to cite from."""
    refs = "\n".join(reference_line(r) for r in references) or "(no group papers were found for this task)"
    hint = f"\nSuggested working title: *{title_hint}*.\n" if title_hint else ""
    return f"""# Research protocol{f' ({task_id})' if task_id else ''}

You are a research agent for Pavel Jungwirth's group (IOCB Prague). Work the way a careful scientist does, in
the open: the page replays every Write/Edit you make to `{MANUSCRIPT}` and `{NOTES}`.
{hint}
## Lab notebook: `{NOTES}`
Create it early with the Write tool; keep it current with Edit. Sections:

```markdown
# Notes to self: <task>
## What I tried
- <command or approach>: <what happened> (<seconds>)
## What was slow
- <step>: <seconds> s (<why>)
## What to do differently next time
- <one concrete, checkable instruction per line>
## Capability gaps
- <name>: <what was missing, what it cost>   (also print `CAPABILITY_GAP: <name>: <why>` once)
```
Good advice is specific: "PySCF is preinstalled in /opt/calc: skip pip (saved 60 s)" beats "be faster".

## Manuscript: `{MANUSCRIPT}`
1. **Draft early**, before the main calculation finishes: title, abstract, methods (with the equations you will
   use), the planned figures, references. Mark every number you don't have yet as **TBD**.
2. **Revise as results arrive**, with Edit: replace each **TBD** with the measured value, rewrite any sentence the
   data contradicts, delete claims you cannot support. Small, targeted edits; the reader watches them happen.
3. **Structure**: `# Title`, `## Abstract` (80–200 words with the key numbers), `## Methods`, `## Results`,
   `## Discussion` (including limitations), `## References`. An `## Introduction` is welcome.
4. **Math** in LaTeX for KaTeX: inline `$E_\\mathrm{{int}}$`, display
   `$$\\Delta E = E_{{AB}} - E_A - E_B$$` on their own lines. No `\\begin{{align}}` (use `\\begin{{aligned}}` inside
   `$$`), no custom macros.
5. **Figures**: matplotlib with the Agg backend, dpi 150, labelled axes with units, a legend when there are
   several series. Save as `results/fig1.png`, `results/fig2.png`, …; embed each as
   `![Figure 1. <one-sentence caption>](fig1.png)` (path relative to the manuscript) and discuss it in the text
   ("Figure 1 shows …").
6. **Citations**: `[n]` for every statement about prior work or the group's results, only with the numbers
   below; several as `[1, 3]`. Numbers computed in this run need no citation. Under `## References`, list each
   number you cited exactly as below. Sources marked "title only" can support only what their title says.
7. **Final pass**: no **TBD** left; the abstract's numbers equal `result.json`; every figure is referenced.
8. Only the Write and Edit tools touch the manuscript: never shell redirection, `tee`, `sed -i`, heredocs or
   Python file writes.

## Time
Never sleep longer than 15 s. Run anything over a minute in the background (Bash `run_in_background`, or
`nohup … > run.log 2>&1 &`) and poll the log every 10–15 s; draft or revise the manuscript while you wait.

## References you may cite
{refs}
"""


def prepare_workspace(folder: Path, references: list[dict], *, task_id: str = "", title_hint: str = "") -> Path:
    """Write PROTOCOL.md into the agent's folder and create results/. Returns the PROTOCOL.md path."""
    folder = Path(folder)
    (folder / "results").mkdir(parents=True, exist_ok=True)
    p = folder / PROTOCOL_FILE
    p.write_text(protocol_markdown(references, task_id=task_id, title_hint=title_hint))
    return p


# ── NOTES_TO_SELF.md → lessons (what evolve ingests after the run) ─────────────────────────────────────────


def note_section(heading: str) -> Optional[str]:
    h = heading.lstrip("#").strip().lower()
    for key, (_, words) in NOTE_SECTIONS.items():
        if any(w in h for w in words):
            return key
    return None


PLACEHOLDER_NOTE_RE = re.compile(r"^\(.*\)$|^(tbd|todo|\.\.\.|…)$", re.I)


def parse_notes(text: str) -> dict[str, list[str]]:
    """{"tried", "slow", "next_time", "gaps": [bullet text, ...]} from a NOTES_TO_SELF.md. Unknown sections are
    ignored; a bullet's wrapped continuation lines are joined to it; placeholders ("(nothing yet)", "TBD") are
    dropped, so a notebook still in its draft state has empty sections."""
    out: dict[str, list[str]] = {k: [] for k in NOTE_SECTIONS}
    cur: Optional[str] = None
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("#"):
            cur = note_section(line) if line.lstrip().startswith("##") else None
            continue
        if cur is None or not line.strip():
            continue
        m = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$", line)
        if m:
            out[cur].append(m.group(1).strip())
        elif out[cur] and raw.startswith((" ", "\t")):
            out[cur][-1] += " " + line.strip()
        else:
            out[cur].append(line.strip())
    return {k: [b for b in v if not PLACEHOLDER_NOTE_RE.match(b.strip())] for k, v in out.items()}


def gaps(text: str) -> list[dict]:
    """CAPABILITY_GAP lines (from notes, a log or the agent's output) plus the notes' "Capability gaps" bullets:
    [{"name", "why"}], de-duplicated by name (first one wins)."""
    found: dict[str, str] = {}
    for m in GAP_RE.finditer(text or ""):
        found.setdefault(m.group(1).lower(), m.group(2).strip().rstrip("`").strip())
    for b in parse_notes(text).get("gaps", []):
        m = re.match(r"^`?([A-Za-z][\w.-]{1,60})`?\s*:\s*(.+)$", b)
        if m and m.group(1).lower() not in ("none", "n/a", "capability_gap"):
            found.setdefault(m.group(1).lower(), m.group(2).strip())
    return [{"name": k, "why": v} for k, v in found.items()]


AVOID_RE = re.compile(r"^(don'?t|do not|never|avoid|stop)\b", re.I)


def notes_to_lessons(text: str, task_id: str = "*") -> list[dict]:
    """Candidate lessons in evolve's shape {task_id, lesson, kind, source}: "next time" bullets → do/avoid,
    "slow" bullets → setting (they carry timings), capability gaps → tool. Evidence ids are the caller's."""
    notes = parse_notes(text)
    out: list[dict] = []
    for b in notes["next_time"]:
        if len(b) >= 12:
            out.append({"task_id": task_id, "lesson": b, "kind": "avoid" if AVOID_RE.search(b) else "do",
                        "source": "notes"})
    for b in notes["slow"]:
        if len(b) >= 12 and re.search(r"\d", b):
            out.append({"task_id": task_id, "lesson": f"Slow last time: {b}", "kind": "setting", "source": "notes"})
    for g in gaps(text):
        out.append({"task_id": task_id, "lesson": f"Missing capability {g['name']}: {g['why']}", "kind": "tool",
                    "source": "notes", "capability": g["name"]})
    return out


# ── CLI ─────────────────────────────────────────────────────────────────────


def _load_refs(path: str) -> list[dict]:
    if not path:
        return []
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tasks.research", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="write PROTOCOL.md + results/ into an agent folder; print the protocol")
    p.add_argument("--dir", required=True, help="the folder the agent step receives (its name = /app/<name>)")
    p.add_argument("--references", default="", help="numbered sources JSON ([{n, key, citation}])")
    p.add_argument("--task-id", default="")
    p.add_argument("--title", default="", help="suggested working title")
    p.add_argument("--python", default="", help="interpreter to check before installing (e.g. /opt/calc/bin/python)")
    p.add_argument("--packages", default="", help="comma-separated imports for that check")
    p.add_argument("--figure", default="", help="what Figure 1 should show")
    p.add_argument("--numbers", default="", help="which result.json numbers the abstract must state")
    n = sub.add_parser("notes", help="NOTES_TO_SELF.md → candidate lessons + gaps (JSON)")
    n.add_argument("file")
    n.add_argument("--task-id", default="*")
    a = ap.parse_args(argv)
    if a.cmd == "notes":
        try:
            text = Path(a.file).read_text(errors="replace")
        except OSError as e:
            print(f"cannot read {a.file}: {e}", file=sys.stderr)
            return 1
        print(json.dumps({"sections": parse_notes(text), "gaps": gaps(text),
                          "lessons": notes_to_lessons(text, a.task_id)}, ensure_ascii=False, indent=1))
        return 0
    d = Path(a.dir)
    refs = _load_refs(a.references)
    prepare_workspace(d, refs, task_id=a.task_id, title_hint=a.title)
    ws = f"/app/{d.resolve().name}"
    out: dict[str, Any] = {
        "dir": str(d), "protocol_file": str(d / PROTOCOL_FILE), "manuscript": MANUSCRIPT, "notes": NOTES,
        "checker": str(CHECKER), "references_file": a.references,
        "protocol": protocol(ws, python=a.python, packages=a.packages, figure_hint=a.figure,
                             numbers_hint=a.numbers),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
