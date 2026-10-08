"""distill(run) → lessons: read one finished run's trace and turn it into lessons for the next run of its task.

With ANTHROPIC_API_KEY: one Claude call reads a digest of the trace (steps, attempts, check output, TraceEvents
with ids, the agent's tool calls with errors and timings, costs, the scripts it wrote, the task's current lessons)
and answers JSON (lessons with evidence ids, which existing lessons it confirms, which scripts are reusable).
Without a key, or if the call fails: the rule-based extractor (rules.py). Either way:
  - lessons are merged into lessons.jsonl (a repeat reinforces the existing lesson instead of duplicating it);
  - the lessons this run had in its instruction get their outcome recorded (used / used_ok; bad ones retire);
  - scripts from passing attempts go to tools/<task_id>/;
  - the run is registered in distilled.json, so calling distill again for it is cheap and changes nothing;
  - what was read and answered (and the call's token cost) is kept in distill/<run_id>.json.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import config, llm, notes, rules, store, tools
from .runs import Run, Step, load, list_run_dirs
from .rules import err_line, short_cmd

log = logging.getLogger("macrae.evolve")

MAX_DIGEST = 60_000
MAX_LLM_LESSONS = 6

SYSTEM = """You improve an AI agent that runs scientific computing tasks for a computational chemistry group \
(Claude Code in a Modal sandbox, driven by a flow with checks and retries). You read the trace of ONE finished run \
of a task and write lessons that make the NEXT run of the same task more likely to pass its check, faster and \
cheaper.

Rules for lessons:
- Each lesson is one or two plain sentences, written to a fresh agent that has not seen this run: imperative, \
specific (exact commands, file names, parameter values, package names, timings and sizes taken from the trace).
- Only what the trace shows. No generic advice ("be careful", "read the instructions"), no restating the task.
- Nothing about infrastructure the agent cannot control (logins, API keys, Modal authentication, rate limits).
- kind: "avoid" = a mistake that cost time or failed a check; "do" = a practice or fix that worked; \
"setting" = hardware, time limits, system sizes, iterations or other parameters to choose up front; \
"tool" = a script or command worth reusing as is.
- evidence: ids of the events that show it, copied exactly from the trace (format run_id/step/seq).
- confidence: 0..1, how sure you are the lesson holds for the next run.
- If an existing lesson (listed with its id) is confirmed by this run, put its id in "reinforces" instead of \
writing it again. Write at most 6 new lessons; zero is fine if the run teaches nothing new.
- For each script under "Scripts written in passing attempts", say in one line what it does and whether a future \
run of this task can reuse it as is (reusable)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "lessons": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "lesson": {"type": "string"},
                "kind": {"type": "string", "enum": list(store.KINDS)},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number"},
            },
            "required": ["lesson", "kind", "evidence", "confidence"],
            "additionalProperties": False,
        }},
        "reinforces": {"type": "array", "items": {"type": "string"}},
        "tools": {"type": "array", "items": {
            "type": "object",
            "properties": {"file": {"type": "string"}, "description": {"type": "string"},
                           "reusable": {"type": "boolean"}},
            "required": ["file", "description", "reusable"],
            "additionalProperties": False,
        }},
    },
    "required": ["summary", "lessons", "reinforces", "tools"],
    "additionalProperties": False,
}


# ── the digest Claude reads ─────────────────────────────────────────────────


def _clip(s: Any, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def _mmss(t: Optional[float], t0: float) -> str:
    if t is None:
        return "  ?  "
    d = max(0, int(t - t0))
    return f"{d // 60:02d}:{d % 60:02d}"


def event_id(run: Run, ev: dict) -> str:
    return f"{run.id}/{ev.get('step') or '-'}/{ev.get('seq')}"


def _step_lines(run: Run, s: Step) -> list[str]:
    secs = f", {s.seconds / 60:.1f} min" if s.seconds else ""
    rw = f", reward {s.reward}" if s.reward is not None else ""
    env = f", {s.environment}" if s.environment else ""
    out = [f"- {s.key} ({s.kind}{env}): {s.status}, attempt {s.attempt}{secs}{rw}"
           + (f" — {s.description}" if s.description else "")]
    if s.error:
        out.append(f"  error: {_clip(s.error, 400)}")
    for a in s.attempts:
        out.append(f"  attempt {a['n']}: {a['status']}, reward {a['reward']}, passed {a['passed']}")
    for c in s.checks:
        out.append(f"  check after attempt {c.attempt} (exit {c.rc}):")
        out += ["    " + ln for ln in _clip(c.text, 900).splitlines()[:14]]
    return out


def _transcript(run: Run, s: Step, budget: int) -> list[str]:
    out = []
    for tr in s.trials:
        t0 = tr.started or (tr.calls[0].t if tr.calls and tr.calls[0].t else run.started)
        cost = f", ${tr.cost_usd:.2f}" if tr.cost_usd is not None else ""
        secs = f", {tr.seconds / 60:.1f} min" if tr.seconds else ""
        out.append(f"### {s.key} attempt {tr.attempt} ({tr.dir.name}, {tr.model or 'model ?'}{secs}{cost}, "
                   f"reward {tr.reward}, {len(tr.calls)} tool calls)")
        if tr.exception:
            out.append(f"exception: {_clip(tr.exception, 300)}")
        items: list[tuple[float, str]] = []
        for t, txt in tr.texts:
            items.append((t or 0, f"[{_mmss(t, t0)}] says: {_clip(' '.join(txt.split()), 220)}"))
        for c in tr.calls:
            secs = f" ({c.seconds:.0f} s)" if c.seconds and c.seconds >= 1 else ""
            if c.command:
                head = f"[{_mmss(c.t, t0)}] Bash{secs}: {short_cmd(c.command, 160)}"
                if c.description:
                    head += f"  # {_clip(c.description, 80)}"
                if c.failed:
                    tail = "\n".join((c.output or "").strip().splitlines()[-6:])
                    head += f"\n      → FAILED: {_clip(tail, 500)}"
                elif c.output and c.seconds and c.seconds >= 20:
                    head += f"\n      → {_clip(' '.join(c.output.split()[-40:]), 240)}"
            else:
                arg = c.path or _clip(json.dumps(c.args, ensure_ascii=False, default=str), 120)
                head = f"[{_mmss(c.t, t0)}] {c.name}{secs}: {arg}"
                if c.failed:
                    head += f"\n      → FAILED: {_clip(err_line(c.output), 240)}"
            items.append((c.t or 0, head))
        items.sort(key=lambda x: x[0])  # stable: texts before calls at equal times
        lines = [x[1] for x in items]
        if sum(len(x) for x in lines) > budget:  # keep the start, the failures and the end
            keep = set(range(min(15, len(lines)))) | set(range(max(0, len(lines) - 25), len(lines)))
            keep |= {i for i, x in enumerate(lines) if "FAILED" in x}
            lines = [x if i in keep else "" for i, x in enumerate(lines)]
            lines = [x for i, x in enumerate(lines) if x or (i > 0 and lines[i - 1])]
            lines = [x or "[…]" for x in lines]
        out += lines
    return out


def digest(run: Run, existing: list[dict], scripts: list[dict]) -> str:
    c = run.costs
    L = [f"# Run {run.id} of task {run.task_id} (“{run.title}”)",
         f"status: {run.status} | wall time: {(run.wall_s or 0) / 60:.1f} min | hardware: {run.hardware or 'default'}"
         f" | cost: LLM ${c.get('llm_usd', 0):.2f} + compute ${c.get('compute_usd', 0):.2f} = "
         f"${c.get('total_usd', 0):.2f} | tokens {json.dumps(c.get('tokens') or {})}",
         "inputs: " + (", ".join(f"{k}={_clip(v, 80)}" for k, v in run.inputs.items()) or "-")]
    if run.lessons_text.strip():
        L += ["", "## Lessons this run was given (from earlier runs)", _clip(run.lessons_text.strip(), 3000)]
    L += ["", "## Steps"]
    for s in run.steps:
        L += _step_lines(run, s)
    L += ["", "## Trace events (id = run_id/step/seq, as shown on the page)"]
    evs = run.events
    if len(evs) > 220:  # keep errors, calcs and results; thin out the rest
        keep = [e for e in evs if e.get("type") in ("error", "calc", "result", "write")]
        rest = [e for e in evs if e not in keep]
        evs = sorted(keep[:180] + rest[: max(0, 220 - len(keep[:180]))], key=lambda e: e.get("seq") or 0)
    for e in evs:
        det = f" — {_clip(' '.join(str(e.get('detail') or '').split()), 160)}" if e.get("type") == "error" else ""
        L.append(f"{event_id(run, e)} [{_mmss(e.get('t'), run.started)}] {e.get('type')}: "
                 f"{_clip(e.get('title'), 110)}{det}")
    budget = max(8000, (MAX_DIGEST - sum(len(x) for x in L)) // max(1, len(run.agent_steps)) - 4000)
    for s in run.agent_steps:
        L += ["", f"## Agent transcript: {s.key}"] + _transcript(run, s, budget)
    own_notes, _, _ = notes.text(run)
    if own_notes.strip():
        L += ["", "## The agent's notes to itself (NOTES_TO_SELF.md, written during the run)",
              _clip(own_notes.strip(), 4000)]
    if scripts:
        L += ["", "## Scripts written in passing attempts"]
        for sc in scripts:
            try:
                body = sc["path"].read_text(errors="replace")
            except OSError:
                continue
            head = "\n".join(body.splitlines()[:40])
            L += [f"### {sc['name']} ({sc['step']}, {body.count(chr(10)) + 1} lines)"
                  + (f" run as: {short_cmd(sc['command'], 120)}" if sc.get("command") else ""), "```", head, "```"]
    if existing:
        L += ["", "## Existing lessons for this task"]
        for x in existing:
            L.append(f"- [{x['id']}] ({x.get('kind')}, seen in {x.get('hits', 1)} run(s), used by "
                     f"{x.get('used', 0)} later run(s), {x.get('used_ok', 0)} passed) {x['lesson']}")
    text = "\n".join(L)
    return text if len(text) <= MAX_DIGEST * 1.5 else text[: int(MAX_DIGEST * 1.5)] + "\n[digest truncated]"


# ── turning Claude's answer into candidates ─────────────────────────────────


def _from_llm(run: Run, data: dict, model: str) -> list[dict]:
    valid = {event_id(run, e) for e in run.events}
    fallback_ev = event_id(run, run.events[-1]) if run.events else f"{run.id}/-/0"
    out = []
    for x in (data.get("lessons") or [])[:MAX_LLM_LESSONS]:
        if not isinstance(x, dict):
            continue
        text = " ".join(str(x.get("lesson") or "").split())
        if len(text) < 12:
            continue
        ev = [e for e in (x.get("evidence") or []) if isinstance(e, str) and e in valid] or [fallback_ev]
        try:
            conf = min(1.0, max(0.0, float(x.get("confidence"))))
        except (TypeError, ValueError):
            conf = 0.7
        out.append({"task_id": run.task_id, "run_id": run.id, "lesson": _clip(text, 500),
                    "kind": x.get("kind") if x.get("kind") in store.KINDS else "do", "evidence": ev,
                    "confidence": conf, "key": "", "source": "claude", "model": model})
    return out


def history(task_id: str, exclude: str = "") -> list[dict]:
    from .metrics import task_rows
    return [r for r in task_rows(task_id) if r.get("run_id") != exclude]


# ── distill ─────────────────────────────────────────────────────────────────

_busy: set[str] = set()
_busy_lock = threading.Lock()


def distill(run_dir: str | Path, force: bool = False, use_llm: Optional[bool] = None) -> list[dict]:
    """Lessons learned from one finished run (new or reinforced), [] if the run isn't finished. Idempotent:
    a run already distilled returns its lessons again without another Claude call (force=True redoes it)."""
    run = load(run_dir)
    if not run.done:
        return []
    with _busy_lock:
        if run.id in _busy:
            return []
        _busy.add(run.id)
    try:
        return _distill(run, force, use_llm)
    finally:
        with _busy_lock:
            _busy.discard(run.id)


def _registered_lessons(entry: dict) -> list[dict]:
    ids = set(entry.get("lessons") or [])
    return [x for x in store.load() if x["id"] in ids]


def _distill(run: Run, force: bool, use_llm: Optional[bool]) -> list[dict]:
    with store.locked():
        entry = store.registry().get(run.id)
    if entry and not force:
        return _registered_lessons(entry)
    t0 = time.time()
    with store.locked():
        current = store.active(store.load(), run.task_id)[:20]
    scripts = tools.candidates(run)
    hist = history(run.task_id, exclude=run.id)
    record: dict[str, Any] = {"run_id": run.id, "task_id": run.task_id, "status": run.status, "at": t0}
    cands: list[dict] = []
    reinforce: list[str] = []
    tool_notes: dict[str, dict] = {}
    source = "rules"
    want_llm = config.llm_enabled() if use_llm is None else (use_llm and config.llm_enabled())
    if want_llm:
        text = digest(run, current, scripts)
        record["digest"] = text
        try:
            data, usage = llm.call_json(SYSTEM, text, SCHEMA)
            record.update(answer=data, usage=usage)
            cands = _from_llm(run, data, usage.get("model", ""))
            known = {x["id"] for x in current}
            reinforce = [i for i in data.get("reinforces") or [] if i in known]
            tool_notes = {str(t.get("file")): t for t in data.get("tools") or [] if isinstance(t, dict)}
            source = "claude"
            cands += [c for c in rules.baseline_rule(run, hist)]  # deterministic planner reference
        except llm.LLMError as e:
            log.warning("distill %s: Claude call failed (%s); using the rule-based extractor", run.id, e)
            record["llm_error"] = str(e)
    if source == "rules":
        cands = rules.extract(run, hist)
    cands += notes.lessons(run)  # the agent's own NOTES_TO_SELF.md, in both modes
    record["candidates"] = cands
    now = time.time()
    with store.locked():
        entry = store.registry().get(run.id)
        if entry and not force:  # another process finished first
            return _registered_lessons(entry)
        lessons = store.load()
        first_time = entry is None
        retired = store.record_use(lessons, run.lessons_used, run.ok) if first_time else []
        touched: dict[str, dict] = {}
        for c in cands:
            lesson, _ = store.merge(lessons, c, now)
            touched[lesson["id"]] = lesson
        by_id = {x["id"]: x for x in lessons}
        end_ev = event_id(run, run.events[-1]) if run.events else f"{run.id}/-/0"
        for i in reinforce:
            x = by_id.get(i)
            if x is None or i in touched:
                continue
            store.merge(lessons, {"task_id": x["task_id"], "lesson": x["lesson"], "run_id": run.id,
                                  "evidence": [end_ev], "confidence": x.get("confidence")}, now)
            touched[i] = x
        store.save(lessons)
        harvested = tools.harvest(run, tool_notes)
        store.register(run.id, {"at": now, "source": source, "status": run.status, "task_id": run.task_id,
                                "lessons": list(touched), "retired": retired,
                                "tools": [t["name"] for t in harvested],
                                "usage": record.get("usage"), "llm_error": record.get("llm_error"),
                                "seconds": round(now - t0, 2)})
    record.update(lessons=list(touched), tools=[t["name"] for t in harvested], retired=retired, source=source)
    try:
        store.write_distill_record(run.id, record)
    except OSError:
        pass
    log.info("distilled %s (%s): %d lesson(s), %d tool(s)", run.id, source, len(touched), len(harvested))
    return [dict(x) for x in touched.values()]


def pending() -> list[str]:
    """Finished runs that haven't been distilled yet, oldest first."""
    reg = store.registry()
    out = []
    for d in list_run_dirs():
        if d.name in reg:
            continue
        st = store.read_json(d / "state.json", {})
        if isinstance(st, dict) and st.get("status") in ("ok", "failed", "cancelled", "crashed"):
            out.append((float(st.get("started") or 0), d.name))
    return [r for _, r in sorted(out)]


def distill_pending(use_llm: Optional[bool] = None) -> dict[str, int]:
    out = {}
    for rid in pending():
        try:
            out[rid] = len(distill(rid, use_llm=use_llm))
        except Exception as e:  # one bad run folder must not stop the rest
            log.warning("distill %s failed: %s", rid, e)
            out[rid] = -1
    return out


def distill_in_background(run_id: str) -> threading.Thread:
    """For the server's run-end hook: distill in a daemon thread, never raising into the caller."""
    def work() -> None:
        try:
            distill(run_id)
        except Exception as e:
            log.warning("background distill of %s failed: %s", run_id, e)

    th = threading.Thread(target=work, name=f"evolve-distill-{run_id}", daemon=True)
    th.start()
    return th
