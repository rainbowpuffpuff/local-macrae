"""emcee StretchMove over the GP surrogate: walkers = 5 x dim, burn-in 2 tau_max, thin 0.5 tau_min."""
import argparse, json
import emcee, numpy as np
from bff import surrogate, likelihood

ap = argparse.ArgumentParser()
ap.add_argument('--walkers', type=int, default=40)
ap.add_argument('--steps', type=int, default=20000)
