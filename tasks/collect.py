"""Last step of every task flow (script): copy the agent's outputs to $FLOW_RUN_DIR/result/ and print a summary.

    python -m tasks.collect --step card --files methods-card.md
    python -m tasks.collect --step calc --files result.json explanation.md run_calc.py output.log

Reads the step's result from $FLOW_CONTEXT (its `files` folder). Prints JSON:
{"result_dir", "files": [...], "summary": "one sentence for the page and the voice agent", "data": {...}}
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
        p = src / name
        if p.is_file():
            shutil.copy2(p, out / name)
            copied.append(str(out / name))
        else:
            print(f"missing {name} in {src}", file=sys.stderr)
    summary, data = summarize(a.step, out)
    (out / "summary.txt").write_text(summary + "\n")
    print(json.dumps({"result_dir": str(out), "files": copied, "summary": summary, "data": data},
                     ensure_ascii=False))
    return 0 if copied else 1


if __name__ == "__main__":
    sys.exit(main())
