"""A reference small-calc run (Na+) in Harbor's trial layout, for tests and for replaying a manuscript edit stream.

The science is real: fixtures/small-calc-na/{run_calc.py, output.log, result.json, fig1.png} come from an actual
PySCF 2.14.0 run of the flow's calculation on 2026-10-08 (B3LYP/def2-SVP scan, CP-corrected def2-TZVP at the
minimum: −25.46 kcal/mol at 2.20 Å, 141 s on one CPU). The agent's side is assembled here, deterministically: the
notebook, the manuscript drafted with **TBD** values while the scan runs, then revised edit by edit (including
deleting a premature def2-SVP claim), and the ATIF trajectory of those Write/Edit calls. The sandbox events
(no preinstalled PySCF, the "ensurepip is not available" venv failure) are the ones our first Modal runs hit.

    python -m tasks.tests.reference_run OUT_DIR          # OUT_DIR/calc-a1__ref/{agent/…, artifacts/app/calc/…}
    python -m tasks.tests.reference_run --demo OUT_DIR   # the replayable demo bundle (research/demo/small-calc-na/)

apply_edits(DRAFT, EDITS) == FINAL is asserted on build, so a replay of the trajectory reproduces the manuscript.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "fixtures" / "small-calc-na"
WS = "/app/calc"
MS = f"{WS}/results/manuscript.md"
NB = f"{WS}/NOTES_TO_SELF.md"

FINAL = r"""# How strongly does Na⁺ bind a single water molecule, and what does charge scaling miss?

## Abstract
Force fields developed in the group scale ionic charges by 0.75 to account for the electronic polarization that
fixed-charge models miss, the electronic continuum correction (ECC) [1, 3]. We ask how a single Na⁺–water contact
computed from first principles compares with the full-charge and ECC-scaled point-charge pictures. A B3LYP/def2-SVP
scan along the water's C$_2$ axis places the minimum at $r_\mathrm{min} = 2.20$ Å. At that distance the
counterpoise-corrected B3LYP/def2-TZVP interaction energy is $E_\mathrm{int}^\mathrm{CP} = -25.46$ kcal/mol, with a
basis-set superposition error of 0.59 kcal/mol. SPC/E point charges give $-30.44$ kcal/mol with the full ion charge
and $-22.83$ kcal/mol with the ECC-scaled charge, bracketing the quantum-chemical value. The small def2-SVP basis
overbinds by about 7 kcal/mol, so it locates the minimum but not its depth. A gas-phase dimer has no surrounding
medium to screen the ion, so this illustrates the size of the electrostatic terms rather than testing ECC itself.

## Introduction
Simple ions such as Na⁺ shape the structure of aqueous salt solutions and their binding to membranes [1, 2].
Non-polarizable force fields describe these interactions with fixed point charges and so miss the electronic
polarization of the surroundings. The group's remedy is to scale ionic charges by
$1/\sqrt{\varepsilon_\mathrm{el}} \approx 0.75$, which has been developed into charge-scaled models of sodium
chloride solutions checked against neutron scattering [1], of sodium and calcium binding to a POPC bilayer [2], of
biologically relevant ions [3] and of water models compatible with charge scaling [4]. The case for including
electronic polarization extends to ion pairing outside water [5]. Here we compute the simplest possible reference:
one Na⁺ ion and one water molecule.

## Methods
The water molecule has the experimental gas-phase geometry (O–H 0.9572 Å, H–O–H 104.52°). The ion sits on the
C$_2$ axis facing the oxygen, as in the first hydration shell, at an ion–oxygen distance $r$ scanned from 1.7 to
3.3 Å in 0.1 Å steps. At each $r$ the interaction energy is

$$E_\mathrm{int}(r) = E_\mathrm{Na^+ \cdots H_2O}(r) - E_\mathrm{Na^+} - E_\mathrm{H_2O},$$

computed with B3LYP/def2-SVP in PySCF 2.14.0. At the scan minimum we recompute it with def2-TZVP and remove the
basis-set superposition error with the counterpoise scheme, evaluating each monomer in the full dimer basis:

$$E_\mathrm{int}^\mathrm{CP} = E_{AB}^{AB} - E_{A}^{AB} - E_{B}^{AB}.$$

The point-charge picture uses SPC/E water charges ($q_\mathrm{O} = -0.8476\,e$, $q_\mathrm{H} = +0.4238\,e$) and the
Coulomb sum

$$E_\mathrm{C}(r) = \frac{1}{4\pi\varepsilon_0} \sum_{i \in \mathrm{H_2O}} \frac{q_\mathrm{ion}\, q_i}{r_i},$$

once with $q_\mathrm{ion} = +1\,e$ and once with the ECC charge $q_\mathrm{eff} = q/\sqrt{\varepsilon_\mathrm{el}} =
0.75\,e$, where $\varepsilon_\mathrm{el} \approx 1.78$ is the electronic (high-frequency) dielectric constant of
water. Energies are in kcal/mol; negative means bound. The whole calculation took 141 s on one CPU core.

## Results
Figure 1 shows the scan. The B3LYP/def2-SVP curve has a single minimum at $r_\mathrm{min} = 2.20$ Å with
$E_\mathrm{int} = -32.23$ kcal/mol. With the def2-TZVP basis the interaction energy at the same distance is
$-26.05$ kcal/mol, and the counterpoise correction raises it by 0.59 kcal/mol to $E_\mathrm{int}^\mathrm{CP} =
-25.46$ kcal/mol. The def2-SVP scan therefore overbinds by 6.8 kcal/mol at the minimum.

At $r_\mathrm{min}$ the full-charge Coulomb energy is $-30.44$ kcal/mol and the ECC-scaled one $-22.83$ kcal/mol;
their ratio is 0.75 by construction. Beyond 2.6 Å both point-charge curves are less attractive than the
quantum-chemical one, as expected for a model without induction, the polarization of the water by the ion. Below
2.0 Å the quantum-chemical curve turns sharply repulsive (Pauli repulsion), which bare point charges cannot
describe; force fields add a Lennard-Jones term for it.

![Figure 1. Na⁺–water interaction energy along the C₂ axis: B3LYP/def2-SVP scan (blue), point-charge Coulomb energy with the full (grey, dashed) and ECC-scaled (gold, dotted) ion charge, and the counterpoise-corrected B3LYP/def2-TZVP value at the minimum (red star).](fig1.png)

## Discussion
The first-principles binding energy of one water molecule to Na⁺, $-25.46$ kcal/mol, falls between the full-charge
($-30.44$ kcal/mol) and ECC-scaled ($-22.83$ kcal/mol) point-charge estimates at the same geometry (Figure 1). This
is not evidence for or against charge scaling: a gas-phase dimer has no surrounding medium, while ECC accounts for
the electronic screening of the ion by the many water molecules around it in solution [1, 4]. What the calculation
does show is the size of the terms involved. Removing a quarter of the ion charge changes the ion–water
electrostatics by 7.6 kcal/mol at contact, comparable to the induction and exchange contributions that a
fixed-charge model has to absorb into its parameters. This is why charge-scaled models are tested against
condensed-phase data such as neutron scattering of salt solutions [1] and ion binding to membranes [2].

Limitations: one water molecule in the gas phase, with no second solvation shell and no bulk screening; one
functional (B3LYP) without a dispersion correction; a rigid water and a one-dimensional scan along the C$_2$ axis;
point charges without the Lennard-Jones term a real force field adds. The natural next step is the same comparison
for K⁺ and Ca²⁺ and for a small water cluster, where screening begins to appear.

## References
[1] J. M. Kohagen; P. E. Mason; P. Jungwirth (2016). Accounting for Electronic Polarization Effects in Aqueous Sodium Chloride via Molecular Dynamics Aided by Neutron Scattering. Journal of Physical Chemistry B. https://doi.org/10.1021/acs.jpcb.5b05221
[2] J. Melcr; H. Martinez-Seara Monne; R. Nencini et al. (2018). Accurate Binding of Sodium and Calcium to a POPC Bilayer by Effective Inclusion of Electronic Polarization. Journal of Physical Chemistry B. https://doi.org/10.1021/acs.jpcb.7b12510
[3] S. Fan; P. E. Mason; V. Cruces Chamorro et al. (2025). Charge Scaling Force Field for Biologically Relevant Ions Utilizing a Global Optimization Procedure. Journal of Chemical Theory and Computation. https://doi.org/10.1021/acs.jctc.5c00873
[4] V. Cruces Chamorro; P. Jungwirth; H. Martinez-Seara (2024). Building Water Models Compatible with Charge Scaling Molecular Dynamics. Journal of Physical Chemistry Letters. https://doi.org/10.1021/acs.jpclett.4c00344
[5] V. Košťál; P. Jungwirth; H. Martinez-Seara (2023). Nonaqueous Ion Pairing Exemplifies the Case for Including Electronic Polarization in Molecular Dynamics Simulations. Journal of Physical Chemistry Letters. https://doi.org/10.1021/acs.jpclett.3c02231
"""

# The revision history, oldest first: (text in the earlier version, text after the edit). Building the draft walks
# it backwards from FINAL; replaying it forwards from DRAFT gives FINAL again.
SCAN_SAYS = "places the minimum at $r_\\mathrm{min} = 2.20$ Å."
EARLY_CLAIM = ("places the minimum at $r_\\mathrm{min} = 2.20$ Å, where it binds the water by about 32 kcal/mol, "
               "close to the full-charge point-charge estimate.")
EDITS: list[tuple[str, str, str]] = [  # (when, old, new)
    ("scan finished",
     "places the minimum at $r_\\mathrm{min} = $ **TBD** Å.", EARLY_CLAIM),
    ("scan finished",
     "single minimum at $r_\\mathrm{min} = $ **TBD** Å with\n$E_\\mathrm{int} = $ **TBD** kcal/mol.",
     "single minimum at $r_\\mathrm{min} = 2.20$ Å with\n$E_\\mathrm{int} = -32.23$ kcal/mol."),
    ("counterpoise result: the def2-SVP claim was wrong",
     EARLY_CLAIM, SCAN_SAYS),
    ("counterpoise result",
     "counterpoise-corrected B3LYP/def2-TZVP interaction energy is **TBD**.",
     "counterpoise-corrected B3LYP/def2-TZVP interaction energy is $E_\\mathrm{int}^\\mathrm{CP} = -25.46$ kcal/mol, "
     "with a\nbasis-set superposition error of 0.59 kcal/mol."),
    ("counterpoise result",
     "With the def2-TZVP basis the interaction energy at the same distance is **TBD**.",
     "With the def2-TZVP basis the interaction energy at the same distance is\n$-26.05$ kcal/mol, and the "
     "counterpoise correction raises it by 0.59 kcal/mol to $E_\\mathrm{int}^\\mathrm{CP} =\n-25.46$ kcal/mol. "
     "The def2-SVP scan therefore overbinds by 6.8 kcal/mol at the minimum."),
    ("point charges",
     "SPC/E point charges give **TBD** kcal/mol with the full and the ECC-scaled ion charge.",
     "SPC/E point charges give $-30.44$ kcal/mol with the full ion charge\nand $-22.83$ kcal/mol with the "
     "ECC-scaled charge, bracketing the quantum-chemical value. The small def2-SVP basis\noverbinds by about 7 "
     "kcal/mol, so it locates the minimum but not its depth."),
    ("point charges",
     "At $r_\\mathrm{min}$ the full-charge and ECC-scaled Coulomb energies are **TBD**.",
     "At $r_\\mathrm{min}$ the full-charge Coulomb energy is $-30.44$ kcal/mol and the ECC-scaled one $-22.83$ "
     "kcal/mol;\ntheir ratio is 0.75 by construction. Beyond 2.6 Å both point-charge curves are less attractive "
     "than the\nquantum-chemical one, as expected for a model without induction, the polarization of the water by "
     "the ion. Below\n2.0 Å the quantum-chemical curve turns sharply repulsive (Pauli repulsion), which bare point "
     "charges cannot\ndescribe; force fields add a Lennard-Jones term for it."),
    ("discussion",
     "**TBD**: compare the corrected binding energy with the two point-charge estimates, then say what this does "
     "and does not show about ECC [1, 4].",
     "The first-principles binding energy of one water molecule to Na⁺, $-25.46$ kcal/mol, falls between the "
     "full-charge\n($-30.44$ kcal/mol) and ECC-scaled ($-22.83$ kcal/mol) point-charge estimates at the same geometry "
     "(Figure 1). This\nis not evidence for or against charge scaling: a gas-phase dimer has no surrounding medium, "
     "while ECC accounts for\nthe electronic screening of the ion by the many water molecules around it in solution "
     "[1, 4]. What the calculation\ndoes show is the size of the terms involved. Removing a quarter of the ion charge "
     "changes the ion–water\nelectrostatics by 7.6 kcal/mol at contact, comparable to the induction and exchange "
     "contributions that a\nfixed-charge model has to absorb into its parameters. This is why charge-scaled models "
     "are tested against\ncondensed-phase data such as neutron scattering of salt solutions [1] and ion binding to "
     "membranes [2]."),
    ("wall time", "The whole calculation took **TBD** s on one CPU core.",
     "The whole calculation took 141 s on one CPU core."),
]

NOTES_DRAFT = """# Notes to self: small-calc, Na+

## What I tried
- Read PROTOCOL.md and task.json; plan: scan in the background, draft the manuscript meanwhile.

## What was slow
- (nothing yet)

## What to do differently next time
- (to fill in at the end)

## Capability gaps
- (none yet)
"""

NOTES_EDITS: list[tuple[str, str, str]] = [
    ("environment",
     "- Read PROTOCOL.md and task.json; plan: scan in the background, draft the manuscript meanwhile.\n",
     "- Read PROTOCOL.md and task.json; plan: scan in the background, draft the manuscript meanwhile.\n"
     "- `/opt/calc/bin/python -c \"import pyscf, numpy, matplotlib\"`: no /opt/calc, nothing preinstalled.\n"
     "- `python3 -m venv /opt/calc` failed: \"ensurepip is not available\" (no python3-venv in the sandbox image).\n"
     "- `apt-get install -y python3-venv`, then the venv and `pip install pyscf numpy matplotlib`: worked.\n"),
    ("environment", "- (none yet)\n",
     "- calc-image: PySCF, numpy and matplotlib are not preinstalled; the venv repair and pip install cost 130 s "
     "before any science.\n"),
    ("end of run", "- (nothing yet)\n",
     "- Installing PySCF, numpy and matplotlib: 130 s, on every run.\n"
     "- The venv failed once (ensurepip) and needed apt-get first.\n"
     "- Scan (17 × B3LYP/def2-SVP) plus def2-TZVP counterpoise at the minimum: 141 s in total; fine.\n"),
    ("end of run", "- (to fill in at the end)\n",
     "- Use an image with PySCF, numpy and matplotlib preinstalled in /opt/calc (pinned versions) and skip the "
     "install: saves about 130 s per run.\n"
     "- On a bare ubuntu image, run `apt-get install -y python3-venv` before `python3 -m venv`.\n"
     "- Never sleep-wait for the scan: start it with nohup and write the manuscript while it runs.\n"
     "- Don't quote the def2-SVP minimum as the binding energy: it overbinds by about 7 kcal/mol; the abstract "
     "takes the counterpoise-corrected def2-TZVP value.\n"),
]


def apply_edits(text: str, edits) -> str:
    for _, old, new in edits:
        assert text.count(old) == 1, f"edit target not unique/present: {old[:60]!r}"
        text = text.replace(old, new)
    return text


def unapply_edits(text: str, edits) -> str:
    for _, old, new in reversed(edits):
        assert text.count(new) == 1, f"edit result not unique/present: {new[:60]!r}"
        text = text.replace(new, old)
    return text


DRAFT = unapply_edits(FINAL, EDITS)
NOTES_FINAL = apply_edits(NOTES_DRAFT, NOTES_EDITS)
GAP_LINE = "CAPABILITY_GAP: calc-image: PySCF/numpy/matplotlib missing; venv repair + pip install took 130 s"


def trajectory(instruction: str = "Run a small but real quantum-chemistry calculation …") -> dict:
    """ATIF steps: reads, the notebook, the failed venv, the install, the background scan, the manuscript draft
    while it runs, polls, then the revisions. Timestamps follow the real timings."""
    t = datetime(2026, 10, 8, 19, 41, 0, tzinfo=timezone.utc)
    steps: list[dict] = [{"step_id": 1, "timestamp": t.isoformat(), "source": "user", "message": instruction}]
    n = 0

    def say(msg: str, secs: float = 2) -> None:
        nonlocal t
        steps.append({"step_id": len(steps) + 1, "timestamp": t.isoformat(), "source": "agent", "message": msg})
        t += timedelta(seconds=secs)

    def call(name: str, args: dict, out: str, secs: float, err: bool = False) -> None:
        nonlocal t, n
        n += 1
        res = {"source_call_id": f"toolu_ref{n:02d}", "content": out}
        if err:
            res["is_error"] = True
        steps.append({"step_id": len(steps) + 1, "timestamp": t.isoformat(), "source": "agent", "message": "",
                      "tool_calls": [{"tool_call_id": f"toolu_ref{n:02d}", "function_name": name, "arguments": args}],
                      "observation": {"results": [res]}})
        t += timedelta(seconds=secs)

    def edit(path: str, e: tuple, secs: float = 6) -> None:
        call("Edit", {"file_path": path, "old_string": e[1], "new_string": e[2]},
             f"The file {path} has been updated successfully.", secs)

    call("Read", {"file_path": f"{WS}/PROTOCOL.md"}, "# Research protocol (small-calc) …", 3)
    call("Read", {"file_path": f"{WS}/task.json"}, '{"ion": "Na+", "charge": 1, …}', 2)
    call("Write", {"file_path": NB, "content": NOTES_DRAFT}, f"File created successfully at: {NB}", 4)
    call("Bash", {"command": '/opt/calc/bin/python -c "import pyscf, numpy, matplotlib"',
                  "description": "Check for a preinstalled calc environment"},
         "/bin/bash: line 1: /opt/calc/bin/python: No such file or directory", 2, err=True)
    call("Bash", {"command": "python3 -m venv /opt/calc", "description": "Create the calc venv"},
         "The virtual environment was not created successfully because ensurepip is not\navailable. On Debian/Ubuntu "
         "systems, you need to install the python3-venv\npackage using the following command.", 3, err=True)
    call("Bash", {"command": "s=$(date +%s); apt-get update -qq && apt-get install -y -qq python3-venv >/dev/null && "
                             "python3 -m venv /opt/calc && /opt/calc/bin/pip install -q pyscf numpy matplotlib && "
                             f"echo \"{GAP_LINE}\" && echo took $(( $(date +%s) - s )) s",
                  "description": "Install python3-venv, then PySCF, numpy and matplotlib"},
         f"{GAP_LINE}\ntook 130 s", 130)
    edit(NB, NOTES_EDITS[0], 5)
    edit(NB, NOTES_EDITS[1], 4)
    call("Write", {"file_path": f"{WS}/run_calc.py", "content": (DATA / "run_calc.py").read_text()},
         f"File created successfully at: {WS}/run_calc.py", 6)
    call("Bash", {"command": "cd /app/calc && nohup /opt/calc/bin/python run_calc.py > output.log 2>&1 &",
                  "description": "Start the scan in the background", "run_in_background": True},
         "Command running in background", 2)
    call("Write", {"file_path": MS, "content": DRAFT}, f"File created successfully at: {MS}", 40)
    log = (DATA / "output.log").read_text().splitlines()
    call("Bash", {"command": "tail -4 /app/calc/output.log", "description": "Poll the scan"}, "\n".join(log[8:12]), 12)
    call("Bash", {"command": "tail -4 /app/calc/output.log", "description": "Poll the scan"}, "\n".join(log[14:17]), 10)
    edit(MS, EDITS[0])
    edit(MS, EDITS[1])
    call("Bash", {"command": "tail -3 /app/calc/output.log", "description": "Poll the scan"}, "\n".join(log[-2:]), 8)
    for e in EDITS[2:]:
        edit(MS, e, 7)
    call("Bash", {"command": "cd /app/calc && /opt/calc/bin/python -c \"import json; r=json.load(open('result.json')); "
                             "print(r['e_int_kcal_mol'], r['r_min_angstrom'])\"",
                  "description": "Check result.json against the manuscript"}, "-25.46 2.2", 3)
    edit(NB, NOTES_EDITS[2], 5)
    edit(NB, NOTES_EDITS[3], 6)
    say("Done: Na+ binds one water by −25.46 kcal/mol at 2.20 Å (CP-corrected B3LYP/def2-TZVP); the manuscript, "
        "Figure 1, result.json and my notes are in /app/calc.")
    return {"schema_version": "ATIF-v1.2", "session_id": "ref-small-calc-na",
            "agent": {"name": "claude-code", "model_name": "claude-opus-5-5"}, "steps": steps}


def build(out: Path, instruction: str = "Run a small but real quantum-chemistry calculation …") -> Path:
    """Write a Harbor trial folder under `out`; returns the agent's returned folder (…/artifacts/app/calc)."""
    assert apply_edits(DRAFT, EDITS) == FINAL
    trial = Path(out) / "calc-a1__ref"
    calc = trial / "artifacts" / "app" / "calc"
    (calc / "results").mkdir(parents=True, exist_ok=True)
    (trial / "agent").mkdir(parents=True, exist_ok=True)
    for name in ("run_calc.py", "output.log", "result.json"):
        shutil.copy2(DATA / name, calc / name)
    shutil.copy2(DATA / "fig1.png", calc / "results" / "fig1.png")
    (calc / "results" / "manuscript.md").write_text(FINAL)
    (calc / "NOTES_TO_SELF.md").write_text(NOTES_FINAL)
    (trial / "agent" / "trajectory.json").write_text(json.dumps(trajectory(instruction), indent=1, ensure_ascii=False))
    return calc


def edit_stream(traj: dict) -> list[dict]:
    """The manuscript's and notebook's Write/Edit calls as the backend's manuscript endpoint returns them
    (CONTRACT v3): [{seq, t, op: "write"|"edit", path, old, new, content}], content = the file after the op."""
    files: dict[str, str] = {}
    out = []
    for st in traj["steps"]:
        for tc in st.get("tool_calls") or []:
            a = tc["arguments"]
            path = str(a.get("file_path") or "")
            if tc["function_name"] not in ("Write", "Edit") or not path.endswith(("manuscript.md", "NOTES_TO_SELF.md")):
                continue
            if tc["function_name"] == "Write":
                files[path] = a["content"]
                op = {"op": "write", "old": None, "new": None}
            else:
                files[path] = files[path].replace(a["old_string"], a["new_string"], 1)
                op = {"op": "edit", "old": a["old_string"], "new": a["new_string"]}
            t = datetime.fromisoformat(st["timestamp"]).timestamp()
            out.append({"seq": len(out) + 1, "t": t, "path": path, **op, "content": files[path]})
    return out


def demo(out: Path) -> Path:
    """research/demo/small-calc-na: the final files, the draft, the trajectory and the edit stream, for the page
    (Manuscript view replay) and the mock backend."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    traj = trajectory()
    stream = edit_stream(traj)
    assert stream[-1]["content"] in (FINAL, NOTES_FINAL)
    assert [x for x in stream if x["path"] == MS][-1]["content"] == FINAL
    shutil.copy2(DATA / "fig1.png", out / "fig1.png")
    shutil.copy2(DATA / "result.json", out / "result.json")
    (out / "manuscript.md").write_text(FINAL)
    (out / "draft.md").write_text(DRAFT)
    (out / "NOTES_TO_SELF.md").write_text(NOTES_FINAL)
    (out / "trajectory.json").write_text(json.dumps(traj, indent=1, ensure_ascii=False) + "\n")
    (out / "edit_stream.json").write_text(json.dumps(stream, indent=1, ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--demo"]:
        print(demo(Path(args[1] if len(args) > 1 else ".")))
    else:
        print(build(Path(args[0] if args else ".")))
