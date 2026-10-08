"""Capability seeds for the Frankenstein story: what our first Modal runs taught us, and the capability they call for.

The dusk observations (first_modal_runs.json: a 170 s agent image build, ~1 min PySCF reinstall per run, a 4-min
sleep while waiting, the "ensurepip is not available" venv failure) point at one missing capability: a calculation
image with the packages pinned and preinstalled. calc-image/ is that capability as we expect the agent to learn
it (Dockerfile + requirements.lock + smoke.py + manifest.json). It is a spec and a yardstick, not something that is
installed by default: the backend's CREATE step has the agent write its own, `checks.py image-capability` judges
it, the installer registers it, and later small-calc runs get it through the flow var `calc_image`.

    python -m tasks.capabilities show                 # evidence, gap, expected capability + its check
    python -m tasks.capabilities check [calc-image]   # TEST the spec (pins, hashes, policy)
    python -m tasks.capabilities rehash calc-image    # after editing the spec: manifest.json files → sha256
    python -m tasks.capabilities gaps FILE...         # recognise missing capabilities in notes / logs / transcripts
    python -m tasks.capabilities lessons [--apply]    # the dusk lessons, evolve-shaped (--apply: add them to evolve)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "first_modal_runs.json"
TASKS_DIR = HERE.parent
if str(TASKS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(TASKS_DIR.parent))

from tasks import checks  # noqa: E402  (stdlib only)
from tasks import research  # noqa: E402

# What a run's notes, logs or transcript say → the capability that would have avoided it. Patterns are matched
# line by line; `why` is filled from the match.
RECOGNISERS: list[tuple[str, re.Pattern, str]] = [
    ("calc-image", re.compile(r"ensurepip is not\s*available", re.I),
     "python3 -m venv failed: ensurepip is not available (no python3-venv in the sandbox image)"),
    ("calc-image", re.compile(r"No module named '?(pyscf|numpy|scipy|matplotlib|h5py)'?", re.I),
     "{0} is not installed in the sandbox"),
    ("calc-image", re.compile(r"pip3?\s+install\b[^\n]*\b(pyscf)\b", re.I),
     "the agent installed {0} itself"),
    ("calc-image", re.compile(r"/opt/calc/bin/python: No such file", re.I),
     "no preinstalled calc environment in /opt/calc"),
]
SLEEP_RE = re.compile(r"\bsleep\s+(\d+)\b")
SECONDS_RE = re.compile(r"(?:took|in|:)\s*(\d+(?:\.\d+)?)\s*s\b")


def evidence() -> dict:
    return json.loads(EVIDENCE.read_text())


def spec_dir(name: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", name or ""):
        raise ValueError(f"bad capability name {name!r}")
    d = HERE / name
    if not (d / "manifest.json").is_file():
        raise FileNotFoundError(f"no capability spec {name!r} in {HERE}")
    return d


def specs() -> list[str]:
    return sorted(p.parent.name for p in HERE.glob("*/manifest.json"))


def manifest(name: str) -> dict:
    return json.loads((spec_dir(name) / "manifest.json").read_text())


def rehash(name: str) -> dict:
    """Record the sha256 of every spec file (except manifest.json) in manifest.json "files"."""
    d = spec_dir(name)
    man = manifest(name)
    man["files"] = {p.name: checks.sha256_file(p) for p in sorted(d.iterdir())
                    if p.is_file() and p.name != "manifest.json" and not p.name.startswith(".")}
    (d / "manifest.json").write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")
    return man


def check(name: str) -> list[str]:
    """The TEST a spec must pass before INSTALL (shape, pins, hashes, no extra authority)."""
    return checks.check_image_capability(spec_dir(name))


def detect_gaps(text: str) -> list[dict]:
    """Missing capabilities in a run's notes, logs or transcript text: the agent's own CAPABILITY_GAP lines and the
    notes' "Capability gaps" section, plus what RECOGNISERS see when the agent didn't say it.
    [{"name", "why", "evidence": [lines], "seconds": float|None, "said_by_agent": bool, "spec": path|None}]"""
    found: dict[str, dict] = {}
    for g in research.gaps(text):
        found[g["name"]] = {"name": g["name"], "why": g["why"], "evidence": [], "said_by_agent": True}
    for line in (text or "").splitlines():
        for name, pat, why in RECOGNISERS:
            m = pat.search(line)
            if not m:
                continue
            g = found.setdefault(name, {"name": name, "why": why.format(*m.groups()), "evidence": [],
                                        "said_by_agent": False})
            if len(g["evidence"]) < 5 and line.strip()[:200] not in g["evidence"]:
                g["evidence"].append(line.strip()[:200])
    for g in found.values():
        secs = [float(s) for ln in g["evidence"] + [g["why"]] for s in SECONDS_RE.findall(ln)]
        g["seconds"] = max(secs) if secs else None
        g["spec"] = str(HERE / g["name"]) if (HERE / g["name"] / "manifest.json").is_file() else None
    return list(found.values())


def text_of(path: Path) -> str:
    """A file as plain lines: JSON (an ATIF trajectory) and JSON lines (stream-json) become their string values,
    so commands, outputs and notes are matched without JSON escaping. Values are separated by a "#" line, which
    ends a notes section, so a notebook's "Capability gaps" doesn't run on into the next value."""
    raw = Path(path).read_text(errors="replace")
    out: list[str] = []

    def walk(x) -> None:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    try:
        walk(json.loads(raw))
        return "\n#\n".join(out)
    except ValueError:
        pass
    for ln in raw.splitlines():
        try:
            walk(json.loads(ln))
        except ValueError:
            out.append(ln)
    return "\n#\n".join(out)


def wasted_sleeps(text: str, min_seconds: int = 60) -> list[int]:
    """`sleep N` commands of a minute or more: waiting the protocol forbids (not a capability, a habit)."""
    return [int(s) for s in SLEEP_RE.findall(text or "") if int(s) >= min_seconds]


def seed_lessons(task_id: str = "small-calc") -> list[dict]:
    """The dusk lessons in evolve's shape {task_id, lesson, kind, evidence, source}."""
    ev = evidence()
    return [{"task_id": task_id, "lesson": o["lesson"], "kind": o["kind"], "evidence": [f"seed/first-modal/{o['id']}"],
             "source": "seed", "capability": o.get("capability")} for o in ev["observations"]]


def apply_seed_lessons(task_id: str = "small-calc") -> list[dict]:
    """Add the dusk lessons to evolve's store (idempotent: evolve merges repeats). Needs the evolve package."""
    import evolve
    return [evolve.add_lesson(x["task_id"], x["lesson"], kind=x["kind"], evidence=x["evidence"])
            for x in seed_lessons(task_id)]


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m tasks.capabilities", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    c = sub.add_parser("check")
    c.add_argument("name", nargs="?", default="")
    r = sub.add_parser("rehash")
    r.add_argument("name")
    g = sub.add_parser("gaps")
    g.add_argument("files", nargs="+")
    sl = sub.add_parser("lessons")
    sl.add_argument("--task-id", default="small-calc")
    sl.add_argument("--apply", action="store_true", help="add them to evolve's lesson store")
    a = ap.parse_args(argv)
    if a.cmd == "show":
        ev = evidence()
        out = {"evidence": ev, "specs": {n: {"manifest": manifest(n), "check": check(n) or "ok"} for n in specs()}}
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return 0
    if a.cmd == "check":
        bad = 0
        for n in [a.name] if a.name else specs():
            problems = check(n)
            print(f"{n}: " + ("ok" if not problems else "FAILED\n" + "\n".join(f"- {p}" for p in problems)))
            bad += bool(problems)
        return 1 if bad else 0
    if a.cmd == "rehash":
        print(json.dumps(rehash(a.name)["files"], indent=1))
        return 0
    if a.cmd == "gaps":
        text = "\n".join(text_of(Path(f)) for f in a.files)
        print(json.dumps({"gaps": detect_gaps(text), "sleeps": wasted_sleeps(text)}, indent=1, ensure_ascii=False))
        return 0
    lessons = apply_seed_lessons(a.task_id) if a.apply else seed_lessons(a.task_id)
    print(json.dumps(lessons, indent=1, ensure_ascii=False))
    return 0
