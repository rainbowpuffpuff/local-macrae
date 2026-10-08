#!/usr/bin/env python3
"""captest: run a capability's tests inside the sandbox and write a report. Stdlib only (Python >= 3.8).

    python3 captest.py CAP_DIR [--report PATH] [--reward PATH] [--timeout S] [--smoke CMD]

CAP_DIR has manifest.json ({"name", "files": [...], "tests": [{"name", "cmd"}], ...}). Each test's cmd runs with
`sh -c` in CAP_DIR with only the policy's allowlisted environment variables (policy.json next to this file, written
by the backend from server/policy.py), within the time limit. The report records the sha256 of every manifest file
as the tests saw it, so the installer can prove the files it installs are the ones that passed.

Used twice: by the forge agent in its sandbox (CREATE), and by the Harbor verifier in a fresh sandbox with no agent
and no network (TEST). Exit 0 = every test passed.
"""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ENV = ["PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "TMPDIR", "PWD", "TERM", "PYTHONPATH",
               "PYTHONUNBUFFERED", "PYTHONHASHSEED", "MPLBACKEND", "MPLCONFIGDIR", "OMP_NUM_THREADS",
               "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_MAX_THREADS", "CUDA_VISIBLE_DEVICES"]


def load_policy():
    for p in (HERE / "policy.json", HERE.parent / "policy.json"):
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            continue
    return {}


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def tail(b, n=1500):
    s = (b or b"").decode("utf-8", "replace")
    return s[-n:]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cap_dir")
    ap.add_argument("--report", default="")
    ap.add_argument("--reward", default="")
    ap.add_argument("--timeout", type=float, default=0)
    ap.add_argument("--smoke", default="", help="an extra command that must exit 0 (image capabilities)")
    a = ap.parse_args(argv)
    cap = Path(a.cap_dir).resolve()
    policy = load_policy()
    limit = a.timeout or float(policy.get("max_test_runtime_s") or 600)
    allow = set(policy.get("env_allowlist") or DEFAULT_ENV)
    report = {"name": "", "passed": False, "tests": [], "files": {}, "problems": [], "t": time.time(),
              "sandbox": {"python": platform.python_version(), "platform": platform.platform(),
                          "host": platform.node()}, "time_limit_s": limit}
    try:
        manifest = json.loads((cap / "manifest.json").read_text())
    except (OSError, ValueError) as e:
        manifest = {}
        report["problems"].append(f"manifest.json: {e}")
    report["name"] = str(manifest.get("name") or "")
    for f in manifest.get("files") or []:
        rel = str(f.get("path") if isinstance(f, dict) else f)
        p = (cap / rel).resolve()
        if cap not in p.parents or not p.is_file():
            report["problems"].append(f"file {rel} is missing")
            continue
        report["files"][rel] = sha256(p)
    tests = [t for t in manifest.get("tests") or [] if isinstance(t, dict) and str(t.get("cmd") or "").strip()]
    if a.smoke:
        tests = tests + [{"name": "smoke", "cmd": a.smoke}]
    if not tests:
        report["problems"].append("no tests")
    env = {k: v for k, v in os.environ.items() if k in allow}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["PYTHONPATH"] = str(cap) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("MPLBACKEND", "Agg")
    t_start = time.time()
    for t in tests:
        left = limit - (time.time() - t_start)
        rec = {"name": str(t.get("name") or t["cmd"])[:120], "cmd": str(t["cmd"])[:2000], "rc": None,
               "seconds": 0.0, "passed": False}
        if left <= 0:
            rec["error"] = f"time limit of {limit:.0f} s reached"
            report["tests"].append(rec)
            continue
        t0 = time.time()
        try:
            p = subprocess.run(["sh", "-c", rec["cmd"]], cwd=str(cap), env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=left)
            rec.update(rc=p.returncode, passed=p.returncode == 0, stdout=tail(p.stdout), stderr=tail(p.stderr))
        except subprocess.TimeoutExpired as e:
            rec.update(rc=-9, error=f"timed out after {left:.0f} s", stdout=tail(e.stdout), stderr=tail(e.stderr))
        rec["seconds"] = round(time.time() - t0, 2)
        report["tests"].append(rec)
    n_ok = sum(1 for r in report["tests"] if r["passed"])
    report["n_tests"] = len(report["tests"])
    report["n_passed"] = n_ok
    report["seconds"] = round(time.time() - t_start, 2)
    report["passed"] = bool(report["tests"]) and n_ok == len(report["tests"]) and not report["problems"]
    text = json.dumps(report, indent=1)
    out = Path(a.report) if a.report else cap / "test_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    if a.reward:
        Path(a.reward).parent.mkdir(parents=True, exist_ok=True)
        Path(a.reward).write_text("1\n" if report["passed"] else "0\n")
    print(f"CAPTEST {report['name'] or cap.name}: {n_ok}/{len(report['tests'])} tests passed"
          + ("" if not report["problems"] else f"; problems: {'; '.join(report['problems'])}"))
    for r in report["tests"]:
        if not r["passed"]:
            print(f"  FAILED {r['name']} (rc {r['rc']}): {(r.get('error') or r.get('stderr') or '')[-400:]}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
