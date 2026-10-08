"""Read a finished run the way the page saw it: state.json, step logs, Harbor trials, TraceEvents, costs.

    run = runs.load("20261008-201851-small-calc-71d4")       # or a run folder path
    run.task_id, run.status, run.wall_s, run.steps[...].trials[...].calls[...]

Sources (all written by others; evolve only reads them):
  <run>/state.json, flow.yaml, macrae.json, logs/<step>.log, outputs/   agent_runner.flows (and tasks.runner)
  <run>/live/<step>.jsonl                                               the backend's live intake (v2), stream-json
  <jobs_dir>/<step>-a<n>[-r<m>]/<trial>/{result.json, agent/trajectory.json, agent/claude-code.txt, artifacts/app/}
  $MACRAE_SERVER_DATA/events/<run_id>.jsonl                             the page's TraceEvents with their seq
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from . import config, costs, store

LOG_LINE_RE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] (.*)$")
TERMINAL = {"ok", "failed", "cancelled", "crashed"}
INTERNAL_VARS = {"task_id", "task_title", "python", "environment", "lessons", "tools_dir"}
ERROR_RE = re.compile(r"Traceback \(most recent call last\)|^\w*(Error|Exception)\b.*:|command not found|"
                      r"No such file or directory|^Exit code [1-9]|\bKilled\b|Segmentation fault|"
                      r"\bfatal error\b|^error:|^ERROR\b", re.M | re.I)


def sanitize(s: str) -> str:
    """Same as agent_runner.flows._sanitize: how step keys become log and job names."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")[:60] or "step"


def ts(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v) / (1000.0 if v > 1e12 else 1.0)
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()


def _json(p: Path) -> Any:
    return store.read_json(p)


def _args(a: Any) -> Any:
    if isinstance(a, str):
        try:
            return json.loads(a)
        except ValueError:
            return a
    return a


def text_of(c: Any) -> str:
    """ATIF / stream-json content: a string, a list of parts ({type: text, text}), or None."""
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(t for t in (text_of(x) for x in c) if t).strip()
    if isinstance(c, dict):
        if "text" in c:
            return str(c.get("text") or "")
        if "content" in c:
            return text_of(c["content"])
        return ""
    return str(c)


# ── shapes ──────────────────────────────────────────────────────────────────


@dataclass
class Call:
    id: str
    name: str
    args: Any
    output: str = ""
    is_error: bool = False
    t: Optional[float] = None
    seconds: Optional[float] = None

    @property
    def command(self) -> str:
        a = self.args
        if isinstance(a, dict):
            return str(a.get("command") or a.get("cmd") or "")
        return a if isinstance(a, str) and self.name.lower() == "bash" else ""

    @property
    def description(self) -> str:
        a = self.args
        return str(a.get("description") or "") if isinstance(a, dict) else ""

    @property
    def path(self) -> str:
        a = self.args
        if isinstance(a, dict):
            return str(a.get("file_path") or a.get("path") or a.get("notebook_path") or "")
        return ""

    @property
    def failed(self) -> bool:
        return self.is_error or bool(self.output and ERROR_RE.search(self.output[-4000:]))


@dataclass
class Trial:
    dir: Path
    step: str
    attempt: int
    started: Optional[float] = None
    finished: Optional[float] = None
    reward: Optional[float] = None
    exception: str = ""
    model: str = ""
    cost_usd: Optional[float] = None
    tokens: dict = field(default_factory=lambda: {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0})
    instruction: str = ""
    calls: list[Call] = field(default_factory=list)
    texts: list[tuple[Optional[float], str]] = field(default_factory=list)
    source: str = ""  # atif | stream | live | ""
    atif: Optional[dict] = None

    @property
    def seconds(self) -> Optional[float]:
        return self.finished - self.started if self.started and self.finished and self.finished >= self.started else None

    @property
    def final_message(self) -> str:
        return self.texts[-1][1] if self.texts else ""

    @property
    def artifacts_app(self) -> Path:
        return self.dir / "artifacts" / "app"


@dataclass
class Check:
    attempt: int
    rc: int
    text: str
    t: float


@dataclass
class Step:
    key: str
    kind: str
    status: str
    attempt: int = 1
    started: Optional[float] = None
    finished: Optional[float] = None
    error: str = ""
    reward: Optional[float] = None
    account: str = ""
    environment: str = ""
    output: Any = None
    job_dir: str = ""
    description: str = ""
    log: list[tuple[float, str]] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)   # [{"n", "status", "reward", "passed"}]
    trials: list[Trial] = field(default_factory=list)

    @property
    def seconds(self) -> Optional[float]:
        return self.finished - self.started if self.started and self.finished and self.finished >= self.started else None


@dataclass
class Run:
    id: str
    dir: Path
    state: dict
    task_id: str
    title: str
    inputs: dict
    status: str
    started: float
    finished: Optional[float]
    steps: list[Step]
    lessons_text: str = ""
    lessons_used: list[str] = field(default_factory=list)
    hardware: str = ""
    _events: Optional[list[dict]] = None
    _costs: Optional[dict] = None

    @property
    def done(self) -> bool:
        return self.status in TERMINAL

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def wall_s(self) -> Optional[float]:
        return round(self.finished - self.started, 1) if self.finished and self.started else None

    @property
    def agent_steps(self) -> list[Step]:
        return [s for s in self.steps if s.kind in ("agent", "task")]

    @property
    def reward(self) -> Optional[float]:
        rs = [float(s.reward) for s in self.agent_steps if isinstance(s.reward, (int, float))]
        if rs:
            return round(sum(rs) / len(rs), 3)
        return None

    def step(self, key: str) -> Optional[Step]:
        return next((s for s in self.steps if s.key == key), None)

    @property
    def events(self) -> list[dict]:
        if self._events is None:
            self._events = load_events(self)
        return self._events

    @property
    def costs(self) -> dict:
        if self._costs is None:
            self._costs = costs.run_costs(self)
        return self._costs


# ── loading ─────────────────────────────────────────────────────────────────


def resolve(run: str | Path) -> Path:
    """A run id (looked up in $AGENT_RUNNER_HOME/runs) or a run folder."""
    p = Path(str(run)).expanduser()
    if p.is_dir() and (p / "state.json").is_file():
        return p.resolve()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}", str(run)) and ".." not in str(run):
        d = config.runs_dir() / str(run)
        if d.is_dir():
            return d
    raise FileNotFoundError(f"no run {run!r} (looked in {config.runs_dir()})")


def _log_time(h: int, m: int, s: int, base: float) -> float:
    b = datetime.fromtimestamp(base)
    dt = b.replace(hour=h, minute=m, second=s, microsecond=0)
    if dt.timestamp() < base - 60:
        dt += timedelta(days=1)  # crossed midnight
    return dt.timestamp()


def read_log(path: Path, base: float) -> list[tuple[float, str]]:
    """(time, message) per entry of a step log; continuation lines are folded into the entry before."""
    entries: list[list] = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    for ln in lines:
        m = LOG_LINE_RE.match(ln)
        if m:
            entries.append([_log_time(int(m[1]), int(m[2]), int(m[3]), base), m[4]])
        elif entries:
            entries[-1][1] += "\n" + ln
    return [(e[0], e[1]) for e in entries]


def _parse_log(step: Step) -> None:
    attempt = 1
    for t, msg in step.log:
        head = msg.split("\n", 1)[0]
        if head.startswith("harbor ("):
            m = re.search(r"--job-name\s+\S*?-a(\d+)(?:-r\d+)?(?:\s|$)", head)
            if m:
                attempt = int(m.group(1))
        elif head.startswith("check exit"):
            m = re.match(r"check exit (-?\d+)", head)
            text = msg.split(":", 1)[1].strip() if ":" in msg else ""
            step.checks.append(Check(attempt, int(m.group(1)) if m else 1, text, t))
        else:
            m = re.match(r"attempt (\d+): (\S+) reward=(\S+) passed=(\S+)", head)
            if m:
                try:
                    rw = float(m.group(3))
                except ValueError:
                    rw = None
                step.attempts.append({"n": int(m.group(1)), "status": m.group(2), "reward": rw,
                                      "passed": m.group(4) == "True", "t": t})
                attempt = int(m.group(1)) + 1


def parse_atif(data: dict, trial: Trial) -> None:
    steps = [s for s in data.get("steps") or [] if isinstance(s, dict)]
    times = [ts(s.get("timestamp")) for s in steps]
    for i, s in enumerate(steps):
        t = times[i]
        nxt = next((x for x in times[i + 1:] if x is not None), None)
        src = str(s.get("source") or "")
        msg = text_of(s.get("message"))
        if src == "user":
            if not trial.instruction and msg.strip():
                trial.instruction = msg
            continue
        if src != "agent":
            continue
        if msg.strip():
            trial.texts.append((t, msg))
        results = [r for r in ((s.get("observation") or {}).get("results") or []) if isinstance(r, dict)]
        by_id = {str(r.get("source_call_id")): r for r in results if r.get("source_call_id") is not None}
        calls = [c for c in s.get("tool_calls") or [] if isinstance(c, dict)]
        for j, c in enumerate(calls):
            cid = str(c.get("tool_call_id") or "")
            r = by_id.get(cid) or (results[j] if len(results) == len(calls) else None) or {}
            secs = (nxt - t) if (t is not None and nxt is not None and nxt >= t) else None
            trial.calls.append(Call(cid, str(c.get("function_name") or ""), _args(c.get("arguments")),
                                    text_of(r.get("content")), bool(r.get("is_error")), t, secs))
    fm = data.get("final_metrics") or {}
    if isinstance(fm, dict):
        if fm.get("total_cost_usd") is not None and trial.cost_usd is None:
            trial.cost_usd = float(fm["total_cost_usd"])
        if fm.get("total_prompt_tokens") and not trial.tokens["in"]:
            trial.tokens["in"] = int(fm.get("total_prompt_tokens") or 0)
            trial.tokens["out"] = int(fm.get("total_completion_tokens") or 0)
            trial.tokens["cache_read"] = int(fm.get("total_cached_tokens") or 0)
    agent = data.get("agent") or {}
    if isinstance(agent, dict) and agent.get("model_name") and not trial.model:
        trial.model = str(agent["model_name"])


def _stream_records(path: Path) -> list[dict]:
    out = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict) and "type" not in d:  # a wrapper {"line": "<stream-json>", …}
            inner = next((d[k] for k in ("line", "raw", "data") if isinstance(d.get(k), str)), None)
            if inner:
                try:
                    d = json.loads(inner)
                except ValueError:
                    continue
        if isinstance(d, dict):
            out.append(d)
    return out


def parse_stream(records: list[dict], trial: Trial) -> None:
    """Claude Code `--output-format stream-json` records."""
    calls: dict[str, Call] = {}
    for d in records:
        typ = d.get("type")
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        content = msg.get("content")
        t = ts(d.get("timestamp"))
        if typ == "assistant" and isinstance(content, list):
            if msg.get("model") and not trial.model:
                trial.model = str(msg["model"])
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and str(b.get("text") or "").strip():
                    trial.texts.append((t, str(b["text"])))
                elif b.get("type") == "tool_use":
                    c = Call(str(b.get("id") or ""), str(b.get("name") or ""), _args(b.get("input")), "", False, t)
                    if c.id:
                        calls[c.id] = c
                    trial.calls.append(c)
        elif typ == "user":
            if isinstance(content, str) and not trial.instruction:
                trial.instruction = content
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        c = calls.get(str(b.get("tool_use_id") or ""))
                        if c is not None:
                            c.output = text_of(b.get("content"))
                            c.is_error = bool(b.get("is_error"))
                            if c.t is not None and t is not None and t >= c.t:
                                c.seconds = t - c.t
        elif typ == "result":
            if d.get("total_cost_usd") is not None:
                trial.cost_usd = float(d["total_cost_usd"])  # Claude Code's own number wins
            u = d.get("usage") or {}
            if isinstance(u, dict) and u:
                trial.tokens = {"in": int(u.get("input_tokens") or 0), "out": int(u.get("output_tokens") or 0),
                                "cache_read": int(u.get("cache_read_input_tokens") or 0),
                                "cache_write": int(u.get("cache_creation_input_tokens") or 0)}
            if d.get("result") and not trial.texts:
                trial.texts.append((t, str(d["result"])))


def read_trial(d: Path, step: str, attempt: int) -> Trial:
    r = _json(d / "result.json") or {}
    tr = Trial(dir=d, step=step, attempt=attempt, started=ts(r.get("started_at")), finished=ts(r.get("finished_at")))
    vr = (r.get("verifier_result") or {}).get("rewards") or {}
    if isinstance(vr, dict) and vr.get("reward") is not None:
        try:
            tr.reward = float(vr["reward"])
        except (TypeError, ValueError):
            pass
    ex = r.get("exception_info") or {}
    if isinstance(ex, dict) and ex:
        tr.exception = f"{ex.get('exception_type', '')}: {ex.get('exception_message', '')}".strip(": ")
    ai = r.get("agent_info") or {}
    tr.model = str(((ai.get("model_info") or {}).get("name")) or "") if isinstance(ai, dict) else ""
    ar = r.get("agent_result") or {}
    if isinstance(ar, dict):
        if ar.get("cost_usd") is not None:
            tr.cost_usd = float(ar["cost_usd"])
        tr.tokens = {"in": int(ar.get("n_input_tokens") or 0), "out": int(ar.get("n_output_tokens") or 0),
                     "cache_read": int(ar.get("n_cache_tokens") or 0), "cache_write": 0}
    atif = _json(d / "agent" / "trajectory.json")
    stream = d / "agent" / "claude-code.txt"
    if isinstance(atif, dict) and atif.get("steps"):
        tr.atif = atif
        parse_atif(atif, tr)
        tr.source = "atif"
        if stream.is_file():  # Claude Code's own cost/usage line is the most exact number
            extra = Trial(dir=d, step=step, attempt=attempt)
            parse_stream([x for x in _stream_records(stream) if x.get("type") == "result"], extra)
            if extra.cost_usd is not None:
                tr.cost_usd = extra.cost_usd
            if any(extra.tokens.values()):
                tr.tokens = extra.tokens
    elif stream.is_file():
        parse_stream(_stream_records(stream), tr)
        tr.source = "stream"
    return tr


def job_dirs(state: dict, key: str, step: dict) -> list[tuple[int, Path]]:
    pat = re.compile(rf"^{re.escape(sanitize(key))}-a(\d+)(?:-r(\d+))?$")
    roots = []
    if state.get("jobs_dir"):
        roots.append(Path(str(state["jobs_dir"])))
    if step.get("job_dir"):
        roots.append(Path(str(step["job_dir"])).parent)
    found: dict[str, tuple[int, int, Path]] = {}
    for r in roots:
        try:
            for p in r.iterdir():
                m = pat.match(p.name)
                if p.is_dir() and m:
                    found[str(p)] = (int(m.group(1)), int(m.group(2) or 0), p)
        except OSError:
            continue
    return [(a, p) for a, _, p in sorted(found.values(), key=lambda x: (x[0], x[1]))]


def _trial_dirs(job: Path) -> list[Path]:
    try:
        kids = sorted(p for p in job.iterdir() if p.is_dir())
    except OSError:
        return []
    return [p for p in kids if (p / "config.json").is_file() or (p / "agent").is_dir() or (p / "result.json").is_file()]


def _descriptions(run_dir: Path) -> dict[str, str]:
    try:
        import yaml
        spec = yaml.safe_load((run_dir / "flow.yaml").read_text()) or {}
    except Exception:
        return {}
    return {str(s.get("id")): str(s.get("description") or "") for s in spec.get("steps") or []
            if isinstance(s, dict) and s.get("id")}


def _task_of(run_dir: Path, state: dict) -> tuple[str, str, dict]:
    vars_ = state.get("vars") if isinstance(state.get("vars"), dict) else {}
    meta = _json(run_dir / "macrae.json") or {}
    side = _json(config.server_data_dir() / "runs" / f"{run_dir.name}.json") or {}
    task_id = str(meta.get("task_id") or vars_.get("task_id") or side.get("task_id") or state.get("name") or "")
    title = str(meta.get("title") or vars_.get("task_title") or side.get("title") or task_id)
    inputs = meta.get("inputs") or side.get("inputs") or {k: v for k, v in vars_.items() if k not in INTERNAL_VARS}
    return task_id, title, inputs if isinstance(inputs, dict) else {}


def load(run: str | Path) -> Run:
    d = resolve(run)
    state = _json(d / "state.json")
    if not isinstance(state, dict):
        raise FileNotFoundError(f"{d}/state.json is missing or unreadable")
    task_id, title, inputs = _task_of(d, state)
    base = float(state.get("started") or 0) or d.stat().st_mtime
    descs = _descriptions(d)
    steps_raw = state.get("steps") or {}
    order = list(state.get("order") or []) + [k for k in steps_raw if k not in (state.get("order") or [])]
    steps: list[Step] = []
    for key in order:
        s = steps_raw.get(key)
        if not isinstance(s, dict) or s.get("fanout") is not None:
            continue  # a foreach parent; its items are steps of their own
        st = Step(key=key, kind=str(s.get("kind") or ""), status=str(s.get("status") or ""),
                  attempt=int(s.get("attempt") or 1), started=s.get("started"), finished=s.get("finished"),
                  error=str(s.get("error") or ""), reward=s.get("reward"), account=str(s.get("account") or ""),
                  environment=str(s.get("environment") or ""), output=s.get("output"),
                  job_dir=str(s.get("job_dir") or ""), description=descs.get(key.split("[", 1)[0], ""))
        st.log = read_log(d / "logs" / f"{sanitize(key)}.log", base)
        _parse_log(st)
        if st.kind in ("agent", "task"):
            for attempt, jd in job_dirs(state, key, s):
                for td in _trial_dirs(jd):
                    st.trials.append(read_trial(td, key, attempt))
            live = d / "live" / f"{sanitize(key)}.jsonl"
            if live.is_file() and not any(t.calls or t.texts for t in st.trials):
                lt = Trial(dir=live.parent, step=key, attempt=st.attempt, source="live")
                parse_stream(_stream_records(live), lt)
                st.trials.append(lt)
        steps.append(st)
    status = str(state.get("status") or "")
    vars_ = state.get("vars") if isinstance(state.get("vars"), dict) else {}
    lessons_text = vars_.get("lessons") if isinstance(vars_.get("lessons"), str) else ""
    hardware = str(vars_.get("hardware") or "")
    plan = _json(d / "plan.json")
    if not hardware and isinstance(plan, dict):
        hardware = str(plan.get("hardware") or "")
    return Run(id=d.name, dir=d, state=state, task_id=task_id, title=title, inputs=inputs, status=status,
               started=base, finished=state.get("finished"), steps=steps, lessons_text=lessons_text,
               lessons_used=store.ids_in(lessons_text), hardware=hardware)


def list_run_dirs() -> list[Path]:
    root = config.runs_dir()
    try:
        return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")
                      and (p / "state.json").is_file())
    except OSError:
        return []


# ── TraceEvents (with the seq the page shows) ───────────────────────────────


def _server():
    """The backend's modules, if this checkout has them (evolve works without)."""
    try:
        from server import events as sev, runs as sruns, trace as strace  # noqa: F401
        return sev, sruns, strace
    except Exception:
        if str(config.REPO_ROOT) not in sys.path:
            sys.path.append(str(config.REPO_ROOT))
            try:
                from server import events as sev, runs as sruns, trace as strace  # noqa: F401
                return sev, sruns, strace
            except Exception:
                return None
        return None


def load_events(run: Run) -> list[dict]:
    """The run's TraceEvents with seq.

    1. the backend's event log ($MACRAE_SERVER_DATA/events/<run>.jsonl): exactly the seqs the page showed;
    2. else the backend's own builder, in memory (never written, so the server's log stays its own);
    3. else a small builder of our own (same step / type vocabulary, our own seq).
    """
    p = config.server_data_dir() / "events" / f"{run.id}.jsonl"
    evs: list[dict] = []
    try:
        for ln in p.read_text().splitlines():
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            ev = rec.get("event") if isinstance(rec, dict) else None
            if isinstance(ev, dict) and ev.get("seq") == len(evs) + 1:
                evs.append(ev)
    except OSError:
        pass
    if evs:
        return evs
    srv = _server()
    if srv is not None:
        sev, sruns, strace = srv
        try:
            state = dict(run.state, _dir=str(run.dir))
            lg = sev.RunLog(run.id, None)
            lg.merge(strace.build(state, run.title), final=run.done)
            if lg.events:
                return list(lg.events)
        except Exception:
            pass
    return _own_events(run)


def _own_events(run: Run) -> list[dict]:
    cands: list[tuple[float, str, str, str, str]] = [(run.started, "", "status", f"Started {run.title}", "")]
    for s in run.steps:
        for t, msg in s.log:
            head = msg.split("\n", 1)[0]
            if head.startswith("check exit"):
                ok = head.startswith("check exit 0")
                cands.append((t, s.key, "status" if ok else "error", "Check passed" if ok else "Check failed",
                              msg.split(":", 1)[-1].strip()))
        for tr in s.trials:
            for c in tr.calls:
                typ = {"read": "read", "write": "write", "edit": "write", "bash": "calc"}.get(c.name.lower(), "status")
                title = c.description or c.command or c.path or c.name
                cands.append((c.t or tr.started or s.started or run.started, s.key, typ, title[:90], c.output[-600:]))
            if tr.exception:
                cands.append((tr.finished or s.finished or run.started, s.key, "error", "Agent run failed", tr.exception))
        if s.status in ("ok", "failed", "cancelled", "skipped"):
            typ = {"ok": "result", "skipped": "status"}.get(s.status, "error")
            cands.append((s.finished or s.started or run.started, s.key, typ, f"{s.key} {s.status}", s.error))
    if run.done:
        cands.append((run.finished or run.started, "", "result" if run.ok else "error",
                      "Run finished" if run.ok else "Run failed", ""))
    cands.sort(key=lambda c: c[0])
    return [{"seq": i + 1, "t": c[0], "step": c[1], "type": c[2], "title": c[3], "detail": c[4][:600],
             "citation": None} for i, c in enumerate(cands)]


def seq_for(run: Run, step: str, t: Optional[float] = None, needle: str = "", types: tuple = ()) -> int:
    """The seq of the event that best matches (step, time, text): evidence for a lesson. 0 = none."""
    evs = [e for e in run.events if e.get("step") == step] or list(run.events)
    if types:
        evs = [e for e in evs if e.get("type") in types] or evs
    if not evs:
        return 0
    if needle:
        n = needle.lower()[:60]
        hit = [e for e in evs if n and (n in str(e.get("title", "")).lower() or n in str(e.get("detail", "")).lower())]
        if hit:
            evs = hit
    if t is not None:
        return int(min(evs, key=lambda e: abs(float(e.get("t") or 0) - t)).get("seq") or 0)
    return int(evs[-1].get("seq") or 0)


def evidence(run: Run, step: str, t: Optional[float] = None, needle: str = "", types: tuple = ()) -> str:
    return f"{run.id}/{step or '-'}/{seq_for(run, step, t, needle, types)}"
