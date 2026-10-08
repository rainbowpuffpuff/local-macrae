"""INSTALL: the backend's gate between what an agent built and the capability registry.

The forge flow (evolve/forge/forge.yaml) calls this module from its script steps, which run on the backend:

    python -m server.installer check DIR [--name N]        create's `check:`: manifest + policy + the sandbox's
                                                           test_report.json matches the files (exit 0 = pass)
    python -m server.installer package DIR --out OUT       hash + stage the files, build the Harbor test task
                                                           (a fresh sandbox, the oracle agent, no network); ledger
                                                           `create`; prints JSON
    python -m server.installer install                     reads $FLOW_CONTEXT: verify sha256 (staged = tested =
                                                           packaged), the test trial's reward and report, the
                                                           policy; then evolve.capabilities.install(); ledger
                                                           `test` + `install`, or `rejected` with the reason

The host never runs capability code: it reads, hashes and copies files, and reads the reports the sandboxes wrote.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Optional

from . import config, policy

FORGE_DIR = config.REPO_ROOT / "evolve" / "forge"
CAPTEST = FORGE_DIR / "captest.py"
REPORT = "test_report.json"
TEST_BASE_IMAGE = "python:3.12-slim"  # tools and writing capabilities are tested on a plain Python image


def _caps():
    from evolve import capabilities
    return capabilities


class Rejected(Exception):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


# ── reading what the agent wrote ────────────────────────────────────────────


def load_manifest(d: Path) -> dict:
    try:
        m = json.loads((d / "manifest.json").read_text())
    except OSError:
        raise Rejected(["manifest.json is missing"])
    except ValueError as e:
        raise Rejected([f"manifest.json is not valid JSON: {e}"])
    if not isinstance(m, dict):
        raise Rejected(["manifest.json is not a JSON object"])
    return m


def file_list(m: dict) -> list[str]:
    return [str(f.get("path") if isinstance(f, dict) else f) for f in m.get("files") or []]


def policy_problems(d: Path, m: dict) -> list[str]:
    """Everything the policy says about this capability folder (manifest, files, fragment, requirements)."""
    out = list(policy.check_manifest(m))
    files = file_list(m)
    total = 0
    for rel in files:
        p = (d / rel)
        try:
            rp = p.resolve()
        except OSError:
            out.append(f"{rel} can't be read")
            continue
        if d.resolve() not in rp.parents or p.is_symlink():
            out.append(f"{rel} is outside the capability folder or a symlink")
            continue
        if not p.is_file():
            out.append(f"{rel} is listed in the manifest but missing")
            continue
        size = p.stat().st_size
        total += size
        if size > policy.MAX_FILE_BYTES:
            out.append(f"{rel} is {size} bytes, over the {policy.MAX_FILE_BYTES} limit")
            continue
        text = p.read_text(errors="replace")
        out += policy.scan_text(rel, text)
    if total > policy.MAX_TOTAL_BYTES:
        out.append(f"the files are {total} bytes in all, over the {policy.MAX_TOTAL_BYTES} limit")
    ep = str(m.get("entrypoint") or "")
    if ep and ep not in files:
        out.append(f"the entrypoint {ep} is not in files")
    reqs = m.get("requirements") or []
    if isinstance(reqs, list):
        out += policy.check_requirements("\n".join(str(r) for r in reqs))
    if m.get("kind") == "image":
        img = m.get("image") if isinstance(m.get("image"), dict) else {}
        for key, check in (("fragment", policy.check_fragment), ("requirements", policy.check_requirements)):
            rel = str(img.get(key) or "")
            if not rel:
                continue
            if rel not in files:
                out.append(f"image.{key} {rel} is not in files")
                continue
            if (d / rel).is_file():
                out += check((d / rel).read_text(errors="replace"))
    return list(dict.fromkeys(out))


def check(d: Path, name: str = "") -> tuple[bool, list[str], Optional[dict]]:
    """create's check: the folder the agent returned is a capability within the policy, and its own run of
    captest (test_report.json) passed on exactly these files. Returns (ok, problems, report)."""
    try:
        m = load_manifest(d)
    except Rejected as e:
        return False, e.reasons, None
    problems = policy_problems(d, m)
    caps = _caps()
    if name and caps.norm_name(str(m.get("name") or "")) != caps.norm_name(name):
        problems.append(f"the manifest's name is {m.get('name')!r}; it must be {caps.norm_name(name)!r} (the gap's name)")
    report = None
    try:
        report = json.loads((d / REPORT).read_text())
    except OSError:
        problems.append(f"{REPORT} is missing: run `python3 captest.py .` in the sandbox after writing the tests")
    except ValueError:
        problems.append(f"{REPORT} is not valid JSON")
    if isinstance(report, dict):
        if not report.get("passed"):
            failed = [t.get("name") for t in report.get("tests") or [] if not t.get("passed")]
            problems.append("the tests did not pass in the sandbox" + (f": {', '.join(map(str, failed))}" if failed
                                                                      else ""))
        n_manifest = len([t for t in m.get("tests") or [] if isinstance(t, dict)])
        n_report = len([t for t in report.get("tests") or [] if t.get("name") != "smoke"])
        if n_report < n_manifest:
            problems.append(f"the report has {n_report} test results for {n_manifest} tests in the manifest")
        for rel in file_list(m):
            p = d / rel
            if p.is_file() and report.get("files", {}).get(rel) != caps.sha256_file(p):
                problems.append(f"{rel} changed after the tests ran (sha256 differs from {REPORT}); run captest again")
    return not problems, problems, report


# ── packaging the independent test ──────────────────────────────────────────


def _record(m: dict, d: Path, run_id: str, step: str, gap: dict) -> dict:
    caps = _caps()
    files = []
    for rel in file_list(m):
        p = d / rel
        files.append({"path": rel, "sha256": caps.sha256_file(p), "bytes": p.stat().st_size})
    now = time.time()
    kind = str(m.get("kind") or "tool")
    rec = {
        "name": caps.norm_name(str(m["name"])), "kind": kind, "purpose": " ".join(str(m.get("purpose")).split()),
        "entrypoint": m.get("entrypoint") or "", "usage": m.get("usage") or "",
        "inputs": m.get("inputs") or [], "outputs": m.get("outputs") or [],
        "files": files, "tests": [{"name": str(t.get("name") or t.get("cmd"))[:120], "cmd": str(t.get("cmd"))[:2000]}
                                  for t in m.get("tests") or [] if isinstance(t, dict)],
        "requirements": [str(r) for r in m.get("requirements") or []],
        "applies_to": [str(x) for x in (m.get("applies_to") or ["*"])][:20],
        "permissions": {"env": [], "network": policy.network_for(kind), "registry_write": False,
                        "max_runtime_s": min(float((m.get("permissions") or {}).get("max_runtime_s")
                                                   or policy.MAX_RUNTIME_S), policy.MAX_RUNTIME_S)},
        "created_by": {"run_id": run_id, "step": step}, "gap": gap or {}, "created": now,
        "runs_in": policy.RUNS_IN, "policy_sha256": policy.POLICY_SHA256,
    }
    if kind == "image":
        img = m.get("image") or {}
        rec["image"] = {"fragment": img.get("fragment"), "requirements": img.get("requirements"),
                        "smoke": str(img.get("smoke") or "")[:500]}
    rec["sha256"] = caps.bundle_sha(files)
    return rec


def _write(p: Path, text: str, mode: int = 0o644) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    p.chmod(mode)


def test_task(rec: dict, staged: Path, dest: Path) -> Path:
    """A Harbor task that re-runs the capability's tests in a fresh sandbox: the oracle agent does nothing, the
    verifier runs captest on a copy of the staged files with no network and the policy's time limit. An image
    capability's environment is the same Dockerfile later runs build (agent image + fragment); the smoke command
    must pass in it."""
    if dest.exists():
        shutil.rmtree(dest)
    env_dir = dest / "environment"
    kind = rec["kind"]
    if kind == "image":
        from agent_runner import harbor
        img = dict(rec.get("image") or {}, name=rec["name"], version="(test)", sha256=rec["sha256"], dir=str(staged))
        harbor.write_image_environment(env_dir, img, base_image=os.environ.get("AGENT_RUNNER_MODAL_IMAGE", ""))
    else:
        env_dir.mkdir(parents=True)
        lines = [f"FROM {TEST_BASE_IMAGE}", "ENV PYTHONUNBUFFERED=1 MPLBACKEND=Agg"]
        if rec.get("requirements"):
            (env_dir / "requirements.txt").write_text("\n".join(rec["requirements"]) + "\n")
            lines += ["COPY requirements.txt /tmp/capability-requirements.txt",
                      "RUN pip install --no-cache-dir -r /tmp/capability-requirements.txt"]
        lines.append("WORKDIR /app")
        (env_dir / "Dockerfile").write_text("\n".join(lines) + "\n")
    tests = dest / "tests"
    shutil.copytree(staged, tests / "cap")
    shutil.copy2(CAPTEST, tests / "captest.py")
    (tests / "policy.json").write_text(json.dumps(policy.describe(), indent=1))
    smoke = (rec.get("image") or {}).get("smoke") if kind == "image" else ""
    smoke_arg = f" --smoke {json.dumps(smoke)}" if smoke else ""
    _write(tests / "test.sh", "#!/bin/bash\n"
           "# TEST: the capability's own tests, again, in a fresh sandbox with no agent and no network.\n"
           "mkdir -p /logs/verifier\n"
           f"python3 /tests/captest.py /tests/cap --report /logs/verifier/report.json "
           f"--reward /logs/verifier/reward.txt --timeout {policy.MAX_TEST_RUNTIME_S}{smoke_arg}\n"
           "exit 0  # the reward file carries the verdict\n", 0o755)
    _write(dest / "solution" / "solve.sh", "#!/bin/bash\n# nothing to do: the verifier runs the tests\ntrue\n", 0o755)
    (dest / "instruction.md").write_text(f"Verify the capability {rec['name']}: the verifier runs its tests.\n")
    toml = [
        'schema_version = "1.4"', "",
        "[metadata]", f"macrae_capability = {json.dumps(rec['name'])}", f"sha256 = {json.dumps(rec['sha256'])}", "",
        "[agent]", "timeout_sec = 120.0", 'network_mode = "no-network"', "",
        "[verifier]", f"timeout_sec = {float(policy.MAX_TEST_RUNTIME_S + 120):.1f}", 'network_mode = "no-network"', "",
        "[environment]", f"build_timeout_sec = {float(policy.MAX_BUILD_S):.1f}", 'workdir = "/app"',
        'network_mode = "no-network"',
    ]
    (dest / "task.toml").write_text("\n".join(toml) + "\n")
    return dest


def package(src: Path, out: Path, run_id: str = "", step: str = "create", name: str = "",
            gap: Optional[dict] = None) -> dict:
    """Stage the files the agent's tests passed on, and build the independent test. Records `create`."""
    ok, problems, report = check(src, name)
    if not ok:
        raise Rejected(problems)
    m = load_manifest(src)
    rec = _record(m, src, run_id, step, gap or {})
    staged = out / "staged" / rec["name"]
    if staged.exists():
        shutil.rmtree(staged)
    staged.mkdir(parents=True)
    for f in rec["files"]:
        (staged / f["path"]).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / f["path"], staged / f["path"])
    # the manifest the sandbox sees: the record (hashes included), so captest can verify the same files
    (staged / "manifest.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))
    (out / "staged" / f"{rec['name']}.record.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))
    task_dir = test_task(rec, staged, out / "test-task")
    _caps().record("create", rec["name"], run_id=run_id, step=step, kind=rec["kind"], sha256=rec["sha256"],
                   files=[f["path"] for f in rec["files"]], tests=len(rec["tests"]), purpose=rec["purpose"][:300],
                   sandbox_tests=(report or {}).get("n_passed"))
    return {"name": rec["name"], "kind": rec["kind"], "sha256": rec["sha256"], "staged": str(staged),
            "record": str(out / "staged" / f"{rec['name']}.record.json"), "task_dir": str(task_dir),
            "files": [f["path"] for f in rec["files"]], "tests": len(rec["tests"])}


# ── the verdict ─────────────────────────────────────────────────────────────


def _ts(v: Any) -> Optional[float]:
    from .trace import ts
    return ts(v)


def _span(t: Any) -> float:
    if not isinstance(t, dict):
        return 0.0
    a, b = _ts(t.get("started_at")), _ts(t.get("finished_at"))
    return max(0.0, b - a) if a and b else 0.0


def read_test(job_dir: Path) -> dict:
    """The independent test's outcome from its Harbor job: reward, the verifier's report, timings."""
    out: dict[str, Any] = {"job": job_dir.name, "reward": None, "report": None, "exception": "", "trial": ""}
    trials = sorted(p for p in job_dir.iterdir() if p.is_dir() and (p / "result.json").is_file()) \
        if job_dir.is_dir() else []
    if not trials:
        out["exception"] = "the test job has no trial"
        return out
    t = trials[-1]
    out["trial"] = t.name
    res = config.read_json(t / "result.json") or {}
    rw = ((res.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    if rw is None and (t / "verifier" / "reward.txt").is_file():
        try:
            rw = float((t / "verifier" / "reward.txt").read_text().strip())
        except (OSError, ValueError):
            rw = None
    out["reward"] = float(rw) if isinstance(rw, (int, float)) else None
    ex = res.get("exception_info") or {}
    if ex:
        out["exception"] = f"{ex.get('exception_type', '')}: {ex.get('exception_message', '')}".strip(": ")
    rep = config.read_json(t / "verifier" / "report.json")
    out["report"] = rep if isinstance(rep, dict) else None
    out["setup_s"] = round(_span(res.get("environment_setup")), 1)
    out["seconds"] = round(_span(res.get("verifier")), 1) or (rep or {}).get("seconds")
    return out


def verify_for_install(record: dict, staged: Path, test: dict) -> list[str]:
    caps = _caps()
    problems = []
    for f in record.get("files") or []:
        p = staged / f["path"]
        if not p.is_file():
            problems.append(f"{f['path']} is missing from the staged copy")
        elif caps.sha256_file(p) != f["sha256"]:
            problems.append(f"{f['path']} changed after packaging (sha256 mismatch)")
    if caps.bundle_sha(record.get("files") or []) != record.get("sha256"):
        problems.append("the bundle sha256 does not match its files")
    if test.get("exception"):
        problems.append(f"the test sandbox failed: {test['exception'][:300]}")
    if test.get("reward") != 1.0:
        problems.append(f"the tests did not pass in a fresh sandbox (reward {test.get('reward')})")
    rep = test.get("report")
    if not isinstance(rep, dict):
        problems.append("the test sandbox wrote no report")
    else:
        if not rep.get("passed"):
            failed = [t.get("name") for t in rep.get("tests") or [] if not t.get("passed")]
            problems.append("failed in the fresh sandbox: " + (", ".join(map(str, failed)) or "see the report"))
        for f in record.get("files") or []:
            if (rep.get("files") or {}).get(f["path"]) != f["sha256"]:
                problems.append(f"{f['path']}: the tested file's sha256 differs from the one being installed")
    staged_m = config.read_json(staged / "manifest.json")
    problems += policy.check_manifest(record)
    if isinstance(staged_m, dict) and staged_m.get("sha256") != record.get("sha256"):
        problems.append("the staged manifest was changed")
    # the files themselves, against the policy (again: nothing between package and install may loosen it)
    problems += [p for p in policy_problems(staged, record) if "is listed in the manifest but missing" not in p]
    return list(dict.fromkeys(problems))


def reject(name: str, reasons: list[str], run_id: str = "", step: str = "install", **extra: Any) -> dict:
    e = _caps().record("rejected", name, run_id=run_id, step=step, reason="; ".join(reasons)[:1500],
                       reasons=reasons[:12], **extra)
    return e or {"event": "rejected", "name": name, "reasons": reasons}


def install_from(record_path: Path, staged: Path, test_job: Path, run_id: str = "") -> dict:
    """Verify, then install. Returns the registry record. Raises Rejected (after recording it)."""
    caps = _caps()
    record = config.read_json(record_path)
    if not isinstance(record, dict):
        raise Rejected([f"no packaged record at {record_path}"])
    name = record["name"]
    test = read_test(test_job)
    rep = test.get("report") or {}
    passed = test.get("reward") == 1.0 and bool(rep.get("passed"))
    caps.record("test", name, run_id=run_id, step="test", passed=passed, reward=test.get("reward"),
                job=test.get("job"), n_tests=rep.get("n_tests"), n_passed=rep.get("n_passed"),
                seconds=test.get("seconds"), setup_s=test.get("setup_s"), sha256=record.get("sha256"),
                sandbox=(rep.get("sandbox") or {}).get("platform"))
    problems = verify_for_install(record, staged, test)
    if problems:
        reject(name, problems, run_id=run_id, sha256=record.get("sha256"))
        raise Rejected(problems)
    record["tested"] = time.time()
    record["test"] = {"run_id": run_id, "job": test.get("job"), "trial": test.get("trial"),
                      "reward": test.get("reward"), "passed": True, "n_tests": rep.get("n_tests"),
                      "seconds": test.get("seconds"), "setup_s": test.get("setup_s"),
                      "sandbox": (rep.get("sandbox") or {})}
    rec = caps.install(record, staged)
    caps.record("install", name, run_id=run_id, step="install", version=rec["version"], sha256=rec["sha256"],
                kind=rec.get("kind"), purpose=rec.get("purpose", "")[:300])
    return rec


def install_from_context(ctx: dict, run_id: str, name: str) -> dict:
    """The forge flow's install step: decide from the results of create, package and test."""
    create = ctx.get("create") or {}
    pkg = ctx.get("package") or {}
    test = ctx.get("test") or {}
    out = pkg.get("output") if isinstance(pkg.get("output"), dict) else None
    if not create.get("ok"):
        reasons = []
        files = create.get("files")
        if files and Path(files).is_dir():
            _, reasons, _ = check(Path(files), name)
        reasons = reasons or [f"the agent could not build a capability that passes its tests "
                              f"({str(create.get('error') or create.get('status') or 'no result')[:300]})"]
        reject(name, reasons, run_id=run_id, step="create")
        raise Rejected(reasons)
    if not out or not pkg.get("ok"):
        reasons = [f"packaging failed: {str(pkg.get('error') or 'no output')[:400]}"]
        reject(name, reasons, run_id=run_id, step="package")
        raise Rejected(reasons)
    job = test.get("job_dir") or ""
    if not job:
        reasons = [f"the independent test did not run: {str(test.get('error') or test.get('status'))[:300]}"]
        _caps().record("test", name, run_id=run_id, step="test", passed=False, reason=reasons[0])
        reject(name, reasons, run_id=run_id)
        raise Rejected(reasons)
    return install_from(Path(out["record"]), Path(out["staged"]), Path(job), run_id)


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m server.installer", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="create's check: manifest, policy and the sandbox's test report")
    c.add_argument("dir", nargs="?", default=".")
    c.add_argument("--name", default="")
    p = sub.add_parser("package", help="stage the files and build the independent Harbor test")
    p.add_argument("dir")
    p.add_argument("--out", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--run-id", default=os.environ.get("MACRAE_RUN_ID", ""))
    p.add_argument("--gap", default="", help="JSON {run_id, step, why} (default: OUT/gap.json, from the launcher)")
    i = sub.add_parser("install", help="verify and install (reads $FLOW_CONTEXT)")
    i.add_argument("--name", required=True)
    i.add_argument("--run-id", default="")
    a = ap.parse_args(argv)
    run_id = getattr(a, "run_id", "") or Path(os.environ.get("FLOW_RUN_DIR", "")).name
    if a.cmd == "check":
        ok, problems, report = check(Path(a.dir), a.name)
        if ok:
            print(f"capability OK: {len((report or {}).get('tests') or [])} tests passed in the sandbox, "
                  "manifest within the policy")
            return 0
        print("The capability is not ready to install:")
        for x in problems:
            print(f"- {x}")
        return 1
    if a.cmd == "package":
        try:
            gap = json.loads(a.gap) if a.gap else (config.read_json(Path(a.out) / "gap.json") or {})
        except ValueError:
            gap = {}
        try:
            print(json.dumps(package(Path(a.dir), Path(a.out), run_id=run_id, name=a.name, gap=gap)))
        except Rejected as e:
            print("not packaged: " + str(e), file=sys.stderr)
            return 1
        return 0
    if a.cmd == "install":
        ctx_file = os.environ.get("FLOW_CONTEXT", "")
        ctx = config.read_json(Path(ctx_file)) if ctx_file else None
        if not isinstance(ctx, dict):
            print("install needs $FLOW_CONTEXT (run it as the forge flow's install step)", file=sys.stderr)
            return 2
        try:
            rec = install_from_context(ctx, run_id, a.name)
        except Rejected as e:
            print(json.dumps({"installed": False, "name": a.name, "reasons": e.reasons}))
            print("rejected: " + str(e), file=sys.stderr)
            return 1
        print(json.dumps({"installed": True, "name": rec["name"], "version": rec["version"],
                          "sha256": rec["sha256"], "kind": rec.get("kind")}))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
