"""Reusable scripts: what the agent wrote in an attempt that passed its check, kept per task with a manifest.

    $MACRAE_EVOLVE_HOME/tools/<task_id>/<file>           the latest passing version of each script
    $MACRAE_EVOLVE_HOME/tools/<task_id>/manifest.json    {"task_id", "updated", "tools": [Tool]}
    Tool = {"name", "file", "description", "sha256", "bytes", "lines", "run_id", "step", "command",
            "created", "updated", "runs": [...], "versions": n}

The folder is handed to the next run as an extra input path (flow var `tools_dir`, uploaded by Harbor to
/app/<task_id>/), and listed in the lessons block, so the agent can start from code that already passed.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from . import config, store
from .runs import Run, Step, Trial

SCRIPT_EXT = {".py", ".sh", ".bash", ".jl", ".r"}
CONFIG_EXT = {".mdp", ".yaml", ".yml", ".toml", ".inp", ".in"}   # only when the agent wrote them
WRITE_TOOLS = {"write", "edit", "multiedit", "notebookedit", "str_replace_based_edit_tool"}
MAX_BYTES = 200_000
MAX_PER_RUN = 8
FILE_RE = re.compile(r"[\w./-]+\.(?:py|sh|bash|jl|R|r)\b")


def task_dir(task_id: str) -> Path:
    return config.tools_root() / re.sub(r"[^A-Za-z0-9._-]+", "-", task_id or "task")


def manifest(task_id: str) -> dict:
    m = store.read_json(task_dir(task_id) / "manifest.json", {})
    if not isinstance(m, dict) or not isinstance(m.get("tools"), list):
        m = {"task_id": task_id, "updated": None, "tools": []}
    return m


def listing(task_id: str) -> list[dict]:
    d = task_dir(task_id)
    return [t for t in manifest(task_id)["tools"] if (d / str(t.get("file"))).is_file()]


def tools_dir(task_id: str) -> str:
    """The folder to pass as flow var `tools_dir` ("" when there's nothing to hand over)."""
    if not config.tools_enabled() or not listing(task_id):
        return ""
    return str(task_dir(task_id))


def _passing_trials(step: Step) -> list[Trial]:
    if step.status != "ok":
        return []
    good = [t for t in step.trials if not t.exception and t.source != "live"]
    passed_attempts = {a["n"] for a in step.attempts if a.get("passed")}
    if passed_attempts:
        good = [t for t in good if t.attempt in passed_attempts] or good[-1:]
    else:
        good = good[-1:]
    return good


def _local(trial: Trial, container_path: str) -> Optional[Path]:
    app = trial.artifacts_app
    if not app.is_dir():
        return None
    p = container_path.strip()
    if p.startswith("/app/"):
        cand = app / p[len("/app/"):]
        return cand if cand.is_file() else None
    if p.startswith("/"):
        return None
    hits = [x for x in app.rglob(Path(p).name) if x.is_file()][:2]
    return hits[0] if len(hits) == 1 else None


def candidates(run: Run) -> list[dict]:
    """Scripts written or run in passing attempts: [{"name", "path", "step", "command", "trial"}]."""
    out: dict[str, dict] = {}
    skip_prefix = f"/app/{task_dir(run.task_id).name}/"
    for step in run.agent_steps:
        for tr in _passing_trials(step):
            written, ran = {}, {}
            for c in tr.calls:
                if c.name.lower() in WRITE_TOOLS and c.path:
                    written.setdefault(c.path, c)
                if c.command and not c.failed:  # how it was run: the last invocation that worked
                    for m in FILE_RE.finditer(c.command):
                        ran[m.group(0)] = c.command
            paths = list(written) + [p for p in ran if p not in written]
            for p in paths:
                ext = Path(p).suffix.lower()
                if ext not in SCRIPT_EXT and not (ext in CONFIG_EXT and p in written):
                    continue
                if p.startswith(skip_prefix):
                    continue
                local = _local(tr, p)
                if local is None or local.stat().st_size > MAX_BYTES:
                    continue
                cmd = next((v for k, v in ran.items() if Path(k).name == local.name), "")
                out.setdefault(local.name, {"name": local.name, "path": local, "step": step.key, "command": cmd,
                                            "trial": tr.dir.name})
    return list(out.values())[:MAX_PER_RUN]


def describe(path: Path) -> str:
    """The script's own docstring or leading comment (first line or two)."""
    try:
        text = path.read_text(errors="replace")[:4000]
    except OSError:
        return ""
    m = re.match(r'\s*(?:#![^\n]*\n)?\s*(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', text, re.S)
    if m:
        lines = [ln.strip() for ln in m.group(1).strip().splitlines() if ln.strip()]
        return _clip(" ".join(lines[:3]))
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("#!") or not s:
            if lines:
                break
            continue
        if s.startswith(("#", "//", "%")):
            lines.append(s.lstrip("#/% ").strip())
            if len(lines) == 2:
                break
        else:
            break
    return _clip(" ".join(x for x in lines if x))


def _clip(s: str, n: int = 240) -> str:
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def harvest(run: Run, notes: Optional[dict[str, dict]] = None) -> list[dict]:
    """Copy passing scripts into tools/<task_id>/ and update the manifest. `notes` (from the distiller):
    {file name: {"description", "reusable"}}; reusable=False skips the file. Returns the tools added or updated."""
    cands = candidates(run)
    if not cands:
        return []
    d = task_dir(run.task_id)
    d.mkdir(parents=True, exist_ok=True)
    m = manifest(run.task_id)
    by_name = {t["name"]: t for t in m["tools"]}
    changed = []
    now = time.time()
    for c in cands:
        note = (notes or {}).get(c["name"]) or {}
        if note.get("reusable") is False:
            continue
        data = c["path"].read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        desc = (note.get("description") or describe(c["path"])
                or f"written by the agent in {run.id}/{c['step']}").strip()
        old = by_name.get(c["name"])
        if old and old.get("sha256") == sha:
            if run.id not in old.setdefault("runs", []):
                old["runs"].append(run.id)
                old["updated"] = now
                changed.append(old)
            if note.get("description"):
                old["description"] = desc
            continue
        shutil.copyfile(c["path"], d / c["name"])
        rec = {"name": c["name"], "file": c["name"], "description": desc, "sha256": sha, "bytes": len(data),
               "lines": data.count(b"\n") + 1, "run_id": run.id, "step": c["step"], "command": c["command"][:300],
               "created": old.get("created", now) if old else now, "updated": now,
               "runs": sorted(set((old or {}).get("runs", [])) | {run.id}),
               "versions": int((old or {}).get("versions") or 0) + 1}
        by_name[c["name"]] = rec
        changed.append(rec)
    m["tools"] = sorted(by_name.values(), key=lambda t: t["name"])
    m["task_id"] = run.task_id
    m["updated"] = now
    store._write_atomic(d / "manifest.json", json.dumps(m, indent=1, ensure_ascii=False))
    return changed
