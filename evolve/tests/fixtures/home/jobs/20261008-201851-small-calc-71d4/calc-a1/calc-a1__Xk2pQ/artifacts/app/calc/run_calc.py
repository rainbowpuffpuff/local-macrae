"""Ion–water binding: B3LYP/def2-SVP scan of the ion–O distance, counterpoise-corrected def2-TZVP at the
minimum, and the SPC/E point-charge Coulomb energy with the full and the ECC-scaled (x0.75) ion charge."""
import json, sys, time
import numpy as np
from pyscf import gto, dft
from pyscf.scf import addons

HARTREE_KCAL = 627.509
ION, CHARGE = sys.argv[1] if len(sys.argv) > 1 else "Na+", 1


def water(r_ion):
    th = np.deg2rad(104.52 / 2)
    return [["O", (0, 0, 0)], ["H", (0.9572 * np.sin(th), 0, 0.9572 * np.cos(th))],
            ["H", (-0.9572 * np.sin(th), 0, 0.9572 * np.cos(th))], ["Na", (0, 0, -r_ion)]]


def energy(atoms, basis, charge, ghost=()):
    mol = gto.M(atom=atoms, basis=basis, charge=charge, verbose=0)
    mf = dft.RKS(mol, xc="b3lyp")
    return mf.kernel()


t0 = time.time()
scan = []
for r in np.arange(1.7, 3.31, 0.1):
    e = (energy(water(r), "def2-svp", CHARGE) - energy(water(r)[3:], "def2-svp", CHARGE)
         - energy(water(r)[:3], "def2-svp", 0)) * HARTREE_KCAL
    scan.append({"r_angstrom": round(float(r), 2), "e_int_kcal_mol": round(float(e), 3)})
    print(f"r={r:.2f} E={e:.2f}", flush=True)
