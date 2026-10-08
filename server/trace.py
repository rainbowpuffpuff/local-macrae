"""Build a run's TraceEvents from what the runner and Harbor leave on disk.

Sources, per the contract:
  (a) the run's state.json and step logs ($AGENT_RUNNER_HOME/runs/<id>/state.json, logs/<step>.log, outputs/)
  (b) each Harbor trial's agent/trajectory.json (ATIF: steps[] with source, message,
      tool_calls[{tool_call_id, function_name, arguments}], observation{results[{source_call_id, content}]})
While a trial is still running, trajectory.json does not exist yet; then we read the live Claude Code stream
(agent/claude-code.txt, stream-json) so the page has something to show. On Modal that file only arrives at the end,
so the agent image's `claude` wrapper also posts the stream to the server while it runs (live.py, <run>/live/).
All three produce the same keys for the same thing (tool calls by tool_use id, assistant text by session + text
hash), so an event seen live is not repeated when the trajectory appears.

`build(state)` returns candidate events, each with one or more stable `keys`. The EventStore (events.py) appends the
ones it hasn't seen and gives them a `seq`, so polling with ?after=<seq> never skips or repeats an event.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from . import catalog
from . import classify as C
from . import config

LOG_LINE_RE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] (.*)$")
TERMINAL_STEP = {"ok", "failed", "skipped", "cancelled"}


@dataclass
class Cand:
    keys: list[str]
    t: float
    step: str
    type: str
    title: str
    detail: str = ""
    citation: Optional[dict] = None
    last: bool = False  # sort after everything else in its batch (the run's final event)
    cost: Optional[dict] = None  # {"usd", "tokens": {"in", "out", "cache"}} (v2, optional)
    elapsed: Optional[float] = None  # seconds since the run (its planning) began (v2, optional)
    extra: Optional[dict] = None  # more optional fields, e.g. {"plan": {...}} on the planner's decision

    def event(self) -> dict:
        ev = {"t": round(float(self.t), 3), "step": self.step, "type": self.type, "title": C.one_line(self.title),
              "detail": C.clip(self.detail), "citation": self.citation}
        if self.cost is not None:
            ev["cost"] = self.cost
        if self.elapsed is not None:
            ev["elapsed_s"] = round(max(0.0, self.elapsed), 1)
        if self.extra:
            ev.update(self.extra)
        return ev


def _h(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def sanitize(s: str) -> str:
    """Same as agent_runner.flows._sanitize: how step keys become file and job names."""
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


# ── file cache: parse each file once per (mtime, size) ──────────────────────

_cache: "OrderedDict[tuple, Any]" = OrderedDict()
_cache_lock = threading.Lock()


def cached(path: Path, parse: Callable[[Path], Any]) -> Any:
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), parse.__name__, st.st_mtime_ns, st.st_size)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    val = parse(path)
    with _cache_lock:
        _cache[key] = val
        while len(_cache) > 256:
            _cache.popitem(last=False)
    return val


def _load_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(errors="replace"))
    except (OSError, ValueError):
        return None


def _read_lines(p: Path) -> list[str]:
    try:
        return p.read_text(errors="replace").splitlines()
    except OSError:
        return []


# ── a normalized agent transcript (from ATIF or from the live stream) ───────


@dataclass
class Call:
    id: str
    name: str
    args: Any
    output: Optional[str] = None
    t: Optional[float] = None
    seconds: Optional[float] = None


@dataclass
class Msg:
    kind: str  # "text" | "call" | "instruction"
    t: Optional[float]
    text: str = ""
    call: Optional[Call] = None
    usage: Optional[dict] = None  # token usage of the API message this block starts (first block only)
    model: str = ""


@dataclass
class Transcript:
    msgs: list[Msg] = field(default_factory=list)
    session: str = ""  # Claude Code session id: the same in the live stream, claude-code.txt and the trajectory


def content_text(c: Any) -> str:
    """ATIF message / observation content: a string, or a list of parts ({type: text, text}), or None."""
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(content_text(x) for x in c if x is not None).strip()
    if isinstance(c, dict):
        if "text" in c:
            return str(c.get("text") or "")
        if "content" in c:
            return content_text(c["content"])
        return ""
    return str(c)


def parse_atif(path: Path) -> Optional[Transcript]:
    data = _load_json(path)
    if not isinstance(data, dict):
        return None
    steps = [s for s in data.get("steps") or [] if isinstance(s, dict)]
    tr = Transcript(session=str(data.get("session_id") or ""))
    default_model = str((data.get("agent") or {}).get("model_name") or "") if isinstance(data.get("agent"), dict) \
        else ""
    times = [ts(s.get("timestamp")) for s in steps]
    seen_instruction = False
    for i, s in enumerate(steps):
        t = times[i]
        nxt = next((x for x in times[i + 1:] if x is not None), None)
        src = str(s.get("source") or "")
        msg = content_text(s.get("message"))
        if src == "user":
            if not seen_instruction and msg.strip():
                tr.msgs.append(Msg("instruction", t, msg))
                seen_instruction = True
            continue
        if src != "agent":
            continue
        first = len(tr.msgs)
        if msg.strip():
            tr.msgs.append(Msg("text", t, msg))
        results = [r for r in ((s.get("observation") or {}).get("results") or []) if isinstance(r, dict)]
        by_id = {str(r.get("source_call_id")): content_text(r.get("content")) for r in results
                 if r.get("source_call_id") is not None}
        calls = [c for c in s.get("tool_calls") or [] if isinstance(c, dict)]
        for j, c in enumerate(calls):
            cid = str(c.get("tool_call_id") or "")
            out = by_id.get(cid)
            if out is None and len(results) == len(calls):
                out = content_text(results[j].get("content"))
            if out is None:
                out = ""  # the trajectory is final: a call without an observation produced no output
            secs = (nxt - t) if (t is not None and nxt is not None and nxt >= t) else None
            tr.msgs.append(Msg("call", t, call=Call(cid, str(c.get("function_name") or ""), c.get("arguments"),
                                                   out, t, secs)))
        if len(tr.msgs) > first and isinstance(s.get("metrics"), dict):
            tr.msgs[first].usage = _atif_usage(s["metrics"])
            tr.msgs[first].model = str(s.get("model_name") or default_model)
    return tr


def _atif_usage(m: dict) -> dict:
    """ATIF step metrics (prompt_tokens = input + cache read + cache write) → an Anthropic-style usage dict."""
    extra = m.get("extra") if isinstance(m.get("extra"), dict) else {}
    prompt, read = int(m.get("prompt_tokens") or 0), int(m.get("cached_tokens") or 0)
    write = int(extra.get("cache_creation_input_tokens") or 0)
    return {"input_tokens": max(0, prompt - read - write), "output_tokens": int(m.get("completion_tokens") or 0),
            "cache_read_input_tokens": read, "cache_creation_input_tokens": write}


def parse_atif_meta(path: Path) -> Optional[dict]:
    """{"session", "model", "tokens", "usd"} from a trajectory's root (final_metrics); usd None if only estimated."""
    from . import costs
    data = _load_json(path)
    if not isinstance(data, dict):
        return None
    fm = data.get("final_metrics") if isinstance(data.get("final_metrics"), dict) else {}
    extra = fm.get("extra") if isinstance(fm.get("extra"), dict) else {}
    prompt, read = int(fm.get("total_prompt_tokens") or 0), int(fm.get("total_cached_tokens") or 0)
    write = int(extra.get("total_cache_creation_input_tokens") or 0)
    tokens = costs.norm_usage({"input_tokens": max(0, prompt - read - write),
                               "output_tokens": int(fm.get("total_completion_tokens") or 0),
                               "cache_read_input_tokens": read, "cache_creation_input_tokens": write})
    usd = fm.get("total_cost_usd")
    if extra.get("cost_source") == "litellm_estimate" or not isinstance(usd, (int, float)):
        usd = None
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    return {"session": str(data.get("session_id") or ""), "model": str(agent.get("model_name") or ""),
            "tokens": tokens if sum(tokens.values()) else None, "usd": usd}


def parse_stream(path: Path) -> Optional[Transcript]:
    """Claude Code `--output-format stream-json` lines (agent/claude-code.txt). Non-JSON lines are skipped."""
    lines = _read_lines(path)
    if not lines:
        return None
    items = []
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            items.append((ts(d.get("timestamp")), d))
    return stream_transcript(items)


def stream_transcript(items: list[tuple[Optional[float], dict]]) -> Transcript:
    """stream-json objects with their times (from claude-code.txt, or posted live by the wrapper)."""
    tr = Transcript()
    calls: dict[str, Call] = {}
    seen_msgs: set[str] = set()
    first_user = True
    for t, d in items:
        typ = d.get("type")
        if not tr.session and d.get("session_id"):
            tr.session = str(d["session_id"])
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        content = msg.get("content")
        if t is None:
            t = ts(d.get("timestamp"))
        if typ == "assistant" and isinstance(content, list):
            mid = str(msg.get("id") or "")
            first_of_msg = bool(mid) and mid not in seen_msgs
            n0 = len(tr.msgs)
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and str(b.get("text") or "").strip():
                    tr.msgs.append(Msg("text", t, str(b["text"])))
                elif b.get("type") == "tool_use":
                    c = Call(str(b.get("id") or ""), str(b.get("name") or ""), b.get("input"), None, t)
                    if c.id:
                        calls[c.id] = c
                    tr.msgs.append(Msg("call", t, call=c))
            if first_of_msg and len(tr.msgs) > n0 and isinstance(msg.get("usage"), dict):
                seen_msgs.add(mid)
                tr.msgs[n0].usage = msg["usage"]
                tr.msgs[n0].model = str(msg.get("model") or "")
        elif typ == "user":
            if isinstance(content, str) and first_user:
                tr.msgs.insert(0, Msg("instruction", t, content))
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        c = calls.get(str(b.get("tool_use_id") or ""))
                        if c is not None:
                            c.output = content_text(b.get("content"))
                            if c.t is not None and t is not None and t >= c.t and c.seconds is None:
                                c.seconds = t - c.t
            first_user = False
    return tr


# ── transcript → candidates ─────────────────────────────────────────────────


# ── capabilities (v3): the agent saying what it lacks, and using what it has ──

GAP_RE = re.compile(r"CAPABILITY_GAP(?:\[(tool|image|writing)\])?:\s*`?([A-Za-z][A-Za-z0-9_ -]{1,60}?)`?\s*:\s*(.+)")
USE_LINE_RE = re.compile(r"CAPABILITY_USE:\s*([a-z][a-z0-9-]{1,47})")
CAP_PATH_RE = re.compile(r"/app/capabilities/([a-z][a-z0-9-]{1,47})/")


def gap_name(raw: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", raw.strip().lower().replace("_", "-")).strip("-")
    return re.sub(r"-{2,}", "-", s)[:48].strip("-")


def find_gaps(text: str) -> list[tuple[str, str, str]]:
    """(name, kind or "", why) for each CAPABILITY_GAP line in the text."""
    out, seen = [], set()
    for ln in (text or "").splitlines():
        m = GAP_RE.search(ln)
        if not m:
            continue
        name, why = gap_name(m.group(2)), m.group(3).strip().strip('"\'`').strip()
        if len(name) < 2 or name in seen or name in ("short-name", "name") or why.startswith("<"):
            continue  # the protocol line itself, echoed back, is not a gap
        seen.add(name)
        out.append((name, m.group(1) or "", why[:500]))
    return out


def find_uses(command: str, output: str, mounted: dict[str, Any]) -> list[str]:
    names = [n for n in CAP_PATH_RE.findall(command or "")] + USE_LINE_RE.findall(output or "")
    return [n for n in dict.fromkeys(names) if n in mounted]


def capability_cands(text_parts: list[str], step: str, t: float, mounted: dict[str, Any],
                     command: str = "", output: str = "") -> list[Cand]:
    out = []
    for part in text_parts:
        for name, kind, why in find_gaps(part):
            out.append(Cand([f"{step}|gap|{name}"], t, step, "gap", f"Missing capability: {name}", why,
                            extra={"capability": {"name": name, "kind": kind, "why": why}}))
    for name in find_uses(command, output, mounted):
        ver = (mounted.get(name) or {}).get("version")
        out.append(Cand([f"{step}|use|{name}"], t, step, "use",
                        f"Used capability {name}" + (f" v{ver}" if ver else ""), command,
                        extra={"capability": {"name": name, "version": ver}}))
    return out


def mounted_capabilities(run_dir: Path) -> dict[str, Any]:
    """name → {name, version, …} for what this run was given (<run>/capabilities.json)."""
    info = cached(run_dir / "capabilities.json", _load_json)
    if not isinstance(info, dict) or info.get("mode") == "dusk":
        return {}
    return {str(c.get("name")): c for c in info.get("mounted") or [] if isinstance(c, dict) and c.get("name")}


def transcript_events(tr: Transcript, step: str, trial_key: str, final: bool, fallback_t: float,
                      mounted: Optional[dict[str, Any]] = None) -> list[Cand]:
    out: list[Cand] = []
    occ: dict[str, int] = {}
    mounted = mounted or {}

    def nth(k: str) -> str:
        occ[k] = occ.get(k, 0) + 1
        return f"{k}#{occ[k]}"

    last_t = fallback_t
    sess = f"{step}|s|{tr.session}" if tr.session else ""
    for m in tr.msgs:
        t = m.t if m.t is not None else last_t
        last_t = t
        cost = _msg_cost(m)
        if m.kind == "instruction":
            continue  # shown with the "Started Claude Code" event; the trajectory would only add it late
        if m.kind == "text":
            norm = re.sub(r"\s+", " ", m.text).strip()
            n = nth(_h(norm[:500]))
            keys = [f"{trial_key}|text|{n}"] + ([f"{sess}|text|{n}"] if sess else [])
            out.append(Cand(keys, t, step, "think", C.first_sentence(m.text), m.text, cost=cost))
            out += capability_cands([m.text], step, t, mounted)
        elif m.kind == "call" and m.call is not None:
            c = m.call
            n = nth('h' + _h(c.name, c.args))
            keys = [f"{trial_key}|call|{n}"]
            if c.id:
                keys += [f"{trial_key}|callid|{c.id}", f"{step}|tool|{c.id}"]
            elif sess:
                keys.append(f"{sess}|call|{n}")
            output = c.output if c.output is not None else ("" if final else None)
            cl = C.classify_tool(c.name, c.args, output, c.seconds)
            if cl.needs_output and output is None:
                continue  # wait for the output (it decides calc vs status); it'll come on a later poll
            out.append(Cand(keys, t, step, cl.type, cl.title, cl.detail, cl.citation, cost=cost))
            args = c.args if isinstance(c.args, dict) else {}
            if c.name.lower() == "bash":
                cmd = str(args.get("command") or "")
                out += capability_cands([cmd, output or ""], step, t, mounted, cmd, output or "")
            if cl.type == "search" and output:
                for cit in C.citations_in_output(output)[:8]:
                    ck = _h(cit['doi'] or cit['title'], cit['page'])
                    out.append(Cand([f"{trial_key}|cite|{keys[0]}|{ck}"] +
                                    ([f"{step}|cite|{c.id}|{ck}"] if c.id else []),
                                    t, step, "cite", C.cite_title(cit), cit.get("quote") or "", cit))
    return out


def _msg_cost(m: Msg) -> Optional[dict]:
    if not m.usage:
        return None
    from . import costs
    tk = costs.norm_usage(m.usage)
    if not sum(tk.values()):
        return None
    return costs.event_cost(costs.llm_usd(m.model, tk), tk)


def live_step_events(run_dir: Path, key: str, step_final: bool, has_trial_stream: bool,
                     fallback_t: float, mounted: Optional[dict[str, Any]] = None) -> list[Cand]:
    """Events from what the claude wrapper posted for this step (<run>/live/<step>.jsonl), one transcript per
    `claude` invocation. Calls still waiting for their output are emitted without it only when the step is over
    and no trial file will ever bring it."""
    from . import live
    out: list[Cand] = []
    for stream, items in live.streams(run_dir, key).items():
        tr = stream_transcript(items)
        out += transcript_events(tr, key, f"{key}|live|{stream}", step_final and not has_trial_stream, fallback_t,
                                 mounted)
    return out


def trial_dirs(job_dir: Path) -> list[Path]:
    try:
        kids = sorted(p for p in job_dir.iterdir() if p.is_dir())
    except OSError:
        return []
    return [p for p in kids if (p / "config.json").is_file() or (p / "agent").is_dir() or (p / "result.json").is_file()]


def trial_events(trial: Path, step: str, step_final: bool, fallback_t: float,
                 mounted: Optional[dict[str, Any]] = None) -> list[Cand]:
    trial_key = f"{step}|{trial.parent.name}/{trial.name}"
    result = cached(trial / "result.json", _load_json)
    result = result if isinstance(result, dict) else {}
    final = step_final or bool(result.get("finished_at"))
    t0 = ts(result.get("started_at")) or fallback_t
    tr = cached(trial / "agent" / "trajectory.json", parse_atif)
    if tr is None:
        tr = cached(trial / "agent" / "claude-code.txt", parse_stream)
    out = transcript_events(tr, step, trial_key, final, t0, mounted) if tr else []
    ex = result.get("exception_info") or {}
    if isinstance(ex, dict) and ex:
        msg = f"{ex.get('exception_type', '')}: {ex.get('exception_message', '')}".strip(": ")
        out.append(Cand([f"{trial_key}|exception"], ts(result.get("finished_at")) or fallback_t, step, "error",
                        f"Agent run failed: {C.one_line(msg, 60)}", msg + "\n" + str(ex.get("exception_traceback")
                                                                                     or "")[-300:]))
    return out


# ── state.json + step logs ──────────────────────────────────────────────────


def _log_time(hms: tuple[int, int, int], base: float) -> float:
    b = datetime.fromtimestamp(base)
    dt = b.replace(hour=hms[0], minute=hms[1], second=hms[2], microsecond=0)
    if dt.timestamp() < base - 60:
        dt += timedelta(days=1)  # crossed midnight
    return dt.timestamp()


def log_entries(path: Path, base: float) -> list[tuple[int, float, str]]:
    """(line index, time, message) for each entry; continuation lines are folded into the entry before."""
    entries: list[list] = []
    for i, ln in enumerate(cached(path, _read_lines) or []):
        m = LOG_LINE_RE.match(ln)
        if m:
            entries.append([i, _log_time((int(m[1]), int(m[2]), int(m[3])), base), m[4]])
        elif entries:
            entries[-1][2] += "\n" + ln
    return [(e[0], e[1], e[2]) for e in entries]


def _attempt_of(job_name: str) -> str:
    m = re.search(r"-a(\d+)(?:-r\d+)?$", job_name)
    return m.group(1) if m else ""


# A script step running one of this repo's task helpers (`python -m tasks.prepare_methods`): not a calculation;
# what it does shows up in its stderr progress lines (STDERR_SEARCH_RE / STDERR_ABSTRACT_RE below).
REPO_MODULE_RE = re.compile(r"(^|\s)-m\s+tasks\.\w+")
STDERR_SEARCH_RE = re.compile(r"^search(?: \(cli\))?: (['\"])(.*)\1 → (\d+) passages?\s*$")
STDERR_ABSTRACT_RE = re.compile(r"^openalex: abstract for (\S+): (\d+) words\s*$")


def _parse_flow_yaml(p: Path) -> dict[str, str]:
    try:
        import yaml  # agent_runner's own dependency
        spec = yaml.safe_load(p.read_text(errors="replace")) or {}
    except Exception:
        return {}
    return {str(s.get("id")): str(s.get("description") or "") for s in spec.get("steps") or []
            if isinstance(s, dict) and s.get("id")}


def step_description(run_dir: Path, key: str) -> str:
    """The step's `description:` from the flow copy the engine keeps in <run>/flow.yaml ("" if none)."""
    return (cached(run_dir / "flow.yaml", _parse_flow_yaml) or {}).get(key.split("[", 1)[0], "")


def stderr_events(msg: str, key: str, idx: int, t: float) -> list[Cand]:
    """Progress lines a script step printed on stderr (the engine logs them as one `stderr: …` entry)."""
    out: list[Cand] = []
    for j, ln in enumerate(msg[len("stderr: "):].splitlines()):
        ln = ln.strip()
        m = STDERR_SEARCH_RE.match(ln)
        if m:
            n = int(m.group(3))
            out.append(Cand([f"{key}|log|{idx}|{j}"], t, key, "search",
                            f"Searched papers: {n} passage{'s' if n != 1 else ''} for “{C.one_line(m.group(2), 50)}”"))
            continue
        m = STDERR_ABSTRACT_RE.match(ln)
        if m:
            doi = m.group(1)
            out.append(Cand([f"{key}|log|{idx}|{j}"], t, key, "read", f"Read the abstract of {doi} (OpenAlex)",
                            ln, catalog.citation_for_doi(doi)))
    return out


def log_events(run_dir: Path, key: str, kind: str, base: float, started: Optional[float] = None) -> list[Cand]:
    out: list[Cand] = []
    for idx, t, msg in log_entries(run_dir / "logs" / f"{sanitize(key)}.log", base):
        if started and 0 < started - t < 1:
            t = started  # log times are whole seconds; don't sort before the step's (precise) start
        k = [f"{key}|log|{idx}"]
        head = msg.split("\n", 1)[0]
        if head.startswith("stderr: "):
            out += stderr_events(msg, key, idx, t)
        elif head.startswith("$ ") and REPO_MODULE_RE.search(head):
            cmd = re.sub(r"\s+\(cwd [^)]*\)\s*$", "", head[2:])
            desc = step_description(run_dir, key)
            out.append(Cand(k, t, key, "status", C.past_tense(desc) if desc else f"Ran {C.one_line(cmd, 70)}",
                            f"$ {cmd}"))
        elif head.startswith("$ "):
            cmd = re.sub(r"\s+\(cwd [^)]*\)\s*$", "", head[2:])
            cl = C.classify_command(cmd, None)
            if cl.type == "status":
                cl.title = f"Ran {C.one_line(cmd, 70)}"
            elif cl.type == "calc":
                prog = C.program_of(cmd)
                cl.title = f"Ran {C.command_label(cmd)}" + (f" ({prog})" if prog else "")
            out.append(Cand(k, t, key, cl.type, cl.title, f"$ {cmd}"))
        elif head.startswith("harbor ("):
            m = re.match(r"harbor \(([^)]*)\): (.*)", head)
            account, cmd = (m.group(1), m.group(2)) if m else ("", head)
            where = "Modal" if re.search(r"\s(-e|--env|--environment)\s+modal\b", cmd) else "Docker"
            jm = re.search(r"--job-name\s+(\S+)", cmd)
            att = _attempt_of(jm.group(1)) if jm else ""
            agent = re.search(r"\s-a\s+(\S+)", cmd)
            who = "Claude Code" if not agent or agent.group(1) == "claude-code" else agent.group(1)
            bits = [f"on {where}"] + ([f"account {account}"] if account and account != "no account" else []) + (
                [f"attempt {att}"] if att and att != "1" else [])
            im = re.search(r"\s-i\s+(.*?)(?:\s+-p\s|\s+--no-scan|\s+--scan|$)", cmd, re.S)
            out.append(Cand(k, t, key, "status", f"Started {who} " + ", ".join(bits),
                            f"Instruction: {im.group(1)}" if im else ""))
        elif head.startswith("capability image "):
            m = re.match(r"capability image (\S+) v(\S+):", head)
            name, ver = (m.group(1), m.group(2)) if m else ("", "")
            out.append(Cand(k, t, key, "use", f"Sandbox built from image capability {name} v{ver}",
                            "Packages preinstalled in the image instead of installed by the agent.",
                            extra={"capability": {"name": name, "version": ver, "kind": "image"}}))
            if name:
                k.append(f"{key}|use|{name}")
        elif head.startswith("waiting for a free account"):
            out.append(Cand(k, t, key, "status", "Waiting for a free Claude account"))
        elif head.startswith("check exit"):
            m = re.match(r"check exit (-?\d+)", head)
            rc = int(m.group(1)) if m else 1
            text = msg.split(":", 1)[1].strip() if ":" in msg else ""
            if rc == 0:
                out.append(Cand(k, t, key, "status", "Check passed", text))
            else:
                out.append(Cand(k, t, key, "error", f"Check failed (exit {rc})", text))
        elif re.match(r"attempt \d+: ", head) and "passed=False" in head:
            m = re.match(r"attempt (\d+): (\S+) reward=(\S+)", head)
            if m:
                rw = "" if m.group(3) == "None" else f", reward {m.group(3)}"
                out.append(Cand(k, t, key, "status", f"Attempt {m.group(1)} did not pass ({m.group(2)}{rw})"))
        elif "hit its limit" in head:
            out.append(Cand(k, t, key, "status", "Claude account hit its usage limit; moving to another", head))
        elif "token rejected" in head:
            out.append(Cand(k, t, key, "error", "Claude login rejected for this account", head))
        elif head.startswith("until ") and " raised " in head:
            out.append(Cand(k, t, key, "error", "Retry condition failed", head))
    return out


def job_dirs_for(state: dict, key: str, step: dict) -> list[Path]:
    """All job folders of a step: <jobs_dir>/<sanitized key>-a<attempt>[-r<reroute>], in attempt order."""
    found: dict[str, Path] = {}
    pat = re.compile(rf"^{re.escape(sanitize(key))}-a(\d+)(?:-r(\d+))?$")
    roots = []
    if state.get("jobs_dir"):
        roots.append(Path(str(state["jobs_dir"])))
    if step.get("job_dir"):
        roots.append(Path(str(step["job_dir"])).parent)
    for r in roots:
        try:
            for p in r.iterdir():
                if p.is_dir() and pat.match(p.name):
                    found[str(p)] = p
        except OSError:
            continue
    if step.get("job_dir") and Path(str(step["job_dir"])).is_dir():
        found.setdefault(str(step["job_dir"]), Path(str(step["job_dir"])))

    def order(p: Path) -> tuple[int, int]:
        m = pat.match(p.name)
        return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)

    return sorted(found.values(), key=order)


def script_output_events(run_dir: Path, key: str, step: dict, t: float) -> list[Cand]:
    """Citations a script step pulled (e.g. `python -m rag search … --json`), from outputs/<key>-a<n>.txt."""
    out: list[Cand] = []
    for f in sorted((run_dir / "outputs").glob(f"{sanitize(key)}-a*.txt")):
        text = cached(f, lambda p: p.read_text(errors="replace")) or ""
        for cit in C.citations_in_output(text)[:8]:
            out.append(Cand([f"{key}|cite|{f.name}|{_h(cit['doi'] or cit['title'], cit['page'])}"], t, key, "cite",
                            C.cite_title(cit), cit.get("quote") or "", cit))
    return out


def step_events(state: dict, run_dir: Path, key: str, step: dict, base: float,
                by_step: Optional[dict] = None) -> list[Cand]:
    status = str(step.get("status") or "")
    kind = str(step.get("kind") or "")
    out: list[Cand] = []
    started = float(step.get("started") or step.get("queued") or base)
    if step.get("fanout") is not None:
        n = step.get("fanout")
        kids = [s for k, s in (state.get("steps") or {}).items() if k.startswith(key + "[") and isinstance(s, dict)]
        t_kids = [float(s.get("queued") or s.get("started") or 0) for s in kids]
        t_split = min([x for x in t_kids if x] or [float(step.get("started") or 0) or base])
        out.append(Cand([f"{key}|fanout"], t_split, key, "status", f"Split {key} into {n} parallel "
                                                                   f"{'item' if n == 1 else 'items'}"))
    else:
        out += log_events(run_dir, key, kind, base, float(step.get("started") or 0) or None)
        final = status in TERMINAL_STEP
        if kind in ("agent", "task"):
            has_stream = False
            mounted = mounted_capabilities(run_dir)
            for jd in job_dirs_for(state, key, step):
                for tdir in trial_dirs(jd):
                    out += trial_events(tdir, key, final, started, mounted)
                    has_stream = has_stream or (tdir / "agent" / "trajectory.json").is_file() or \
                        (tdir / "agent" / "claude-code.txt").is_file()
            out += live_step_events(run_dir, key, final, has_stream, started, mounted)
        if kind == "run" and final:
            cmd_events = [c for c in out if c.type == "search"]
            if cmd_events:
                out += script_output_events(run_dir, key, step, float(step.get("finished") or started))
    fin = float(step.get("finished") or started)
    if status == "skipped" and not (step.get("started") or step.get("queued") or step.get("finished")):
        # the engine stamps no time on a skipped step; it was skipped when its upstream ended
        fin = max([float(s.get("finished") or 0) for s in (state.get("steps") or {}).values()
                   if isinstance(s, dict)] + [fin])
    sc = (by_step or {}).get(key) or {}
    cost = None
    if sc.get("llm_usd") or sc.get("compute_usd"):
        tk = sc.get("tokens") or {}
        cost = {"usd": round(float(sc.get("llm_usd") or 0) + float(sc.get("compute_usd") or 0), 6),
                "llm_usd": sc.get("llm_usd") or 0.0, "compute_usd": sc.get("compute_usd") or 0.0,
                "tokens": {"in": tk.get("in", 0), "out": tk.get("out", 0), "cache": tk.get("cache", 0)}}
    if status == "ok":
        reward = step.get("reward")
        rw = f" (reward {reward:g})" if isinstance(reward, (int, float)) else ""
        note = f" ({step['note']})" if step.get("note") and not rw else ""
        out.append(Cand([f"{key}|done"], fin, key, "result", f"Finished {key}{rw}{note}",
                        str(step.get("output") or step.get("note") or ""), cost=cost))
    elif status == "failed":
        out.append(Cand([f"{key}|done"], fin, key, "error", f"{key} failed",
                        str(step.get("error") or step.get("note") or ""), cost=cost))
    elif status == "cancelled":
        out.append(Cand([f"{key}|done"], fin, key, "error", f"{key} cancelled", ""))
    elif status == "skipped":
        out.append(Cand([f"{key}|done"], fin, key, "status", f"Skipped {key}",
                        str(step.get("note") or step.get("error") or "")))
    return out


def plan_events(run_dir: Path, plan: dict) -> list[Cand]:
    """The planner's events: "Planning…" while the call runs, then its decision (type "plan") with its cost."""
    from . import planner
    if not plan.get("started"):
        return []
    t0 = float(plan["started"])
    out = [Cand(["plan|start"], t0, "", "status", "Planning the run: stages, hardware and budget",
                f"Asking {plan.get('model') or planner.model_name()} to decide what to run and where.")]
    if plan.get("status") in ("ok", "default") and plan.get("finished"):
        from . import costs
        tk = costs.norm_usage(plan.get("usage"))
        cost = costs.event_cost(float(plan.get("cost_usd") or 0), tk) if sum(tk.values()) else None
        decision = {k: plan.get(k) for k in ("plan", "hardware", "params", "budget_usd", "why", "status", "model")}
        decision["hardware_label"] = costs.hardware_label(str(plan.get("hardware") or ""))
        out.append(Cand(["plan|decision"], float(plan["finished"]), "", "plan", planner.decision_title(plan),
                        planner.decision_detail(plan), cost=cost, extra={"plan": decision}))
    return out


LEDGER_TITLES = {
    "gap": lambda e: f"Missing capability: {e['name']}",
    "create": lambda e: f"Created capability {e['name']}: {len(e.get('files') or [])} files, "
                        f"{e.get('sandbox_tests') or e.get('tests') or 0} tests passed in its sandbox",
    "test": lambda e: (f"Tests passed in a fresh sandbox ({e.get('n_passed')}/{e.get('n_tests')}, no network)"
                       if e.get("passed") else f"Tests failed in a fresh sandbox for {e['name']}"),
    "install": lambda e: f"Installed {e['name']} v{e.get('version')} (sha256 {str(e.get('sha256') or '')[:12]}…)",
    "rejected": lambda e: f"Rejected {e['name']}: {str((e.get('reasons') or [e.get('reason') or ''])[0])[:70]}",
    "use": lambda e: f"Used capability {e['name']}" + (f" v{e['version']}" if e.get("version") else ""),
}


def ledger_events(run_id: str) -> list[Cand]:
    """The capability ledger's entries for this run (create/test/install/rejected from a forge run, a gap found in
    the trace after the run): same keys as the transcript's own gap/use events, so nothing shows twice."""
    try:
        from . import bridges
        caps = bridges._import("evolve.capabilities")
        entries = caps.ledger(run_id=run_id)
    except Exception:
        return []
    out = []
    for e in entries:
        ev = e.get("event")
        if ev not in LEDGER_TITLES:
            continue
        keys = [str(e.get("event_key") or ""), f"ledger|{e.get('key')}"]
        detail = e.get("why") or e.get("reason") or e.get("purpose") or ""
        if ev == "test" and e.get("job"):
            detail = f"Harbor job {e['job']}" + (f", built in {e['setup_s']:.0f} s" if e.get("setup_s") else "") + \
                (f", tests took {e['seconds']:.1f} s" if isinstance(e.get("seconds"), (int, float)) else "")
        if ev == "install":
            detail = f"{e.get('purpose') or ''}\nsha256 {e.get('sha256') or ''}".strip()
        if ev == "rejected" and e.get("reasons"):
            detail = "\n".join(f"- {r}" for r in e["reasons"])
        cap = {k: e.get(k) for k in ("name", "version", "kind", "sha256") if e.get(k) is not None}
        out.append(Cand([k for k in keys if k], float(e.get("t") or 0), str(e.get("step") or ""), ev,
                        LEDGER_TITLES[ev](e), str(detail), extra={"capability": cap}))
    return out


def build(state: dict, title: str = "", costs_obj: Optional[dict] = None) -> list[Cand]:
    """All candidate events for a run, in a stable order."""
    from . import costs, planner
    run_dir = Path(str(state.get("_dir") or config.runs_dir() / str(state.get("id"))))
    plan = planner.load(run_dir)
    t_zero = float(plan.get("started") or 0) or float(state.get("started") or 0) or None
    if state.get("_synthetic") and state.get("status") == "starting":
        # the engine hasn't written state.json yet (it starts once the plan is made); show the planning
        out = plan_events(run_dir, plan)
        for c in out:
            c.elapsed = (c.t - t_zero) if t_zero else None
        return out
    base = float(state.get("started") or 0) or 0.0
    vars_ = state.get("vars") if isinstance(state.get("vars"), dict) else {}
    inputs = "\n".join(f"{k} = {v if isinstance(v, str) else json.dumps(v, default=str)}" for k, v in vars_.items())
    if costs_obj is None:
        try:
            costs_obj = costs.run_costs(state, plan)
        except Exception:  # costs are extra; never lose the trace over them
            costs_obj = {}
    by_step = costs_obj.get("by_step") or {}
    out: list[Cand] = plan_events(run_dir, plan)
    out.append(Cand(["run|start"], base, "", "status", f"Started {title or state.get('name') or 'run'}", inputs))
    steps = state.get("steps") or {}
    order = list(state.get("order") or []) + [k for k in steps if k not in (state.get("order") or [])]
    for key in order:
        if key in steps and isinstance(steps[key], dict):
            out += step_events(state, run_dir, key, steps[key], base, by_step)
    out += ledger_events(str(state.get("id") or run_dir.name))
    status = str(state.get("status") or "")
    fin = float(state.get("finished") or 0) or max([c.t for c in out] + [base])
    sts = [str(s.get("status")) for s in steps.values() if isinstance(s, dict) and s.get("fanout") is None]
    counts = ", ".join(f"{sts.count(k)} {k}" for k in ("ok", "failed", "skipped", "cancelled") if sts.count(k))
    run_cost = None
    if costs_obj.get("total_usd"):
        tk = costs_obj.get("tokens") or {}
        run_cost = {"usd": costs_obj["total_usd"], "llm_usd": costs_obj.get("llm_usd", 0.0),
                    "compute_usd": costs_obj.get("compute_usd", 0.0),
                    "tokens": {"in": tk.get("in", 0), "out": tk.get("out", 0), "cache": tk.get("cache", 0)}}
    spent = f"\nCost ${costs_obj['total_usd']:.2f} (LLM ${costs_obj.get('llm_usd', 0):.2f}, compute " \
            f"${costs_obj.get('compute_usd', 0):.2f}), {costs_obj.get('wall_s', 0):.0f} s" if run_cost else ""
    if status == "ok":
        out.append(Cand(["run|end"], fin, "", "result", "Run finished", counts + spent, last=True, cost=run_cost))
    elif status in ("failed", "crashed"):
        err = str(state.get("error") or "")
        out.append(Cand(["run|end"], fin, "", "error", "Run failed" if status == "failed" else "Run stopped unexpectedly",
                        (counts + spent + ("\n" + err if err else "")).strip(), last=True, cost=run_cost))
    elif status == "cancelled":
        out.append(Cand(["run|end"], fin, "", "error", "Run cancelled", counts + spent, last=True, cost=run_cost))
    for c in out:
        c.elapsed = (c.t - t_zero) if t_zero else None
    return out
