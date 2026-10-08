"""The capability registry: versioned, tested tools, environment images and writing templates the agent built for
itself, plus the ledger of how each came to be (gap → create → test → install → use, or rejected).

    $MACRAE_EVOLVE_HOME/capabilities/                  (synced to R2 with the rest of the state)
        registry.json                  {"capabilities": {name: Capability}, "updated"}
        <name>/v<N>/                   the installed files (read-only) + manifest.json (the full record)
        ledger.jsonl                   one event per line: {t, event, name, run_id, step, key, version, ...}
        forges.json                    name → the forge runs started for it (so a gap is built once)
        snapshots/<label>.json         the registry at a moment (benchmark dusk/dawn), for reproducible reports

    Capability = {name, kind: tool|image|writing, version, purpose, entrypoint, usage, inputs, outputs, files:
                  [{path, sha256, bytes}], tests: [{name, cmd}], sha256 (of the whole bundle), applies_to: [task_id|*],
                  requirements, image: {fragment, requirements, smoke}, created_by: {run_id, step}, gap: {run_id,
                  step, why}, created, tested, installed, last_used, uses, test: {run_id, job, reward, sandbox},
                  policy_sha256, versions: [...]}

Only the installer (server/installer.py, after server/policy.py and the sandboxed tests said yes) calls install();
nothing a capability runs can reach this folder: runs get a copy (export()), uploaded into their sandbox.

    export(run_dir, task_id, mode)    what a run gets: <run>/capabilities/ (mounted at /app/capabilities) and
                                      <run>/capabilities.json (mode, list, instruction block, image) for agent_runner
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Optional

from . import config
from .store import _flock, _write_atomic, read_json

EVENTS = ("gap", "create", "test", "install", "use", "rejected")
SANDBOX_DIR = "/app/capabilities"   # where every run sees the installed capabilities (Harbor uploads <run>/capabilities)
GAP_PREFIX = "CAPABILITY_GAP"
USE_PREFIX = "CAPABILITY_USE"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,47}$")

_tlock = threading.RLock()


def root() -> Path:
    return config.home() / "capabilities"


def registry_file() -> Path:
    return root() / "registry.json"


def ledger_file() -> Path:
    return root() / "ledger.jsonl"


@contextlib.contextmanager
def locked() -> Iterator[None]:
    root().mkdir(parents=True, exist_ok=True)
    with _tlock, _flock(root() / ".lock"):
        yield


def norm_name(name: str) -> str:
    """'Block_Average ' → 'block-average' (what the gap line said, as a registry name)."""
    s = re.sub(r"[^a-z0-9-]+", "-", str(name or "").strip().lower().replace("_", "-")).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:48].strip("-")
    if s and not s[0].isalpha():
        s = "c-" + s
    return s[:48]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def bundle_sha(files: list[dict]) -> str:
    """One hash for the whole capability: the sorted (path, sha256) pairs."""
    pairs = sorted((str(f["path"]), str(f["sha256"])) for f in files)
    return hashlib.sha256(json.dumps(pairs).encode()).hexdigest()


# ── registry ────────────────────────────────────────────────────────────────


def _load() -> dict:
    d = read_json(registry_file(), {})
    if not isinstance(d, dict) or not isinstance(d.get("capabilities"), dict):
        d = {"capabilities": {}, "updated": None}
    return d


def _save(d: dict) -> None:
    d["updated"] = time.time()
    _write_atomic(registry_file(), json.dumps(d, indent=1, ensure_ascii=False))


def installed() -> list[dict]:
    """Every installed capability (its current version), oldest install first."""
    caps = list(_load()["capabilities"].values())
    return sorted(caps, key=lambda c: float(c.get("installed") or 0))


def get(name: str) -> Optional[dict]:
    return _load()["capabilities"].get(norm_name(name))


def version_dir(name: str, version: int) -> Path:
    return root() / norm_name(name) / f"v{int(version)}"


def applies(cap: dict, task_id: str) -> bool:
    to = cap.get("applies_to") or ["*"]
    return "*" in to or (task_id and task_id in to)


def for_task(task_id: str, kinds: tuple[str, ...] = ("tool", "image", "writing")) -> list[dict]:
    return [c for c in installed() if c.get("kind", "tool") in kinds and applies(c, task_id)]


def verify(cap: dict) -> list[str]:
    """Problems with an installed capability's files (missing, changed since install)."""
    d = version_dir(cap["name"], cap["version"])
    out = []
    for f in cap.get("files") or []:
        p = d / f["path"]
        if not p.is_file():
            out.append(f"{f['path']} is missing")
        elif sha256_file(p) != f["sha256"]:
            out.append(f"{f['path']} changed since it was installed (sha256 mismatch)")
    return out


def _read_only(path: Path) -> None:
    for p in sorted(path.rglob("*")):
        if p.is_file():
            p.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def install(record: dict, src_dir: Path) -> dict:
    """Copy the verified files into <name>/v<N>/ (read-only) and make it the current version. Called only by the
    installer, after the policy and the sandboxed tests said yes. Returns the stored record."""
    name = norm_name(record["name"])
    with locked():
        reg = _load()
        old = reg["capabilities"].get(name)
        version = int((old or {}).get("version") or 0) + 1
        dest = version_dir(name, version)
        if dest.exists():  # a half-finished earlier install of the same version
            _make_writable(dest)
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        for f in record.get("files") or []:
            target = dest / f["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_dir / f["path"], target)
        now = time.time()
        rec = dict(record, name=name, version=version, installed=now, uses=int((old or {}).get("uses") or 0),
                   last_used=(old or {}).get("last_used"),
                   versions=list((old or {}).get("versions") or []) + [
                       {"version": version, "sha256": record.get("sha256"), "installed": now,
                        "run_id": (record.get("created_by") or {}).get("run_id")}])
        rec["created"] = (old or {}).get("created") or record.get("created") or now
        (dest / "manifest.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))
        _read_only(dest)
        reg["capabilities"][name] = rec
        _save(reg)
        return rec


def _make_writable(path: Path) -> None:
    for p in path.rglob("*"):
        with contextlib.suppress(OSError):
            p.chmod(p.stat().st_mode | stat.S_IWUSR)


def note_use(name: str, t: Optional[float] = None) -> None:
    with locked():
        reg = _load()
        c = reg["capabilities"].get(norm_name(name))
        if not c:
            return
        c["uses"] = int(c.get("uses") or 0) + 1
        c["last_used"] = t or time.time()
        _save(reg)


# ── ledger ──────────────────────────────────────────────────────────────────


def ledger(since: Optional[float] = None, name: str = "", run_id: str = "") -> list[dict]:
    out = []
    try:
        lines = ledger_file().read_text().splitlines()
    except OSError:
        return []
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("event") not in EVENTS:
            continue
        if since is not None and float(e.get("t") or 0) < since:
            continue
        if name and e.get("name") != name:
            continue
        if run_id and e.get("run_id") != run_id:
            continue
        out.append(e)
    out.sort(key=lambda e: float(e.get("t") or 0))
    return out


def record(event: str, name: str, run_id: str = "", step: str = "", key: str = "", t: Optional[float] = None,
           **extra: Any) -> Optional[dict]:
    """Append to the ledger. Idempotent on `key` (default: event|name|run_id|step). Returns the entry, or None if
    it was already there."""
    if event not in EVENTS:
        raise ValueError(f"ledger event must be one of {EVENTS}")
    name = norm_name(name) or "unnamed"
    key = key or f"{event}|{name}|{run_id}|{step}"
    with locked():
        if any(e.get("key") == key for e in ledger()):
            return None
        e = {"t": round(float(t or time.time()), 3), "event": event, "name": name, "run_id": run_id, "step": step,
             "key": key}
        e.update({k: v for k, v in extra.items() if v is not None})
        ledger_file().parent.mkdir(parents=True, exist_ok=True)
        with open(ledger_file(), "a") as f:
            f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
    if event == "use":
        note_use(name, e["t"])
    return e


# ── forges (a gap is built once) ────────────────────────────────────────────


def forges() -> dict:
    d = read_json(root() / "forges.json", {})
    return d if isinstance(d, dict) else {}


def note_forge(name: str, entry: dict) -> None:
    with locked():
        d = forges()
        d.setdefault(norm_name(name), []).append(entry)
        _write_atomic(root() / "forges.json", json.dumps(d, indent=1, ensure_ascii=False))


# ── snapshots ───────────────────────────────────────────────────────────────


def snapshot(label: str, extra: Optional[dict] = None) -> Path:
    """Freeze the registry (names, versions, hashes) and the ledger length under snapshots/<label>.json. The
    versioned files never change after install, so the snapshot plus <name>/v<N>/ reproduce the state."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", label)[:100] or "snapshot"
    with locked():
        reg = _load()
        snap = {"label": label, "t": time.time(), "registry": reg, "ledger_len": len(ledger()),
                "policy_sha256": _policy_sha()}
        snap.update(extra or {})
        p = root() / "snapshots" / f"{safe}.json"
        _write_atomic(p, json.dumps(snap, indent=1, ensure_ascii=False, default=str))
    return p


def load_snapshot(label: str) -> Optional[dict]:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", label)[:100]
    d = read_json(root() / "snapshots" / f"{safe}.json", None)
    return d if isinstance(d, dict) else None


def _policy_sha() -> str:
    try:
        from server import policy
        return policy.POLICY_SHA256
    except Exception:
        return ""


# ── what a run gets ─────────────────────────────────────────────────────────

GAP_PROTOCOL = (
    "MISSING A CAPABILITY? If you need a reusable tool, a prebuilt environment or a writing template that is not "
    "listed above, and it would help later runs of this kind of task, print this on its own line (once per "
    f"capability): {GAP_PREFIX}: <short-name>: <what it must do and why you need it>\n"
    "Then carry on with a workaround. A separate sandboxed run will build and test it for next time. Never write "
    f"into {SANDBOX_DIR}.")


def usage_line(c: dict) -> str:
    u = str(c.get("usage") or "").strip()
    if not u and c.get("entrypoint"):
        ep = str(c["entrypoint"])
        run = "python3" if ep.endswith(".py") else "bash" if ep.endswith(".sh") else ""
        u = f"{run} {SANDBOX_DIR}/{c['name']}/{ep}".strip()
    return u


def instruction_block(caps: list[dict], mode: str, image: Optional[dict] = None) -> str:
    lines = []
    tools = [c for c in caps if c.get("kind", "tool") != "image"]
    if mode == "dusk":
        lines.append("CAPABILITIES: none (this run starts from scratch, without anything learned earlier).")
    elif tools:
        lines.append(f"CAPABILITIES (tested and installed by earlier runs; read-only copies in {SANDBOX_DIR}/). "
                     "Use them instead of rewriting them:")
        for c in tools:
            req = c.get("requirements") or []
            needs = f" Needs: {', '.join(req)}." if req else ""
            lines.append(f"- {c['name']} v{c.get('version', 1)} ({c.get('kind', 'tool')}): "
                         f"{' '.join(str(c.get('purpose') or '').split())[:300]} "
                         f"Use: {usage_line(c) or SANDBOX_DIR + '/' + c['name'] + '/'}.{needs}")
        lines.append(f"Run them from {SANDBOX_DIR}/<name>/ (don't copy them elsewhere), so each use is recorded.")
    else:
        lines.append("CAPABILITIES: none installed yet.")
    if image and mode != "dusk":
        smoke = (image.get("image") or {}).get("smoke") or ""
        lines.append(f"ENVIRONMENT: this sandbox was built from the image capability {image['name']} "
                     f"v{image.get('version', 1)}: {' '.join(str(image.get('purpose') or '').split())[:300]} "
                     f"Its packages are already installed; don't reinstall them."
                     + (f" (Check: {smoke})" if smoke else ""))
    lines.append(GAP_PROTOCOL)
    return "\n".join(lines)


def export(run_dir: Path, task_id: str, mode: str = "dawn") -> dict:
    """Write what a run gets: <run>/capabilities/<name>/ copies (verified against their sha256) plus README.md,
    and <run>/capabilities.json, which agent_runner reads to mount the folder at /app/capabilities, append the
    instruction block to every agent step, and build the sandbox from an image capability. mode "dusk": nothing
    is mounted (the benchmark's from-scratch state); the gap protocol is still given."""
    run_dir = Path(run_dir)
    mode = "dusk" if mode == "dusk" else "dawn"
    mounted: list[dict] = []
    image: Optional[dict] = None
    skipped: list[dict] = []
    cdir = run_dir / "capabilities"
    if mode != "dusk":
        for c in for_task(task_id):
            problems = verify(c)
            if problems:
                skipped.append({"name": c["name"], "version": c.get("version"), "problems": problems})
                continue
            if c.get("kind") == "image":
                if image is None or float(c.get("installed") or 0) > float(image.get("installed") or 0):
                    image = c
                continue
            dest = cdir / c["name"]
            if dest.exists():
                _make_writable(dest)
                shutil.rmtree(dest)
            shutil.copytree(version_dir(c["name"], c["version"]), dest)
            _make_writable(dest)  # the run's own copy; the registry's stays read-only
            mounted.append(c)
        if mounted:
            (cdir / "README.md").write_text(_readme(mounted))
    img = None
    if image:
        idir = run_dir / "capability-image" / image["name"]
        if idir.exists():
            shutil.rmtree(idir)
        shutil.copytree(version_dir(image["name"], image["version"]), idir)
        _make_writable(idir)
        spec = image.get("image") or {}
        img = {"name": image["name"], "version": image.get("version"), "sha256": image.get("sha256"),
               "dir": str(idir), "fragment": spec.get("fragment") or "Dockerfile.fragment",
               "requirements": spec.get("requirements") or "requirements.txt", "smoke": spec.get("smoke") or "",
               "purpose": image.get("purpose") or ""}
    info = {
        "mode": mode, "task_id": task_id, "t": time.time(),
        "dir": str(cdir) if mounted else "", "sandbox_dir": SANDBOX_DIR, "mount": bool(mounted),
        "mounted": [{k: c.get(k) for k in ("name", "version", "kind", "sha256", "purpose", "entrypoint")}
                    | {"usage": usage_line(c)} for c in mounted],
        "image": img, "skipped": skipped,
        "instruction": instruction_block(mounted, mode, image),
    }
    _write_atomic(run_dir / "capabilities.json", json.dumps(info, indent=1, ensure_ascii=False))
    return info


def _readme(caps: list[dict]) -> str:
    out = ["# Installed capabilities", "",
           "Built, tested in a fresh sandbox and installed by earlier runs. Read-only copies; run them from here.", ""]
    for c in caps:
        out += [f"## {c['name']} v{c.get('version', 1)} ({c.get('kind', 'tool')})", "",
                str(c.get("purpose") or ""), "", f"Use: `{usage_line(c)}`", ""]
        for io in ("inputs", "outputs"):
            items = c.get(io) or []
            if items:
                out.append(f"{io.capitalize()}:")
                for x in items:
                    if isinstance(x, dict):
                        out.append(f"- {x.get('name', '')}: {x.get('description') or x.get('type') or ''}")
                    else:
                        out.append(f"- {x}")
                out.append("")
        out.append(f"sha256 {c.get('sha256', '')}, tests: {len(c.get('tests') or [])} passed in a sandbox")
        out.append("")
    return "\n".join(out)


def run_info(run_dir: Path) -> dict:
    d = read_json(Path(run_dir) / "capabilities.json", {})
    return d if isinstance(d, dict) else {}


def public(c: dict) -> dict:
    """A capability as the page shows it (GET /api/capabilities)."""
    t = c.get("test") or {}
    keep = ("name", "kind", "version", "purpose", "entrypoint", "inputs", "outputs", "applies_to", "requirements",
            "sha256", "created_by", "gap", "created", "tested", "installed", "last_used", "uses", "policy_sha256",
            "versions", "image")
    out = {k: c.get(k) for k in keep}
    out["usage"] = usage_line(c)
    out["files"] = [{"path": f.get("path"), "sha256": f.get("sha256"), "bytes": f.get("bytes")}
                    for f in c.get("files") or []]
    out["tests"] = {"n": len(c.get("tests") or []), "passed": t.get("passed"), "names": [
        str(x.get("name") or x.get("cmd") or "") for x in c.get("tests") or []][:12],
        "run_id": t.get("run_id"), "job": t.get("job"), "seconds": t.get("seconds"),
        "setup_s": t.get("setup_s")}
    return out


def env_off() -> bool:
    return os.environ.get("MACRAE_CAPABILITIES", "").strip().lower() in ("0", "off", "false", "no")
