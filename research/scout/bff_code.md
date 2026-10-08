# Scout: BayesicForceFields (BFF) code, what Macrae can run on Modal in < 20 min

Date: 2026-10-08. Scout box: shared Linux container, 16 vCPU, AVX2, no GPU, ~19 GB free RAM, host load avg
11–16 from other tenants (so absolute MD timings are noisy and pessimistic). Modal itself was **not** tested (no
Modal credentials in this container); everything below ran locally with the same commands a Modal sandbox would use.

## 1. Sources

| what | where |
|---|---|
| Code | https://github.com/vojtechkostal/BayesicForceFields, HEAD `24b6b40` (2026-10-02) = release **v0.4.2**, GPL-3.0 |
| PyPI | `bfflearn==0.4.2` (CLI `bff`, import `bff`) |
| Paper | Košťál, Shanks, Jungwirth, Martinez-Seara, *J. Chem. Theory Comput.* 22 (5) 2652–2663 (2026), doi:10.1021/acs.jctc.5c02051; preprint arXiv:2511.05398 |
| Paper code snapshot | git tag `v0.0.1` (old CLI: `bff initialize/runsims/analyze/learn`, emcee sampler). Docs say to use it for exact reproduction; the current package is a refactor |
| Surrogate method origin | Shanks, Sullivan, Shazed, Hoepfner, *JCTC* (2024), doi:10.1021/acs.jctc.3c01358 (LGPMD, https://github.com/hoepfnergroup/LGPMD) |
| Docs | https://vojtechkostal.github.io/BayesicForceFields/ (sources in `docs/`) |

Paper facts (from arXiv HTML): reference data come from CP2K 9.1 revPBE-D3/TZV2P AIMD at 300 K with 0.5 fs steps. The
classical boxes are ≈1.6 nm with 128 TIP4P/2005 waters. QoIs are solute–water-O RDFs, H-bond counts (3.5 Å, 150°) and
restrained ion–solute distance histograms. The total charge is scaled by 0.8 (ECC). 18 fragments were fitted (acetate,
N-methylacetamide, guanidinium, ethylammonium, phosphates, sulfate, …), followed by a Ca²⁺–troponin application. Up to
5000 (acetate) and 10000 (NMA) FFMD samples were used. MCMC was emcee with 5×dim walkers. No compute timings are
reported.

## 2. The pipeline in the code (v0.4.2)

```
build -> label-snapshots -> [external MLIP training + reference MD, NOT in BFF]
      -> sample-parameters -> build-qoi-datasets -> fit-lgp -> learn -> validate
```

| stage (CLI) | code | external dep | output |
|---|---|---|---|
| `bff build` | `bff/workflows/build/main.py`, `bff/topology.py` | GROMACS `gmx` (+Colvars or PLUMED for biased systems) | `systems/<id>/` + virtual-site-free `reference/{topology.top,coordinates.gro}` |
| `bff label-snapshots` | `bff/workflows/label_snapshots/main.py`, `bff/io/cp2k.py` | CP2K (`cp2k.psmp`), xTB/revPBE(0)-D3 inputs in `examples/acetate/inputs/reference-inputs/` | `train.extxyz`, `test.extxyz` for an MLIP |
| reference MD | **not in BFF**. The repo says "BFF deliberately does not train or run an MLIP" | user's MLIP | `02-reference-md/trajectories/<id>/trajectory.xtc` |
| `bff sample-parameters` | `bff/workflows/_shared/campaign.py`, `bff/workflows/md/main.py` (internal `bff md`) | GROMACS | `specs.yaml`, `samples.yaml`, `samples/<sid>/<id>/production.xtc` |
| `bff build-qoi-datasets` | `bff/qoi/{rdf,hbonds,routines,data}.py` | MDAnalysis | `qoi/<name>.pt` (`QoIDataset`) |
| `bff fit-lgp` | `bff/bayes/gaussian_process.py` (`LocalGaussianProcess`, `LGPCommittee`), `learning.py:fit_lgp_committee` | torch | `models/<name>.lgp` |
| `bff learn` | `bff/bayes/learning.py:LearningProblem.learn`, `bff/mcmc/sampler.py` | torch | `outputs/posterior.pt`, `mcmc.ckpt`, `plots/*.pdf` |
| `bff validate` | `bff/workflows/validate/` | GROMACS | rerun campaign of posterior draws |

Inference internals:
- **Surrogate**: local GP with a squared-exponential kernel (`bff/bayes/kernels.py`). Hyperparameters come from MAP
  over the leave-one-out log-likelihood (`likelihoods.py:loo_log_likelihood`, Sundararajan & Keerthi 2001). The mean is
  a constant, a sigmoid for RDFs (`means.py`), or any user callable (neon example: a Mie PMF mean).
- **Likelihood** (`likelihoods.py:gaussian_log_likelihood_by_qoi`): per QoI,
  `-0.5·n_eff·MSE/σ² − n_eff·log σ`. Here σ is either fixed (`nuisance`) or learned as `log_sigma_<qoi>`. `n_eff`
  comes from `effective_observations.py:estimate_curve_n_eff` (prominence-based peak count of the reference curve,
  `tolerance` set in the config) or `independent_observations: true`.
- **Constraints**: charge neutrality/target via `bff/domain/specs.py:ChargeConstraint`, with one "implicit" charge
  reconstructed.
- **MCMC**: an in-house torch Metropolis–Hastings sampler with an adaptive Gaussian proposal
  (`bff/mcmc/proposal.py`). It uses batched walkers and stops early when rank-normalized split-R̂ < `rhat_tol` and
  ESS > `ess_min` (`bff/mcmc/convergence.py`). This differs from the paper's emcee.
- **Parameters**: charges, LJ σ/ε, and function-9 dihedral force constants, all on GROMACS topologies.

Dependencies: `pyproject.toml` lists gmxtopology, MDAnalysis, numpy, scipy, matplotlib, PyYAML and typer. **PyTorch is
deliberately not a dependency**; install it yourself, CPU or CUDA. GROMACS is required for build/sample/validate. CP2K is
needed only for label-snapshots and PLUMED only for PLUMED biases. There is no OpenMM, no xtb Python dependency, and no
MLIP code.

## 3. Example inputs shipped

| example | needs MD? | content |
|---|---|---|
| `examples/arbitrary-data/` | no | 36 synthetic (ε_O, σ_O) → density/ΔH_vap/D rows + targets; notebook |
| `examples/neon-mie-lgpmd/` | no | LGPMD neon data: 480 training + 160 test RDFs, experimental RDF at 42 K (pickles, BSD-3) |
| `examples/acetate/` | yes | 3 systems (acetate; acetate+Ca²⁺ contact, restrained; separated, restrained), 128 four-site ECC waters (`IW` virtual site), Ca charge 1.6 (ECC); configs 01–07 |
| `examples/acetate/inputs/reference-trajectories/pos-00{0,1,2}.xtc` | — | **not referenced by any config or doc.** Byte-identical (same git blobs) to `v0.0.1:examples/acetate/02-reference-trjs/`, the CP2K reference trajectories shipped with the paper snapshot. 391/392 atoms, already vsite-free and atom-order-matched to `reference/coordinates.gro`, ~1000 frames each |

`bff examples` (from the pip install) downloads the tree for the installed tag from GitHub in 1.3 s (8.2 MB).

## 4. What I ran: exact commands and timings

### 4.1 Install (cold, `--no-cache-dir`)
```bash
python3 -m venv /tmp/scout/venv2                                       # Python 3.12.3
pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu   # 35.3 s  (torch 2.14.1+cpu)
pip install --no-cache-dir bfflearn==0.4.2                              # 22.9 s
python -c "import bff, torch"                                           # 1.6 s
# GROMACS (conda-forge; Ubuntu apt has no Colvars-enabled build) via micromamba:
curl -sL https://micro.mamba.pm/api/micromamba/linux-64/latest -o mm.tar.bz2   # then extract bin/micromamba
micromamba create -y -p /opt/gmx -c conda-forge "gromacs=2026.3=nompi*"        # 7 s, 211 MB
/opt/gmx/bin/gmx --version   # 2026.3, mixed precision, AVX2_256, OpenMP, Colvars enabled, GPU: OpenCL
```
The venv is 1.3 GB with CPU torch. The resolved stack was numpy 2.5.3, scipy 1.18.1, MDAnalysis 2.10.0,
gmxtopology 0.2.0 and matplotlib 3.11.2. The repo test suite passes: `python -m pytest -q` → **222 passed in 9.3 s**.

Notebooks were run without Jupyter by a 25-line runner that execs the code cells in order inside `__main__` (like a
kernel) with the Agg backend, timing each cell. It lives at `/tmp/scout/scripts/run_nb.py`:
`python -I run_nb.py <notebook.ipynb> <workdir>`. Pickles in the neon example were audited with `pickletools`
beforehand; they reference only numpy/torch reconstructors.

### 4.2 No-MD examples (CPU)

| run | wall | result |
|---|---|---|
| arbitrary-data notebook, as shipped | **11.8 s** (fit-lgp 5.0 s, MCMC 1.3 s, plots 2.5 s) | SMAPE 0.21/0.07/0.52 %. Posterior **bimodal**: σ_O peaks at ≈0.28 and ≈0.36 nm, not near the 0.315 truth the notebook promises (see §5.1) |
| same, bounds clipped to training range | **8.5 s** | ε_O = 0.648 ± 0.006 kJ/mol, σ_O = 0.3149 ± 0.0006 nm (truth 0.65 / 0.315) ✔ |
| neon-mie-lgpmd notebook (10 000 MCMC steps, 200 hyperparameter restarts) | **31.2 s** (fit 6.4 s, MCMC 8.3 s, plots 13.5 s) | ε = 0.0556 ± 0.0100 kcal/mol, λ = 11.69 ± 1.71, σ = 2.794 ± 0.032 Å, RDF σ = 0.037. Split SMAPE 0.80 %, held-out SMAPE 0.27 %, n_eff(RDF) = 5.6. Final R̂ 1.013 > 1.01 tolerance (ran to max steps). The GP MAP search hit max iterations (lr 5e-5). Posterior-mean RDF overlays the experiment closely |

### 4.3 Acetate, full BFF-owned pipeline on CPU (label-snapshots/CP2K skipped; shipped AIMD trajectories used as the MLIP handoff)
```bash
bff examples && cd examples/acetate            # or copy from the clone
# thread-pinning wrapper used as gmx_cmd (see §5.2): /tmp/scout/scripts/gmx-nt
#   if [ "$1" = mdrun ]; then shift; exec gmx mdrun -ntmpi 1 -ntomp "${GMX_NT:-1}" -pin off "$@"; fi; exec gmx "$@"
export GMX_NT=4
mkdir 01-build && sed 's#command: gmx#command: /path/gmx-nt#' configs/01-build-colvars.yaml > 01-build/config.yaml
(cd 01-build && bff build config.yaml)
for i in 0 1 2; do s=(acetate acetate-contact acetate-separated); mkdir -p 02-reference-md/trajectories/${s[$i]}
  ln -s ../../../inputs/reference-trajectories/pos-00$i.xtc 02-reference-md/trajectories/${s[$i]}/trajectory.xtc; done
mkdir 03-sample && sed 's#^gmx_cmd: gmx#gmx_cmd: /path/gmx-nt#' configs/03-sample-local.yaml > 03-sample/config.yaml
(cd 03-sample && bff sample-parameters config.yaml)
mkdir 04-qoi && cp configs/04-build-qoi-datasets.yaml 04-qoi/config.yaml && (cd 04-qoi && bff build-qoi-datasets config.yaml)
mkdir 05-lgp && sed 's/device: cuda/device: cpu/' configs/05-fit-lgp.yaml > 05-lgp/config.yaml && (cd 05-lgp && bff fit-lgp config.yaml)
mkdir 06-learn && python -c "import yaml;c=yaml.safe_load(open('configs/06-learn.yaml'));c['plots'].pop('plot_metadata');c['mcmc']['device']='cpu';yaml.safe_dump(c,open('06-learn/config.yaml','w'),sort_keys=False)"
(cd 06-learn && bff learn config.yaml)
mkdir 07-validate && sed 's#^gmx_cmd: gmx#gmx_cmd: /path/gmx-nt#' configs/07-validate.yaml > 07-validate/config.yaml
(cd 07-validate && bff validate config.yaml)
```

| stage | work | wall | notes |
|---|---|---|---|
| 01 build | EM + 10 000-step (20 ps) seed run × 3 systems (~520 atoms incl. vsites) | **8.8 s** with `-ntomp 4`; **3 m 39 s** with BFF's default `gmx mdrun` (16 OpenMP threads, 30 ns/day) | ~1100 ns/day per run when pinned |
| 03 sample-parameters | 20 charge samples × 3 systems × 20 000 steps (40 ps) = 2.4 ns, sequential | **3 m 49 s** | 4.8 MB of xtc |
| 04 build-qoi-datasets | RDF (200 bins), H-bonds, 2 custom distance histograms | **8.7 s** | 16 workers |
| 05 fit-lgp | 4 LGPs, committee 1, 200 restarts, CPU | **19.8 s** | MAPE: rdf 10.2 %, hb 3.2 %, contact-dist 52.6 %, separated-dist 55.1 % |
| 06 learn | 35 walkers, stopped converged at step 4000/10000 (max R̂ 1.042 < 1.05) | **19.5 s** (fails at plotting with the shipped config, §5.3) | n_eff: rdf 35.1, hb 3, contact 10.0, separated 11.3 |
| 07 validate | 10 posterior draws + mean × 3 systems × 1000 steps | **43.7 s** | |
| **total** | | **≈ 5.5 min** | fits easily on one Modal CPU box, with no GPU needed |

Acetate posterior (ECC total −0.8): q(O1,O2) = −0.513 ± 0.029, q(C1) = −0.270 ± 0.131, q(H) = 0.035 ± 0.029, implied
q(C2) = 0.393 ± 0.122. With only 20 samples × 40 ps this is a demo, not production; the paper used thousands of
samples.

mdrun thread benchmark on the acetate `production.tpr` (3000 steps): 1 thread 595 ns/day, 2 → 882, 4 → 1132,
8 → 1239. BFF's default (16 threads) ran at 30–95 ns/day.

## 5. Problems found (each reproducible; worth upstream issues/PRs, and good "agent finds a real bug" stories)

1. **Arbitrary-data example yields a spurious bimodal posterior.** The training rows span σ_O 0.3074–0.3228 nm and
   ε_O 0.5823–0.7163, but `Specs` bounds are σ_O [0.25, 0.38] and ε_O [0.58, 0.718]. Away from the data the
   GP reverts to its constant mean, which is the training-output average (≈ target values). The likelihood is therefore
   flat-high there, and MCMC piles mass at σ ≈ 0.28 and 0.36 nm. Clipping bounds to the data hull restores
   0.315 ± 0.0006. Nothing in `LearningProblem` warns when bounds exceed the training support.
2. **mdrun threading.** Neither `bff build` nor `bff md` passes `-nt/-ntomp`
   (`bff/workflows/_shared/preparation.py:172`, `bff/workflows/md/main.py:303`). `gmx_cmd` is split and prefixed, so
   flags can't be appended after `mdrun` from the config. On these ~500-atom boxes, the default thread count is
   12–40× slower on a shared/loaded box. Workaround: the `gmx-nt` wrapper above. The local scheduler also runs samples
   strictly sequentially (`campaign.py:592`, `subprocess.run` per sample); parallel local dispatch has no public
   re-collect step.
3. **`examples/acetate/configs/06-learn.yaml` crashes after sampling.** `plots.plot_metadata` defines `define VSA` /
   `define VSD`, which are not acetate parameters. `bff learn` exits 2 with "plot_metadata contains unknown
   parameter(s)" (`bff/plotting.py:289`) after writing the posterior. `tests/test_examples.py:82` asserts these keys
   exist but never runs plotting against the acetate specs.
4. **Separated-pair restraint no longer matches the shipped reference trajectory.** `inputs/biases/colvars-002.dat`
   and `plumed-002.dat` centre C2–Ca at 0.55 nm. The v0.0.1 config used `x0: 0.50`, and `pos-002.xtc` sits at
   5.02 Å (5–95 %: 4.85–5.19). The classical samples sit at 5.50 Å (5.25–5.78), so the `separated-distance` QoI
   compares non-overlapping histograms whatever the charges are. Use centre 0.50 nm with these trajectories.
5. **The learn log is misleading at early stop.** The last printed line shows R̂ 1.09 > tolerance, then "Done". The
   converging checkpoint (step 4000, R̂ 1.042) isn't printed; it is only visible in `outputs/mcmc.ckpt` /
   `posterior.pt` metadata (`converged: True`). Also, the neon summary key reads `log_sigma_rdf` but holds σ (0.037).

## 6. Concrete Macrae task candidates (all < 20 min on one Modal CPU box; GPU not needed)

| id | what the agent does | runtime measured | traceable calc events |
|---|---|---|---|
| A. `bff-acetate-ecc` | Full acetate pipeline (§4.3) with the shipped AIMD refs and the 0.50 nm fix. Report ECC charges and posterior widths; cite JCTC 2026 + ECC papers | ≈ 5.5 min | 6 `bff` stages, each a `calc` |
| B. `bff-surrogate-support` | Run the arbitrary-data example twice (shipped bounds vs. data-hull bounds) and explain why the surrogate creates fake modes | 20 s | 2 runs + corner plots |
| C. `bff-neon-mie` | Infer Mie ε, λ, σ from experimental neon RDF; plot fit; compare with the LGPMD paper | 31 s | fit + MCMC |
| D. `bff-qoi-ablation` | From one acetate campaign (stages 01–05 cached), rerun `learn` with RDF only / +HB / +distances, then compare posteriors and QoI-attributed marginals | ~20 s per learn | 3–4 learn runs |
| E. `bff-neff-sensitivity` | Vary `tolerance` (n_eff) for the RDF in `06-learn.yaml`: 0.3/0.1/0.03 → posterior width | ~20 s each | |
| F. `bff-sample-budget` | `n_samples` 10/20/40 in 03-sample → surrogate MAPE and posterior width vs. MD cost | ~2/4/8 min of MD | |

Modal image recipe (untested on Modal): ubuntu:24.04 base (the agent image) + micromamba → conda-forge
`gromacs=2026.3=nompi*` (7 s, Colvars built in) + `pip install torch --index-url …/cpu` + `bfflearn==0.4.2` (~60 s
cold). Bake these into `agent_runner/image/Dockerfile` or a task-specific image so Modal caches them. 8 vCPU is enough.
Run 2–4 mdrun threads per job and pin threads via the wrapper. Out of scope for < 20 min: `label-snapshots` (CP2K
AIMD, "weeks on a cluster" per the v0.0.1 README) and any MLIP training/MD.
