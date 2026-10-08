# How strongly does Na⁺ bind a single water molecule, and what does charge scaling miss?

## Abstract
Force fields developed in the group scale ionic charges by 0.75 to account for the electronic polarization that
fixed-charge models miss, the electronic continuum correction (ECC) [1, 3]. We ask how a single Na⁺–water contact
computed from first principles compares with the full-charge and ECC-scaled point-charge pictures. A B3LYP/def2-SVP
scan along the water's C$_2$ axis places the minimum at $r_\mathrm{min} = $ **TBD** Å. At that distance the
counterpoise-corrected B3LYP/def2-TZVP interaction energy is **TBD**. SPC/E point charges give **TBD** kcal/mol with the full and the ECC-scaled ion charge. A gas-phase dimer has no surrounding
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
water. Energies are in kcal/mol; negative means bound. The whole calculation took **TBD** s on one CPU core.

## Results
Figure 1 shows the scan. The B3LYP/def2-SVP curve has a single minimum at $r_\mathrm{min} = $ **TBD** Å with
$E_\mathrm{int} = $ **TBD** kcal/mol. With the def2-TZVP basis the interaction energy at the same distance is **TBD**.

At $r_\mathrm{min}$ the full-charge and ECC-scaled Coulomb energies are **TBD**.

![Figure 1. Na⁺–water interaction energy along the C₂ axis: B3LYP/def2-SVP scan (blue), point-charge Coulomb energy with the full (grey, dashed) and ECC-scaled (gold, dotted) ion charge, and the counterpoise-corrected B3LYP/def2-TZVP value at the minimum (red star).](fig1.png)

## Discussion
**TBD**: compare the corrected binding energy with the two point-charge estimates, then say what this does and does not show about ECC [1, 4].

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
