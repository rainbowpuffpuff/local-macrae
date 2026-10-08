# Notes to self: small-calc, Na+

## What I tried
- Read PROTOCOL.md and task.json; plan: scan in the background, draft the manuscript meanwhile.
- `/opt/calc/bin/python -c "import pyscf, numpy, matplotlib"`: no /opt/calc, nothing preinstalled.
- `python3 -m venv /opt/calc` failed: "ensurepip is not available" (no python3-venv in the sandbox image).
- `apt-get install -y python3-venv`, then the venv and `pip install pyscf numpy matplotlib`: worked.

## What was slow
- Installing PySCF, numpy and matplotlib: 130 s, on every run.
- The venv failed once (ensurepip) and needed apt-get first.
- Scan (17 × B3LYP/def2-SVP) plus def2-TZVP counterpoise at the minimum: 141 s in total; fine.

## What to do differently next time
- Use an image with PySCF, numpy and matplotlib preinstalled in /opt/calc (pinned versions) and skip the install: saves about 130 s per run.
- On a bare ubuntu image, run `apt-get install -y python3-venv` before `python3 -m venv`.
- Never sleep-wait for the scan: start it with nohup and write the manuscript while it runs.
- Don't quote the def2-SVP minimum as the binding energy: it overbinds by about 7 kcal/mol; the abstract takes the counterpoise-corrected def2-TZVP value.

## Capability gaps
- calc-image: PySCF, numpy and matplotlib are not preinstalled; the venv repair and pip install cost 130 s before any science.
