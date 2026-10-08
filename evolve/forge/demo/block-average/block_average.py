#!/usr/bin/env python3
"""Block-averaged mean and standard error of a correlated time series (an MD observable).

Flyvbjerg–Petersen blocking: average neighbouring pairs again and again; the naive standard error of the blocked
series grows with the block size until the blocks are longer than the correlation time, then levels off. The
reported error is the first plateau value (the estimate stops growing within its own uncertainty), so correlated
samples don't give a falsely small error bar.

    python3 block_average.py series.dat [--column N] [--skip FRACTION] [--json]

Input: whitespace-separated columns, lines starting with # or @ are skipped (GROMACS .xvg works); --column is
0-based (default: the last column). Output (--json): {"n", "mean", "sem", "sem_naive", "block_size", "n_blocks",
"tau_int", "plateau", "levels": [{"block_size", "n_blocks", "sem", "sem_err"}]}. Stdlib only.
"""

import argparse
import json
import math
import sys


def read_series(path, column=-1, skip=0.0):
    xs = []
    with open(path) as f:
        for ln in f:
            s = ln.strip()
            if not s or s[0] in "#@":
                continue
            parts = s.split()
            try:
                xs.append(float(parts[column]))
            except (IndexError, ValueError):
                continue
    start = int(len(xs) * max(0.0, min(0.9, skip)))
    return xs[start:]


def _sem(xs):
    n = len(xs)
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var / n)


def block_average(xs, min_blocks=8):
    n = len(xs)
    if n < 2 * min_blocks:
        raise ValueError(f"need at least {2 * min_blocks} samples, got {n}")
    mean = sum(xs) / n
    levels, cur, size = [], list(xs), 1
    while len(cur) >= min_blocks:
        s = _sem(cur)
        levels.append({"block_size": size, "n_blocks": len(cur), "sem": s,
                       "sem_err": s / math.sqrt(2.0 * (len(cur) - 1))})
        cur = [(cur[i] + cur[i + 1]) / 2.0 for i in range(0, len(cur) - 1, 2)]
        size *= 2
    plateau = None
    for i in range(len(levels) - 1):
        a, b = levels[i], levels[i + 1]
        if b["sem"] - a["sem"] <= max(a["sem_err"], b["sem_err"]) and i > 0:
            plateau = i
            break
    pick = levels[plateau] if plateau is not None else max(levels, key=lambda lv: lv["sem"])
    sem_naive = levels[0]["sem"]
    tau = 0.5 * ((pick["sem"] / sem_naive) ** 2) if sem_naive > 0 else 0.0
    return {"n": n, "mean": mean, "sem": pick["sem"], "sem_naive": sem_naive, "block_size": pick["block_size"],
            "n_blocks": pick["n_blocks"], "tau_int": tau, "plateau": plateau is not None,
            "levels": [{k: (round(v, 10) if isinstance(v, float) else v) for k, v in lv.items()} for lv in levels]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("series")
    ap.add_argument("--column", type=int, default=-1)
    ap.add_argument("--skip", type=float, default=0.0, help="fraction of the start to drop as equilibration")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        res = block_average(read_series(a.series, a.column, a.skip))
    except (OSError, ValueError) as e:
        print(f"block_average: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(res))
    else:
        print(f"mean = {res['mean']:.6g} ± {res['sem']:.3g} (block size {res['block_size']}, {res['n_blocks']} "
              f"blocks; naive ± {res['sem_naive']:.3g}; tau_int ≈ {res['tau_int']:.1f} samples)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
