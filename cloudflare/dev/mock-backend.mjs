// A stand-in for the backend (server/) that speaks the CONTRACT.md HTTP API, v1 and the v2 addendum, with
// made-up runs. For building, rehearsing and screenshotting the page without Cloudflare, Modal, Claude or ElevenLabs:
//   node cloudflare/dev/mock-backend.mjs            (port 8080, secret "dev-secret")
//   PORT=9000 MACRAE_TOOL_SECRET=x MOCK_SPEED=4 node cloudflare/dev/mock-backend.mjs
//   MOCK_PLANNER=fail …                              (the planner "fails": the plan event says the defaults were used)
//   MOCK_HISTORY=0 …                                 (no seeded past runs, lessons or evolution metrics)
// Runs play a scripted trace live (~40 s for methods-card/small-calc, ~100 s for the BFF task; divide by MOCK_SPEED):
// the planner's decision first (type "plan", with its token cost), then every tool call as it happens, each agent event
// with its own `cost` and `elapsed_s`. GET /api/runs/{id} carries `costs` (LLM + compute, tokens, per step, per phase)
// that grow while the run goes. GET /api/evolution returns per-task metrics and lessons; past runs are seeded so the
// lessons' evidence links open real traces, and every run that finishes here adds a metrics row (and sometimes a lesson).
// Citations use real titles and DOIs from data/group_publications.json when the file is there; passage text is invented
// and says so. Prices and rates below are illustrative; the real tables live in server/costs.py.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PUBS_FILE = path.resolve(HERE, "../../data/group_publications.json");

const FALLBACK_PUBS = [
  { title: "Specific ion effects at the air/water interface", authors: "P. Jungwirth; D. J. Tobias", year: 2006, journal: "Chem. Rev.", doi: "10.1021/cr0403741" },
  { title: "Electronic continuum correction for charged molecular simulations", authors: "M. Kohagen; P. E. Mason; P. Jungwirth", year: 2016, journal: "J. Phys. Chem. B", doi: "10.1021/acs.jpcb.5b05221" },
  { title: "Calcium binding to proteins and membranes", authors: "H. Martinez-Seara; P. Jungwirth", year: 2020, journal: "J. Phys. Chem. Lett.", doi: "10.1021/acs.jpclett.0c00001" },
];

function loadPubs() {
  try {
    const list = JSON.parse(fs.readFileSync(PUBS_FILE, "utf8")).filter((p) => p.doi && p.title && p.year > 1970);
    return list.length ? list : FALLBACK_PUBS;
  } catch {
    return FALLBACK_PUBS;
  }
}

// ---------- prices (illustrative) ----------
// $ per million tokens: input, output, cache read.
const MODELS = {
  "claude-opus-5-5": { in: 5, out: 25, cache: 0.5 },
  "claude-sonnet-5-5": { in: 3, out: 15, cache: 0.3 },
};
// $ per sandbox second, roughly Modal's list prices (CPU per core, plus memory, plus the GPU).
const HARDWARE = {
  "cpu-2": { label: "2 CPU", usd_s: 2 * 0.0000131 + 4 * 0.00000222 },
  "cpu-8": { label: "8 CPU", usd_s: 8 * 0.0000131 + 16 * 0.00000222 },
  "gpu-a10g": { label: "GPU A10G", usd_s: 0.000306 + 4 * 0.0000131 + 16 * 0.00000222 },
};

function tokens(inp, out, cache = 0, model = "claude-opus-5-5") {
  const p = MODELS[model];
  const usd = (inp * p.in + out * p.out + cache * p.cache) / 1e6;
  return { usd: Number(usd.toFixed(5)), tokens: { in: inp, out, cache } };
}

const TASKS = (pubs) => [
  {
    id: "methods-card", title: "Methods card for a paper", subtitle: "Pulls passages, writes a cited summary of the methods", icon: "card",
    prompt: "Given a DOI from the group's publications, the agent searches the indexed papers for its methods sections and writes a methods card in which every claim has a [n] citation.",
    flow: "tasks/flows/methods-card.yaml", inputs: [{ name: "doi", label: "DOI", default: pubs[0].doi }],
  },
  {
    id: "small-calc", title: "Ion–water interaction energy", subtitle: "A tiny real calculation on Modal with xtb", icon: "flask",
    prompt: "An agent on Modal sets up a Na⁺–water cluster, runs GFN2-xTB, writes result.json with the binding energy, and explains it with citations to the group's work.",
    flow: "tasks/flows/small-calc.yaml", inputs: [{ name: "ion", label: "Ion", default: "Na+", options: ["Li+", "Na+", "K+", "Mg2+", "Ca2+"] }],
  },
  {
    id: "bff-charges", title: "Bayesian charges for a fragment (BFF)", subtitle: "MD → GP surrogate → MCMC on a Modal GPU, live", icon: "molecule",
    prompt: "An agent on Modal learns ECC-scaled partial charges for a small fragment with BayesicForceFields: Latin-hypercube charge sets, short GROMACS runs, a Gaussian-process surrogate of the RDFs, emcee sampling, and a cited explanation of the posterior.",
    flow: "tasks/flows/bff-charges.yaml",
    inputs: [{ name: "fragment", label: "Fragment", default: "acetate", options: ["acetate", "guanidinium", "ethylammonium"] }, { name: "samples", label: "Samples", default: "32" }],
  },
];

function citation(p, i, page = 2 + (i % 7), quote = "") {
  return {
    key: `[${i + 1}]`, title: p.title, authors: p.authors, year: p.year, journal: p.journal, doi: p.doi, page,
    url: `https://doi.org/${p.doi}`, quote: quote || `(mock passage) ${p.title.slice(0, 120)} — page ${page}.`,
  };
}

// ---------- the planner ----------
const PLANS = {
  "methods-card": (inputs) => ({
    hardware: "cpu-2", budget_usd: 0.6, model: "claude-opus-5-5",
    plan: ["Pull the paper's passages from the index", "Write the methods card, one [n] per claim", "Check every claim cites a passage"],
    params: { doi: inputs.doi, k: 8, max_claims: 8 },
    why: "Text-only work: two CPU cores are plenty and nothing needs a GPU. Eight passages cover a JCTC methods section; the budget is mostly tokens for one agent step.",
  }),
  "small-calc": (inputs) => ({
    hardware: "cpu-8", budget_usd: 1.0, model: "claude-opus-5-5",
    plan: ["Scan the ion–O distance (B3LYP/def2-SVP)", "Counterpoise energy at the minimum (def2-TZVP)", "Point charges: full vs ECC-scaled", "Explain with citations"],
    params: { ion: inputs.ion || "Na+", scan: "1.7–3.3 Å, 0.1 Å", basis_final: "def2-TZVP", threads: 8 },
    why: "A 17-point DFT scan of a 4-atom complex parallelises over 8 cores in about a minute; a GPU would sit idle. PySCF is preinstalled in the image, so no install step (lesson from an earlier run).",
  }),
  "bff-charges": (inputs) => ({
    hardware: "gpu-a10g", budget_usd: 2.0, model: "claude-opus-5-5",
    plan: ["Read the BFF paper and the fragment setup", `Latin hypercube: ${inputs.samples || 32} charge sets`, `MD: ${inputs.samples || 32} × 200 ps (GROMACS, GPU)`, "GP surrogate of the RDFs (LOO)", "MCMC with emcee", "Validate the posterior mean", "Explain with citations"],
    params: { fragment: inputs.fragment || "acetate", samples: Number(inputs.samples || 32), md_ps: 200, waters: 128, net_charge: -0.8, walkers: 20, max_iter: 20000 },
    why: "The MD stage dominates: 32 short GROMACS runs take about 4 min on one A10G and about 25 min on 8 CPU cores, so the GPU is cheaper overall. The net charge is held at 0.8 × formal (ECC). Budget: about $0.30 of GPU time plus tokens for one agent step.",
  }),
};

function planEvent(task, inputs, lessons, { fail = false } = {}) {
  const p = PLANS[task.id](inputs);
  const used = lessons.filter((l) => l.task_id === task.id).slice(-8);
  if (fail) {
    return {
      at: 0.6, step: "plan", type: "plan", title: `Planner unavailable: using ${task.title}'s defaults`,
      detail: "The planning call failed (Anthropic API: 529 overloaded). The run continues with the task's default hardware and parameters.",
      plan: { ...p, source: "defaults", why: "The planner call failed, so these are the task's defaults.", lessons_used: 0 },
      cost: tokens(0, 0), elapsed_s: 0.6,
    };
  }
  const hw = HARDWARE[p.hardware];
  const why = used.length ? `${p.why} Applied ${used.length} lesson${used.length === 1 ? "" : "s"} from earlier runs.` : p.why;
  return {
    at: 1.4, step: "plan", type: "plan",
    title: `Decided: ${hw.label}, ${p.plan.length} stages, budget $${p.budget_usd.toFixed(2)}`,
    detail: why, plan: { ...p, why, source: "claude", planner_model: "claude-sonnet-5-5", lessons_used: used.length, lessons: used.map((l) => l.lesson) },
    cost: tokens(3400 + 180 * used.length, 420, 0, "claude-sonnet-5-5"), elapsed_s: 1.4,
  };
}

// ---------- scripted traces ----------
// Each event: {at (script seconds), step, type, title, detail?, citation?, cost?, elapsed_s?, plan?}.
// marks: when setup, work and check begin and the run ends (script seconds), for the phase times.
function script(task, pubs, inputs, { lessons = [], plannerFail = false, fail = null } = {}) {
  const byDoi = (doi, i) => pubs.find((p) => p.doi === doi) || pubs[i % pubs.length];
  const c = (i, page, q) => citation(pubs[i % pubs.length], i, page, q);
  const E = (at, step, type, title, detail = "", extra = {}) => ({ at, step, type, title, detail, ...extra });
  const plan = planEvent(task, inputs, lessons, { fail: plannerFail });
  let out;
  if (task.id === "small-calc") {
    const ion = inputs.ion || "Na+";
    out = {
      marks: { setup: 2, work: 5, check: 37, end: 40 },
      events: [
        plan,
        E(2, "prepare", "status", "Set up the calculation folder and the group papers to cite", `small-calc · ion ${ion} · environment modal`),
        E(3, "prepare", "search", "Searched the papers: ion hydration energies", 'query: "ion water binding energy first solvation shell"', { citation: c(1, 3) }),
        E(5, "calc[0]", "status", "Started Claude Code on Modal (8 CPU), account api-key", ""),
        E(7, "calc[0]", "read", "Read context.md", "3 group papers to cite, ECC scaling 0.75", { citation: c(1, 4, "Scaling the ionic charges by 1/sqrt(eps_el) ≈ 0.75 accounts for electronic polarization in a mean-field way."), cost: tokens(9800, 210, 4200), elapsed_s: 0.1 }),
        E(9, "calc[0]", "think", "Planning the scan", "One water on the C2 axis, ion facing O. Scan 1.7–3.3 Å with B3LYP/def2-SVP, then counterpoise at the minimum with def2-TZVP.", { cost: tokens(2100, 640, 14000) }),
        E(12, "calc[0]", "write", "Wrote calc/run_calc.py", "from pyscf import gto, dft\nR = np.arange(1.7, 3.31, 0.1)\nfor r in R: …", { cost: tokens(1900, 1450, 16000), elapsed_s: 0.1 }),
        E(16, "calc[0]", "calc", `Ran the ${ion}–water distance scan (PySCF, 41 s)`, "r/Å   E_int/kcal·mol⁻¹\n2.0   -19.84\n2.2   -24.12\n2.3   -24.40  ← min\n2.5   -22.97", { cost: tokens(1200, 160, 18000), elapsed_s: 41.2 }),
        E(21, "calc[0]", "calc", "Ran the counterpoise correction (def2-TZVP, 18 s)", "E_int(CP) = -23.1 kcal/mol, BSSE 1.3 kcal/mol", { cost: tokens(1100, 150, 19000), elapsed_s: 18.3 }),
        E(24, "calc[0]", "calc", "Ran the point-charge comparison (0.1 s)", "Coulomb, full charge  -31.6 kcal/mol\nCoulomb, ECC (0.75)  -23.7 kcal/mol", { cost: tokens(900, 140, 19500), elapsed_s: 0.1 }),
        E(27, "calc[0]", "write", "Wrote calc/result.json", `{"ion": "${ion}", "r_min_angstrom": 2.3, "e_int_kcal_mol": -23.1, "e_coulomb_ecc_kcal_mol": -23.7, "ecc_scaling": 0.75}`, { cost: tokens(1300, 520, 20000), elapsed_s: 0.1 }),
        E(30, "calc[0]", "cite", "Cited the scaled-charge argument", "", { citation: c(1, 5), cost: tokens(800, 60, 20000) }),
        E(33, "calc[0]", "write", "Wrote calc/explanation.md", "With ECC [1] the effective charge is smaller, so the scaled point charges land within 1 kcal/mol of the DFT number, while full charges overbind by 8.", { cost: tokens(1500, 980, 21000), elapsed_s: 0.1 }),
        E(37, "check", "status", "check passed: result.json has numbers", "reward 1.0"),
        E(39, "calc[0]", "result", `${ion}–water binding energy: −23.1 kcal/mol`, "B3LYP/def2-TZVP, counterpoise-corrected; ECC point charges within 0.6 kcal/mol. See explanation.md for the cited reasoning [1]."),
      ],
    };
  } else if (task.id === "bff-charges") {
    const frag = inputs.fragment || "acetate";
    const n = Number(inputs.samples || 32);
    const bff = byDoi("10.1021/acs.jctc.5c02051", 0);
    const ecc = byDoi("10.1063/5.0017775", 1);
    const raman = byDoi("10.1039/d0cp02987d", 2);
    out = {
      marks: { setup: 2, work: 8, check: 96, end: 100 },
      events: [
        plan,
        E(2, "prepare", "status", "Pulled the BFF paper's passages and the fragment setup", `fragment ${frag} · ${n} samples · environment modal`),
        E(3.5, "prepare", "search", "Searched the papers: Bayesian partial charges, ECC", 'query: "Bayesian learning partial charges RDF surrogate"', { citation: citation(bff, 0, 3) }),
        E(5, "prepare", "read", "Read the abstract of 10.1021/acs.jctc.5c02051 (OpenAlex)", "", { citation: citation(bff, 0, 1, "Charges learned against AIMD RDFs, hydrogen bonds and ion-pair distances with a GP surrogate and MCMC.") }),
        E(8, "fit[0]", "status", "Started Claude Code on Modal (GPU A10G), account api-key", "image macrae/bff:2026-10 (BFF, GROMACS 2025 CUDA, emcee) · cached"),
        E(10, "fit[0]", "read", "Read paper_brief.txt", "Net charge 0.8 × formal (ECC); prior normal at mid-range, sd = range/5; sigmoid RDF baseline x0 = 3 Å.", { citation: citation(bff, 0, 4, "The target net charge is 0.8 times the formal charge (electronic continuum correction), held fixed."), cost: tokens(14200, 260, 6000), elapsed_s: 0.1 }),
        E(12, "fit[0]", "think", "Setting up the fit", `${frag}: 4 charge parameters (C, CH3 carbon, O, H) with one implicit so the sum is −0.8 e. ${n} Latin-hypercube samples inside chemical bounds.`, { cost: tokens(2600, 720, 20000) }),
        E(15, "fit[0]", "write", "Wrote fit/config.yaml", `fragment: ${frag}\nnet_charge: -0.8\nsamples: ${n}\nmd: {engine: gromacs, ps: 200, waters: 128, T: 300}\nsurrogate: {kernel: rbf, loo: true}\nmcmc: {walkers: 20, max_iter: 20000}`, { cost: tokens(1800, 690, 22000), elapsed_s: 0.1 }),
        E(18, "fit[0]", "calc", `Ran Latin hypercube sampling (${n} charge sets, 0.3 s)`, `bff sample --n ${n} --seed 7\nq_O ∈ [-0.75, -0.45]  q_C ∈ [0.40, 0.90]  …\nwrote samples.npy (${n} × 3)`, { cost: tokens(1300, 210, 23000), elapsed_s: 0.3 }),
        E(22, "fit[0]", "calc", `MD 8 of ${n} done (GROMACS on A10G, 38 s)`, "gmx mdrun -nb gpu -pme gpu -deffnm s007\nPerformance: 412.6 ns/day", { cost: tokens(900, 120, 23500), elapsed_s: 38.4 }),
        E(30, "fit[0]", "calc", `MD 16 of ${n} done (GROMACS on A10G, 79 s)`, "Performance: 418.1 ns/day", { cost: tokens(900, 120, 24000), elapsed_s: 79.2 }),
        E(38, "fit[0]", "calc", `MD 24 of ${n} done (GROMACS on A10G, 117 s)`, "Performance: 415.3 ns/day", { cost: tokens(900, 120, 24500), elapsed_s: 117.0 }),
        E(46, "fit[0]", "calc", `MD ${n} of ${n} done (GROMACS on A10G, 156 s)`, `all ${n} trajectories: 200 ps each, 128 TIP4P/2005 waters\nRDFs O–Ow, C–Ow, H-bond counts written to qoi/`, { cost: tokens(1100, 180, 25000), elapsed_s: 156.4 }),
        E(51, "fit[0]", "calc", "Trained the GP surrogate (leave-one-out, 12 s)", "LOO NMAE  RDF O–Ow 2.1 %  ·  RDF C–Ow 3.4 %  ·  H-bonds 6.8 %\nlength scales: q_O 0.11, q_C 0.32, q_H 0.47", { cost: tokens(1400, 260, 26000), elapsed_s: 12.1 }),
        E(56, "fit[0]", "think", "The surrogate is good enough to sample", "O–Ow RDF error is well under 5 %, H-bonds under 10 %: inside the paper's ranges [1], so MCMC on the surrogate is meaningful.", { cost: tokens(1100, 410, 27000) }),
        E(60, "fit[0]", "calc", "MCMC: 6 000 of 20 000 iterations (emcee, τ ≈ 140)", "acceptance 0.31 · τ_max 141 · not yet converged (need 50 τ)", { cost: tokens(900, 140, 27500), elapsed_s: 21.7 }),
        E(67, "fit[0]", "calc", "MCMC converged after 14 000 iterations (49 s)", "τ_max 152 · burn-in 304 · thin 38\nposterior mean  q_O −0.61 ± 0.02 e   q_C +0.71 ± 0.06 e", { cost: tokens(1200, 220, 28000), elapsed_s: 49.3 }),
        E(72, "fit[0]", "calc", "Validated the posterior mean with fresh MD (GROMACS, 41 s)", "NMAE vs AIMD reference\nRDF O–Ow 3.9 %   RDF C–Ow 4.6 %   H-bonds 11.2 %\nCHARMM36 (same box): 7.8 % · 9.1 % · 18.5 %", { cost: tokens(1300, 230, 29000), elapsed_s: 41.0 }),
        E(77, "fit[0]", "write", "Wrote fit/posterior.png", "corner plot of 3 free charges, 1-σ and 2-σ contours", { cost: tokens(1500, 880, 30000), elapsed_s: 2.2 }),
        E(81, "fit[0]", "write", "Wrote fit/result.json", `{"fragment": "${frag}", "net_charge": -0.8, "q_mean": {"O": -0.61, "C": 0.71}, "nmae_rdf_O_Ow": 0.039, "tau_max": 152, "samples": ${n}}`, { cost: tokens(1300, 610, 31000), elapsed_s: 0.1 }),
        E(84, "fit[0]", "cite", "Cited the ECC rationale", "", { citation: citation(ecc, 1, 4), cost: tokens(900, 70, 31500) }),
        E(86, "fit[0]", "cite", "Cited the Raman reference for acetate binding", "", { citation: citation(raman, 2, 6), cost: tokens(900, 70, 32000) }),
        E(90, "fit[0]", "write", "Wrote fit/explanation.md", "The carboxylate oxygens land at −0.61 e, inside the −0.65 to −0.55 e range the BFF paper reports for carboxylates [1]; the 0.8 scaling follows ECC [2].", { cost: tokens(2100, 1300, 33000), elapsed_s: 0.1 }),
        E(96, "check", "status", "check passed: result.json has the posterior and NMAE numbers", "reward 1.0"),
        E(99, "fit[0]", "result", `${frag}: q_O = −0.61 ± 0.02 e, RDF error 3.9 %`, `Learned in ${Math.round(n * 4.9)} s of GPU MD + 49 s of MCMC. Better than CHARMM36 on every RDF in the same box [1].`),
      ],
    };
  } else {
    const doi = inputs.doi || pubs[0].doi;
    const target = pubs.find((p) => p.doi === doi) || pubs[0];
    const tIdx = pubs.indexOf(target);
    out = {
      marks: { setup: 2, work: 8, check: 27, end: 30 },
      events: [
        plan,
        E(2, "passages", "status", "Flow started", `methods-card · doi ${doi}`),
        E(3, "passages", "search", "Searched the papers for its methods", `python -m rag search "methods ${target.title.slice(0, 60)}"`, { citation: citation(target, 0, 2) }),
        E(5, "passages", "read", "Read 6 passages from the paper", "pages 2–5: simulation details, force field, analysis", { citation: citation(target, 0, 3) }),
        E(8, "card[0]", "status", "Agent step started (claude-code on Modal, 2 CPU, account auto)", ""),
        E(10, "card[0]", "read", "Read passages.json", "6 passages, 4 812 tokens", { cost: tokens(11800, 190, 3000), elapsed_s: 0.1 }),
        E(13, "card[0]", "think", "Sorting methods by kind", "Force fields and charges first, then sampling (lengths, ensembles), then analysis. Every claim must point at a passage [n].", { cost: tokens(2200, 560, 15000) }),
        E(16, "card[0]", "read", "Read a cited reference paper", "", { citation: citation(pubs[(tIdx + 1) % pubs.length], 1, 6), cost: tokens(3100, 120, 16000), elapsed_s: 0.2 }),
        E(20, "card[0]", "write", "Wrote methods-card.md", "## Methods\n- Force field: CHARMM36 with ECC-scaled ions [1]\n- Sampling: 500 ns NpT at 300 K [1]\n- Analysis: radial distribution functions, binding free energies [2]", { cost: tokens(2400, 1600, 18000), elapsed_s: 0.1 }),
        E(24, "card[0]", "cite", "Checked every claim has a citation", "4 claims · 4 with [n]", { citation: citation(target, 0, 4), cost: tokens(1500, 230, 19000) }),
        E(27, "check", "status", "check passed: every claim cites a passage", "reward 1.0"),
        E(29, "card[0]", "result", "Methods card ready", "4 cited claims from 2 papers. Open methods-card.md in the run folder."),
      ],
    };
  }
  if (fail) out = failVariant(out, fail);
  return out;
}

// A past run that went wrong: the trace stops at `fail.after` script seconds with an error, then the run fails.
function failVariant({ marks, events }, { after, step, title, detail }) {
  const kept = events.filter((e) => e.at <= after);
  const end = after + 3;
  kept.push({ at: after + 1.5, step, type: "error", title, detail });
  kept.push({ at: end, step: "", type: "error", title: "Run failed", detail: "" });
  return { marks: { ...marks, check: Math.min(marks.check, end), end }, events: kept, failed: true };
}

// Seeded history: older runs are slower, pricier and fail more; later ones used more lessons.
const HISTORY = {
  "methods-card": {
    runs: [
      { daysAgo: 6, stretch: 9.4, llm: 1.9, lessons: 0 },
      { daysAgo: 4, stretch: 8.1, llm: 1.6, lessons: 1, fail: { after: 16, step: "card[0]", title: "Check failed: 3 claims have no [n]", detail: "check: every claim must cite a passage → 3 of 7 claims had no citation. Retries exhausted." } },
      { daysAgo: 2, stretch: 6.0, llm: 1.2, lessons: 2 },
      { daysAgo: 1, stretch: 5.2, llm: 1.0, lessons: 3 },
    ],
    lessons: [
      { kind: "avoid", lesson: "Claims written from memory failed the citation check. Write each claim straight from a passage and put its [n] in the same sentence.", from: 1 },
      { kind: "do", lesson: "Read passages.json once and keep the [n] map in the card's front matter; re-reading it cost 20 k tokens per claim.", from: 0 },
      { kind: "setting", lesson: "k = 8 passages covered every methods section seen so far; k = 12 only added noise.", from: 2 },
    ],
  },
  "small-calc": {
    runs: [
      { daysAgo: 7, stretch: 22, llm: 2.1, lessons: 0, fail: { after: 12, step: "calc[0]", title: "pip install pyscf timed out after 600 s", detail: "ERROR: Operation cancelled by user (timeout 600 s). Building wheel for pyscf (pyproject.toml) … still compiling." } },
      { daysAgo: 6, stretch: 16, llm: 1.8, lessons: 1 },
      { daysAgo: 4, stretch: 11, llm: 1.4, lessons: 2 },
      { daysAgo: 3, stretch: 9, llm: 1.3, lessons: 3, fail: { after: 24, step: "calc[0]", title: "Check failed: e_int_kcal_mol is a string", detail: "check: result.json field e_int_kcal_mol must be a number, got \"-23.1 kcal/mol\"" } },
      { daysAgo: 1, stretch: 6.5, llm: 1.0, lessons: 4 },
    ],
    lessons: [
      { kind: "setting", lesson: "Compiling PySCF from source on Modal exceeds the 10 min step timeout. Use the prebuilt image with PySCF instead of pip install.", from: 0 },
      { kind: "avoid", lesson: "Never write units into result.json values: the check wants numbers (e_int_kcal_mol: -23.1, not \"-23.1 kcal/mol\").", from: 3 },
      { kind: "do", lesson: "Run the 17 scan points in one PySCF process with 8 threads; one process per point spent most of its time importing.", from: 2 },
      { kind: "tool", lesson: "calc/run_calc.py from an earlier run passed the check; reuse it and change only the ion.", from: 4, tool: "run_calc.py" },
    ],
  },
  "bff-charges": {
    runs: [
      { daysAgo: 5, stretch: 14, llm: 2.4, lessons: 0, hardware: "cpu-8", fail: { after: 38, step: "fit[0]", title: "Step timeout during MD (24 of 32 runs on CPU)", detail: "gmx mdrun on 8 CPU cores: 61 ns/day. 24 of 32 trajectories done when the 10 min step timeout hit." } },
      { daysAgo: 4, stretch: 11, llm: 2.0, lessons: 1, fail: { after: 60, step: "fit[0]", title: "MCMC did not converge (τ_max 610 > iterations / 50)", detail: "emcee: 5 000 iterations, τ_max 610. The chain is too short; the posterior can't be trusted." } },
      { daysAgo: 3, stretch: 8.5, llm: 1.6, lessons: 2 },
      { daysAgo: 2, stretch: 6.0, llm: 1.3, lessons: 3 },
      { daysAgo: 1, stretch: 4.6, llm: 1.1, lessons: 4 },
    ],
    lessons: [
      { kind: "setting", lesson: "GROMACS on one A10G runs these 128-water boxes at ~415 ns/day vs ~61 on 8 CPU cores; pick gpu-a10g for the MD stage.", from: 0 },
      { kind: "setting", lesson: "Run emcee until 50 τ, not a fixed 5 000 iterations: τ_max was ~150 with 20 walkers once the sigmoid baseline was subtracted.", from: 1 },
      { kind: "do", lesson: "Subtract the sigmoid RDF baseline (x0 = 3 Å, a = 5/Å) before training the GP; it cut LOO error on O–Ow from 9 % to 2 %.", from: 2 },
      { kind: "avoid", lesson: "Don't sleep-wait on mdrun; poll the .log every 10 s so progress events reach the page.", from: 3 },
    ],
  },
};

export function createMock({ secret = "dev-secret", speed = 1, now = () => Date.now() / 1000, planner = "ok", history = true } = {}) {
  const pubs = loadPubs();
  const tasks = TASKS(pubs);
  const runs = new Map();
  const lessons = []; // {task_id, lesson, kind, evidence: ["run/step/seq"], created, tool?}
  const metricsAdded = new Set();
  let counter = 0;

  const runId = (taskId, at) => `${new Date(at * 1000).toISOString().slice(0, 19).replace(/[-:T]/g, "")}-${taskId}-${(++counter).toString(36)}`;

  // scale: real seconds per script second (live runs 1/speed; seeded past runs much more, as real runs took minutes).
  function addRun(task, inputs, { started = now(), scale = 1 / speed, llm = 1, hardware = null, fail = null, lessonsUsed = null, plannerFail = planner === "fail" } = {}) {
    const run_id = runId(task.id, started);
    const s = script(task, pubs, inputs, { lessons, plannerFail, fail });
    if (hardware) {
      const plan = s.events[0].plan;
      plan.hardware = hardware;
      s.events[0].title = s.events[0].title.replace(/^Decided: [^,]+/, `Decided: ${HARDWARE[hardware].label}`);
    }
    if (lessonsUsed !== null) s.events[0].plan.lessons_used = lessonsUsed;
    const run = { run_id, task_id: task.id, title: task.title, inputs, started, scale, llm, ...s };
    run.events.forEach((e, seq) => (e.seq = seq));
    runs.set(run_id, run);
    return run;
  }

  const age = (run) => (now() - run.started) / run.scale; // script seconds elapsed

  function events(run) {
    const a = age(run);
    return run.events
      .filter((e) => e.at <= a)
      .map((e) => {
        const ev = { seq: e.seq, t: run.started + e.at * run.scale, step: e.step, type: e.type, title: e.title, detail: e.detail || "", citation: e.citation || null };
        if (e.cost) ev.cost = { usd: Number((e.cost.usd * run.llm).toFixed(5)), tokens: e.cost.tokens };
        if (e.elapsed_s !== undefined) ev.elapsed_s = e.elapsed_s;
        if (e.plan) ev.plan = e.plan;
        return ev;
      });
  }

  const isDone = (run) => age(run) >= run.marks.end;
  const hardwareOf = (run) => (run.events[0].plan && run.events[0].plan.hardware) || "cpu-2";

  // Costs at this moment: LLM from the events so far, compute from the sandbox seconds of the work phase.
  function costs(run) {
    const a = Math.min(age(run), run.marks.end);
    const evs = events(run);
    const rate = HARDWARE[hardwareOf(run)].usd_s;
    const sec = (from, to) => Math.max(0, Math.min(a, to) - from) * run.scale;
    const phases = {
      plan: sec(0, run.marks.setup), setup: sec(run.marks.setup, run.marks.work),
      work: sec(run.marks.work, run.marks.check), check: sec(run.marks.check, run.marks.end),
    };
    const tok = { in: 0, out: 0, cache: 0 };
    const by_step = {};
    let llm = 0;
    for (const e of evs) {
      const st = (by_step[e.step || "run"] ||= { llm_usd: 0, compute_usd: 0, seconds: 0, model: "", hardware: "" });
      if (e.cost) {
        llm += e.cost.usd;
        st.llm_usd += e.cost.usd;
        st.model = e.step === "plan" ? "claude-sonnet-5-5" : "claude-opus-5-5";
        for (const k of ["in", "out", "cache"]) tok[k] += e.cost.tokens[k] || 0;
      }
    }
    // step seconds from first to last event of the step (the agent step runs to the check)
    for (const key of Object.keys(by_step)) {
      const own = run.events.filter((e) => (e.step || "run") === key && e.at <= a);
      const first = own[0].at;
      const agent = key.includes("[");
      const last = agent ? Math.min(a, run.marks.check) : own[own.length - 1].at;
      by_step[key].seconds = Number(((Math.max(last, first) - first) * run.scale + (agent ? 0 : 0.4)).toFixed(1));
      if (agent) {
        by_step[key].hardware = hardwareOf(run);
        by_step[key].compute_usd = Number((by_step[key].seconds * rate).toFixed(5));
      }
      by_step[key].llm_usd = Number(by_step[key].llm_usd.toFixed(5));
    }
    const compute = Object.values(by_step).reduce((s, x) => s + x.compute_usd, 0);
    for (const k of Object.keys(phases)) phases[k] = Number(phases[k].toFixed(1));
    return {
      llm_usd: Number(llm.toFixed(5)), compute_usd: Number(compute.toFixed(5)), total_usd: Number((llm + compute).toFixed(5)),
      tokens: tok, by_step, wall_s: Number((a * run.scale).toFixed(1)), phases,
      hardware: hardwareOf(run), hardware_label: HARDWARE[hardwareOf(run)].label, usd_per_hour: Number((HARDWARE[hardwareOf(run)].usd_s * 3600).toFixed(3)),
    };
  }

  function summary(run) {
    const evs = events(run);
    const done = isDone(run);
    const keys = [...new Set(run.events.map((e) => e.step).filter((k) => k && k !== "plan"))];
    const steps = keys.map((key) => {
      const own = evs.filter((e) => e.step === key);
      const failedHere = run.failed && done && run.events.some((e) => e.step === key && e.type === "error");
      const stepDone = done || (own.length && evs.some((e) => e.step !== key && e.seq > own[own.length - 1].seq));
      const status = failedHere ? "failed" : !own.length ? (done ? "skipped" : "pending") : stepDone ? "ok" : "running";
      return {
        key, kind: key.includes("[") ? "agent" : "run", status,
        account: key.includes("[") ? "api-key" : "", reward: key.includes("[") && status === "ok" ? 1.0 : status === "failed" ? 0 : null,
        attempt: own.some((e) => e.type === "error") ? 2 : 1, error: failedHere ? run.events.find((e) => e.step === key && e.type === "error").title : "",
      };
    });
    return {
      run_id: run.run_id, task_id: run.task_id, title: run.title, status: done ? (run.failed ? "failed" : "ok") : "running",
      started: run.started, finished: done ? run.started + run.marks.end * run.scale : null, steps,
      inputs: run.inputs, plan: run.events[0].plan || null, costs: costs(run),
    };
  }

  function start(taskId, inputs = {}) {
    const task = tasks.find((t) => t.id === taskId);
    if (!task) return null;
    return addRun(task, inputs).run_id;
  }

  // ---------- evolution ----------
  function metricsRow(run) {
    const s = summary(run);
    return {
      run_id: run.run_id, ok: s.status === "ok", reward: s.status === "ok" ? 1.0 : 0.0, wall_s: s.costs.wall_s,
      total_usd: Number(s.costs.total_usd.toFixed(4)), lessons_used: (run.events[0].plan && run.events[0].plan.lessons_used) || 0, started: run.started,
    };
  }

  // A run that finished here teaches something (the mock's stand-in for evolve.distill). A lesson seen before is
  // confirmed again: it moves to the top with this run as its evidence.
  const LIVE_LESSONS = {
    "methods-card": [
      "Group the card by force field, sampling, analysis: the check passed first time with one [n] per bullet.",
      "Quote the passage's own numbers (ns, K, nm) instead of rounding them; the check compares them to the source.",
    ],
    "small-calc": [
      "The scan minimum sat at 2.3 Å for this ion; start the next scan at 1.9 Å and save four points.",
      "Counterpoise at def2-TZVP changed E_int by only 1.3 kcal/mol; report BSSE but keep the TZVP step, it is 18 s.",
    ],
    "bff-charges": [
      "32 Latin-hypercube samples gave LOO error under 5 % on every RDF; 24 would likely do and save ~40 s of GPU.",
      "emcee converged at τ_max ≈ 150 after 14 000 iterations; start with 15 000 and extend only if 50 τ isn't reached.",
    ],
  };
  let distilled = 0;
  function distill(run) {
    if (metricsAdded.has(run.run_id)) return;
    metricsAdded.add(run.run_id);
    const work = run.events.filter((e) => e.type === "calc" || e.type === "write");
    const ev = work[work.length - 1] || run.events[run.events.length - 1];
    const pool = LIVE_LESSONS[run.task_id];
    const text = pool[distilled++ % pool.length];
    const evidence = [`${run.run_id}/${ev.step}/${ev.seq}`];
    const seen = lessons.find((l) => l.task_id === run.task_id && l.lesson === text);
    if (seen) Object.assign(seen, { evidence, created: now() });
    else {
      lessons.push({ task_id: run.task_id, kind: "do", lesson: text, evidence, created: now() });
      const own = lessons.filter((l) => l.task_id === run.task_id).sort((a, b) => a.created - b.created);
      if (own.length > 8) lessons.splice(lessons.indexOf(own[0]), 1);
    }
  }

  function evolution() {
    const by_task = {};
    for (const run of [...runs.values()].sort((a, b) => a.started - b.started)) {
      if (!isDone(run)) continue;
      if (!run.seeded) distill(run);
      (by_task[run.task_id] ||= []).push(metricsRow(run));
    }
    return { by_task, lessons: lessons.slice().sort((a, b) => b.created - a.created) };
  }

  if (history) {
    for (const task of tasks) {
      const h = HISTORY[task.id];
      const made = h.runs.map((r, i) => {
        const started = now() - r.daysAgo * 86400 - 3600 * (i + 2);
        const inputs = Object.fromEntries(task.inputs.map((x) => [x.name, x.default]));
        const run = addRun(task, inputs, { started, scale: r.stretch, llm: r.llm, hardware: r.hardware || null, fail: r.fail || null, lessonsUsed: r.lessons, plannerFail: false });
        run.seeded = true;
        return run;
      });
      for (const l of h.lessons) {
        const run = made[l.from];
        const ev = run.events.find((e) => e.type === "error") || run.events.filter((e) => e.type === "calc" || e.type === "write" || e.type === "read").pop();
        lessons.push({ task_id: task.id, kind: l.kind, lesson: l.lesson, evidence: [`${run.run_id}/${ev.step}/${ev.seq}`], created: run.started + run.marks.end * run.scale + 60, ...(l.tool ? { tool: l.tool } : {}) });
      }
    }
  }

  function search(query, k = 6) {
    const words = String(query || "").toLowerCase().split(/\W+/).filter((w) => w.length > 2);
    if (!words.length) return [];
    return pubs
      .map((p) => ({ p, score: words.filter((w) => p.title.toLowerCase().includes(w)).length / words.length }))
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score || b.p.year - a.p.year)
      .slice(0, k)
      .map(({ p, score }, i) => {
        const cite = citation(p, i);
        return { id: `doi:${p.doi}#p${cite.page}c1`, text: `(mock passage — the real one comes from rag/) ${p.title}. ${p.authors}, ${p.journal} ${p.year}.`, score: Number(score.toFixed(2)), citation: cite };
      });
  }

  // ---------- costs for people (server/cost_api.py) ----------
  // The real price table lives in server/costs.py (with its sources); this copy has its shape and the 2026-10-08 values.
  const PRICE_TABLE = {
    as_of: "2026-10-08", currency: "USD",
    sources: {
      anthropic: { label: "Claude API list prices", url: "https://platform.claude.com/docs/en/about-claude/pricing" },
      modal: { label: "Modal sandbox and GPU prices", url: "https://modal.com/pricing" },
      backend: { label: "Cloudflare Containers prices", url: "https://developers.cloudflare.com/containers/pricing/" },
      voice: { label: "ElevenLabs Agents prices", url: "https://elevenlabs.io/pricing/api" },
    },
    llm: { unit: "per million tokens", models: [
      { id: "claude-opus-5-5", input: 4, output: 20, cache_read: 0.2, cache_write_5m: 5, cache_write_1h: 8 },
      { id: "claude-sonnet-5-5", input: 2, output: 10, cache_read: 0.1, cache_write_5m: 2.5, cache_write_1h: 4 },
      { id: "claude-haiku-5-5", input: 0.1, output: 0.5, cache_read: 0.01, cache_write_5m: 0.125, cache_write_1h: 0.2 },
    ] },
    compute: { unit: "per second", cpu_core_s: 0.00003942, mem_gib_s: 0.00000667, gpu_s: { a10g: 0.000306, h100: 0.001097 } },
    backend: { unit: "per second", vcpu_s: 0.00002, mem_gib_s: 0.0000025, disk_gb_s: 0.00000007, instance: { name: "standard-1", vcpu: 0.5, mem_gib: 4, disk_gb: 8 }, usd_per_hour: 0.074 },
    voice: { usd_per_min: 0.08 },
  };
  const BACKEND_USD_S = 0.074 / 3600;

  function quartiles(xs) {
    const v = xs.slice().sort((a, b) => a - b);
    const at = (q) => {
      const i = (v.length - 1) * q;
      const lo = Math.floor(i);
      return v[lo] + (v[Math.ceil(i)] - v[lo]) * (i - lo);
    };
    return v.length < 4 ? [at(0.5), v[0], v[v.length - 1]] : [at(0.5), at(0.25), at(0.75)];
  }

  function costEstimates() {
    const out = {};
    const finished = {};
    const done = [...runs.values()].filter(isDone).sort((a, b) => b.started - a.started);
    for (const run of done) {
      const c = summary(run).costs;
      finished[run.run_id] = { total_usd: c.total_usd, llm_usd: c.llm_usd, compute_usd: c.compute_usd, wall_s: c.wall_s };
    }
    for (const task of tasks) {
      const mine = done.filter((r) => r.task_id === task.id);
      const ok = mine.filter((r) => !r.failed);
      const use = (ok.length ? ok : mine).slice(0, 10);
      if (!use.length) {
        out[task.id] = { task_id: task.id, basis: "none", n: 0, n_ok: 0, usd: null, hardware: "cpu-2", usd_per_hour: 0.38, runs: [] };
        continue;
      }
      const cs = use.map((r) => summary(r).costs);
      const [usd, lo, hi] = quartiles(cs.map((c) => c.total_usd));
      const [secs, slo, shi] = quartiles(cs.map((c) => c.wall_s));
      const r4 = (x) => Number(x.toFixed(4));
      out[task.id] = {
        task_id: task.id, basis: "past_runs", n: use.length, n_ok: ok.length,
        success_rate: Number((mine.slice(0, 10).filter((r) => !r.failed).length / Math.min(10, mine.length)).toFixed(3)),
        usd: r4(usd), usd_low: r4(lo), usd_high: r4(hi), llm_usd: r4(quartiles(cs.map((c) => c.llm_usd))[0]),
        compute_usd: r4(quartiles(cs.map((c) => c.compute_usd))[0]), seconds: Math.round(secs), seconds_low: Math.round(slo),
        seconds_high: Math.round(shi), hardware: cs[0].hardware, usd_per_hour: cs[0].usd_per_hour, runs: use.map((r) => r.run_id),
      };
    }
    return { estimates: out, runs: finished, as_of: PRICE_TABLE.as_of };
  }

  async function handle(req, res) {
    const url = new URL(req.url, "http://mock");
    const send = (status, body) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(body));
    };
    const body = req.method === "POST" ? await new Promise((ok) => {
      let s = "";
      req.on("data", (d) => (s += d));
      req.on("end", () => { try { ok(s ? JSON.parse(s) : {}); } catch { ok(null); } });
    }) : {};
    if (body === null) return send(400, { detail: "invalid JSON" });
    const p = url.pathname;
    if (p === "/api/health") return send(200, { ok: true, papers: pubs.length, chunks: pubs.length * 12, modal: true });
    if (req.headers["x-macrae-secret"] !== secret) return send(401, { detail: "missing or wrong X-Macrae-Secret" });
    let m;
    if (p === "/api/tasks" && req.method === "GET") return send(200, { tasks });
    if ((m = p.match(/^\/api\/tasks\/([^/]+)\/start$/)) && req.method === "POST") {
      const run_id = start(decodeURIComponent(m[1]), body.inputs || {});
      return run_id ? send(200, { run_id }) : send(404, { detail: "no such task" });
    }
    if (p === "/api/runs") return send(200, { runs: [...runs.values()].map(summary).sort((a, b) => b.started - a.started).slice(0, 50) });
    if ((m = p.match(/^\/api\/runs\/([^/]+)\/events$/))) {
      const run = runs.get(decodeURIComponent(m[1]));
      if (!run) return send(404, { detail: "no such run" });
      const after = url.searchParams.has("after") ? Number(url.searchParams.get("after")) : -1;
      const all = events(run);
      return send(200, { events: all.filter((e) => e.seq > after), done: isDone(run) && all.length === run.events.length });
    }
    if ((m = p.match(/^\/api\/runs\/([^/]+)$/))) {
      const run = runs.get(decodeURIComponent(m[1]));
      return run ? send(200, summary(run)) : send(404, { detail: "no such run" });
    }
    if (p === "/api/evolution" && req.method === "GET") return send(200, evolution());
    if (p === "/api/search" && req.method === "POST") {
      const seconds = 0.2 + Math.random() * 0.4;
      const compute = Number((seconds * BACKEND_USD_S).toFixed(8));
      return send(200, { passages: search(body.query, body.k || 6), cost: { llm_usd: 0, compute_usd: compute, total_usd: compute, seconds: Number(seconds.toFixed(3)), model: "", tokens: { in: 0, out: 0, cache: 0 }, compute: "backend" } });
    }
    if (p === "/api/costs/prices" && req.method === "GET") return send(200, PRICE_TABLE);
    if (p === "/api/costs/estimates" && req.method === "GET") return send(200, costEstimates());
    if ((m = p.match(/^\/api\/tasks\/([^/]+)\/estimate$/)) && req.method === "GET") {
      const e = costEstimates().estimates[decodeURIComponent(m[1])];
      return e ? send(200, e) : send(404, { detail: "no such task" });
    }
    if (p === "/api/tools/search_papers" && req.method === "POST") {
      const passages = search(body.query, 6);
      return send(200, { answer_context: passages.map((x, i) => `[${i + 1}] ${x.text}`).join("\n\n"), citations: passages.map((x) => x.citation) });
    }
    if (p === "/api/tools/start_task" && req.method === "POST") {
      const run_id = start(body.task_id, {});
      return run_id ? send(200, { run_id, message: "Started." }) : send(404, { detail: "no such task" });
    }
    if (p === "/api/tools/run_status" && req.method === "POST") {
      const run = runs.get(body.run_id);
      if (!run) return send(404, { detail: "no such run" });
      const s = summary(run);
      const evs = events(run);
      return send(200, { status: s.status, summary: `${run.title}: ${evs.length} of ${run.events.length} events, $${s.costs.total_usd.toFixed(2)} so far`, recent: evs.slice(-3).map((e) => e.title) });
    }
    return send(404, { detail: "not found" });
  }

  return { handle, start, runs, tasks, lessons, evolution };
}

export function listen({ port = 8080, host = "127.0.0.1", ...opts } = {}) {
  const mock = createMock(opts);
  const server = http.createServer((req, res) => {
    mock.handle(req, res).catch((err) => {
      res.writeHead(500, { "content-type": "application/json" });
      res.end(JSON.stringify({ detail: String(err) }));
    });
  });
  return new Promise((ok) => server.listen(port, host, () => ok({ server, mock, port: server.address().port })));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { port } = await listen({
    port: Number(process.env.PORT || 8080),
    secret: process.env.MACRAE_TOOL_SECRET || "dev-secret",
    speed: Number(process.env.MOCK_SPEED || 1),
    planner: process.env.MOCK_PLANNER === "fail" ? "fail" : "ok",
    history: process.env.MOCK_HISTORY !== "0",
  });
  console.log(`mock backend on http://127.0.0.1:${port} (secret: ${process.env.MACRAE_TOOL_SECRET || "dev-secret"})`);
}
