"""calc-image smoke test: the pinned environment imports, computes and plots. Run at image build time (a failing
smoke test fails the build) and by the TEST stage before INSTALL. Prints one JSON line; exit 0 = ok.

    /opt/calc/bin/python smoke.py
"""
import json
import os
import sys
import tempfile
import time

# one thread: OpenMP otherwise starts a thread per host core, which on a busy or CPU-limited sandbox makes this
# 1-second test take 10+ s (measured on a shared 16-core host at load 50: 10.8 s vs 1.4 s)
os.environ.setdefault("OMP_NUM_THREADS", "1")
t0 = time.time()
out = {"ok": False, "python": sys.version.split()[0]}
try:
    import h5py
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy
    import pyscf
    import scipy
    from pyscf import gto, scf

    out["versions"] = {m.__name__: m.__version__ for m in (pyscf, numpy, scipy, matplotlib, h5py)}
    # the calculation small-calc runs, in miniature (HF/STO-3G keeps it to about a second): Na+ facing water's O
    water = [("O", (0.0, 0.0, 0.0)), ("H", (0.7572, 0.0, 0.5865)), ("H", (-0.7572, 0.0, 0.5865))]

    def energy(atoms, charge):
        return scf.RHF(gto.M(atom=atoms, basis="sto-3g", charge=charge, verbose=0)).kernel()

    ion = [("Na", (0.0, 0.0, -2.2))]
    e_int = (energy(ion + water, 1) - energy(ion, 1) - energy(water, 0)) * 627.509474
    out["e_int_kcal_mol"] = round(e_int, 2)
    assert -80.0 < e_int < -5.0, f"Na+-water at 2.2 Å should be bound, got {e_int:.2f} kcal/mol"
    fig, ax = plt.subplots(figsize=(3, 2), dpi=100)
    ax.plot([1, 2, 3], [3, 1, 2])
    path = os.path.join(tempfile.gettempdir(), "calc-image-smoke.png")
    fig.savefig(path)
    assert os.path.getsize(path) > 1000, "matplotlib wrote an empty figure"
    out["ok"] = True
except Exception as e:  # noqa: BLE001 - the smoke test reports any failure as data
    out["error"] = f"{type(e).__name__}: {e}"
out["seconds"] = round(time.time() - t0, 2)
print(json.dumps(out))
sys.exit(0 if out["ok"] else 1)
