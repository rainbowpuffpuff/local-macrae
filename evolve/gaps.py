"""Gaps the agent didn't say but its trace shows: time lost installing packages, and environments that broke.

    trace_gaps(run_dir) -> [{"name", "kind": "image", "why", "evidence": [str], "step", "install_s"}]

A finished run whose agent spent INSTALL_S_MIN seconds or more on installs (pip/conda/apt/venv), or hit a known
environment failure (ensurepip missing, a module not importable, a resolver conflict), needs an environment
capability: the same packages, pinned, baked into its sandbox image (`<task_id>-env`). Nothing is proposed for a
run that already ran in an image capability (its sandbox log says so), or when one is installed for the task.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import capabilities
from .runs import load

INSTALL_S_MIN = 30.0
INSTALL_RE = re.compile(r"\b(pip3?|uv pip|python3? -m pip|conda|mamba|micromamba|apt(-get)?)\s+install\b|"
                        r"\bpython3? -m venv\b|\bvirtualenv\b", re.I)
BREAKAGE = [
    (re.compile(r"ensurepip is not available|No module named ensurepip", re.I),
     "the venv could not be created (ensurepip is not available)"),
    (re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'"), "module {0} was not importable"),
    (re.compile(r"ResolutionImpossible|conflicting dependencies|incompatible with|requires .* but you have", re.I),
     "package versions conflicted"),
    (re.compile(r"error: externally-managed-environment", re.I), "the system Python refused pip installs"),
]
PKG_RE = re.compile(r"install\s+((?:-[\w-]+(?:\s+\S+)?\s+)*)(.+)$")


def _packages(cmd: str) -> list[str]:
    out = []
    for part in re.split(r"&&|;|\|\|", cmd):
        m = INSTALL_RE.search(part)
        if not m or "install" not in part:
            continue
        tail = part.split("install", 1)[1]
        for tok in tail.split():
            if tok.startswith("-") or tok in ("|", ">") or "/" in tok or tok.endswith(".txt"):
                continue
            out.append(tok.strip("'\""))
    return list(dict.fromkeys(out))


def trace_gaps(run_dir: str | Path) -> list[dict]:
    run = load(run_dir)
    if not run.done or not run.task_id:
        return []
    name = capabilities.norm_name(f"{run.task_id}-env")
    if any(c.get("kind") == "image" for c in capabilities.for_task(run.task_id, kinds=("image",))):
        return []
    for s in run.agent_steps:
        if any("capability image " in msg for _, msg in s.log):
            return []
    install_s, evidence, pkgs, broke, where = 0.0, [], [], [], ""
    for s in run.agent_steps:
        for tr in s.trials:
            for c in tr.calls:
                cmd = c.command
                if cmd and INSTALL_RE.search(cmd):
                    secs = float(c.seconds or 0)
                    install_s += secs
                    pkgs += _packages(cmd)
                    where = where or s.key
                    evidence.append(f"`{' '.join(cmd.split())[:160]}` took {secs:.0f} s in {s.key}")
                for rx, msg in BREAKAGE:
                    m = rx.search(c.output or "")
                    if m:
                        text = msg.format(*m.groups()) if m.groups() else msg
                        if text not in broke:
                            broke.append(text)
                            where = where or s.key
                            evidence.append(f"{text}: `{' '.join((cmd or c.name).split())[:120]}` in {s.key}")
    if install_s < INSTALL_S_MIN and not broke:
        return []
    pkgs = list(dict.fromkeys(pkgs))[:20]
    why = (f"the agent spent {install_s:.0f} s installing packages" + (f" ({', '.join(pkgs[:8])})" if pkgs else "")
           + (" and " + "; ".join(broke) if broke else "") + ": bake them, pinned, into this task's sandbox image")
    return [{"name": name, "kind": "image", "why": why, "evidence": evidence[:12], "step": where,
             "install_s": round(install_s, 1), "packages": pkgs, "breakage": broke}]
