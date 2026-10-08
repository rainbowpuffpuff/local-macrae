# Scout: the Jungwirth group's papers → research directions for BFF runs

Scope: force-field / ion / charge-scaling (ECC) / Bayesian / membrane / interface papers, 2020–2026, from
`data/group_publications.json`, plus what the BFF paper (Košťál, Shanks, Jungwirth, Martinez-Seara, JCTC 2026,
22, 2652–2663, doi:10.1021/acs.jctc.5c02051, `.reference/bff/Kostal2026BayesianLearningAccurate.pdf`) leaves open.
Scouted 2026-10-08.

## 0. How this was gathered (reproducible)

| step | command | time / result |
|---|---|---|
| Filter the list | `data/group_publications.json`: 479 papers; `year >= 2020` → 146, 145 with a DOI | <1 s |
| OpenAlex abstracts | `python3 -I research/scout/fetch_openalex.py data/group_publications.json research/scout/papers_openalex.json` (4 batched `GET https://api.openalex.org/works?filter=doi:a\|b\|…`, 40 DOIs each) | **4.7 s**; 145/145 found, 125 with abstract |
| Topic tagging | regex over title+abstract (ECC, FF, ion, bayes, memb, iface, aimd), stored as `tags` in `papers_openalex.json` | ~38 core papers read in full (Section 2) |
| BFF paper text | pymupdf in a venv, `fitz.open(pdf).get_text()` per page → 12 pages, 1542 lines | <2 s |
| Citing works | `GET https://api.openalex.org/works?filter=cites:<BFF id>` | 1 citing work so far (Kirsh…Boxer 2026, doi:10.1063/5.0318326, an AMOEBA test) |
| Non-listed preprints | OpenAlex author search for Košťál, Shanks, Cruces Chamorro since 2024-06 | found arXiv:2607.22338 (Section 2.5) |
| Group website | `curl https://jungwirth.group.uochb.cz/en/publications` → 80 kB, **JS-rendered, no DOIs in raw HTML** | the JSON stays the source of truth |
| BFF repo examples | GitHub tree API + raw files of `vojtechkostal/BayesicForceFields@main` (v0.4.2, 2026-10-02) | Section 3 |

`research/scout/papers_openalex.json` = the 145 records (title, journal, year, doi, authors, OpenAlex id, abstract,
cited_by, topics, tags). The ideate step can grep it directly.

## 1. What the BFF paper does, and what it leaves open (with page refs)

The method: partial charges of 18 solvated fragments are learned from condensed-phase AIMD (CP2K 9.1, revPBE-D3 with
D3 off for Ca²⁺ pairs, TZV2P, 400 Ry, 300 K, 0.5 fs). The quantities of interest (QoIs) are solute–water-O RDFs,
H-bond counts and restrained ion–solute distance distributions. Each fragment has three setups: solute alone, a
contact counterion and a solvent-shared counterion. Training is several thousand LHS charge vectors run in GROMACS
2024.3 (128 TIP4P/2005-type waters, ~1.6 nm box, 0.7 nm cutoff). The surrogate is a local GP (one length scale per
charge, one GP per RDF bin, sigmoid baseline). The sampler is emcee StretchMove. Net charge is fixed at **0.8 ×
formal charge** (ECC), with water and ions from refs 46 and 47 (below). Validation:
- NMAE vs AIMD for 10 posterior samples: RDFs mostly <5 %, H-bonds 10–20 %, ion distances <20 %.
- Solution densities within ~1 % up to near solubility (p. 7).
- Troponin C (PDB 1AP4) Ca²⁺ ΔG_bind: posterior sets within 10 kJ/mol of experiment (−28.6 kJ/mol), some within
  ~1 kJ/mol; the acetate MAP underbinds by ~6.
- Other force fields: CHARMM36 −105.7, CHARMM36-nbfix −31.7 (error cancellation), prosECCo75 −13.5 kJ/mol.
- Good sets have a CH₂COO⁻ dipole of 2.7–4.4 D (CHARMM 8.9 D, prosECCo75 6.6 D) (pp. 8–9).

Future work and limitations stated **explicitly** in the text:

| # | statement (paraphrased, page) | why it matters for a run |
|---|---|---|
| L1 | Likelihood: one observation per QoI, conditionally independent, homoscedastic. It "neglects correlations in the observation noise between QoIs" and "cannot capture … vanishing variance in RDFs at short range". "More rigorous likelihoods that incorporate learned posterior covariances from reference data (cf. ref 22) could be employed" (p. 4, after eq. 7) | Re-running the **surrogate + MCMC only** with a different noise model needs no new MD → cheap |
| L2 | "exploratory work optimizing Lennard-Jones nonbonded parameters within the same framework points to additional improvements" (p. 9). Bonded and LJ terms were held fixed, "a decision that affects the resulting optimized charges" (p. 5) | Joint charge + LJ is the stated next step |
| L3 | Shown up to 10 parameters; "extensions … up to roughly 30 dimensions will be feasible, although future work will be required to systematically assess the practical limits" (p. 10). The bottleneck is **surrogate training data**, not MCMC | A scaling study (training-set size vs dimension vs posterior quality) is explicitly asked for |
| L4 | "any source of reference data, experimental and computational … thermodynamic properties and neutron/X-ray scattering patterns … integrating experimental data with simulation-based models" (p. 10) | Experimental likelihood terms (NDIS, densities, binding constants) are named as a goal; none were used in the fit (densities were validation only) |
| L5 | Posterior "remains conditional on … likelihood, priors, and surrogate models"; mis-specification can under- or over-state uncertainty (p. 10) | Sensitivity and ablation runs (prior width, noise model, surrogate size) are legitimate results |
| L6 | The acetate MAP underbinds in the protein: "best represents acetate in bulk water is not necessarily optimal in this biological context", the "central limitation of fixed-charge … optimizers" (p. 9) | Posterior **propagation** to a binding observable, rather than a single MAP, is the selling point |
| L7 | High-uncertainty parameters "can be identified and more aggressively refined by selecting new or additional reference data" (p. 7) | Active learning / optimal design of the next reference QoI |
| L8 | "Hybrid strategies that combine our fragment-based training with targeted refinements for system-specific properties" for large-scale reparametrization (p. 10) | Fragment posterior as prior, then a small system-specific update |

Implicit but unstated:
- The ECC factor 0.8 is fixed (p. 5: "adopted from our recent work"), while prosECCo75 uses 0.75. Košťál 2023 (doi:10.1021/acs.jpclett.3c02231) concludes the AIMD data "justify the potential employment of weaker charge scaling factors". The scale factor itself has never been inferred.
- Concentration transfer was only checked by densities.

## 2. The group's relevant lines of work, 2020–2026 (DOIs; abstracts in `papers_openalex.json`)

### 2.1 ECC / charge scaling: theory and the prosECCo75 force field
- **Duboué-Dijon … Martinez-Seara 2020, JCP** (doi:10.1063/5.0017775, 125 cites): practical guide; scale only the
  charged groups, factor 0.75.
- **Kirby & Jungwirth 2019, JPCL** (doi:10.1021/acs.jpclett.9b02652): the "Charge Scaling Manifesto"; the physical
  basis is 1/√ε_el.
- **Melcr … Ollila 2020, JCTC** (doi:10.1021/acs.jctc.9b00824): ECC-POPS with charges ×0.75 **and LJ σ ×0.89** for
  headgroup segments, validated by NMR order parameters and the "electrometer concept". Precedent that charge
  scaling needs LJ re-tuning (→ L2).
- **Nencini … Jungwirth 2024, JCTC** (doi:10.1021/acs.jctc.4c00743, 38 cites): **prosECCo75**, CHARMM36 with scaled
  integer and partial charges for lipids, proteins and saccharides. It is the baseline BFF is compared against
  (troponin −13.5 kJ/mol).
- **Košťál, Jungwirth, Martinez-Seara 2023, JPCL** (doi:10.1021/acs.jpclett.3c02231): AIMD of like-charged ion pairs
  in a nonaqueous solvent (polarization is the only dielectric response). Confirms ECC and supports **weaker
  scaling (>0.75)**, i.e. the origin of 0.8.
- **Antila … Jungwirth, Ollila 2022, JPCB** (doi:10.1021/acs.jpcb.2c01954): perspective; calls for *automated,
  physically justified* parametrization of all membrane components with ECC, scored against quality-evaluated
  databanks (NMRlipids). BFF is the tool this perspective asks for.

### 2.2 ECC-consistent water and ions (the force-field base BFF sits on)
- **Cruces Chamorro, Jungwirth, Martinez-Seara 2024, JPCL** (doi:10.1021/acs.jpclett.4c00344): 4-site water with
  ε ≈ 45 (consistent with scaled charges, avoiding "overscaling"), found by ML-assisted search. A "sizable range of
  parameters" satisfies the constraint, a natural target for a posterior rather than a point estimate.
- **Cruces Chamorro … 2025, JCP** (doi:10.1063/5.0299233): many high-quality 4-site models with ε 45–75; quality is
  insensitive to ε, optimum 55–70; reports **correlations between parameters** of good models. That is a
  posterior-shaped finding obtained without a posterior.
- **Fan, Mason, Cruces Chamorro, Shanks, Martinez-Seara, Jungwirth 2025, JCTC** (doi:10.1021/acs.jctc.5c00873): ECC
  ions Li⁺, Na⁺, K⁺, Ca²⁺, Mg²⁺, Cl⁻, Br⁻, I⁻ on that water, ML-accelerated global optimization. The abstract
  states the open item: "**a future need for improving consistently and simultaneously the water and ion models
  within the ECC framework**".
- The BFF repo ships these as `data/ff/water/ecc75|ecc80`, `data/ff/ions/ecc80|ecc81` (the paper's refs 46 and 47).

### 2.3 Applications showing what charge scaling changes (targets for posterior propagation)
- **Le Nguyen, Žák, Jungwirth, Lepšík 2025, JPCL** (doi:10.1021/acs.jpclett.5c01786): insulin salt bridges.
  ff19SB/CHARMM36m give 4–5 kcal/mol dissociation barriers, prosECCo75 gives ~1 kcal/mol; NMR supports ECC.
- **Riopedre Fernández, Košťál … Biriukov 2024, JCIM** (doi:10.1021/acs.jcim.4c00981): Ca²⁺–methylsulfate and
  N-methylsulfamate PMFs against AIMD. AIMD says solvent-shared pairing is preferred; **only the scaled-charge models
  agree**, and Drude/AMOEBA fall short. A direct AIMD-referenced ion-pair benchmark.
- **Phan … Martinez-Seara … Tucker 2023, BPJ** (doi:10.1016/j.bpj.2023.03.026): Cl⁻ in a chloride-pumping rhodopsin.
  Polarization (prosECCo, AMOEBA) **strengthens** anion binding in a hydrophobic site. ECC is not a uniform
  weakening.
- **Yesylevskyy, Martinez-Seara, Jungwirth 2023, JPCB** (doi:10.1021/acs.jpcb.3c01962): Ca²⁺ binds the concave side of
  curved PS/PC bilayers; scaled charges reduce Ca²⁺–PS binding.
- **Mendes de Oliveira … Duboué-Dijon 2020, PCCP** (doi:10.1039/d0cp02987d, 73 cites): Raman-MCR **experimental 1:1
  binding constants** for Zn²⁺, Ca²⁺, Mg²⁺–acetate, with ECC MD and AIMD; the measured constants correspond to
  contact pairs. A ready **experimental scalar** for acetate–Ca²⁺, the same system as the BFF acetate example.
- **Biriukov … Jungwirth, Heyda, Vazdar 2026, Aggregate** (doi:10.1002/agt2.70276): Gdm⁺–Gdm⁺ like-charge contact
  pairing measured by NMR (weak and attractive), oligo-Arg aggregation by cryo-EM. Guanidinium is a BFF fragment
  (→ Arg), so the measurement is an out-of-training test.
- **Lukasheva … Martinez-Seara, Karttunen 2022, Polymers** (doi:10.3390/polym14020252): 7 force fields × {native,
  NBFIX, ECC} for Na⁺/K⁺ with polyanions. Results depend on the ion model.

### 2.4 Experimental structure benchmarks: neutron diffraction (NDIS), Mason / Jungwirth
- **Mason … Martinez-Seara 2024, PCCP** (doi:10.1039/d3cp05449g): TMA⁺ hydration by NDIS.
- **Le Nguyen … Mason, Duboué-Dijon 2025, PCCP** (doi:10.1039/D4CP04312J): TMA–acetate ion pairing by NDIS + FFMD +
  AIMD. Acetate again.
- **Červenka, Shanks, Mason, Jungwirth 2025, JPCB** (doi:10.1021/acs.jpcb.5c02001): cation–π, TMA–pyridine. ECC 0.75
  on CHARMM36 improves agreement with neutron data. ILL beam time "Exploring Biologically Relevant Cation-pi
  Interactions in Water" (doi:10.5291/ill-data.dir-442, 2026) means more NDIS data is coming.
- **Biriukov … Předota 2022, JCP** (doi:10.1063/5.0093643): 7.3 m CaCl₂ by Cl-NDIS. Models fitted to Ca-NDIS
  overestimate contact pairs because Ca–Cl is "hidden" in that signal. The conclusion "which model is the best" →
  "which model is better for a given research" is a model-selection / posterior-predictive question in Bayesian
  terms.
- **Rampal … Biriukov … Stack 2021, JML** (doi:10.1016/j.molliq.2021.116898): ZnCl₂ NDIS used to calibrate ECC MD.
- **Nguyen … Jungwirth 2021, JPCB** (doi:10.1021/acs.jpcb.0c10599): LiCl/NaCl equal number density, a density-based
  test.

### 2.5 Bayesian / GP machinery (Shanks, with Hoepfner at Utah) feeding BFF
- **Shanks, Sullivan, Shazed, Hoepfner 2024, JCTC** (doi:10.1021/acs.jctc.3c01358): the **local GP surrogate** BFF uses;
  1.76×10⁶× speed-up over MD for the neon RDF. Code: github.com/hoepfnergroup/LGPMD.
- **Shanks, Sullivan, Hoepfner 2024, JPCL** (doi:10.1021/acs.jpclett.4c02941): Bayesian recovery of Mie potentials
  from S(q). Recommends noise <0.005 up to ~30 Å⁻¹ (bin 0.05 Å⁻¹) for ε to ±0.024 kcal/mol.
- **Sullivan, Červenka, Shanks, Hoepfner 2025, JPCB** (doi:10.1021/acs.jpcb.5c05024): non-stationary GP inference of
  g(r) **with uncertainty** from scattering (Ar, water). This is BFF ref 22, the cure named for L1.
- **Shanks, Sullivan, Jungwirth, Hoepfner 2025, JCP** (doi:10.1063/5.0260274): probabilistic iterative Boltzmann
  inversion; noble-gas force fields reduce to **one parameter** tied to polarizability (quantum Drude oscillator
  behaviour).
- **Škorňa, Gottfried, Janáčková, Baxová, Jungwirth, Shanks 2026, arXiv:2607.22338** (not in the JSON yet):
  hierarchical GP UQ for free-energy profiles (umbrella sampling, metadynamics) of **peptide–membrane**
  interactions. Data on Zenodo, doi:10.5281/zenodo.21358801. This is the missing piece to put error bars on PMFs
  computed from BFF posterior samples.

### 2.6 Membranes and interfaces (where ECC charges are used downstream)
- Benchmarks against NMR order parameters, diffusion and X-ray form factors: **Javanainen … Ollila 2023, JCTC**
  (doi:10.1021/acs.jctc.3c00648, POPC–cholesterol, "none clearly outperforms"; metrics "will foster … automated
  approaches").
- **Tempra, Ollila, Javanainen 2022, JCTC** (doi:10.1021/acs.jctc.1c00951): monolayers need water with correct
  surface tension.
- **Biriukov & Javanainen 2023, JCTC** (doi:10.1021/acs.jctc.3c00614): flat-bottom restraints for asymmetric
  solvent across a single bilayer. Cheap set-ups.
- Peptide–lipid adsorption, an ion-specific and ECC-sensitive regime: Arg vs Lys at PC bilayers (Tempra, Brkljača,
  Vazdar 2023, doi:10.1039/D3CP02411C); cell-penetrating peptides vs ionic strength (Nguyen … Vazdar 2022,
  doi:10.1021/acs.langmuir.2c01435); EDTA binding to PC (Vazdar … 2023, doi:10.1021/acs.jpcb.3c03207); Ca²⁺/Na⁺ on
  DPPC monolayers with VSFG (Javanainen … Allen 2020, doi:10.1021/acs.langmuir.0c02555).
- Too large for a <20 min BFF run (bilayers are 10⁴–10⁵ atoms, ns–µs). Use them only as *cited motivation* or
  downstream tests of a fragment posterior (phosphate, choline/TMA, carboxylate, guanidinium).

## 3. What exists in the BFF repo that a run can reuse without new AIMD

(Code details and timings belong to the `bff_code` scout. Only the data-availability facts that limit ideas are listed here.)
- `examples/acetate/`: the paper's acetate workflow as 7 staged configs: build → label-snapshots (CP2K) →
  *external MLIP + reference MD* → sample-parameters (GROMACS) → build-qoi-datasets → fit-lgp → learn → validate.
  - **Reference trajectories are shipped**: `inputs/reference-trajectories/pos-000|001|002.xtc` (1.4–1.5 MB each).
  - Parsed headers here: 391 atoms (acetate + 128 3-site waters) and 392 atoms (plus Ca²⁺). About 900–1000 frames,
    t ≈ 244 → 2450–2690 ps, so ~2.2–2.4 ns per system. That length points to MLIP-driven reference MD rather than
    raw AIMD; provenance is **not stated in the repo**, so check before calling it "AIMD".
  - So the acetate / acetate–Ca²⁺ contact / solvent-shared trio is the one AIMD-level reference available
    **without running CP2K**.
  - Default template: `n_samples: 20`, 20 000 steps (40 ps) per training MD, 0.7 nm cutoff, `ecc80` water and ions.
    Charge bounds: C2 [0, 1], O1 O2 [−0.8, −0.3], C1 [−1, 0.3], H [−0.3, 0.3]; net −0.80, C2 implicit → 4 free
    charges. MCMC 10 000 steps, `device: cuda`.
- `examples/arbitrary-data/`: no MD. Tabular simulation results + experimental targets (density 997 ± 4,
  ΔH_vap 44.0 ± 0.25, D 2.30 ± 0.07) → posterior over water ε_O, σ_O. It is the **entry point for experimental
  scalar likelihoods** (L4).
- `examples/neon-mie-lgpmd/`: no MD. Liquid-neon experimental RDF + LGPMD training data → Mie ε, λ, σ (Shanks 2024).
- AIMD (CP2K revPBE-D3, 128 waters) for a *new* fragment does not fit the <20 min / <$5 budget. Any idea needing a
  new reference must use experimental data (Section 2.3/2.4) or the shipped acetate trajectories.

## 4. Open questions mapped to BFF runs

Each row: the gap → the evidence → a concrete BFF run. MD estimates assume GROMACS on CPU for a ~400-atom box; they
are **unmeasured guesses** until the bff_code scout's timings exist.

| # | open question | evidence | BFF run (inputs exist?) | cost class |
|---|---|---|---|---|
| Q1 | **Is 0.8 the right ECC factor, and can the data tell?** Treat the net-charge scale as inferred (e.g. 0.70–0.90) instead of fixed | BFF p. 5 fixes 0.8; prosECCo75 uses 0.75; Košťál 2023 JPCL supports weaker scaling; Kirby & Jungwirth 2019 | Acetate trio with shipped references. Either (a) add the scale as a sampled parameter (target net charge = −s, C2 implicit), or (b) run 3–5 fixed-s fits and compare evidence / NMAE | New training MD: N samples × 3 systems × 40–100 ps; parallel over containers. Medium |
| Q2 | **How much do the charges and their uncertainties depend on the noise model?** Heteroscedastic per-bin RDF noise, a correlated RDF↔H-bond term, a vs b scale | BFF L1 (p. 4); Sullivan 2025 JPCB (ref 22) gives the covariance-aware alternative | Re-run `fit-lgp`/`learn` on the same `qoi/*.pt`, swapping the likelihood. Report shifts of the posterior modes and 95 % CIs, and NMAE of the predictive | **No new MD**: surrogate + MCMC only. Cheapest; best demo-per-dollar |
| Q3 | **Joint charges + LJ** (L2): does adding O/Ca²⁺ σ, ε move the charges, and does it fix the troponin underbinding seen with the acetate MAP? | BFF p. 9 "exploratory LJ"; Melcr 2020 needed σ×0.89 alongside ×0.75; Fan 2025 tunes ion LJ on ECC water | Acetate–Ca²⁺ trio, 4 charges + 2–4 LJ (O_carboxyl, Ca²⁺ or the pair) ≈ 6–8 dims, shipped references | New training MD at larger N; Medium–High |
| Q4 | **Scaling wall** (L3): posterior quality vs training-set size N and dimension D | BFF p. 10 "up to ~30 … future work" | Learning curve on one fragment: N ∈ {20, 50, 100, 200, …}, held-out LGP error and posterior KL/CI width vs the largest N. Can reuse one large MD set by sub-sampling | One MD set, many cheap fits. Medium; strong scientific value |
| Q5 | **Experimental likelihood** (L4): fuse an experimental scalar with the structural reference | BFF p. 10; Raman binding constants for Ca²⁺–acetate (Mendes de Oliveira 2020); densities (BFF Fig. 3c refs 41–44); NDIS data for TMA–acetate (Le Nguyen 2025) | Simplest: `arbitrary-data`-style scalar QoI (solution density at given molality, from short NpT MD per training sample) added to the acetate likelihood. A binding constant needs PMFs per sample, too expensive inside the loop | Density QoI: + one NpT box per sample. Medium. NDIS: needs data access (not checked public) |
| Q6 | **Posterior propagation to an observable** (L6): ensemble prediction with error bars instead of a MAP | BFF Fig. 5 (troponin spread up to 10 kJ/mol); Škorňa … Shanks 2026 arXiv (hierarchical GP PMF UQ); Raman Ca²⁺–acetate K | Draw ~5–10 posterior charge sets → short umbrella PMF of Ca²⁺–acetate (C2–Ca 0.25–0.8 nm) per set → posterior-predictive ΔG_contact with a credible band vs prosECCo75 / CHARMM36 charges | 5–10 sets × ~15 windows × ~0.5–1 ns of a 400-atom box, embarrassingly parallel. Medium |
| Q7 | **Which charge to refine next** (L7): optimal design / active learning | BFF p. 7 | From an acetate posterior, rank parameters by marginal width and by expected information gain of each QoI (drop one QoI, re-learn). Reports which observable constrains which atom | Surrogate + MCMC only. Cheap |
| Q8 | **Water–ion co-optimization in ECC** | Fan 2025 abstract ("simultaneously the water and ion models"); Cruces Chamorro 2024/2025 (wide good region, parameter correlations) | `arbitrary-data` mode over 4-site water + one ion LJ, with targets density, ΔH_vap, ε (≈45 for ECC), ion hydration (first-peak RDF position or CN from neutron/AIMD) | ε needs ≥ns per sample → expensive; a demo needs pre-tabulated runs. High |
| Q9 | **Noble-gas / Mie baseline** | Shanks 2024 JCTC, 2024 JPCL, 2025 JCP | Shipped `neon-mie-lgpmd` end to end | No MD; minutes. Low novelty, good smoke test for the image |

Not runnable in budget (leave as cited context): troponin double decoupling (100 ns windows, BFF p. 6); bilayer and
monolayer Ca²⁺ binding; any new AIMD reference.

## 5. Ranking for the ideate step (scout's view)

1. **Q2 + Q7 (likelihood ablation and information attribution on acetate)**
   - Uses only shipped references plus one training set, and needs no CP2K.
   - Re-fits take minutes once `qoi/*.pt` exist, and answer a limitation the paper names (L1, L5, L7).
   - Demo: the corner plots visibly change, and you can say which atom each observable pins.
   - Risk: we must generate the training set ourselves. The paper's "several thousand" samples are far beyond the
     budget, so use 100–300 short samples and state it.
2. **Q1 (infer the ECC scale factor)**
   - Directly tests a group assumption, the 0.75 vs 0.8 debate (Košťál 2023, prosECCo75, BFF).
   - Outcome is a posterior on *s* or an evidence curve, a clear number for a voice agent.
   - Needs new training MD at several *s* (or *s* as a parameter); same references.
   - Risk: *s* and the charge distribution may be degenerate under RDF-only QoIs. The contact/solvent-shared Ca²⁺
     distance QoIs are what should break the degeneracy; check that.
3. **Q6 (posterior → Ca²⁺–acetate contact-pair ΔG with credible band)**
   - Ties BFF to the Raman binding data (doi:10.1039/d0cp02987d) and to the new hierarchical-GP free-energy UQ
     (arXiv:2607.22338).
   - Can start from the published acetate posterior if available. Check the SI or repo; I did not find posterior
     files in the repo tree.
4. Q4 (scaling curve): strong science, weaker demo. Q3 (charges + LJ): the most "next paper", but the budget is
   tight.

## 6. Guardrails (from `.reference/bff/paper_brief.txt` and this read)

- Do not invent per-fragment charge tables or name fragments not in the text.
  - Named in the text: acetate, guanidinium, ethylammonium, tetramethylammonium, hydrogen and dihydrogen phosphate,
    formate, ethanol, ammonium, N-acetamide, sulfate. The full 18 are only in Fig. 3a, an image.
  - Charge values quoted in text only: carboxylate O −0.65 to −0.55e, sulfate O ≈ −0.4e, ether O −0.3 to −0.1e,
    posterior means −0.8 to +0.9e.
- Keep the GP nugget σ_k² and the likelihood nuisance n_k distinct.
  - Prior: normal centred at the bound midpoint, SD = range/5.
  - Nuisance: log n_k ~ N(−2, 2).
- Troponin stage I uses 100 ns windows and stage V 10 ns (p. 6); do not rename the stages.
- Results from our shortened training sets are **not** reproductions of the paper's numbers. Report them as a
  scaled-down protocol.
