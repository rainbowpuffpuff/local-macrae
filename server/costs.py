"""What a run costs and where its time goes: LLM tokens × prices, Modal sandbox seconds × rates, time per phase.

`run_costs(state)` → the "costs" object of GET /api/runs/{id} (CONTRACT v2):
    {"llm_usd", "compute_usd", "total_usd", "tokens": {"in", "out", "cache_read", "cache_write", "cache"},
     "by_step": {step: {"llm_usd", "compute_usd", "seconds", "model", "hardware", "tokens"}},
     "wall_s", "phases": {"plan", "setup", "work", "check"}, "hardware", "estimated", "updated"}
It is recomputed from the files on every call, so it grows while the run goes.

LLM cost per Claude Code session, best source first: Claude Code's own total (`total_cost_usd` on the stream's
final `result` line, or Harbor's result.json `agent_result.cost_usd`), else tokens × PRICES. The planner's call
is its own step, "plan". Compute = sandbox seconds × the hardware's per-second rate (COMPUTE_RATES); only steps
that ran on Modal are charged; script steps run on the backend and cost nothing extra here.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

# ── prices ──────────────────────────────────────────────────────────────────
# USD per million tokens: (input, output, cache read, cache write 5 min). A 1-hour cache write is 2× input.
# Source: Anthropic list prices (platform.claude.com pricing, as of 2026-10-06). First-party API rates; a Claude
# subscription login (AGENT_RUNNER_TOKEN_*) is not billed per token, so for those runs this is the API-equivalent.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-mythos-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-fable-5": (10.0, 50.0, 1.0, 12.5),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-opus-5": (5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-8": (5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-7": (5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-6": (5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-5": (5.0, 25.0, 0.50, 6.25),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-sonnet-5": (2.0, 10.0, 0.20, 2.5),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75),
    "claude-sonnet-4-5": (3.0, 15.0, 0.30, 3.75),
    "claude-sonnet-4": (3.0, 15.0, 0.30, 3.75),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),
}
FAMILY_DEFAULT = {"fable": "claude-fable-5-1", "mythos": "claude-mythos-5-1", "opus": "claude-opus-5-5",
                  "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}
UNKNOWN_MODEL = "claude-sonnet-5-5"  # Harbor's claude-code agent without -m uses Claude Code's default model

# ── compute ─────────────────────────────────────────────────────────────────
# Modal list prices in USD per second (modal.com/pricing as known on 2026-10-08: CPU per physical core, memory per
# GiB, GPU per device). Sandboxes are billed by the same meters. Override any of them without a deploy:
# MACRAE_COMPUTE_RATES='{"cpu_core_s": 0.0000131, "gpu_s": {"a10g": 0.000306}}'.
COMPUTE_RATES: dict[str, Any] = {
    "cpu_core_s": 0.0000131,
    "mem_gib_s": 0.00000222,
    "gpu_s": {"t4": 0.000164, "l4": 0.000222, "a10g": 0.000306, "l40s": 0.000542, "a100": 0.000583,
              "a100-80gb": 0.000694, "h100": 0.001097, "h200": 0.001261, "b200": 0.001736},
}

# What the planner may choose. cores = Modal physical cores; mem in GiB.
HARDWARE: dict[str, dict[str, Any]] = {
    "cpu-2": {"cores": 2, "mem_gib": 4, "gpu": "", "label": "2 CPU cores"},
    "cpu-4": {"cores": 4, "mem_gib": 8, "gpu": "", "label": "4 CPU cores"},
    "cpu-8": {"cores": 8, "mem_gib": 16, "gpu": "", "label": "8 CPU cores"},
    "cpu-16": {"cores": 16, "mem_gib": 32, "gpu": "", "label": "16 CPU cores"},
    "gpu-t4": {"cores": 4, "mem_gib": 16, "gpu": "t4", "label": "GPU T4"},
    "gpu-l4": {"cores": 4, "mem_gib": 16, "gpu": "l4", "label": "GPU L4"},
    "gpu-a10g": {"cores": 4, "mem_gib": 16, "gpu": "a10g", "label": "GPU A10G"},
    "gpu-l40s": {"cores": 8, "mem_gib": 32, "gpu": "l40s", "label": "GPU L40S"},
    "gpu-a100": {"cores": 8, "mem_gib": 32, "gpu": "a100", "label": "GPU A100"},
    "gpu-h100": {"cores": 8, "mem_gib": 32, "gpu": "h100", "label": "GPU H100"},
}
DEFAULT_HARDWARE = "cpu-2"


def rates() -> dict[str, Any]:
    r = json.loads(json.dumps(COMPUTE_RATES))
    raw = os.environ.get("MACRAE_COMPUTE_RATES", "").strip()
    if raw:
        try:
            over = json.loads(raw)
            if isinstance(over, dict):
                gpu = over.pop("gpu_s", None)
                r.update({k: float(v) for k, v in over.items() if isinstance(v, (int, float))})
                if isinstance(gpu, dict):
                    r["gpu_s"].update({str(k).lower(): float(v) for k, v in gpu.items()
                                       if isinstance(v, (int, float))})
        except ValueError:
            pass
    return r


def hardware_rate(hw: str) -> float:
    """USD per second for one sandbox of this hardware option."""
    spec = HARDWARE.get(hw) or HARDWARE[DEFAULT_HARDWARE]
    r = rates()
    usd = spec["cores"] * r["cpu_core_s"] + spec["mem_gib"] * r["mem_gib_s"]
    if spec["gpu"]:
        usd += float(r["gpu_s"].get(spec["gpu"], 0.0))
    return usd


def hardware_options() -> list[dict]:
    return [{"id": k, "label": v["label"], "cores": v["cores"], "mem_gib": v["mem_gib"], "gpu": v["gpu"] or None,
             "usd_per_hour": round(hardware_rate(k) * 3600, 3)} for k, v in HARDWARE.items()]


def hardware_label(hw: str) -> str:
    return (HARDWARE.get(hw) or {}).get("label") or hw


# ── LLM ─────────────────────────────────────────────────────────────────────


def price_key(model: str) -> tuple[str, bool]:
    """(PRICES key, exact?) for a model id as Claude Code / Harbor / the API report it."""
    m = (model or "").strip().lower()
    m = re.sub(r"^(anthropic[/.]|us\.anthropic\.|global\.anthropic\.)", "", m)
    m = re.sub(r"\[[^\]]*\]$", "", m)
    m = re.sub(r"(-v\d+(:\d+)?)$", "", m)
    m = re.sub(r"[-@]\d{8}$", "", m)
    if m in PRICES:
        return m, True
    for k in sorted(PRICES, key=len, reverse=True):
        if m.startswith(k):
            return k, True
    for fam, k in FAMILY_DEFAULT.items():
        if fam in m:
            return k, False
    return UNKNOWN_MODEL, False


def norm_usage(u: Any) -> dict[str, int]:
    """Anthropic `usage` (input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens,
    cache_creation{ephemeral_1h_input_tokens}) → {"in", "out", "cache_read", "cache_write", "cache_write_1h"}."""
    u = u if isinstance(u, dict) else {}

    def n(*names: str) -> int:
        for k in names:
            v = u.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return int(v)
        return 0

    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    one_h = int(cc.get("ephemeral_1h_input_tokens") or 0)
    return {"in": n("input_tokens", "in"), "out": n("output_tokens", "out"),
            "cache_read": n("cache_read_input_tokens", "cache_read"),
            "cache_write": n("cache_creation_input_tokens", "cache_write"), "cache_write_1h": one_h}


def llm_usd(model: str, tokens: dict[str, int]) -> float:
    key, _ = price_key(model)
    p_in, p_out, p_read, p_write = PRICES[key]
    w1h = min(tokens.get("cache_write_1h", 0), tokens.get("cache_write", 0))
    w5m = tokens.get("cache_write", 0) - w1h
    return (tokens.get("in", 0) * p_in + tokens.get("out", 0) * p_out + tokens.get("cache_read", 0) * p_read +
            w5m * p_write + w1h * 2 * p_in) / 1e6


def add_tokens(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    return {k: a.get(k, 0) + b.get(k, 0) for k in ("in", "out", "cache_read", "cache_write", "cache_write_1h")}


def event_cost(usd: float, tokens: dict[str, int]) -> dict:
    """The optional `cost` field of a TraceEvent."""
    return {"usd": round(usd, 6), "tokens": {"in": tokens.get("in", 0), "out": tokens.get("out", 0),
                                             "cache": tokens.get("cache_read", 0) + tokens.get("cache_write", 0)}}


def public_tokens(t: dict[str, int]) -> dict[str, int]:
    return {"in": t.get("in", 0), "out": t.get("out", 0), "cache_read": t.get("cache_read", 0),
            "cache_write": t.get("cache_write", 0), "cache": t.get("cache_read", 0) + t.get("cache_write", 0)}


class Session:
    """One Claude Code invocation's spend, built from stream-json lines."""

    def __init__(self, sid: str = ""):
        self.id = sid
        self.model = ""
        self.msgs: dict[str, dict[str, int]] = {}  # message id → usage (max per field: lines repeat it per block)
        self.msg_models: dict[str, str] = {}
        self.reported_usd: Optional[float] = None  # Claude Code's total_cost_usd (wins)
        self.final_tokens: Optional[dict[str, int]] = None  # the result line's cumulative usage
        self.first_t: Optional[float] = None
        self.last_t: Optional[float] = None

    def feed(self, d: dict, t: Optional[float] = None) -> None:
        if t is not None:
            self.first_t = t if self.first_t is None else min(self.first_t, t)
            self.last_t = t if self.last_t is None else max(self.last_t, t)
        self.id = self.id or str(d.get("session_id") or "")
        typ = d.get("type")
        if typ == "system" and d.get("model"):
            self.model = str(d["model"])
        elif typ == "assistant" and isinstance(d.get("message"), dict):
            m = d["message"]
            if d.get("parent_tool_use_id"):
                pass  # subagent messages carry their own ids and usage; counted the same way
            mid = str(m.get("id") or f"anon{len(self.msgs)}")
            u = norm_usage(m.get("usage"))
            prev = self.msgs.get(mid)
            self.msgs[mid] = {k: max(u[k], (prev or {}).get(k, 0)) for k in u}
            if m.get("model"):
                self.msg_models[mid] = str(m["model"])
                self.model = self.model or str(m["model"])
        elif typ == "result":
            if isinstance(d.get("total_cost_usd"), (int, float)):
                self.reported_usd = float(d["total_cost_usd"])
            if isinstance(d.get("usage"), dict):
                self.final_tokens = norm_usage(d["usage"])

    def tokens(self) -> dict[str, int]:
        if self.final_tokens and sum(self.final_tokens.values()):
            return self.final_tokens
        out: dict[str, int] = {}
        for u in self.msgs.values():
            out = add_tokens(out, u)
        return out

    def usd(self) -> tuple[float, bool]:
        """(usd, reported by Claude Code?)"""
        if self.reported_usd is not None:
            return self.reported_usd, True
        if self.msgs:
            return sum(llm_usd(self.msg_models.get(mid) or self.model, u) for mid, u in self.msgs.items()), False
        return llm_usd(self.model, self.tokens()), False


def sessions_from_lines(items: list[tuple[Optional[float], dict]]) -> dict[str, Session]:
    """stream-json lines (time, dict) → sessions by session_id."""
    out: dict[str, Session] = {}
    for t, d in items:
        if not isinstance(d, dict):
            continue
        sid = str(d.get("session_id") or "")
        s = out.get(sid) or out.get("")
        if s is None or (sid and s.id and s.id != sid):
            s = out.setdefault(sid, Session(sid))
        elif sid and not s.id:
            out.pop("", None)
            s.id = sid
            out[sid] = s
        s.feed(d, t)
    return out


# ── per run ─────────────────────────────────────────────────────────────────


def _span(timing: Any) -> float:
    from .trace import ts
    if not isinstance(timing, dict):
        return 0.0
    a, b = ts(timing.get("started_at")), ts(timing.get("finished_at"))
    return max(0.0, b - a) if a and b else 0.0


def _stream_items(path: Path) -> list[tuple[Optional[float], dict]]:
    from .trace import _read_lines, ts
    out = []
    for ln in _read_lines(path):
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append((ts(d.get("timestamp")), d))
    return out


def _stream_sessions(path: Path) -> dict[str, Session]:
    return sessions_from_lines(_stream_items(path))


def _check_seconds(run_dir: Path, key: str, base: float) -> float:
    """Time spent in the flow's own `check:` commands (log lines `check $ …` → `check exit …`)."""
    from .trace import log_entries, sanitize
    total, t_open = 0.0, None
    for _, t, msg in log_entries(run_dir / "logs" / f"{sanitize(key)}.log", base):
        if msg.startswith("check $"):
            t_open = t
        elif msg.startswith("check exit") and t_open is not None:
            total += max(0.0, t - t_open)
            t_open = None
    return total


def step_costs(state: dict, run_dir: Path, key: str, step: dict, hardware: str, now: float) -> dict:
    """{"llm_usd", "compute_usd", "seconds", "model", "hardware", "tokens", "setup", "work", "check",
        "sessions": [...], "reported": bool} for one leaf step."""
    from . import live
    from .trace import cached, job_dirs_for, trial_dirs, _load_json, parse_atif_meta
    kind = str(step.get("kind") or "")
    status = str(step.get("status") or "")
    started = float(step.get("started") or 0) or None
    finished = float(step.get("finished") or 0) or None
    out: dict[str, Any] = {"llm_usd": 0.0, "compute_usd": 0.0, "seconds": 0.0, "model": "", "hardware": "",
                           "tokens": {}, "setup": 0.0, "work": 0.0, "check": 0.0, "reported": False}
    if kind == "run":
        secs = ((finished or now) - started) if started else 0.0
        out.update(seconds=round(max(0.0, secs), 1), work=max(0.0, secs))
        return out
    if kind not in ("agent", "task"):
        return out
    on_modal = str(step.get("environment") or "").lower() == "modal"
    tokens: dict[str, int] = {}
    usd, reported_all, models = 0.0, True, []
    covered: set[str] = set()
    trial_secs, setup, work, check = 0.0, 0.0, 0.0, 0.0
    running_trial_start: Optional[float] = None
    any_trial = False
    for jd in job_dirs_for(state, key, step):
        for tdir in trial_dirs(jd):
            any_trial = True
            res = cached(tdir / "result.json", _load_json)
            res = res if isinstance(res, dict) else {}
            meta = cached(tdir / "agent" / "trajectory.json", parse_atif_meta) or {}
            sess = cached(tdir / "agent" / "claude-code.txt", _stream_sessions) or {}
            sids = {s for s in sess if s} | ({meta["session"]} if meta.get("session") else set())
            covered |= sids
            ar = res.get("agent_result") if isinstance(res.get("agent_result"), dict) else None
            model = str(((res.get("agent_info") or {}).get("model_info") or {}).get("name") or meta.get("model")
                        or next((s.model for s in sess.values() if s.model), ""))
            if ar and (ar.get("n_input_tokens") or ar.get("n_output_tokens") or ar.get("cost_usd") is not None):
                n_in, n_cache = int(ar.get("n_input_tokens") or 0), int(ar.get("n_cache_tokens") or 0)
                tk = {"in": max(0, n_in - n_cache), "out": int(ar.get("n_output_tokens") or 0),
                      "cache_read": n_cache, "cache_write": 0, "cache_write_1h": 0}
                if isinstance(ar.get("cost_usd"), (int, float)):
                    usd += float(ar["cost_usd"])
                else:
                    usd += llm_usd(model, tk)
                    reported_all = False
                tokens = add_tokens(tokens, tk)
            elif sess:
                for s in sess.values():
                    u, rep = s.usd()
                    usd += u
                    reported_all = reported_all and rep
                    tokens = add_tokens(tokens, s.tokens())
            elif meta.get("tokens"):
                tokens = add_tokens(tokens, meta["tokens"])
                if meta.get("usd") is not None:
                    usd += meta["usd"]
                else:
                    usd += llm_usd(model, meta["tokens"])
                    reported_all = False
            if model:
                models.append(model)
            from .trace import ts
            t0, t1 = ts(res.get("started_at")), ts(res.get("finished_at"))
            if t0 and t1:
                trial_secs += max(0.0, t1 - t0)
                setup += _span(res.get("environment_setup")) + _span(res.get("agent_setup"))
                work += _span(res.get("agent_execution"))
                check += _span(res.get("verifier"))
            elif t0:
                running_trial_start = max(t0, running_trial_start or 0)
    # live lines from sessions the trial files don't have yet (the agent is still running)
    first_live: Optional[float] = None  # first live line of the current attempt
    for _, items in live.streams(run_dir, key).items():
        for sid, s in sessions_from_lines([(t, d) for t, d in items]).items():
            if s.first_t is not None and started and s.first_t >= started - 5:
                first_live = s.first_t if first_live is None else min(first_live, s.first_t)
            if sid and sid in covered:
                continue
            u, rep = s.usd()
            usd += u
            reported_all = reported_all and rep
            tokens = add_tokens(tokens, s.tokens())
            if s.model:
                models.append(s.model)
    # sandbox seconds: finished trials, plus the attempt still going (from its trial's start, else the step's)
    running = status not in ("ok", "failed", "skipped", "cancelled")
    if running and started and status not in ("queued", "waiting-account"):
        t_start = max(running_trial_start or 0, started)
        trial_secs += max(0.0, now - t_start)
        if first_live and first_live > started:
            setup += max(0.0, first_live - started)
            work += max(0.0, now - first_live)
        else:
            setup += max(0.0, now - started)
    if not trial_secs and started and finished:
        trial_secs = max(0.0, finished - started)  # no trial timings at all (e.g. harbor failed early)
        setup += trial_secs
    check += _check_seconds(run_dir, key, float(state.get("started") or now))
    compute = trial_secs * hardware_rate(hardware) if on_modal else 0.0
    out.update(llm_usd=round(usd, 6), compute_usd=round(compute, 6), seconds=round(trial_secs, 1),
               model=price_key(models[-1])[0] if models else "", hardware=hardware if on_modal else "local",
               tokens=public_tokens(tokens), setup=setup, work=work, check=check,
               reported=bool(models) and reported_all)
    out["_tokens_raw"] = tokens
    return out


def run_costs(state: dict, plan: Optional[dict] = None, now: Optional[float] = None) -> dict:
    """The run's costs object (see the module doc). `plan` is planner.load(run_dir)."""
    from .trace import sanitize  # noqa: F401  (keeps the import graph obvious: trace is the file reader)
    now = now or time.time()
    run_dir = Path(str(state.get("_dir") or ""))
    plan = plan or {}
    hardware = str(plan.get("hardware") or DEFAULT_HARDWARE)
    if hardware not in HARDWARE:
        hardware = DEFAULT_HARDWARE
    steps = state.get("steps") or {}
    by_step: dict[str, dict] = {}
    tokens: dict[str, int] = {}
    llm, compute, estimated = 0.0, 0.0, False
    phases = {"plan": 0.0, "setup": 0.0, "work": 0.0, "check": 0.0}
    if plan.get("started"):
        p_end = float(plan.get("finished") or now)
        phases["plan"] = max(0.0, p_end - float(plan["started"]))
        p_tok = norm_usage(plan.get("usage"))
        p_usd = float(plan.get("cost_usd") or 0.0)
        if plan.get("model") or p_usd:
            by_step["plan"] = {"llm_usd": round(p_usd, 6), "compute_usd": 0.0, "seconds": round(phases["plan"], 1),
                               "model": price_key(plan.get("model") or "")[0] if plan.get("model") else "",
                               "hardware": "", "tokens": public_tokens(p_tok)}
            llm += p_usd
            tokens = add_tokens(tokens, p_tok)
    for key, st in steps.items():
        if not isinstance(st, dict) or st.get("fanout") is not None:
            continue
        sc = step_costs(state, run_dir, key, st, hardware, now)
        raw = sc.pop("_tokens_raw", {})
        phases["setup"] += sc.pop("setup")
        phases["work"] += sc.pop("work")
        phases["check"] += sc.pop("check")
        reported = sc.pop("reported")
        if sc["seconds"] or sc["llm_usd"] or sc["model"]:
            by_step[key] = sc
        llm += sc["llm_usd"]
        compute += sc["compute_usd"]
        tokens = add_tokens(tokens, raw)
        if sc["model"] and not reported:
            estimated = True
    t_start = float(plan.get("started") or 0) or float(state.get("started") or 0) or now
    t_end = float(state.get("finished") or 0) or now
    wall = max(0.0, t_end - t_start)
    return {"llm_usd": round(llm, 6), "compute_usd": round(compute, 6), "total_usd": round(llm + compute, 6),
            "tokens": public_tokens(tokens), "by_step": by_step, "wall_s": round(wall, 1),
            "phases": {k: round(v, 1) for k, v in phases.items()}, "hardware": hardware,
            "hardware_label": hardware_label(hardware), "usd_per_hour": round(hardware_rate(hardware) * 3600, 3),
            "estimated": estimated, "updated": round(now, 3)}


def save(run_dir: Path, costs: dict) -> None:
    """<run>/costs.json, for evolve (distill / metrics) and anyone reading the run folder."""
    try:
        tmp = run_dir / "costs.json.tmp"
        tmp.write_text(json.dumps(costs, indent=1))
        tmp.replace(run_dir / "costs.json")
    except OSError:
        pass
