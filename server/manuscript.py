"""Reconstruct how the agent wrote a file (the run's manuscript, its NOTES_TO_SELF.md) from its Write/Edit calls.

Sources, the same ones trace.py turns into events, for every agent step in state.json:
  (a) each Harbor trial of the step, <jobs_dir>/<step>-a<n>[-r<m>]/<trial>/: agent/trajectory.json (ATIF), else
      agent/claude-code.txt (stream-json) while the trial runs locally
  (b) what the claude wrapper posted while the agent ran (<run>/live/<step>.jsonl, live.py), one stream per
      `claude` invocation
Claude Code's file tools: Write {file_path, content}, Edit {file_path, old_string, new_string, replace_all},
MultiEdit {file_path, edits: [{old_string, new_string, replace_all}]}. A call seen live and again in the trajectory
is one edit: same tool_use id, or (no ids) the nth call with the same name and arguments in the step.

Each trial is a session in its own sandbox. A live stream joins the trial it shares a tool_use id or session id
with, else the trial without a transcript yet that started last before it (the attempt that is running). A session
starts from whatever the sandbox had, which we don't see: usually its first op is a Write; an Edit first is applied
to what the session before left if it fits, else the content is unknown (None) until the next Write. When the edits
don't determine the final file, the latest trial's copy (artifacts/app/**/<file>) is used.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import config, live, runs, trace

MANUSCRIPT = "results/manuscript.md"
NOTES = "NOTES_TO_SELF.md"
MAX_EDITS = 2000  # the newest ones are kept; seq still counts from the first edit
MAX_STR = 200 * 1024
FILE_TOOLS = {"Write", "Edit", "MultiEdit"}
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
# Claude Code's tool errors come back as "<tool_use_error>…</tool_use_error>" (the ATIF result has only the text)
ERROR_RE = re.compile(r"<tool_use_error>|String to replace not found|Found \d+ matches of the string|"
                      r"File has not been read yet|File has been (?:unexpectedly )?modified since|"
                      r"No changes to make|^Error(?: editing| writing)?\b", re.I | re.M)
IMG_MD_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^)]*[\"'])?\s*\)")
IMG_HTML_RE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
JOB_RE = re.compile(r"-a(\d+)(?:-r(\d+))?$")


def _h(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def clip(s: Optional[str]) -> Optional[str]:
    if s is None or len(s) <= MAX_STR:
        return s
    return s[:MAX_STR]


def matches(path: str, suffix: str) -> bool:
    p, s = path.replace("\\", "/"), suffix.strip("/")
    return p == s or p.endswith("/" + s)


def _args(a: Any) -> dict:
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except ValueError:
            return {}
    return a if isinstance(a, dict) else {}


def is_error(output: Optional[str]) -> bool:
    return bool(output) and bool(ERROR_RE.search(output[:2000]))


# ── transcripts with their tool errors ──────────────────────────────────────


def _stream_errors(items: list[tuple[Optional[float], dict]]) -> frozenset[str]:
    """tool_use ids whose tool_result has is_error (stream_transcript keeps only the text)."""
    out = set()
    for _, d in items:
        content = (d.get("message") or {}).get("content") if isinstance(d.get("message"), dict) else None
        if d.get("type") == "user" and isinstance(content, list):
            out.update(str(b.get("tool_use_id")) for b in content
                       if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"))
    return frozenset(out)


def _parse_stream_file(path: Path) -> Optional[tuple[trace.Transcript, frozenset[str]]]:
    items = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            items.append((trace.ts(d.get("timestamp")), d))
    return (trace.stream_transcript(items), _stream_errors(items)) if items else None


def _atif_errors(path: Path) -> frozenset[str]:
    """Observation results flagged as errors, if the converter kept the flag (is_error, or extra.is_error)."""
    try:
        data = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError):
        return frozenset()
    out = set()
    for s in data.get("steps") or [] if isinstance(data, dict) else []:
        obs = s.get("observation") if isinstance(s, dict) else None
        for r in (obs or {}).get("results") or [] if isinstance(obs, dict) else []:
            if isinstance(r, dict) and (r.get("is_error") or (isinstance(r.get("extra"), dict)
                                                              and r["extra"].get("is_error"))):
                out.add(str(r.get("source_call_id")))
    return frozenset(out)


def trial_transcript(trial: Path) -> Optional[tuple[trace.Transcript, frozenset[str]]]:
    atif = trial / "agent" / "trajectory.json"
    tr = trace.cached(atif, trace.parse_atif)
    if tr is not None:
        return tr, trace.cached(atif, _atif_errors) or frozenset()
    return trace.cached(trial / "agent" / "claude-code.txt", _parse_stream_file)


def keyed_calls(tr: trace.Transcript) -> list[tuple[str, trace.Call]]:
    """(dedupe key, call) per tool call: the tool_use id, else the nth call with this name and these arguments."""
    out, occ = [], {}
    for m in tr.msgs:
        if m.kind != "call" or m.call is None:
            continue
        c = m.call
        if c.id:
            out.append((c.id, c))
        else:
            h = "h" + _h(c.name, c.args)
            occ[h] = occ.get(h, 0) + 1
            out.append((f"{h}#{occ[h]}", c))
    return out


# ── sessions: one per trial (or per live stream nobody else claims) ──────────


@dataclass
class Entry:
    key: str
    call: trace.Call
    t: Optional[float]
    output: Optional[str]
    err: bool
    rank: tuple[int, int]


@dataclass
class Session:
    step: str
    attempt: int
    order: tuple
    t0: Optional[float]
    sid: str = ""
    has_base: bool = False
    entries: dict[str, Entry] = field(default_factory=dict)
    k: int = 0

    def add_base(self, tr: trace.Transcript, errors: frozenset[str]) -> None:
        """The trial's own transcript: its order is the session's order."""
        self.has_base = True
        self.sid = self.sid or tr.session
        for i, (key, c) in enumerate(keyed_calls(tr)):
            self.entries.setdefault(key, Entry(key, c, c.t, c.output, c.id in errors or is_error(c.output), (i, 0)))

    def add_live(self, tr: trace.Transcript, errors: frozenset[str]) -> None:
        """A live stream: known calls fill in times/outputs; new ones go right after the last known call before them."""
        self.sid = self.sid or tr.session
        anchor = -1
        for key, c in keyed_calls(tr):
            e = self.entries.get(key)
            if e is not None:
                anchor = e.rank[0]
                e.t = e.t if e.t is not None else c.t
                if not e.output and c.output:
                    e.output = c.output
                e.err = e.err or c.id in errors or is_error(c.output)
                continue
            self.k += 1
            self.entries[key] = Entry(key, c, c.t, c.output, c.id in errors or is_error(c.output), (anchor, self.k))

    def ordered(self) -> list[Entry]:
        out = sorted(self.entries.values(), key=lambda e: e.rank)
        last = self.t0
        for e in out:
            if e.t is None:
                e.t = last
            last = e.t if e.t is not None else last
        return out


def _job_order(job: Path) -> tuple[int, int]:
    m = JOB_RE.search(job.name)
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


def _trial_start(trial: Path) -> Optional[float]:
    res = config.read_json(trial / "result.json")
    return trace.ts(res.get("started_at")) if isinstance(res, dict) else None


def agent_steps(state: dict) -> list[tuple[str, dict]]:
    steps = state.get("steps") or {}
    order = [k for k in state.get("order") or [] if k in steps] + [k for k in steps if k not in (state.get("order")
                                                                                                    or [])]
    return [(k, steps[k]) for k in order if isinstance(steps.get(k), dict) and steps[k].get("fanout") is None
            and str(steps[k].get("kind") or "") in ("agent", "task")]


def step_trials(state: dict, key: str, step: dict) -> list[tuple[int, int, Path]]:
    """(attempt, reroute, trial dir) of a step, oldest first."""
    out = []
    for jd in trace.job_dirs_for(state, key, step):
        a, r = _job_order(jd)
        out += [(a, r, td) for td in trace.trial_dirs(jd)]
    return out


def step_sessions(state: dict, run_dir: Path, key: str, step: dict) -> list[Session]:
    t_step = float(step.get("started") or 0) or None
    sessions = []
    for a, r, td in step_trials(state, key, step):
        s = Session(key, a, (a, r, td.name), _trial_start(td) or t_step)
        got = trial_transcript(td)
        if got:
            s.add_base(*got)
        sessions.append(s)
    for name, items in sorted(live.streams(run_dir, key).items(), key=lambda kv: kv[1][0][0] if kv[1] else 0):
        tr = trace.stream_transcript(items)
        _session_for(sessions, tr, key, step, items[0][0] if items else None).add_live(tr, _stream_errors(items))
    return sorted(sessions, key=lambda s: s.order)


def _session_for(sessions: list[Session], tr: trace.Transcript, key: str, step: dict,
                 t_first: Optional[float]) -> Session:
    ids = {c.id for _, c in keyed_calls(tr) if c.id}
    for s in sessions:
        if ids & set(s.entries):
            return s
    if tr.session:
        for s in sessions:
            if s.sid == tr.session:
                return s
    # the attempt that is running: no transcript of its own yet, started last before this stream
    waiting = [s for s in sessions if not s.has_base and (s.t0 is None or t_first is None or s.t0 <= t_first + 5)]
    if waiting:
        return waiting[-1]
    att = int(step.get("attempt") or 0) or max([s.attempt for s in sessions] + [1])
    s = Session(key, att, (att, 1 << 30, "~live"), t_first)
    sessions.append(s)
    return s


# ── replaying the ops ───────────────────────────────────────────────────────


def _apply(base: Optional[str], old: str, new: str, replace_all: bool) -> Optional[str]:
    """base with old replaced by new; None if base is unknown or old isn't in it."""
    if old == "":
        return new if not base else None  # Edit with an empty old_string creates the file
    if base is None:
        return None
    if old not in base:
        return None
    return base.replace(old, new) if replace_all else base.replace(old, new, 1)


@dataclass
class FileState:
    content: Optional[str] = None
    guessed: bool = True  # carried over from an earlier session (another sandbox): only a guess


def replay(e: Entry, path: str, st: FileState) -> list[dict]:
    """The edit records of one call, updating st. Content is the file after each op (None = unknown)."""
    c, args = e.call, _args(e.call.args)
    confirmed = bool(e.output) and not e.err  # Claude Code said it worked
    base = {"t": e.t, "path": path}
    if c.name == "Write":
        new = str(args.get("content") or "")
        if not e.err:
            st.content, st.guessed = new, False
        return [dict(base, op="write", old=None, new=new, content=st.content, ok=not e.err)]
    subs = [args] if c.name == "Edit" else [x for x in args.get("edits") or [] if isinstance(x, dict)]
    subs = [(str(x.get("old_string") or ""), str(x.get("new_string") or ""), bool(x.get("replace_all")))
            for x in subs]
    if e.err:
        return [dict(base, op="edit", old=o, new=n, content=st.content, ok=False) for o, n, _ in subs]
    steps, cur = [], st.content
    for o, n, ra in subs:
        cur = _apply(cur, o, n, ra)
        steps.append(cur)
    if st.content is not None and None in steps and not st.guessed and not confirmed:
        # a known file, old not found, no word from the tool yet: Claude Code would refuse it too (atomically)
        return [dict(base, op="edit", old=o, new=n, content=st.content, ok=False) for o, n, _ in subs]
    # None once a sub-edit doesn't fit: the file changed in ways we didn't see (a Bash command, another sandbox)
    out = [dict(base, op="edit", old=o, new=n, content=cur, ok=True) for (o, n, _), cur in zip(subs, steps)]
    if steps:
        st.content = steps[-1]
        st.guessed = st.guessed and st.content is None
    return out


def _edits(state: dict, run_dir: Path, suffix: str) -> list[dict]:
    out: list[dict] = []
    files: dict[str, FileState] = {}
    for key, step in agent_steps(state):
        for s in step_sessions(state, run_dir, key, step):
            for st in files.values():
                st.guessed = True  # a new sandbox
            for e in s.ordered():
                if e.call.name not in FILE_TOOLS:
                    continue
                path = str(_args(e.call.args).get("file_path") or "")
                if not path or not matches(path, suffix):
                    continue
                for rec in replay(e, path, files.setdefault(path, FileState())):
                    out.append({"seq": len(out) + 1, "t": rec["t"], "step": key, "attempt": s.attempt, **rec})
    return out


# ── figures and the trial's copy of the file ────────────────────────────────


def figures(content: Optional[str], suffix: str, path: str = "") -> list[str]:
    """Images the file references, as paths relative to the app folder the file lives in (e.g. results/fig1.png)."""
    if not content:
        return []
    folder = posixpath.dirname(suffix.strip("/"))
    app = path[:-len(suffix.strip("/"))] if path and matches(path, suffix) else ""  # "/app/paper/"
    out: list[str] = []
    found = sorted([(m.start(), m.group(1)) for rx in (IMG_MD_RE, IMG_HTML_RE) for m in rx.finditer(content)])
    for _, ref in found:
        ref = re.split(r"[?#]", ref.strip(), 1)[0]
        if not ref or re.match(r"^[a-z][a-z0-9+.-]*:", ref, re.I) or not ref.lower().endswith(IMAGE_EXT):
            continue
        if ref.startswith("/"):
            if not app or not ref.startswith(app):
                continue
            rel = ref[len(app):]
        elif folder and (ref.startswith(folder + "/") or ref.startswith("./" + folder + "/")):
            rel = ref
        else:
            rel = posixpath.join(folder, ref)
        rel = posixpath.normpath(rel)
        if rel.startswith("..") or rel.startswith("/") or rel == "." or rel in out:
            continue
        out.append(rel)
    return out


def latest_trials(state: dict) -> list[Path]:
    """Every trial of the run's agent steps, newest first."""
    found = []
    for i, (key, step) in enumerate(agent_steps(state)):
        for a, r, td in step_trials(state, key, step):
            found.append(((_trial_start(td) or 0.0), i, a, r, td.name, td))
    return [x[-1] for x in sorted(found, key=lambda x: x[:-1], reverse=True)]


def _inside(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def artifact_text(state: dict, suffix: str) -> Optional[str]:
    """The file as the newest trial that has it left it (artifacts/app/**/<suffix>)."""
    name = posixpath.basename(suffix)
    for td in latest_trials(state):
        app = td / "artifacts" / "app"
        if not app.is_dir():
            continue
        try:
            hits = [p for p in app.rglob(name) if matches(p.relative_to(app).as_posix(), suffix)]
        except OSError:
            continue
        hits = [p for p in hits if p.is_file() and _inside(p, app)]
        if hits:
            best = max(hits, key=lambda p: p.stat().st_mtime)
            try:
                return clip(best.read_text(errors="replace"))
            except OSError:
                continue
    return None


# ── public ──────────────────────────────────────────────────────────────────


def edit_stream(run_id: str, path_suffix: str = MANUSCRIPT) -> Optional[dict]:
    """The ordered edits of the file(s) ending in path_suffix, with the content after each. None = no such run."""
    state = runs.read_state(run_id)
    if state is None:
        return None
    edits = _edits(state, runs.run_dir(run_id), path_suffix)
    last = edits[-1] if edits else None
    content = last["content"] if last else None
    if content is None:
        content = artifact_text(state, path_suffix)
    times = [e["t"] for e in edits if e["t"] is not None]
    for e in edits[-MAX_EDITS:]:
        e.update(old=clip(e["old"]), new=clip(e["new"]), content=clip(e["content"]),
                 t=round(float(e["t"]), 3) if e["t"] is not None else None)
    return {"run_id": run_id, "path": path_suffix, "edits": edits[-MAX_EDITS:], "content": clip(content),
            "done": runs.public_status(str(state.get("status") or "")) in runs.TERMINAL,
            "figures": figures(content, path_suffix, last["path"] if last else ""),
            "updated": round(max(times), 3) if times else None}


def notes(run_id: str) -> Optional[dict]:
    """{"run_id", "notes": the latest NOTES_TO_SELF.md or None, "edits": n}. None = no such run."""
    res = edit_stream(run_id, NOTES)
    if res is None:
        return None
    return {"run_id": run_id, "notes": res["content"], "edits": len(res["edits"])}
