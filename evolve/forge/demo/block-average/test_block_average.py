"""Tests for block_average.py against answers known independently (run: python3 test_block_average.py)."""

import json
import math
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from block_average import block_average  # noqa: E402


def ar1(n, phi, sigma=1.0, seed=7):
    rng = random.Random(seed)
    x, out = 0.0, []
    for _ in range(n):
        x = phi * x + rng.gauss(0.0, sigma)
        out.append(x)
    return out


def test_white_noise_matches_sigma_over_sqrt_n():
    rng = random.Random(1)
    xs = [rng.gauss(2.5, 0.3) for _ in range(20000)]
    r = block_average(xs)
    assert abs(r["mean"] - 2.5) < 0.01
    assert abs(r["sem"] / (0.3 / math.sqrt(len(xs))) - 1) < 0.2, r["sem"]


def test_correlated_series_gets_the_larger_error():
    phi, n = 0.9, 2 ** 15
    xs = ar1(n, phi)
    sigma_x = 1.0 / math.sqrt(1 - phi * phi)
    true_sem = sigma_x / math.sqrt(n) * math.sqrt((1 + phi) / (1 - phi))
    r = block_average(xs)
    assert r["sem"] > 2.5 * r["sem_naive"]
    assert abs(r["sem"] / true_sem - 1) < 0.3, (r["sem"], true_sem)


def test_constant_series_has_zero_error():
    r = block_average([1.0] * 64)
    assert r["sem"] == 0.0 and r["mean"] == 1.0


def test_cli_reads_xvg_and_rejects_short_input():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "dist.xvg")
        with open(p, "w") as f:
            f.write("# GROMACS\n@ title \"d\"\n" + "".join(f"{i} {v}\n" for i, v in enumerate(ar1(4096, 0.5))))
        out = subprocess.run([sys.executable, os.path.join(HERE, "block_average.py"), p, "--json"],
                             capture_output=True, text=True, check=True).stdout
        assert json.loads(out)["n"] == 4096
        short = os.path.join(d, "short.dat")
        with open(short, "w") as f:
            f.write("1\n2\n3\n")
        rc = subprocess.run([sys.executable, os.path.join(HERE, "block_average.py"), short], capture_output=True)
        assert rc.returncode == 2


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
