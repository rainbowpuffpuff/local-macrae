"""Starting a forge run: the CREATE → TEST → INSTALL flow (evolve/forge/forge.yaml) for one missing capability.

    forge.start(name, why, kind="", gap_run="", gap_step="", task_id="", evidence=[])  → {"status", "run_id"?}

Called by server/capabilities.py when a run's trace shows a gap (a CAPABILITY_GAP line, or slow installs /
version breakage found after the run), or by POST /api/capabilities/forge. A capability is built once: nothing
starts if it is installed, already being forged, or failed MAX_TRIES times in the last day. The forge run is a
normal agent_runner run (its own trace, costs, live events), with a sidecar marking it as task "forge".

    MACRAE_FORGE=off              never start one (gaps are still recorded)
    MACRAE_FORGE_ENVIRONMENT      where its sandboxes run (default modal)
    MACRAE_FORGE_MAX_TRIES        failed forges per capability per day before giving up (default 2)
    MACRAE_FORGE_MAX_ACTIVE       forge runs at once (default 2)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import config, live, policy, runs

log = logging.getLogger("macrae.server")

FLOW = config.REPO_ROOT / "evolve" / "forge" / "forge.yaml"
TASK = {"id": "forge", "title": "Forge a capability"}
_lock = threading.Lock()


def _caps() -> Any:
    from . import bridges
    return bridges._import("evolve.capabilities")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def enabled() -> bool:
    return os.environ.get("MACRAE_FORGE", "").strip().lower() not in ("off", "0", "false", "no")


IMAGE_HINT = ("install", "image", "environment", "env", "preinstall", "pip", "conda", "venv", "ensurepip",
              "version", "dependency", "dependencies", "package")
WRITING_HINT = ("style", "template", "manuscript", "writing", "prose", "citation format", "latex", "references")


def kind_for(name: str, why: str, given: str = "") -> str:
    if given in policy.KINDS:
        return given
    words = f"{name} {why}".lower()
    if name.endswith(("-env", "-image")) or sum(w in words for w in IMAGE_HINT) >= 2:
        return "image"
    if any(w in words for w in WRITING_HINT):
        return "writing"
    return "tool"


def _active_forges(caps: Any) -> dict[str, str]:
    """name → run id for forge runs still going."""
    out = {}
    for name, entries in caps.forges().items():
        for e in reversed(entries or []):
            st = runs.read_state(str(e.get("run_id") or ""))
            if st is not None and runs.public_status(str(st.get("status"))) == "running":
                out[name] = e["run_id"]
                break
    return out


def _recent_failures(caps: Any, name: str, within: float = 86400) -> int:
    now = time.time()
    n = 0
    for e in caps.forges().get(name) or []:
        if now - float(e.get("t") or 0) > within:
            continue
        st = runs.read_state(str(e.get("run_id") or ""))
        if st is not None and runs.public_status(str(st.get("status"))) in ("failed", "cancelled"):
            n += 1
    return n


def gap_md(name: str, kind: str, why: str, task_id: str, gap_run: str, gap_step: str, evidence: list[str],
           existing: list[dict]) -> str:
    lines = [f"# Missing capability: {name}", "",
             f"- kind: **{kind}**", f"- needed by: task `{task_id or '?'}`, run `{gap_run or '?'}`, step "
             f"`{gap_step or '?'}`", f"- why: {why}", ""]
    if evidence:
        lines += ["## Evidence from that run's trace", ""] + [f"- {e}" for e in evidence[:16]] + [""]
    lines += ["## What to build", ""]
    if kind == "image":
        lines += [
            "An **environment capability**: the packages this task keeps installing, baked into its sandbox image",
            "so later runs start with them. Ship:",
            "- `requirements.txt`: every package pinned with `==` (pin the versions you verified work together);",
            "- `Dockerfile.fragment`: only `RUN`, `ENV`, `WORKDIR`, `LABEL` lines. Your files are copied to",
            f"  `{policy.IMAGE_CAP_DIR}/{name}/` before the fragment runs, on top of the agent image (Ubuntu 24.04,",
            "  python3 with python3-venv, so `python3 -m venv` works and the 'ensurepip is not available' failure",
            "  can't happen). For example:",
            f"  `RUN python3 -m venv /opt/calc && /opt/calc/bin/pip install --no-cache-dir -r {policy.IMAGE_CAP_DIR}/{name}/requirements.txt`",
            "- `image.smoke` in the manifest: one command that proves the image works (e.g. imports + a 2-second",
            "  calculation), and tests that do the same;",
            "- `applies_to`: the task ids that should run in this image.",
            "In this sandbox, install the same pinned requirements into /opt/calc yourself and run the tests against",
            "it, so you know the pins work; the independent test then builds the image for real and runs the smoke",
            "command in it.",
        ]
    elif kind == "writing":
        lines += [
            "A **writing capability**: style rules or templates as Markdown files, plus a small checker script",
            "(stdlib Python) that validates a document against the rules, with tests on good and bad examples.",
        ]
    else:
        lines += [
            "A **tool**: a small, general script with a command-line interface and JSON output (stdlib Python,",
            "plus numpy if it really helps: list it in `requirements` pinned with `==`). Tests must check results",
            "against answers known independently: analytic cases (e.g. block averaging of uncorrelated noise gives",
            "a standard error of σ/√N; an ideal gas has g(r) = 1), synthetic data with a planted answer, edge cases.",
        ]
    if existing:
        lines += ["", "## Already installed (reuse, don't duplicate)", ""] + [
            f"- {c['name']} v{c.get('version')}: {c.get('purpose')}" for c in existing]
    return "\n".join(lines) + "\n"


def spec_md() -> str:
    d = policy.describe()
    ex = {"name": "block-average", "kind": "tool", "purpose": "Mean and block-averaged standard error of a time "
          "series (MD observable), choosing the block size at the plateau.", "entrypoint": "block_average.py",
          "usage": "python3 /app/capabilities/block-average/block_average.py series.dat --column 2 --json",
          "inputs": [{"name": "series", "type": "file", "description": "whitespace columns; # comments skipped"}],
          "outputs": [{"name": "stdout", "type": "json", "description": "{mean, sem, block_size, n_blocks}"}],
          "files": ["block_average.py", "test_block_average.py"],
          "tests": [{"name": "white noise: sem ≈ σ/√N", "cmd": "python3 test_block_average.py"}],
          "requirements": [], "applies_to": ["*"], "permissions": {"env": [], "network": [], "max_runtime_s": 120}}
    return "\n".join([
        "# Capability manifest (manifest.json) and the fixed limits", "",
        "```json", json.dumps(ex, indent=2, ensure_ascii=False), "```", "",
        "- `files`: every file of the capability, relative paths (tests included). Nothing else is installed.",
        "- `tests`: commands run with `sh -c` inside the capability folder by `captest.py`, which only passes the",
        "  allowlisted environment variables. Exit 0 = pass. They run again later in a fresh sandbox with no",
        "  network, so they must not download anything.",
        "- image capabilities add `\"image\": {\"fragment\": \"Dockerfile.fragment\", \"requirements\": "
        "\"requirements.txt\", \"smoke\": \"/opt/calc/bin/python -c 'import pyscf'\"}`.",
        "", "## Authority (fixed; the installer rejects a manifest that asks for more)", "",
        *[f"- {r}" for r in d["rules"]],
        f"- Allowed environment variables: {', '.join(d['env_allowlist'])}.",
        f"- Network: tools none; image builds only {', '.join(d['network']['image'])}.",
        f"- At most {d['max_files']} files, {d['max_file_bytes'] // 1024} KiB each, {d['max_total_bytes'] // 1024} KiB "
        "in all.",
        "- Never reference secrets, the registry or the backend's paths in the files.",
        "", "Run `python3 /app/capability/captest.py /app/capability` when you are done; it must say all tests passed.",
    ]) + "\n"


def prepare_workspace(run_dir: Path, name: str, kind: str, why: str, task_id: str, gap_run: str, gap_step: str,
                      evidence: list[str]) -> tuple[Path, Path]:
    caps = _caps()
    ws = run_dir / "capability"
    ws.mkdir(parents=True, exist_ok=True)
    existing = [c for c in caps.installed() if c["name"] != name]
    (ws / "GAP.md").write_text(gap_md(name, kind, why, task_id, gap_run, gap_step, evidence, existing))
    (ws / "SPEC.md").write_text(spec_md())
    shutil.copy2(config.REPO_ROOT / "evolve" / "forge" / "captest.py", ws / "captest.py")
    (ws / "policy.json").write_text(json.dumps(policy.describe(), indent=1))
    fdir = run_dir / "forge"
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / "gap.json").write_text(json.dumps({"run_id": gap_run, "step": gap_step, "why": why,
                                               "task_id": task_id, "kind": kind}, ensure_ascii=False))
    return ws, fdir


def start(name: str, why: str, kind: str = "", gap_run: str = "", gap_step: str = "", task_id: str = "",
          evidence: Optional[list[str]] = None, origin: Optional[str] = None, force: bool = False) -> dict:
    """Start the forge for one capability, unless it exists, is being built, or keeps failing."""
    from . import launch
    caps = _caps()
    name = caps.norm_name(name)
    if not policy.NAME_RE.match(name):
        return {"status": "invalid", "name": name, "detail": "not a valid capability name"}
    if not enabled():
        return {"status": "off", "name": name, "detail": "MACRAE_FORGE=off"}
    with _lock:
        if caps.get(name) and not force:
            return {"status": "installed", "name": name, "version": caps.get(name).get("version")}
        active = _active_forges(caps)
        if name in active:
            return {"status": "in-progress", "name": name, "run_id": active[name]}
        if len(active) >= _int("MACRAE_FORGE_MAX_ACTIVE", 2):
            return {"status": "busy", "name": name, "detail": f"{len(active)} forge runs are already going"}
        if _recent_failures(caps, name) >= _int("MACRAE_FORGE_MAX_TRIES", 2) and not force:
            return {"status": "gave-up", "name": name, "detail": "failed too often in the last day"}
        kind = kind_for(name, why, kind)
        run_id = launch.new_run_id("forge")
        run_dir = config.runs_dir() / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        live.setup_run(run_dir, live.public_url(origin))
        ws, fdir = prepare_workspace(run_dir, name, kind, why, task_id, gap_run, gap_step, list(evidence or []))
        caps.export(run_dir, "forge", "dawn")  # the forge sees what's installed (and the gap protocol)
        title = f"Forge capability: {name}"
        meta = {"run_id": run_id, "task_id": TASK["id"], "title": title, "flow": str(FLOW), "started": time.time(),
                "inputs": {"capability": name, "kind": kind, "why": why, "gap_run": gap_run}, "forge": True}
        (run_dir / "macrae.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
        runs.save_sidecar(run_id, {"id": TASK["id"], "title": title},
                          {"capability": name, "kind": kind, "gap_run": gap_run})
        caps.note_forge(name, {"run_id": run_id, "t": time.time(), "kind": kind, "gap_run": gap_run,
                               "gap_step": gap_step, "why": why[:500]})
        vars_ = {"capability": name, "kind": kind, "why": why[:500], "workspace": str(ws), "forge_dir": str(fdir),
                 "environment": os.environ.get("MACRAE_FORGE_ENVIRONMENT", "").strip() or "modal",
                 "python": sys.executable, "task_id": TASK["id"]}
        try:
            launch.spawn_engine(FLOW, run_id, vars_)
        except Exception as e:
            launch._fail_run(run_dir, run_id, "forge", f"could not start the forge: {type(e).__name__}: {e}")
            return {"status": "failed", "name": name, "run_id": run_id, "detail": str(e)[:300]}
    log.info("forge %s (%s) for gap in %s: run %s", name, kind, gap_run or "-", run_id)
    return {"status": "started", "name": name, "kind": kind, "run_id": run_id}


def is_forge_run(run_id: str) -> bool:
    return runs.load_sidecar(run_id).get("task_id") == TASK["id"]
