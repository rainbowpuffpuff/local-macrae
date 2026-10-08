"""Downloadable proof of a run: everything it left on disk as one zip, plus a self-contained HTML report.

    GET /api/runs/{id}/trace.zip     macrae-trace-<id>/{report.html, README.md, run.json, events.json, manifest.json,
                                     run/ (the agent_runner run folder: state.json, flow.yaml, logs/, outputs/,
                                     live/*.jsonl, plan.json, costs.json, …), jobs/ (the Harbor job folders: per trial
                                     config.json, result.json, agent/trajectory.json + claude-code.txt, artifacts/,
                                     verifier/), server/ (the event log the page polled, the run → task sidecar)}
    GET /api/runs/{id}/report.html   the report alone (server/report.py)

Where the files come from: the local disk ($AGENT_RUNNER_HOME/runs/<id>, the run's jobs folder, $MACRAE_SERVER_DATA)
when the run is there. On Cloudflare the container restores runs from R2 when it starts (deploy/r2sync.py), but a run
can be in R2 and not (yet) on this disk: then, when MACRAE_SYNC_URL is set, its objects (state/runs/<id>/,
state/jobs/<id>/, state/macrae/…) are fetched through the Worker's R2 endpoint into a cache outside the synced folders
($MACRAE_TRACE_CACHE, default <tmp>/macrae-trace-cache) and read from there. Nothing under runs/ or jobs/ is written.

What is left out or changed: the run's live token (live/token) is never included; text files go through `Redactor`,
which replaces the values of this process's secret environment variables, the live token, Claude/Anthropic keys,
bearer tokens and `NAME=value` / `"NAME": "value"` pairs whose NAME looks like a secret with ***. Files over
MACRAE_TRACE_MAX_FILE_MB (50) and anything past MACRAE_TRACE_MAX_MB (400) in total are listed in manifest.json as
skipped, not packed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from . import catalog, config, costs, events, planner, runs, trace

STORED_SUFFIXES = {".zip", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf",
                   ".mp4", ".npz", ".h5", ".xtc", ".trr", ".dcd"}
NEVER = {("live", "token")}  # (parent dir name, file name) pairs that are never packed
MANUSCRIPT_RE = re.compile(r"(^|/)(manuscript|paper|draft)[^/]*\.(md|markdown|tex|txt|html)$", re.I)
MANUSCRIPT_DIR_RE = re.compile(r"(^|/)manuscript/[^/]+\.(md|markdown|tex|txt|html)$", re.I)
TEXT_SNIFF = 4096


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def max_file_bytes() -> int:
    return int(_float_env("MACRAE_TRACE_MAX_FILE_MB", 50) * 1024 * 1024)


def max_total_bytes() -> int:
    return int(_float_env("MACRAE_TRACE_MAX_MB", 400) * 1024 * 1024)


def jobs_root() -> Path:
    """Same rule as agent_runner.config: AGENT_RUNNER_JOBS, else $AGENT_RUNNER_HOME/jobs."""
    return Path(os.environ.get("AGENT_RUNNER_JOBS", "").strip() or config.runner_home() / "jobs").expanduser()


def cache_root() -> Path:
    return Path(os.environ.get("MACRAE_TRACE_CACHE", "").strip()
                or Path(tempfile.gettempdir()) / "macrae-trace-cache").expanduser()


# ── where a run's files are ─────────────────────────────────────────────────


@dataclass
class RunFiles:
    run_id: str
    run_dir: Path
    jobs_dir: Optional[Path]
    events_log: Optional[Path]  # $MACRAE_SERVER_DATA/events/<id>.jsonl (what the page was shown)
    sidecar: Optional[Path]  # $MACRAE_SERVER_DATA/runs/<id>.json (run → task, inputs)
    source: str = "local"  # "local" | "r2"
    notes: list[str] = field(default_factory=list)

    def roots(self) -> list[tuple[str, Path]]:
        """(prefix in the zip, folder) for each folder that is packed whole."""
        out = [("run", self.run_dir)]
        if self.jobs_dir and self.jobs_dir.is_dir():
            out.append(("jobs", self.jobs_dir))
        return out

    def singles(self) -> list[tuple[str, Path]]:
        return [(name, p) for name, p in (("server/events.jsonl", self.events_log), ("server/run.json", self.sidecar))
                if p is not None and p.is_file()]


def _jobs_dir(run_id: str, state: dict) -> Optional[Path]:
    """The run's Harbor jobs folder: state.json's jobs_dir (always <jobs>/<run id>), else <jobs root>/<run id>."""
    cands = []
    if state.get("jobs_dir"):
        cands.append(Path(str(state["jobs_dir"])).expanduser())
    cands.append(jobs_root() / run_id)
    for p in cands:
        if p.name == run_id and p.is_dir():  # never pack some other tree a broken state.json points at
            return p
    return None


def locate(run_id: str) -> Optional[RunFiles]:
    """The run's files on this disk, else fetched from R2 (when MACRAE_SYNC_URL is set). None: no such run."""
    if not runs.valid_run_id(run_id):
        return None
    d = runs.run_dir(run_id)
    if d.is_dir():
        st = config.read_json(d / "state.json")
        sd = config.server_data_dir()
        return RunFiles(run_id, d, _jobs_dir(run_id, st if isinstance(st, dict) else {}),
                        sd / "events" / f"{run_id}.jsonl", sd / "runs" / f"{run_id}.json", "local")
    try:
        return from_r2(run_id)
    except R2Error as e:
        raise LookupError(f"run {run_id} is not on this disk and R2 could not be read: {e}") from e


# ── R2 (through the Worker's endpoint, as deploy/r2sync.py uses it) ──────────


class R2Error(RuntimeError):
    pass


class R2:
    """GET /?prefix=P[&cursor=C] → {"objects": [{"key", "size", "mtime"}], "cursor"}; GET /KEY → bytes."""

    def __init__(self, base: str, timeout: float = 30.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> tuple[int, dict, bytes]:
        req = urllib.request.Request(self.base + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
        except urllib.error.HTTPError as e:
            return e.code, {}, e.read()
        except (urllib.error.URLError, OSError) as e:
            raise R2Error(f"GET {path}: {e}") from e

    def list(self, prefix: str) -> list[dict]:
        out, cursor = [], None
        while True:
            q = {"prefix": prefix, **({"cursor": cursor} if cursor else {})}
            status, _, body = self._get("/?" + urllib.parse.urlencode(q))
            if status != 200:
                raise R2Error(f"list {prefix}: HTTP {status}")
            page = json.loads(body)
            out += [o for o in page.get("objects") or [] if isinstance(o, dict) and o.get("key")]
            cursor = page.get("cursor")
            if not cursor:
                return out

    def get(self, key: str) -> Optional[tuple[bytes, Optional[float]]]:
        status, headers, body = self._get("/" + urllib.parse.quote(key, safe="/"))
        if status == 404:
            return None
        if status != 200:
            raise R2Error(f"get {key}: HTTP {status}")
        try:
            return body, float(headers["x-macrae-mtime"]) if headers.get("x-macrae-mtime") else None
        except ValueError:
            return body, None


def _inside(root: Path, rel: str) -> Optional[Path]:
    parts = rel.split("/")
    if not rel or any(p in ("", ".", "..") for p in parts):
        return None
    return root.joinpath(*parts)


def from_r2(run_id: str, remote: Optional[R2] = None) -> Optional[RunFiles]:
    """Fetch the run's objects into the cache (only new or changed ones). None if R2 has no such run."""
    if remote is None:
        url = os.environ.get("MACRAE_SYNC_URL", "").strip()
        if not url:
            return None
        remote = R2(url)
    objs = remote.list(f"state/runs/{run_id}/")
    if not objs:
        return None
    base = cache_root() / run_id
    rf = RunFiles(run_id, base / "runs" / run_id, base / "jobs" / run_id, base / "macrae" / "events" /
                  f"{run_id}.jsonl", base / "macrae" / "runs" / f"{run_id}.json", "r2")
    wanted = [(o, _inside(rf.run_dir, o["key"][len(f"state/runs/{run_id}/"):])) for o in objs]
    wanted += [(o, _inside(rf.jobs_dir, o["key"][len(f"state/jobs/{run_id}/"):]))
               for o in remote.list(f"state/jobs/{run_id}/")]
    for key, dest in ((f"state/macrae/events/{run_id}.jsonl", rf.events_log),
                      (f"state/macrae/runs/{run_id}.json", rf.sidecar)):
        wanted += [(o, dest) for o in remote.list(key) if o["key"] == key]
    budget = max_total_bytes()
    for o, dest in wanted:
        if dest is None:
            continue
        size = int(o.get("size") or 0)
        if size > max_file_bytes() or size > budget:
            rf.notes.append(f"not fetched from R2 (too big, {size} bytes): {o['key']}")
            continue
        budget -= size
        try:
            if dest.is_file() and dest.stat().st_size == size:
                continue
        except OSError:
            pass
        got = remote.get(o["key"])
        if got is None:
            continue
        data, mtime = got
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.tmp")
        tmp.write_bytes(data)
        if mtime:
            os.utime(tmp, (mtime, mtime))
        tmp.replace(dest)
    if not rf.run_dir.is_dir():
        return None
    if not rf.jobs_dir.is_dir():
        rf.jobs_dir = None
    return rf


# ── secrets ─────────────────────────────────────────────────────────────────

SECRET_NAME = r"[A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|ACCESS_KEY|PASSWORD|PRIVATE_KEY|CREDENTIALS?)[A-Z0-9_]*"
SECRET_ENV_RE = re.compile(rf"^{SECRET_NAME}$")
PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=\-]{8,}"),
    re.compile(rf"(\b{SECRET_NAME}=)(?!\*\*\*)[^\s\"'&]+"),
    re.compile(rf"(\"{SECRET_NAME}\"\s*:\s*\")(?!\*\*\*)[^\"]+(?=\")"),
    re.compile(r"(?i)(x-macrae-(?:secret|live):\s*)\S+"),
]


def _secret_like(v: str) -> bool:
    """A value worth hiding everywhere: long enough, one word, not a path or a plain lowercase word ("disabled")."""
    return len(v) >= 8 and not re.search(r"\s", v) and not v.startswith(("/", "~", ".")) and not v.isalpha()


class Redactor:
    def __init__(self, extra: Optional[list[str]] = None):
        vals = {v.strip() for k, v in os.environ.items() if SECRET_ENV_RE.match(k) and _secret_like(v.strip())}
        vals |= {v for v in (extra or []) if _secret_like(v)}
        self.values = sorted(vals, key=len, reverse=True)
        self.count = 0

    def text(self, s: str) -> str:
        for v in self.values:
            if v in s:
                self.count += s.count(v)
                s = s.replace(v, "***")
        for rx in PATTERNS:
            s, n = rx.subn(lambda m: (m.group(1) if m.groups() else "") + "***", s)
            self.count += n
        return s

    def obj(self, o: Any) -> Any:
        """A JSON-able object with every string redacted."""
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, list):
            return [self.obj(x) for x in o]
        if isinstance(o, dict):
            return {k: self.obj(v) for k, v in o.items()}
        return o


def redactor_for(rf: RunFiles) -> Redactor:
    try:
        token = (rf.run_dir / "live" / "token").read_text().strip()
    except OSError:
        token = ""
    return Redactor([token])


def is_text(data: bytes) -> bool:
    head = data[:TEXT_SNIFF]
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError as e:
        return e.start >= len(head) - 4  # a multibyte character cut at the sniff boundary


# ── the files ───────────────────────────────────────────────────────────────


def _skip_name(name: str) -> bool:
    return name.startswith(".") or name.endswith(".tmp") or name == "__pycache__"


def iter_files(rf: RunFiles) -> Iterator[tuple[str, Path]]:
    """(path in the zip, file) for everything packed, in a stable order. Symlinks are never followed."""
    for prefix, root in rf.roots():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not _skip_name(d) and not (Path(dirpath) / d).is_symlink())
            for name in sorted(filenames):
                p = Path(dirpath) / name
                if _skip_name(name) or p.is_symlink() or (p.parent.name, name) in NEVER:
                    continue
                yield f"{prefix}/{p.relative_to(root).as_posix()}", p
    yield from rf.singles()


def read_text(p: Path, limit: int = 2_000_000) -> str:
    try:
        with open(p, "rb") as f:
            data = f.read(limit + 1)
    except OSError:
        return ""
    s = data[:limit].decode("utf-8", errors="replace")
    return s + ("\n… (cut; the whole file is in the zip)" if len(data) > limit else "")


# ── what the report shows ───────────────────────────────────────────────────


def _state(rf: RunFiles) -> dict:
    if rf.source == "local":
        st = runs.read_state(rf.run_id) or {}
    else:
        st = config.read_json(rf.run_dir / "state.json")
        st = st if isinstance(st, dict) else {"id": rf.run_id, "status": "unknown", "steps": {}, "order": []}
        st.setdefault("id", rf.run_id)
    st = dict(st)
    st["_dir"] = str(rf.run_dir)
    if rf.jobs_dir:
        st["jobs_dir"] = str(rf.jobs_dir)
    return st


def _summary(rf: RunFiles, st: dict) -> dict:
    s = runs.summarize(st, with_extra=True)
    side = config.read_json(rf.sidecar) if rf.sidecar else None
    if isinstance(side, dict) and rf.source != "local":
        task = catalog.find_task(str(side.get("task_id") or "")) if side.get("task_id") else None
        s["task_id"] = side.get("task_id") or s["task_id"]
        s["title"] = (task or {}).get("title") or side.get("title") or s["title"]
        s["inputs"] = side.get("inputs") or s.get("inputs")
    return s


def _events(rf: RunFiles, st: dict, title: str) -> list[dict]:
    """The TraceEvents, exactly as the page got them (the server's event log), brought up to date first."""
    if rf.source == "local":
        res = events.poll(rf.run_id, 0, limit=10 ** 7)
        if res is not None:
            return res["events"]
    lg = events.RunLog(rf.run_id, rf.events_log if rf.events_log and rf.events_log.is_file() else None)
    lg.path = None  # never write to the cache's (or anyone's) log from here
    if not lg.final:
        lg.merge(trace.build(st, title), final=False)
    return list(lg.events)


CHECK_CMD_RE = re.compile(r"^check \$ (.*?)(?:\s+\(in (.*)\))?$", re.S)
CHECK_EXIT_RE = re.compile(r"^check exit (-?\d+):?\s*(.*)$", re.S)
ATTEMPT_RE = re.compile(r"^attempt (\d+): (\S+) reward=(\S+)(?: passed=(\S+))?")


def checks_from_log(path: Path, base: float) -> list[dict]:
    """Each attempt's check (command, exit code, output) and outcome, from the step log the engine writes."""
    out: list[dict] = []
    cur: dict = {}
    for _, t, msg in trace.log_entries(path, base):
        m = CHECK_CMD_RE.match(msg)
        if m:
            cur = {"t": t, "command": m.group(1).strip(), "cwd": (m.group(2) or "").strip(), "exit": None, "output": ""}
            out.append(cur)
            continue
        m = CHECK_EXIT_RE.match(msg)
        if m:
            if not cur or cur.get("exit") is not None:
                cur = {"t": t, "command": "", "cwd": "", "output": ""}
                out.append(cur)
            cur["exit"], cur["output"] = int(m.group(1)), m.group(2).strip()
            continue
        if msg.startswith("check timed out"):
            cur = {"t": t, "command": "", "cwd": "", "exit": None, "output": msg}
            out.append(cur)
            continue
        m = ATTEMPT_RE.match(msg)
        if m:
            target = cur if cur and "attempt" not in cur else None
            rec = {"attempt": int(m.group(1)), "outcome": m.group(2),
                   "reward": None if m.group(3) == "None" else m.group(3),
                   "passed": {"True": True, "False": False}.get(m.group(4) or "")}
            if target is not None:
                target.update(rec)
            else:
                out.append({"t": t, "command": "", "cwd": "", "exit": None, "output": "", **rec})
            cur = {}
    return out


def _calls(tr: Optional[trace.Transcript]) -> list[dict]:
    if not tr:
        return []
    out = []
    for m in tr.msgs:
        if m.kind == "call" and m.call:
            c = m.call
            out.append({"kind": "call", "t": m.t, "id": c.id, "name": c.name, "args": c.args, "output": c.output,
                        "seconds": c.seconds, "usage": m.usage, "model": m.model})
        else:
            out.append({"kind": m.kind, "t": m.t, "text": m.text, "usage": m.usage, "model": m.model})
    return out


def _trial(tdir: Path, rel: str) -> dict:
    res = config.read_json(tdir / "result.json")
    res = res if isinstance(res, dict) else {}
    src = ""
    tr = None
    if (tdir / "agent" / "trajectory.json").is_file():
        tr, src = trace.parse_atif(tdir / "agent" / "trajectory.json"), "agent/trajectory.json"
    if tr is None and (tdir / "agent" / "claude-code.txt").is_file():
        tr, src = trace.parse_stream(tdir / "agent" / "claude-code.txt"), "agent/claude-code.txt"
    verifier = {}
    vdir = tdir / "verifier"
    if vdir.is_dir():
        for p in sorted(vdir.rglob("*")):
            if p.is_file() and p.stat().st_size <= 200_000:
                verifier[p.relative_to(vdir).as_posix()] = read_text(p, 20_000)
    ai = res.get("agent_info") if isinstance(res.get("agent_info"), dict) else {}
    mi = ai.get("model_info") if isinstance(ai.get("model_info"), dict) else {}
    return {"name": tdir.name, "path": rel, "source": src, "started": trace.ts(res.get("started_at")),
            "finished": trace.ts(res.get("finished_at")), "model": mi.get("name") or "",
            "agent": ai.get("name") or "", "agent_result": res.get("agent_result"),
            "verifier_result": res.get("verifier_result"), "exception": res.get("exception_info"),
            "verifier": verifier, "transcript": _calls(tr)}


def _steps(rf: RunFiles, st: dict) -> list[dict]:
    from . import live
    base = float(st.get("started") or 0) or time.time()
    steps = st.get("steps") or {}
    out = []
    for s in runs.step_list(st):
        raw = steps.get(s["key"]) or {}
        item = dict(s, output=str(raw.get("output") or ""), log=read_text(rf.run_dir / "logs" /
                                                                          f"{trace.sanitize(s['key'])}.log", 200_000),
                    checks=checks_from_log(rf.run_dir / "logs" / f"{trace.sanitize(s['key'])}.log", base),
                    attempts=[], live=[])
        if s.get("fanout") is None and s["kind"] in ("agent", "task"):
            for jd in trace.job_dirs_for(st, s["key"], raw):
                trials = [_trial(t, f"jobs/{jd.name}/{t.name}") for t in trace.trial_dirs(jd)]
                item["attempts"].append({"job": jd.name, "attempt": trace._attempt_of(jd.name), "trials": trials})
            for stream, items in live.streams(rf.run_dir, s["key"]).items():
                item["live"].append({"stream": stream, "transcript": _calls(trace.stream_transcript(items))})
        out.append(item)
    return out


def _rel(rf: RunFiles, p: Path) -> str:
    for prefix, root in rf.roots():
        try:
            return f"{prefix}/{p.relative_to(root).as_posix()}"
        except ValueError:
            continue
    return p.name


def _results(rf: RunFiles, files: list[tuple[str, Path]]) -> list[dict]:
    """What the run produced: script step outputs, and the result files the agents wrote (result*.json, *.csv)."""
    out = []
    for arc, p in files:
        name = p.name.lower()
        if arc.startswith("run/outputs/") or (arc.startswith("jobs/") and "/artifacts/" in arc and (
                re.match(r"^results?([._-].*)?\.(json|csv|txt|md)$", name) or name in ("summary.md", "explanation.md"))):
            out.append({"path": arc, "size": _size(p), "text": read_text(p, 60_000)})
    return out


def _manuscripts(files: list[tuple[str, Path]]) -> list[dict]:
    found = [(arc, p) for arc, p in files if MANUSCRIPT_RE.search(arc) or MANUSCRIPT_DIR_RE.search(arc)]
    found.sort(key=lambda ap: _mtime(ap[1]), reverse=True)  # newest (the last attempt's) first
    return [{"path": arc, "size": _size(p), "mtime": _mtime(p), "format": p.suffix.lower().lstrip("."),
             "text": read_text(p, 400_000)} for arc, p in found]


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def citations(evs: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for e in evs:
        c = e.get("citation")
        if not isinstance(c, dict):
            continue
        k = str(c.get("doi") or c.get("url") or c.get("title") or "").lower()
        if k and k not in seen:
            seen[k] = dict(c, first_seq=e.get("seq"), steps=[])
        if k and e.get("step") and e["step"] not in seen[k]["steps"]:
            seen[k]["steps"].append(e["step"])
    return list(seen.values())


def gather(rf: RunFiles) -> dict:
    """Everything the report shows, from the run's files (redacted)."""
    st = _state(rf)
    summary = _summary(rf, st)
    plan = planner.load(rf.run_dir)
    cost = config.read_json(rf.run_dir / "costs.json")  # recorded when the run ended
    if summary.get("status") not in runs.TERMINAL or not isinstance(cost, dict):
        try:
            cost = costs.run_costs(st, plan)
        except Exception:  # an odd trial file must not cost us the report
            cost = cost if isinstance(cost, dict) else None
    evs = _events(rf, st, summary.get("title") or "")
    files = list(iter_files(rf))
    data = {
        "run": summary, "costs": cost, "events": evs, "steps": _steps(rf, st), "citations": citations(evs),
        "plan": {k: plan.get(k) for k in ("status", "plan", "hardware", "params", "budget_usd", "why", "model",
                                          "cost_usd", "error", "lessons")} if plan else None,
        "results": _results(rf, files), "manuscripts": _manuscripts(files),
        "files": [{"path": arc, "size": _size(p)} for arc, p in files],
        "source": rf.source, "notes": list(rf.notes), "generated": time.time(),
        "flow_yaml": read_text(rf.run_dir / "flow.yaml", 100_000),
    }
    red = redactor_for(rf)
    return red.obj(data)


# ── the zip ─────────────────────────────────────────────────────────────────

README = """# Trace of run {run_id}

Everything this run left behind, as the backend had it on {when} ({source}). Open `report.html` in any browser
(it works offline: no scripts, no external files).

| path | what |
|---|---|
| `report.html` | the readable report: timeline, steps, every tool call with its output, checks, costs, results, manuscript, citations |
| `run.json` | the run as `GET /api/runs/{run_id}` returns it, with costs and the planner's decision |
| `events.json` | the trace events the page showed, in order (`seq`) |
| `manifest.json` | every file below with its size and sha256; files left out and why |
| `run/` | the agent_runner run folder: `state.json`, `flow.yaml`, `logs/<step>.log`, `outputs/`, `plan.json`, `costs.json`, `live/<step>.jsonl` (the agent's stream-json as it was posted live) |
| `jobs/` | the Harbor job folders, one per step attempt (`<step>-a<n>`), with one folder per trial: `config.json`, `result.json`, `agent/trajectory.json` (ATIF), `agent/claude-code.txt`, `artifacts/` (the files the agent produced), `verifier/` |
| `server/` | the server's event log for this run and its run → task record |

Secrets were removed: the run's live token is not included, and values that look like keys or tokens are shown as
`***` ({redactions} replacement(s)).
"""


def build_zip(rf: RunFiles, out: Optional[Path] = None) -> Path:
    """Write the zip (to `out`, or a temporary file the caller deletes). Returns its path."""
    from . import report
    red = redactor_for(rf)
    data = gather(rf)
    top = f"macrae-trace-{rf.run_id}"
    if out is None:
        fd, tmp = tempfile.mkstemp(prefix=f"{top}-", suffix=".zip")
        os.close(fd)
        out = Path(tmp)
    manifest: dict[str, Any] = {"run_id": rf.run_id, "source": rf.source, "generated": data["generated"],
                                "files": [], "skipped": [{"path": "run/live/token", "why": "secret"}]
                                if (rf.run_dir / "live" / "token").exists() else [], "notes": list(rf.notes)}
    budget, per_file = max_total_bytes(), max_file_bytes()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for arc, p in iter_files(rf):
            size = _size(p)
            if size > per_file or size > budget:
                manifest["skipped"].append({"path": arc, "size": size,
                                            "why": "over MACRAE_TRACE_MAX_FILE_MB" if size > per_file
                                            else "over MACRAE_TRACE_MAX_MB for the whole zip"})
                continue
            try:
                raw = p.read_bytes()
            except OSError as e:
                manifest["skipped"].append({"path": arc, "size": size, "why": f"unreadable: {e.strerror or e}"})
                continue
            redacted = False
            if is_text(raw):
                before = red.count
                text = red.text(raw.decode("utf-8", errors="replace"))
                if red.count != before:
                    raw, redacted = text.encode("utf-8"), True
            budget -= len(raw)
            info = zipfile.ZipInfo(f"{top}/{arc}", date_time=_zip_time(p))
            info.compress_type = zipfile.ZIP_STORED if p.suffix.lower() in STORED_SUFFIXES else zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, raw)
            manifest["files"].append({"path": arc, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                                      **({"redacted": True} if redacted else {})})
        run_obj = dict(data["run"], costs=data["costs"], plan=data["plan"])
        extras = {
            "report.html": report.render(data),
            "run.json": json.dumps(run_obj, indent=1, ensure_ascii=False, default=str),
            "events.json": json.dumps({"run_id": rf.run_id, "events": data["events"]}, indent=1, ensure_ascii=False,
                                      default=str),
            "README.md": README.format(run_id=rf.run_id, source="local disk" if rf.source == "local" else "R2",
                                       when=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data["generated"])),
                                       redactions=red.count),
        }
        for name, text in extras.items():
            z.writestr(_now_info(f"{top}/{name}"), text)
        z.writestr(_now_info(f"{top}/manifest.json"), json.dumps(manifest, indent=1, ensure_ascii=False))
    return out


def _zip_time(p: Path) -> tuple:
    t = max(_mtime(p), 315532800.0)  # zip can't store dates before 1980
    return time.localtime(t)[:6]


def _now_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def report_html(rf: RunFiles) -> str:
    from . import report
    return report.render(gather(rf))
