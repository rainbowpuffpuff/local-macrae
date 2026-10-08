"""The raw record of a run, for people who want more than the page's events: Harbor job folders and trajectories,
a zip of everything, the agent's manuscript as an edit stream, and the images it made.

Sources (all written by others; we only read them):
  $AGENT_RUNNER_HOME/runs/<id>/                 state.json, flow.yaml, logs/, live/, outputs/, plan.json, costs.json,
                                                macrae.json, engine.log (live/token is the run's secret: never served)
  <state.jobs_dir>/<step>-a<n>[-r<m>]/<trial>/  result.json, agent/trajectory.json (ATIF), agent/claude-code.txt,
                                                artifacts/app/<dir>/... (the files the agent left in /app)
  $MACRAE_SERVER_DATA/events/<id>.jsonl         the page's TraceEvents with their seq (events.py)

`router` has no auth of its own: app.py mounts it with the X-Macrae-Secret dependency.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import IO, Iterator, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from . import config, manuscript, runs, trace
from .live import live_dir

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\]]{0,200}$")
SUFFIX_RE = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9._\-/ ]{0,199}\.md$")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".svg": "image/svg+xml"}
MAX_FILE = 50 * 1024 * 1024
MAX_TOTAL = 500 * 1024 * 1024
MAX_RELPATH = 300
SECRET_NAMES = {"token", ".credentials.json", "credentials.json", ".env"}
SECRET_EXT = (".key", ".pem")
# Anthropic keys and Claude OAuth tokens (sk-ant-api03-…, sk-ant-oat01-…), wherever they ended up
ANTHROPIC_KEY_RE = re.compile(rb"sk-ant-[A-Za-z0-9_\-]{8,}")
SECRET_KEY_RE = re.compile(r"token|secret|password|passwd|api[_-]?key|credential|authorization|cookie", re.I)
ARTIFACT_HEADERS = {"X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
                    "Cache-Control": "private, max-age=60"}


def _inside(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def _rel(p: Path, root: Optional[Path]) -> str:
    if root is not None:
        try:
            return p.relative_to(root).as_posix()
        except ValueError:
            pass
    return f"{p.parent.name}/{p.name}"  # a step's job_dir outside jobs_dir: <its parent>/<job>


def jobs_root(state: dict) -> Optional[Path]:
    """The folder job paths are relative to: state.jobs_dir's parent ($AGENT_RUNNER_HOME/jobs)."""
    return Path(str(state["jobs_dir"])).parent if state.get("jobs_dir") else None


def run_job_dirs(state: dict) -> list[tuple[str, int, Path]]:
    """(step key or "", attempt, job dir): every agent step's job folders, then any other folder in jobs_dir."""
    out, seen = [], set()
    for key, step in manuscript.agent_steps(state):
        for jd in trace.job_dirs_for(state, key, step):
            if str(jd) not in seen:
                seen.add(str(jd))
                out.append((key, manuscript._job_order(jd)[0], jd))
    jd_root = Path(str(state["jobs_dir"])) if state.get("jobs_dir") else None
    try:
        others = sorted(p for p in jd_root.iterdir() if p.is_dir() and str(p) not in seen) if jd_root else []
    except OSError:
        others = []
    out += [("", manuscript._job_order(p)[0], p) for p in others if trace.trial_dirs(p)]
    return out


def _trial(run_id: str, job: Path, td: Path, root: Optional[Path]) -> dict:
    res = config.read_json(td / "result.json")
    res = res if isinstance(res, dict) else {}
    rewards = (res.get("verifier_result") or {}).get("rewards") if isinstance(res.get("verifier_result"), dict) \
        else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    ex = res.get("exception_info")
    exc = None
    if isinstance(ex, dict) and ex:
        exc = f"{ex.get('exception_type', '')}: {ex.get('exception_message', '')}".strip(": ") or "exception"
    traj, stream = td / "agent" / "trajectory.json", td / "agent" / "claude-code.txt"
    has_traj = traj.is_file()
    return {"name": td.name, "path": _rel(td, root),
            "trajectory": _rel(traj, root) if has_traj else None,
            "stream": _rel(stream, root) if stream.is_file() else None,
            "reward": reward if isinstance(reward, (int, float)) else None, "exception": exc,
            "started": trace.ts(res.get("started_at")), "finished": trace.ts(res.get("finished_at")),
            "url": f"/api/runs/{run_id}/jobs/{job.name}/{td.name}/trajectory" if has_traj else None}


def jobs(run_id: str) -> Optional[dict]:
    """The run's Harbor job folders and trials, and its live stream files. None = no such run."""
    state = runs.read_state(run_id)
    if state is None:
        return None
    root = jobs_root(state)
    out = [{"step": key, "attempt": att, "job": jd.name, "path": _rel(jd, root),
            "trials": [_trial(run_id, jd, td, root) for td in trace.trial_dirs(jd)]}
           for key, att, jd in run_job_dirs(state)]
    names = {trace.sanitize(k): k for k in (state.get("steps") or {})}
    lv = []
    d = live_dir(runs.run_dir(run_id))
    for f in sorted(d.glob("*.jsonl")) if d.is_dir() else []:
        if f.name.endswith(".batches.jsonl") or not f.is_file():
            continue
        lv.append({"step": names.get(f.stem, f.stem), "path": f"live/{f.name}", "bytes": f.stat().st_size})
    return {"run_id": run_id, "jobs": out, "live": lv}


def trajectory_file(run_id: str, job: str, trial: str) -> Optional[Path]:
    """A trial's agent/trajectory.json (or agent/claude-code.txt when there is none), if the job is this run's."""
    if not (NAME_RE.match(job or "") and NAME_RE.match(trial or "")) or ".." in job or ".." in trial:
        return None
    state = runs.read_state(run_id)
    if state is None:
        return None
    for _, _, jd in run_job_dirs(state):
        if jd.name != job:
            continue
        td = jd / trial
        if not td.is_dir() or not _inside(td, jd):
            return None
        for f in (td / "agent" / "trajectory.json", td / "agent" / "claude-code.txt"):
            if f.is_file() and _inside(f, td):
                return f
        return None
    return None


# ── the zip ─────────────────────────────────────────────────────────────────


def _secret(p: Path) -> bool:
    return p.name in SECRET_NAMES or p.name.endswith(SECRET_EXT)


def _scrub_json(v: object) -> object:
    if isinstance(v, dict):
        return {k: ("***" if SECRET_KEY_RE.search(str(k)) and isinstance(v2, (str, int, float)) and v2 not in ("", None)
                    else _scrub_json(v2)) for k, v2 in v.items()}
    if isinstance(v, list):
        return [_scrub_json(x) for x in v]
    return v


def _scrub(p: Path, data: bytes, live_token: bytes) -> bytes:
    """Take secrets out of a file's bytes: the run's live token, Anthropic keys, and secret-looking keys in Harbor's
    config.json (agent env, kwargs)."""
    if live_token and live_token in data:
        data = data.replace(live_token, b"***")
    data = ANTHROPIC_KEY_RE.sub(b"sk-ant-***", data)
    if p.name == "config.json":
        try:
            data = json.dumps(_scrub_json(json.loads(data)), indent=1, ensure_ascii=False).encode()
        except (ValueError, UnicodeDecodeError):
            pass
    return data


class _Zip:
    def __init__(self, out: IO[bytes], live_token: str):
        self.z = zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True, compresslevel=6)
        self.total = 0
        self.n = 0
        self.skipped: list[str] = []
        self.token = live_token.encode()

    def add(self, p: Path, arc: str, root: Path) -> None:
        if _secret(p):
            self.skipped.append(f"{arc}  (secret, never exported)")
            return
        if p.is_symlink() and not _inside(p, root):
            self.skipped.append(f"{arc}  (symlink outside the folder)")
            return
        try:
            size = p.stat().st_size
        except OSError as e:
            self.skipped.append(f"{arc}  (unreadable: {type(e).__name__})")
            return
        if size > MAX_FILE:
            self.skipped.append(f"{arc}  ({size} bytes > {MAX_FILE} per file)")
            return
        if self.total + size > MAX_TOTAL:
            self.skipped.append(f"{arc}  ({size} bytes: over the {MAX_TOTAL} byte total)")
            return
        try:
            data = p.read_bytes()[:MAX_FILE]
        except OSError as e:
            self.skipped.append(f"{arc}  (unreadable: {type(e).__name__})")
            return
        self.z.writestr(arc, _scrub(p, data, self.token))
        self.total += len(data)
        self.n += 1

    def tree(self, root: Path, prefix: str) -> None:
        if not root.is_dir():
            return
        for d, dirs, files in os.walk(root, followlinks=False):
            dp = Path(d)
            for name in sorted(dirs):
                if (dp / name).is_symlink():
                    self.skipped.append(f"{prefix}{(dp / name).relative_to(root).as_posix()}/  (symlinked folder)")
            dirs[:] = sorted(x for x in dirs if not (dp / x).is_symlink())
            for name in sorted(files):
                p = dp / name
                self.add(p, prefix + p.relative_to(root).as_posix(), root)

    def close(self, run_id: str) -> None:
        lines = [f"trace of run {run_id}", f"{self.n} files, {self.total} bytes", "",
                 "run/        the run folder ($AGENT_RUNNER_HOME/runs/<id>)",
                 "jobs/       the run's Harbor job folders (<jobs_dir>/<step>-a<n>/<trial>/...)",
                 "events.jsonl  the page's TraceEvents (server event log)", "",
                 f"skipped ({len(self.skipped)}):"] + [f"  {s}" for s in self.skipped]
        self.z.writestr("MANIFEST.txt", "\n".join(lines) + "\n")
        self.z.close()


def trace_zip(run_id: str) -> Optional[IO[bytes]]:
    """A zip of the run folder, its job folders and its event log, in a temp file positioned at 0 (the caller
    closes it). None = no such run."""
    state = runs.read_state(run_id)
    if state is None:
        return None
    run_dir = runs.run_dir(run_id)
    token = ""
    try:
        token = (live_dir(run_dir) / "token").read_text().strip()
    except OSError:
        pass
    out = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    z = _Zip(out, token)
    try:
        z.tree(run_dir, "run/")
        jd_root = Path(str(state["jobs_dir"])) if state.get("jobs_dir") else None
        if jd_root is not None:
            z.tree(jd_root, "jobs/")
        for _, _, jd in run_job_dirs(state):  # a step's job_dir outside jobs_dir
            if jd_root is None or not _inside(jd, jd_root):
                z.tree(jd, f"jobs/{jd.name}/")
        ev = config.server_data_dir() / "events" / f"{run_id}.jsonl"
        if ev.is_file():
            z.add(ev, "events.jsonl", ev.parent)
        z.close(run_id)
    except BaseException:
        out.close()
        raise
    out.seek(0)
    return out


# ── images the agent made ───────────────────────────────────────────────────


def valid_relpath(relpath: str) -> bool:
    return (bool(relpath) and len(relpath) <= MAX_RELPATH and ".." not in relpath and "\\" not in relpath
            and "\x00" not in relpath and not relpath.startswith("/") and not re.match(r"^[A-Za-z]:", relpath))


def image_type(relpath: str) -> Optional[str]:
    return IMAGE_TYPES.get(os.path.splitext(relpath)[1].lower())


def artifact_file(run_id: str, relpath: str) -> Optional[Path]:
    """The newest copy of an image the agent left: in a trial's artifacts/app/*/<relpath> or artifacts/app/<relpath>
    (newest trial first), then <run>/result*/<relpath>. None if not found, not an image, or a bad path."""
    if not valid_relpath(relpath) or not image_type(relpath):
        return None
    state = runs.read_state(run_id)
    if state is None:
        return None
    cands: list[tuple[Path, Path]] = []  # (file, the root it must stay under)
    for td in manuscript.latest_trials(state):
        app = td / "artifacts" / "app"
        if not app.is_dir():
            continue
        try:
            subs = sorted(p for p in app.iterdir() if p.is_dir())
        except OSError:
            subs = []
        cands += [(s / relpath, app) for s in subs] + [(app / relpath, app)]
    run_dir = runs.run_dir(run_id)
    try:
        cands += [(d / relpath, run_dir) for d in sorted(run_dir.iterdir()) if d.is_dir()
                  and d.name.startswith("result")]
    except OSError:
        pass
    best: Optional[tuple[float, int, Path]] = None
    for i, (p, root) in enumerate(cands):
        if not p.is_file() or not _inside(p, root):
            continue
        key = (p.stat().st_mtime, -i, p)
        if best is None or key[:2] > best[:2]:
            best = key
    return best[2] if best else None


# ── routes (mounted by app.py with the X-Macrae-Secret dependency) ──────────

router = APIRouter()


def _need_run(run_id: str) -> dict:
    st = runs.read_state(run_id) if runs.valid_run_id(run_id) else None
    if st is None:
        raise HTTPException(404, f"no run {run_id!r}")
    return st


@router.get("/api/runs/{run_id}/jobs")
def get_jobs(run_id: str) -> dict:
    _need_run(run_id)
    res = jobs(run_id)
    if res is None:
        raise HTTPException(404, f"no run {run_id!r}")
    return res


@router.get("/api/runs/{run_id}/jobs/{job}/{trial}/trajectory")
def get_trajectory(run_id: str, job: str, trial: str) -> FileResponse:
    _need_run(run_id)
    f = trajectory_file(run_id, job, trial)
    if f is None:
        raise HTTPException(404, "no such trial, or it has no trajectory yet")
    media = "application/json" if f.suffix == ".json" else "text/plain; charset=utf-8"
    return FileResponse(f, media_type=media, headers={
        "Content-Disposition": f'inline; filename="{trial}-{f.name}"', "X-Content-Type-Options": "nosniff"})


def _chunks(f: IO[bytes]) -> Iterator[bytes]:
    try:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            yield b
    finally:
        f.close()


@router.get("/api/runs/{run_id}/trace")
def get_trace_zip(run_id: str) -> StreamingResponse:
    _need_run(run_id)
    f = trace_zip(run_id)
    if f is None:
        raise HTTPException(404, f"no run {run_id!r}")
    return StreamingResponse(_chunks(f), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{run_id}-trace.zip"'})


@router.get("/api/runs/{run_id}/manuscript")
def get_manuscript(run_id: str, path: str = Query(manuscript.MANUSCRIPT, max_length=200)) -> dict:
    _need_run(run_id)
    if not SUFFIX_RE.match(path) or ".." in path or "//" in path:
        raise HTTPException(400, "path must be a relative file name ending in .md, without '..'")
    res = manuscript.edit_stream(run_id, path)
    if res is None:
        raise HTTPException(404, f"no run {run_id!r}")
    return res


@router.get("/api/runs/{run_id}/notes")
def get_notes(run_id: str) -> dict:
    _need_run(run_id)
    res = manuscript.notes(run_id)
    if res is None:
        raise HTTPException(404, f"no run {run_id!r}")
    return res


@router.get("/api/runs/{run_id}/artifacts/{path:path}")
def get_artifact(run_id: str, path: str) -> FileResponse:
    _need_run(run_id)
    if not valid_relpath(path):
        raise HTTPException(400, "bad artifact path")
    media = image_type(path)
    if not media:
        raise HTTPException(400, f"only images are served ({', '.join(sorted(IMAGE_TYPES))})")
    f = artifact_file(run_id, path)
    if f is None:
        raise HTTPException(404, "no such image in this run")
    return FileResponse(f, media_type=media, headers=dict(ARTIFACT_HEADERS))
