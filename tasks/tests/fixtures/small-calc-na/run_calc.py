"""Na+–water: B3LYP/def2-SVP scan along the C2 axis, CP-corrected B3LYP/def2-TZVP at the minimum,
point-charge Coulomb (SPC/E water, full vs ECC-scaled ion charge) along the scan; writes result.json + fig1.png."""
import json, time
import numpy as np
import pyscf
from pyscf import gto, dft
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HARTREE_KCAL = 627.509474
KE = 332.0637  # kcal/mol·Å/e²
ION, Z, Q = "Na", 1, 1
t0 = time.time()
roh, ang = 0.9572, np.deg2rad(104.52)
# water in the xz plane, O at origin, C2 axis along +z (H side), ion on -z (facing O)
H1 = (roh * np.sin(ang / 2), 0.0, roh * np.cos(ang / 2))
H2 = (-roh * np.sin(ang / 2), 0.0, roh * np.cos(ang / 2))
water = [("O", (0, 0, 0)), ("H", H1), ("H", H2)]

def energy(atoms, basis, charge, ghosts=()):
    mol = gto.M(atom=[(s, c) for s, c in atoms] + [("Ghost-" + s, c) for s, c in ghosts], basis=basis,
                charge=charge, spin=0, unit="Angstrom", verbose=0)
    mf = dft.RKS(mol); mf.xc = "b3lyp"; mf.conv_tol = 1e-9
    return mf.kernel()

def e_int(r, basis, cp=False):
    ion = [(ION, (0, 0, -r))]
    eab = energy(ion + water, basis, Q)
    if cp:
        ea = energy(ion, basis, Q, ghosts=water); eb = energy(water, basis, 0, ghosts=ion)
    else:
        ea = energy(ion, basis, Q); eb = energy(water, basis, 0)
    return (eab - ea - eb) * HARTREE_KCAL

def coulomb(r, q):
    qs = {"O": -0.8476, "H": 0.4238}
    return sum(KE * q * qs[s] / np.linalg.norm(np.array(c) - np.array((0, 0, -r))) for s, c in water)

scan = []
for r in np.round(np.arange(1.7, 3.31, 0.1), 2):
    e = e_int(r, "def2-svp"); scan.append({"r_angstrom": float(r), "e_int_kcal_mol": round(e, 3),
                                          "e_coulomb_full_kcal_mol": round(coulomb(r, Q), 3),
                                          "e_coulomb_ecc_kcal_mol": round(coulomb(r, 0.75 * Q), 3)})
    print(f"r={r:.2f} Å  E_int(B3LYP/def2-SVP)={e:8.3f} kcal/mol", flush=True)
best = min(scan, key=lambda p: p["e_int_kcal_mol"]); rmin = best["r_angstrom"]
e_unc = e_int(rmin, "def2-tzvp"); e_cp = e_int(rmin, "def2-tzvp", cp=True)
print(f"minimum r={rmin:.2f} Å: def2-TZVP E_int={e_unc:.3f}, CP-corrected {e_cp:.3f} kcal/mol", flush=True)
res = {"ion": "Na+", "charge": Q, "software": f"PySCF {pyscf.__version__}", "method": "B3LYP",
       "basis_scan": "def2-SVP", "basis_final": "def2-TZVP", "r_min_angstrom": rmin,
       "e_int_kcal_mol": round(e_cp, 2), "e_int_uncorrected_kcal_mol": round(e_unc, 2),
       "bsse_kcal_mol": round(e_cp - e_unc, 2), "scan": scan,
       "e_coulomb_full_charge_kcal_mol": round(coulomb(rmin, Q), 2),
       "e_coulomb_ecc_kcal_mol": round(coulomb(rmin, 0.75 * Q), 2), "ecc_scaling": 0.75,
       "figures": ["results/fig1.png"], "wall_time_s": round(time.time() - t0, 1)}
json.dump(res, open("result.json", "w"), indent=2)
rs = [p["r_angstrom"] for p in scan]
fig, ax = plt.subplots(figsize=(6.4, 4.2), dpi=150)
ax.plot(rs, [p["e_int_kcal_mol"] for p in scan], "o-", color="#1f6feb", label="B3LYP/def2-SVP")
ax.plot(rs, [p["e_coulomb_full_kcal_mol"] for p in scan], "--", color="#8b949e", label="point charges, full ion charge")
ax.plot(rs, [p["e_coulomb_ecc_kcal_mol"] for p in scan], ":", color="#d29922", lw=2, label="point charges, ECC (×0.75)")
ax.plot([rmin], [e_cp], "*", ms=14, color="#cf222e", label="CP-corrected def2-TZVP at minimum")
ax.set_xlabel("Na$^+$–O distance $r$ (Å)"); ax.set_ylabel("interaction energy (kcal/mol)")
ax.axhline(0, color="k", lw=0.5); ax.legend(frameon=False, fontsize=8); fig.tight_layout()
fig.savefig("results/fig1.png"); print(f"wall {time.time()-t0:.1f} s")
