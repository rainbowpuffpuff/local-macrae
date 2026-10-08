"""small-calc, step 1 (script, runs on the backend): the calculation folder and the group papers it should cite.

    python -m tasks.prepare_calc --ion Na+           (ion also from $TASK_ION)

Writes $FLOW_RUN_DIR/small-calc/calc/{task.json, references.json, context.md, PROTOCOL.md, results/} and prints JSON
for the flow: {"dir", "ion", "charge", "element", "name", "n_references", "references_file", "checker", "protocol",
"manuscript", "citations"}. `protocol` is the research protocol block (tasks/research.py) for the agent instruction.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

from tasks import research as R
from tasks import sources as S

# ions the calculation supports: name, element, charge, words to find related group papers
IONS = {
    "Li+": ("lithium", "Li", 1), "Na+": ("sodium", "Na", 1), "K+": ("potassium", "K", 1),
    "Mg2+": ("magnesium", "Mg", 2), "Ca2+": ("calcium", "Ca", 2),
}
ECC_RE = re.compile(r"charge scaling|electronic polarization|electronic continuum|\becc\b|scaled charge|"
                    r"polarization effects|prosecco", re.I)
WATER_RE = re.compile(r"water|aqueous|hydration|solvation|\bions?\b", re.I)
MAX_REFS = 6
# the calculation environment: a venv the agent builds, or the pinned calc image (tasks/capabilities/calc-image)
# where it is preinstalled. The protocol makes the agent check it before installing anything.
CALC_PYTHON = "/opt/calc/bin/python"
CALC_IMPORTS = "pyscf, numpy, matplotlib"
FIGURE_1 = ("the B3LYP/def2-SVP interaction energy along the scan together with the full-charge and ECC-scaled "
            "point-charge Coulomb curves, and the counterpoise-corrected def2-TZVP value at the minimum")


def score_publication(p: dict, ion_name: str) -> float:
    t = (p.get("title") or "").lower()
    s = 0.0
    s += 3 * bool(ECC_RE.search(t))
    s += 2 * (ion_name in t)
    s += 1 * bool(WATER_RE.search(t))
    s += 0.5 * any(w in t for w in ("force field", "ab initio", "molecular dynamics"))
    return s + (p.get("year") or 0) / 10000.0  # newer first among equals


def references(ion: str) -> list[dict]:
    name, _, _ = IONS[ion]
    items: list[dict] = []
    for q in (f"{name} ion water interaction charge scaling electronic polarization",
              f"{name} hydration ab initio force field"):
        for h in S.rag_search(q, k=6):
            items.append({"text": h["text"], "citation": h["citation"], "origin": "group paper (RAG index)"})
    items = S.dedupe(items)[:MAX_REFS]
    have = {S.norm_doi(i["citation"].get("doi")) for i in items}
    pubs = [p for p in S.load_publications() if p.get("doi") and S.norm_doi(p["doi"]) not in have]
    pubs.sort(key=lambda p: -score_publication(p, name))
    for p in pubs[: max(0, MAX_REFS - len(items))]:
        if score_publication(p, name) < 3:
            break
        items.append({"text": f"{p['title']}. {p.get('authors', '')}. {p.get('citation', '')}.",
                      "citation": S.citation_from_publication(p), "origin": "group publication list (title only)"})
    return S.number_sources(items)


def parse_ion(raw: str) -> str:
    s = re.sub(r"\s+", "", raw or "")
    for k in IONS:
        if s.lower() in (k.lower(), IONS[k][0], IONS[k][1].lower() + ("+" * IONS[k][2]),
                         IONS[k][1].lower() + "+" + str(IONS[k][2])):
            return k
    raise ValueError(f"unsupported ion {raw!r}; choose one of {', '.join(IONS)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ion", default=os.environ.get("TASK_ION", "Na+"))
    ap.add_argument("--out", default="", help="folder (default $FLOW_RUN_DIR/small-calc)")
    a = ap.parse_args(argv)
    try:
        ion = parse_ion(a.ion)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    name, element, charge = IONS[ion]
    base = Path(a.out or Path(os.environ.get("FLOW_RUN_DIR") or ".") / "small-calc")
    d = base / "calc"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    refs = references(ion)
    S.log(f"references for {ion}: {len(refs)}")
    task = {"ion": ion, "name": name, "element": element, "charge": charge, "ecc_scaling": 0.75,
            "water_model_charges": {"model": "SPC/E", "O": -0.8476, "H": 0.4238}}
    S.write_json(d / "task.json", task)
    S.write_json(d / "references.json", refs)
    header = (f"# Group papers to cite in calc/{R.MANUSCRIPT}\n\nCite as [n]; only these numbers exist. Entries "
              "marked 'title only' have no full text here: cite them only for what their title states.")
    (d / "context.md").write_text(S.context_markdown(refs, header))
    S.write_json(base / "references.json", refs)
    R.prepare_workspace(d, refs, task_id="small-calc",
                        title_hint=f"How strongly does {ion} bind a single water molecule, and what does charge "
                                   "scaling miss?")
    protocol = R.protocol(f"/app/{d.name}", python=CALC_PYTHON, packages=CALC_IMPORTS, figure_hint=FIGURE_1,
                          numbers_hint="the counterpoise-corrected binding energy (e_int_kcal_mol) and the "
                                       "equilibrium distance (r_min_angstrom)")
    print(json.dumps({"dir": str(d), "ion": ion, "charge": charge, "element": element, "name": name,
                      "n_references": len(refs), "references_file": str(base / "references.json"),
                      "checker": str(Path(__file__).resolve().parent / "checks.py"),
                      "protocol": protocol, "manuscript": R.MANUSCRIPT, "notes": R.NOTES,
                      "citations": [r["citation"] for r in refs]},  # → `cite` trace events on the page
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
