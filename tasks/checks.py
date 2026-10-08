#!/usr/bin/env python3
"""Checks for task outputs. Run by agent_runner `check:` inside the folder an agent returned; exit 0 = reward 1.
Whatever is printed goes back to the agent as feedback on a retry, so messages say exactly what to fix.

    python3 tasks/checks.py card methods-card.md --sources /path/sources.json
    python3 tasks/checks.py calc . --references /path/references.json --ion Na+

Standard library only: it runs with whatever python3 the backend has.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

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


def check_calc(folder: Path, references_file: str, ion: str = "") -> list[str]:
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
    if not ex.is_file():
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="check task outputs")
    sub = ap.add_subparsers(dest="what", required=True)
    c = sub.add_parser("card")
    c.add_argument("file")
    c.add_argument("--sources", default="")
    k = sub.add_parser("calc")
    k.add_argument("folder", nargs="?", default=".")
    k.add_argument("--references", default="")
    k.add_argument("--ion", default="")
    a = ap.parse_args(argv)
    if a.what == "card":
        problems = check_card(Path(a.file), a.sources)
    else:
        problems = check_calc(Path(a.folder), a.references, a.ion)
    if problems:
        print("FAILED:\n" + "\n".join(f"- {p}" for p in problems))
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
