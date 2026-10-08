"""Toy timing of PR 18 pieces (FPCASurrogate, PlausibilityGateV2) + emcee on a synthetic 2-charge 'acetate' RDF.
Synthetic forward model only: no MD. Usage: python -I toy_pr18.py <pr18_dir>"""
import sys, time, importlib.util, json
import numpy as np, emcee
from scipy.stats import qmc

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m); return m
d = sys.argv[1]
fp = load(f"{d}/bff/bayes/fpca_surrogate.py", "fpca"); gt = load(f"{d}/bff/bayes/plausibility_v2.py", "gate")

r = np.linspace(0.5, 10, 200)
lo, hi = np.array([-0.5, -0.9]), np.array([0.1, -0.4])          # site's prior bounds for q(C1), q(O)
def rdf(th, rng=None, noise=0.0):
    qc, qo = th
    h1 = 1.0 + 6.0 * (-qo) + 0.8 * qc           # first-shell height grows with |qO| (synthetic)
    r1 = 1.75 + 0.25 * (qo + 0.65)              # contact shifts with qO
    g = 1/(1+np.exp(-5*(r-1.4)))                 # zero core, bulk 1 (like the paper's sigmoid baseline)
    g = g + (h1-1)*np.exp(-((r-r1)/0.18)**2) - 0.5*np.exp(-((r-2.45)/0.2)**2) + 1.4*np.exp(-((r-3.4)/0.5)**2)
    if rng is not None: g = g + rng.normal(0, noise, r.size)
    return g

rng = np.random.default_rng(0)
truth = np.array([-0.20, -0.62]); y_ref = rdf(truth, rng, 0.02)
out = {}
for N in (40, 200):
    X = qmc.scale(qmc.LatinHypercube(d=2, seed=1).random(N), lo, hi)
    Y = np.array([rdf(x, rng, 0.02) for x in X])
    t = time.perf_counter(); s = fp.FPCASurrogate(n_components=5).fit(X, Y); tfit = time.perf_counter() - t
    t = time.perf_counter(); s.predict(np.tile(truth, (1000, 1))); tpred = (time.perf_counter() - t) / 1000
    def logp(th):
        if np.any(th[:2] < lo) or np.any(th[:2] > hi): return -np.inf
        sig = np.exp(th[2]); mu = (lo + hi) / 2; sd = (hi - lo) / 5
        lp = -0.5*np.sum(((th[:2]-mu)/sd)**2) - 0.5*((th[2]+2)/2)**2            # paper's priors
        res = s.predict(th[None, :2])[0] - y_ref
        return lp - 0.5*np.sum(res**2)/sig**2 - 1*np.log(sig)                    # Eq. 7, n_obs = 1
    nw = 5*3; p0 = np.column_stack([rng.uniform(lo[0], hi[0], nw), rng.uniform(lo[1], hi[1], nw), rng.normal(-2, .1, nw)])
    sam = emcee.EnsembleSampler(nw, 3, logp); t = time.perf_counter(); sam.run_mcmc(p0, 1000); tmc = time.perf_counter() - t
    ch = sam.get_chain(discard=300, flat=True)
    # gate over a 3007-ish grid, like the site's "3,007 candidates"
    G = np.array(np.meshgrid(np.linspace(*[lo[0], hi[0]], 59), np.linspace(*[lo[1], hi[1]], 51))).reshape(2, -1).T
    mu_g, sd_g = s.predict(G, return_std=True)
    _, ok = gt.PlausibilityGateV2(y_ref, 0.02, kappa=2.0, tau=3.0).evaluate(mu_g, sd_g)
    out[N] = dict(evr5=float(s.explained_variance_ratio_.sum()), fit_s=round(tfit, 2), predict_ms=round(tpred*1e3, 3),
                  mcmc_s_15walkers_1000it=round(tmc, 1), post_mean=ch[:, :2].mean(0).round(3).tolist(),
                  post_sd=ch[:, :2].std(0).round(3).tolist(), gate_accept_frac=round(float(ok.mean()), 4), grid=len(G))
print(json.dumps(out, indent=1)); print("truth", truth.tolist())
