# Scout: bff.think2earn.com and the owner's BFF guides

Scout: `bff_site`. Fetched and checked 2026-10-08 (UTC). Every number below comes from the cited file or URL, or from a
command run here. Numbers checked or computed by this scout are marked **[checked]**.

## 0. Bottom line

1. **https://bff.think2earn.com/ is not the JCTC paper.** It is a one-page "Working Draft / Preprint" (author list:
   "BFF Optimization Working Group"). It writes up **PR 18** on the BFF repo: a Modal fan-out of GROMACS, an FPCA
   surrogate, "PlausibilityGateV2", a Jensen log-correction, MBAR reweighting and NUTS. It shows **one acetate campaign**:
   2 charges, 40 + 10 runs of 200 ps, "181.8 s", "< $1". The run itself is not exposed: no logs, data, trajectories or
   code endpoint. Every path on the site returns the same HTML.
2. **The public code cannot reproduce the site's Modal run.** PR 18 (`modal_engine.py`) imports
   `new_optimizations.modal_bff_campaign`, which does not exist. It also never puts the charge vector into the job
   items. The PR contains no active-learning loop, MBAR or NUTS code **[checked: read source at head
   `90a13f18`]**. The primer (`.reference/bff/bff-primer.html`, section "Paper, current code, and pull request 18")
   reached the same conclusion independently.
3. **The site's figures contradict each other and the text** (§2.3). Do not reuse its numbers as ground truth, and do
   not cite it as group work: it is not in `data/group_publications.json`.
4. **Most of the reusable value is in the owner's guides:**
   - `paper_brief.txt`: a vetted fact sheet with "do not invent" rules, ready to use as agent context and for
     `check:` rules.
   - Protocol cards with exact GROMACS/CP2K settings.
   - Formulas for NMAE, the likelihood and the priors.
   - The primer's "first intern project" (a better noise model plus a calibration test on synthetic data). This is the
     best-sized BFF research task for a run of under 20 minutes.
   - Self-contained JS widgets that could animate a run in the macrae web view: GP, stretch-move walkers, NMAE, thermo
     cycle.
5. **Reusable code from PR 18:** `FPCASurrogate` and `PlausibilityGateV2` run standalone with numpy and scikit-learn.
   A toy campaign (synthetic 2-charge RDFs, FPCA fit, emcee posterior, gate) took **47 s on a 16-core CPU** (§5).
   `ModalGromacsBackend` is not usable.

## 1. Sources

| source | what | id |
|---|---|---|
| https://bff.think2earn.com/ | one static HTML page (29,823 B, KaTeX from jsDelivr), served by Cloudflare. All other paths (`/robots.txt`, `/paper_figures/`, `/data/`, `/api/`, `/manifest.json`, `/paper.pdf`, …) return the same page with HTTP 200 (SPA fallback) **[checked]** | fetched 2026-10-08 18:34 UTC |
| `https://bff.think2earn.com/paper_figures/fig{1..8}_*.png` | 8 PNGs, 3254–3870 px wide, 268–403 kB, each downloaded in 0.16–0.99 s | sha256 prefixes: fig1 `be257cbb`, fig2 `4e8fa2ad`, fig3 `d74b475a`, fig4 `64c93a5c`, fig5 `a5c43fa8`, fig6 `ef09aeee`, fig7 `52b16853`, fig8 `b8ff041e` |
| https://github.com/vojtechkostal/BayesicForceFields/pull/18 | "feat(bayes): Functional PCA spatial surrogates, PlausibilityGateV2, and serverless high-concurrency execution". Opened 2026-08-26 by `rainbowpuffpuff`; open, not merged; +376/−0 lines, 7 files. Its body links to the site | head `rainbowpuffpuff/BayesicForceFields@90a13f184fd1e42c676eb2df6a09e7d3b58e61b3`, base `ffac83cd` |
| Paper | Košťál, Shanks, Jungwirth, Martinez-Seara, *J. Chem. Theory Comput.* 2026, 22, 2652–2663 | doi:10.1021/acs.jctc.5c02051 (PDF: `.reference/bff/Kostal2026BayesianLearningAccurate.pdf`) |
| `.reference/bff/paper_brief.txt` | 5.8 kB fact sheet: method, protocols, results, "do not" rules | — |
| `.reference/bff/bff-primer.html` | 255 kB interview-prep primer: paper, code v0.4.1 vs v0.0.1, PR 18, likelihood deep dive, toy widgets, quiz, changelog dated 2026-09-29 | links: repo, docs https://vojtechkostal.github.io/BayesicForceFields/, PR 18, the site |
| `.reference/bff/guide-seminar.html` | 122 kB "Force fields with a posterior", a twelve-room study guide with 10 canvases and protocol cards | loads `questions-hard.js` and links `variant-2-exhibits.html`; neither is in `.reference/bff/`, and on the site both return the fallback page |

## 2. What bff.think2earn.com shows

### 2.1 Claimed pipeline (their Fig. 1, file `fig1_architecture.png`)
1. Space-filling exploration: N₁ = 40–100 runs of 200 ps.
2. Modal fan-out: "40 parallel workers", "~300–600 ns/day".
3. FPCA: SVD of RDFs, "88.2 % var (r = 5)", extracted with MDAnalysis.
4. Jensen-corrected log-GP: GPyTorch Matérn-5/2.
5. PlausibilityGateV2, `|μ(θ)−μ₀| + κσ(θ) ≤ τσ₀`. It either evaluates MBAR `C_ij` "in 0.0015 s" or dispatches
   stage-2 MD (N₂ = 10–20).

PR 18's `modal_engine.py` hard-codes `n_steps=100000` and `dt_ps=0.002`, which is 200 ps. That matches the figure.

### 2.2 Systems and the "run" it reports
- **Acetate:** 1 acetate + 128 TIP4P waters, 519 atoms. θ = [q(C1), q(O1,O2)], with the methyl C2 implicit:
  `q(C2) = −1.07e − q(C1) − 2q(O)`, bounded to [+0.30, +0.90] e. Priors: q(C1) ∈ [−0.50, +0.10], q(O) ∈ [−0.90, −0.40].
- **Phenol:** 537 atoms, 12 out-of-plane virtual sites, θ ∈ ℝ⁶. The site shows no phenol result. Phenol numbers
  appear only in the PR 18 body: "N=40 … within 0.14 % of N=1,500 grid, 3.2 min", against a "MACE reference".
- **Campaign:** 40 stage-1 + 10 stage-2 trajectories in **181.8 s**. Posterior: q(C1) = −0.2223 ± 0.1714 e
  (95 % CI [−0.4865, +0.0810]); q(O) = −0.6581 ± 0.1315 e (CI [−0.8851, −0.4163]); q(C2) = +0.7385 e.
- **Gate:** 3,007 candidates, 495 admitted (16.46 %).
- **Fisher "stiff/sloppy" eigenvalues:** 0.0294 and 0.0172 (ratio 1.71).
- **Timing comparison:** 5.0 h workstation, 2.5 h Slurm, 3.0 min Modal, labelled "250×". Also "under $1.00", "0.0 %
  unphysical waste" and a "SHA-256 provenance audit 6/6".

Figure-to-caption map: the PNG names and the in-image titles do not match the HTML numbering.

| HTML caption | file | title inside PNG |
|---|---|---|
| Fig 1 architecture | fig1_architecture | Figure 1 |
| Fig 2 FPCA | fig3_surrogate_accuracy | Figure 3 |
| Fig 3 gate | fig2_phase_space | Figure 2 |
| Fig 4 acetate on Modal | fig7_acetate_generality | Figure 7 |
| Fig 5 posteriors and Fisher | fig6_posterior_distributions | Figure 6 |
| Fig 6 speed | fig4_convergence_efficiency | Figure 4 |
| Fig 7 ablation | fig5_ablation_and_sensitivity | Figure 5 |
| Fig 8 MBAR and audit | fig8_future_directions | Figure 8 |

### 2.3 Consistency problems (all [checked] from the page and PNGs)
- **Implicit charge does not add up.** −1.07 − (−0.2223) − 2(−0.6581) = **+0.4685 e**, not the stated +0.7385 e. The
  −1.07 target is also unexplained. It is consistent with total −0.8 (ECC 0.8) only if the three methyl H carry
  +0.09 e each, which the site never states. The primer calls it "the ion stays at −1".
- **The posterior is the prior.** The 95 % CIs cover 95 % (C1) and 94 % (O) of the prior box. The posterior SDs (0.171,
  0.132) are wider than the paper's prior SD, which is range/5 = 0.12 and 0.10. In Fig 6 (`fig6_posterior_distributions`)
  both densities are truncated Gaussians cut off by the bounds. So the 50-run campaign learned almost nothing. Compare
  the paper's acetate oxygen: about −0.57 e, plausible range about −0.65 to −0.50 (primer, Fig. 5 discussion).
- **The gate figure contradicts the posterior.** The gate's admitted region (fig2 panel b) is about q(O) ∈ [−0.75, −0.57]
  and q(C1) ∈ [−0.33, −0.10]. That is much tighter than the "converged posterior" in Fig 6.
- **The RDFs look synthetic.** In fig3 and fig7, "MD Trajectory Truth" g(r) equals **1.0 at r = 0.5–1 Å** (the core
  should be 0). The surrogate and the "truth" overlap perfectly, and the curves are smooth Gaussian bumps.
- **The FPCA variances don't add up.** Fig 3a lists modes 1–3 at 64.2 + 15.8 + 8.2 = **88.2 %**, which is already the
  total quoted for **5** modes. That leaves 0 % for modes 4–5.
- **The speedup is not 250×.** 300 min / 3.03 min = **99×**, and 150 / 3.03 = **50×**.
- **Other mismatches:**
  - Fig 7 says "5,000 runs" for the baseline; PR 18 says a 1,500-point grid; the paper says "several thousand".
  - The ablation uses an "N = 120 budget" while the campaign is N = 50.
  - The audit panel says "6/6 suites" but draws 5 bars.
  - The header reads "Article • Final Production Version" but also "Working Draft / Preprint".
- **Ref. [4] cites the paper as "2025, 22, 2652–2676".** It is 2026, 22, 2652–2663.
- **The method is described wrongly.** PR text and site say "200 RDF bins → 200 GPs, 200 hyperparameter fits, 97.5 %
  cut". In the paper and in v0.4.1 the bins share one kernel per QoI, so there is one hyperparameter set per QoI
  (primer; paper Eq. 1–2).

### 2.4 What PR 18 code actually contains (read at `90a13f18`)
| file | lines | does |
|---|---|---|
| `bff/bayes/fpca_surrogate.py` | 113 | SVD of centred curves, then M = 5 scikit-learn `GaussianProcessRegressor` fits (Matérn ν = 2.5, ARD, White noise, 3 restarts). `predict(return_std=True)` propagates mode variance to bins. No GPyTorch. Not called by `bff learn`. |
| `bff/bayes/plausibility_v2.py` | 68 | `score = (|μ−μ₀| + κσ)/(τσ₀)`, accept if ≤ 1. Multi-QoI uses the **max over columns**. Defaults κ = 2, τ = 3. `Tuple` is used without being imported (harmless because of `from __future__ import annotations`). |
| `bff/bayes/jensen_correction.py` | 47 | `σ²_ln = log1p(var/(n_eff·O²))` with `n_eff = n_frames/(2τ+1)`, and `E[O] = exp(μ + σ²/2)` |
| `bff/backends/modal_engine.py` | 63 | Builds items `{sample_id, top/gro/mdp text, n_steps, dt}`, **without the parameters**, and calls `run_gromacs_simulation.map` from a missing module |
| `tests/bayes/test_fpca_and_plausibility.py` | 76 | 3 tests on synthetic arrays |

Missing from the PR: active-learning acquisition, MBAR/`C_ij`, NUTS/NumPyro, the SHA-256 audit, and the scripts that
make the figures.

## 3. What the owner's guides give (and which paper experiments they reproduce)

Neither guide re-runs anything. Both state that they are "teaching reconstructions": no digitized values, no invented
charge tables. They cover the following experiments and figures.

| paper item | guide coverage | numbers to use (from the paper, via the guides) |
|---|---|---|
| Fig 1 workflow (a data, b surrogate, c inference, d validation) | seminar fig. 3 (4-act animation), primer "method, stage by stage" | LHS of several thousand θ; 3 setups (solute in water, contact ion, solvent-shared ion); QoIs: RDF to water O, H-bonds (< 3.5 Å, > 150°), ion distance density |
| Eq. 1–2 local GP | seminar fig. 7 (editable 1-D GP, JS `cholesky`/`gpPosterior`), primer "equations" | SE kernel, one length scale per charge; sigmoid baseline x₀ = 3 Å, a = 5 Å⁻¹; LOO marginal likelihood, 80/20 split; hyperparameters frozen |
| Eq. 6–7 posterior and likelihood | primer toy posterior (canvas `demoCanvas`), 2-QoI tug-of-war widget, seminar fig. 8 (prior × likelihood), fig. 9 (real stretch move, a = 2) | prior N(mid, range/5) truncated; `log n_k ~ N(−2, 2)`; n_obs = 1 per QoI; emcee walkers = 5 × dim, ≤ 1e5 iterations, ≥ 100τ, burn-in 2τ_max, thin 0.5τ_min |
| Eq. 8 NMAE, Fig 3b | seminar fig. 10 (drag a curve, live NMAE) | RDF < ~5 %, H-bond 10–20 %, ion distance < ~20 %; better than CHARMM36-nbfix for nearly all species |
| Fig 3c densities | seminar §7 text | 4.5 nm box, 2000 waters, 200 ps NpT + 1.2 ns, 1.2 nm cutoff; within ~1 % up to ~50 % mass |
| Fig 4 posterior chemistry | seminar figs. 12–13 (18 fragments, functional-group ranges) | span −0.8 to +0.9 e (CHARMM36-nbfix −1.0 to +1.5); COO⁻/PO₄ terminal O −0.65 to −0.55; sulfate O ~−0.4; ether O −0.3 to −0.1 |
| Fig 2 + Fig 5 troponin ΔG | seminar fig. 14 (5-stage cycle animation), fig. 15 (ΔG chooser) | expt −28.6; C36 −105.7; nbfix −31.7; prosECCo75 −13.5 kJ/mol; MAP underbinds by ~6; best draws within ~1; dipole 2.7–4.4 D best (C36 8.9 D, prosECCo75 6.6 D); 21 λ windows (11 elec + 10 vdW); `ΔG_restr = −RT ln(V/V°)`, V° = 1.661 nm³; PME term with ζ = −2.837, ε = 80 |

Protocol cards (seminar §4 and §9). These are the exact settings a macrae MD step should cite or down-scale:
- **AIMD reference:** CP2K 9.1, revPBE-D3 (D3 off for Ca²⁺ pairs), TZV2P, GTH, 400 Ry, NVT 300 K, 5 ps Langevin
  (friction 0.02) then CSVR τ = 1 ps, 0.5 fs.
- **Classical training:** GROMACS 2024.3, CHARMM-GUI topologies, ~1.6 nm cube, 128 TIP4P/2005. Steepest descent, then
  10 ns NpT (drop 1 ns), then 1 ns NVT. C-rescale 1 ps, compressibility 4.5e-5 bar⁻¹, 300 K, v-rescale 1 ps, LINCS
  on H-bonds, 0.7 nm cutoff, PME with potential-shift.
- **Troponin:** PDB 1AP4, 5.25 nm cube, 150 mM KCl, 310 K, 1.2 nm cutoff, ECC water and ions. 100 ns windows for
  stage I and 10 ns for stage V (as worded in the methods; "do not rename stage I").

Code facts from the primer (repo at v0.4.1, released 2026-08-25; the paper is tag v0.0.1). For the `bff_code` scout to confirm:
- `RandomParamsGenerator` in `bff/domain/specs.py`.
- `LocalGaussianProcess` in `bff/bayes/gaussian_process.py`.
- `gaussian_log_likelihood_by_qoi` and `loo_log_likelihood` in `bff/bayes/likelihoods.py`.
- `estimate_curve_n_eff` in `bff/bayes/effective_observations.py`: Savitzky–Golay window 15, prominence ≥ 5 × noise,
  `max(1, ln(prom/tol))`.
- n_eff modes in the learn YAML: `tolerance:`, `n_eff:` or `independent_observations: true`.
- `plot_qoi_marginals` in `bff/plotting.py`.
- Own PyTorch sampler with rank-normalized split-R̂.
- Worked examples: acetate (full workflow), tabular-data notebook, neon Mie from RDFs.
- Primer's own claim: n_eff ≈ 6.3 on a 0.1 Å grid vs ≈ 9.0 on 0.05 Å for the same made-up curve, because the window
  counts points, not Å.

## 4. What a "run" looks like on the site vs what macrae can trace

What the site shows of a run: only figures and summary numbers. There are no logs, per-run parameters, wall-times per
job, trajectories, seeds or code. The "SHA-256 provenance audit" is a bar chart of 100 % values.

A Harbor trial in macrae records each tool call (`agent/trajectory.json`, ATIF), files and reward (CONTRACT.md
"TraceEvent"). So the macrae version can show for real what the site only asserts. One stage of the site's Fig 1 maps
to one visible TraceEvent group:

| site stage | macrae step / TraceEvent | concrete content |
|---|---|---|
| read the method | `read` / `search` | RAG on doi:10.1021/acs.jctc.5c02051 plus `paper_brief.txt` as `context.md` |
| 1. space filling | `calc` | `scipy.stats.qmc.LatinHypercube` over bounded charges; implicit atom enforces the sum |
| 2. MD fan-out | `calc` (one row per batch) | GROMACS runs inside the Modal sandbox, several `gmx mdrun -nt k` in parallel. Not a nested Modal app: that would need a Modal token inside the sandbox |
| 3–4. surrogate | `calc` | BFF LGP (paper method) and optionally PR 18 `FPCASurrogate` as an ablation |
| 5. inference | `calc` | emcee or BFF sampler; R̂ / τ printed |
| validation | `calc` | fresh MD at ~3 posterior draws, NMAE (Eq. 8) |
| report | `write`, `cite`, `result` | `result.json` + posterior PNG + cited explanation; `check:` recomputes simple invariants |
| provenance | Harbor trace + `check:` | sha256 of topology, mdp, inputs and outputs written into `result.json`, then verified by the check script (real, unlike the site's chart) |

Budget sanity for the site's own campaign on Modal (pricing page fetched 2026-10-08: CPU $0.0000131 per physical core
per second, memory $0.00000222 per GiB per second): 50 jobs × 4 cores × ~60 s (the site's "Dispatch Cloud MD (60 s)",
i.e. 200 ps at ~300 ns/day) = 12,000 core-s, about **$0.16** CPU plus ~$0.01–0.03 memory. "< $1" is plausible. A
**200 ps** window is 1/55 of the paper's 11 ns per setup per θ, so reference noise and surrogate noise will be much
larger than in the paper. That has to be stated in any result.

## 5. Toy run done here (PR 18 pieces, synthetic data, no MD)

Script: `research/scout/bff_site_toy_pr18.py`. It loads the PR files by path; the synthetic RDF has a zero core and
peak height driven by q(O). It uses the site's prior bounds, the paper's priors (N(mid, range/5), log σ ~ N(−2, 2)),
Eq. 7 with n_obs = 1, and emcee with 15 walkers × 1000 iterations (burn-in 300). Truth θ = (−0.20, −0.62), reference
noise 0.02 per bin, 200 bins.

```bash
python3 -m venv /tmp/scoutvenv && /tmp/scoutvenv/bin/pip install -q numpy scipy scikit-learn emcee   # 21 s
mkdir -p /tmp/pr18_src && cd /tmp/pr18_src && for f in bff/bayes/fpca_surrogate.py bff/bayes/plausibility_v2.py \
  bff/bayes/jensen_correction.py bff/backends/modal_engine.py; do mkdir -p $(dirname $f); curl -sS -o $f \
  https://raw.githubusercontent.com/rainbowpuffpuff/BayesicForceFields/90a13f184fd1e42c676eb2df6a09e7d3b58e61b3/$f; done
cd /some/other/dir && /tmp/scoutvenv/bin/python -I bff_site_toy_pr18.py /tmp/pr18_src      # 47 s wall, 16 cores
```
Versions: numpy 2.5.3, scipy 1.18.1, scikit-learn 1.9.1, emcee 3.1.6, Python 3.12.3.

| N training | FPCA 5-mode var | FPCA fit | predict / point | emcee 15 × 1000 | posterior mean (C1, O) | posterior SD | gate accept (3009-pt grid, σ₀ = 0.02, κ = 2, τ = 3) |
|---|---|---|---|---|---|---|---|
| 40 | 98.9 % | 6.1 s | 0.04 ms | 10.0 s | (−0.201, −0.619) | (0.112, 0.043) | 0.0 % |
| 200 | 98.8 % | 17.4 s | 0.10 ms | 11.7 s | (−0.183, −0.625) | (0.108, 0.048) | 0.2 % |

What this shows:
- The surrogate and MCMC stages cost seconds on CPU, so the MD is the whole budget.
- The explained-variance figure (88 % vs 99 %) depends on the data and says nothing by itself.
- With max-over-bins scoring against a noisy 200-bin reference, the gate rejects almost everything at τ = 3. The site's
  16.46 % depends entirely on σ₀ and τ, and the site gives neither for its run.
- q(C1) is barely identified here by construction, which is what a weak QoI does to a parameter.

## 6. Reusable for macrae runs (concrete)

1. **`paper_brief.txt` as agent context and check rules.** Copy it into each BFF task's `context.md`. Turn its "do
   not" lines into `check:` assertions:
   - no per-molecule charge tables beyond the paper's ranges;
   - do not mix up the GP nugget σ_k² with the nuisance n_k;
   - do not rename stage I;
   - 0.8 ECC factor, neutrals stay neutral;
   - quote ΔG values exactly: −28.6 / −105.7 / −31.7 / −13.5 kJ/mol.
2. **Protocol cards → an mdp template** with the paper's settings (0.7 nm cutoff, PME potential-shift, C-rescale,
   v-rescale, LINCS h-bonds, TIP4P/2005, 128 waters) and a scaled-down length. The result must report the scale-down
   factor.
3. **Formulas for scripted checks:**
   - NMAE (Eq. 8);
   - the paper's Eq. 7 and the v0.4.1 MSE·n_eff form;
   - the implicit-atom sum (the check must recompute it; the site got it wrong);
   - for any free-energy task: `ΔG_restr = −RT ln(V/V°)` and the PME finite-size term.
4. **Web run view (web module).** The seminar's canvases are plain JS with no build step, functions `drawWalk`,
   `drawGp`/`gpPosterior`/`cholesky`, `drawNmae`, `drawCycle`, `drawWorkflow`, with seeded RNG `mulberry32`. They could
   animate a live run: stretch-move walkers replaying real posterior samples, the GP band filling in as training runs
   arrive, live NMAE.
   - The primer's toy-posterior canvas and its 2-QoI weighting widget are about 4–28 kB of script each.
   - These are owner-made files. Ask before publishing them, and label any replay of real data as such.
5. **Narrative for the voice agent.** Reuse the seminar's "model answers": the ten-sentence summary, why one charge
   vector is an awkward deliverable, the refusal list. Their honesty markers ("schematic", "a claim in the writeup",
   "not a test in the diff") are the right tone for narrating runs.
6. **PR 18 `FPCASurrogate` / `PlausibilityGateV2`.** Usable as a labelled *ablation* next to BFF's LGP. They are not the
   group's method and not merged. **Do not reuse** `ModalGromacsBackend`, the site's numbers, or its claim that the
   paper fits "200 GPs".
7. **Plotting conventions worth copying** (the content is not): one figure per stage (design scatter of θ by stage, RDF
   vs surrogate with band, posterior marginals with the prior overlaid, wall-time per stage). Add the **prior overlay**
   the site leaves out; it would have shown that its posterior is the prior.

## 7. Research seeds from this area (for the ideate step)

Each seed below needs no AIMD of its own: it uses a reference already in the BFF repo, synthetic data, or published
experiments. The `bff_code` scout must confirm that the repo's acetate example ships its reference QoIs.

- **S1. An honest version of the site's acetate campaign** (paper doi:10.1021/acs.jctc.5c02051).
  - Setup: 2–3 acetate charges; LHS N = 40–60; 100–200 ps GROMACS runs in the Modal sandbox (16–32 cores, ~8–12
    parallel `mdrun -nt 2..4`).
  - Inference: BFF LGP + emcee, prior overlaid; FPCA as an ablation.
  - Validation: 3 posterior draws re-simulated, NMAE against the repo's AIMD reference.
  - Output: posterior vs the paper's acetate (oxygen ~−0.57 e), and how much of the prior the short-run campaign
    actually rules out.
  - Directly answers "is a 3-minute Modal campaign informative?".
- **S2. The noise model and calibration study** (primer "first intern project"; ref. 22: Sullivan, Cervenka, Shanks,
  Hoepfner, J. Phys. Chem. B 2025).
  - Generate synthetic references from known θ using the surrogate or short MD.
  - Compare n_eff = 1, the peak count, and all grid points; optionally block-averaged heteroskedastic or Ledoit–Wolf
    covariance.
  - Measure 95 % coverage over ~50–100 repeats.
  - CPU-only, minutes. Output: a coverage table that a script can verify.
- **S3. The n_eff grid-spacing artifact.** Run `estimate_curve_n_eff` on the repo's acetate reference RDFs at 0.1 vs
  0.05 Å, and the window in points vs in Å. Report the change in the posterior width of q(O). Under 5 minutes; a good
  warm-up demo.
- **S4. Transfer check: density** (paper Fig 3c; group ECC papers doi:10.1021/acs.jpclett.4c00344 water models,
  doi:10.1021/acs.jctc.5c00873 ions, doi:10.1021/acs.jctc.4c00743 prosECCo75).
  - Sodium acetate solution density with MAP vs 2–3 posterior draws vs CHARMM36 charges, against experiment.
  - The paper's box is 2000 waters, 1.2 ns; that needs to be timed on Modal before committing, since it is uncertain
    to fit in 20 minutes on CPU, so plan for a GPU or a smaller box.
- **Out of scope for < 20 min:** the troponin double-decoupling (21 windows × 10–100 ns) and any new AIMD.

## 8. Open questions for other scouts or the owner
- Does BFF v0.4.1 ship the acetate AIMD reference QoIs and training topologies, and which GROMACS version does it
  expect? (bff_code)
- Who runs bff.think2earn.com / `rainbowpuffpuff`? Is it the owner's own draft? (The primer, written for an
  interview, treats PR 18 as a third party's proposal.) If it is the owner's, the inconsistencies in §2.3 should be
  fixed before anything from it reaches the voice agent.
- `guide-seminar.html` references `questions-hard.js` and `variant-2-exhibits.html`, which are not in `.reference/bff/`.
  Their checks or exhibits may hold more vetted Q&A.
