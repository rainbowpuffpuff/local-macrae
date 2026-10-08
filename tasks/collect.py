"""Last step of every task flow (script): copy the agent's outputs to $FLOW_RUN_DIR/result/ and print a summary.

    python -m tasks.collect --step card --files methods-card.md
    python -m tasks.collect --step calc --files result.json results/manuscript.md 'results/fig*.png' NOTES_TO_SELF.md

Reads the step's result from $FLOW_CONTEXT (its `files` folder). --files takes names, sub-paths and glob patterns;
sub-paths are kept (results/manuscript.md → <run>/result/results/manuscript.md), so the manuscript's relative figure
links keep working. Prints JSON:
{"result_dir", "files": [...], "summary": "one sentence for the page and the voice agent", "data": {...},
 "manuscript": {"path", "title", "words", "figures", "citations"} | null, "notes_to_self": path | null}
NOTES_TO_SELF.md (the agent's lab notebook) is what evolve ingests after the run: tasks.research.notes_to_lessons.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path


def step_result(ctx: dict, step: str) -> dict:
    r = ctx.get(step)
    if isinstance(r, list):  # foreach: first instance that came back ok
        r = next((x for x in r if isinstance(x, dict) and x.get("ok")), r[0] if r else None)
    return r if isinstance(r, dict) else {}


def summarize(step: str, out: Path) -> tuple[str, dict]:
    res = out / "result.json"
    if res.is_file():
        try:
            d = json.loads(res.read_text())
        except ValueError:
            d = {}
        e, r = d.get("e_int_kcal_mol"), d.get("r_min_angstrom")
        full, ecc = d.get("e_coulomb_full_charge_kcal_mol"), d.get("e_coulomb_ecc_kcal_mol")
        if isinstance(e, (int, float)) and isinstance(r, (int, float)):
            s = (f"{d.get('ion', 'ion')}–water binding: {e:.1f} kcal/mol at {r:.2f} Å "
                 f"({d.get('method', '')}/{d.get('basis_final', d.get('basis', ''))}, {d.get('software', '')})")
            if isinstance(full, (int, float)) and isinstance(ecc, (int, float)):
                s += f"; point-charge Coulomb {full:.1f} → {ecc:.1f} kcal/mol with ECC scaling"
            return s + ".", d
    card = out / "methods-card.md"
    if card.is_file():
        text = card.read_text()
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), "methods card")
        n = len({m for m in re.findall(r"\[(\d+)\]", text)})
        return f"Wrote '{title}' ({len(text.split())} words, {n} sources cited).", {}
    return f"{step} finished.", {}


def manuscript_info(out: Path) -> dict | None:
    ms = out / "results" / "manuscript.md"
    if not ms.is_file():
        return None
    text = ms.read_text(errors="replace")
    title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), "")
    figs = re.findall(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)", text)
    body = re.split(r"(?im)^\s*##\s+references\b", text, maxsplit=1)[0]
    cites = sorted({int(n) for grp in re.findall(r"\[(\d+(?:\s*,\s*\d+)*)\]", body) for n in re.split(r"\s*,\s*", grp)})
    return {"path": str(ms), "title": title, "words": len(text.split()), "figures": figs, "citations": cites}


def expand(src: Path, pattern: str) -> list[Path]:
    """Files under src matching a name, sub-path or glob; never outside src."""
    root = src.resolve()
    hits = sorted(src.glob(pattern)) if any(c in pattern for c in "*?[") else [src / pattern]
    return [p for p in hits if p.is_file() and root in p.resolve().parents]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--step", required=True)
    ap.add_argument("--files", nargs="+", required=True)
    a = ap.parse_args(argv)
    ctx = json.loads(Path(os.environ["FLOW_CONTEXT"]).read_text())
    run_dir = Path(os.environ.get("FLOW_RUN_DIR") or ".")
    r = step_result(ctx, a.step)
    src = Path(r.get("files") or "")
    if not r.get("files") or not src.is_dir():
        print(f"step {a.step} returned no files folder", file=sys.stderr)
        return 1
    out = run_dir / "result"
    out.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in a.files:
        hits = expand(src, name)
        if not hits:
            print(f"missing {name} in {src}", file=sys.stderr)
        for p in hits:
            dest = out / p.relative_to(src)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            copied.append(str(dest))
    summary, data = summarize(a.step, out)
    (out / "summary.txt").write_text(summary + "\n")
    notes = out / "NOTES_TO_SELF.md"
    print(json.dumps({"result_dir": str(out), "files": copied, "summary": summary, "data": data,
                      "manuscript": manuscript_info(out), "notes_to_self": str(notes) if notes.is_file() else None},
                     ensure_ascii=False))
    return 0 if copied else 1


if __name__ == "__main__":
    sys.exit(main())
