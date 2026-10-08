"""The planner: before a task's flow starts, one Claude call decides what to run and where.

It gets the task (title, prompt, inputs), the flow's steps, the tunable flow vars, recent lessons from evolve and
the hardware options with their prices, and returns (structured output)
    {"plan": [stage…], "hardware": "cpu-8|gpu-a10g|…", "params": {...}, "budget_usd": x, "why": "…"}
which becomes flow vars (`plan`, `hardware`, `budget_usd`, plus each param that names a tunable flow var). The
decision and its token cost are kept in <run>/plan.json and shown as the run's first events (type "plan").
If the API can't be used (no key, an error, a refusal, a timeout) the task's defaults are used and the event says so.

Env: ANTHROPIC_API_KEY (or MACRAE_PLANNER_API_KEY), MACRAE_PLANNER_MODEL (default claude-sonnet-5-5),
MACRAE_PLANNER_EFFORT (default medium), MACRAE_PLANNER=off to skip the call, MACRAE_PLANNER_TIMEOUT (s, default 60).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import costs, safety

log = logging.getLogger("macrae.server")

DEFAULT_MODEL = "claude-sonnet-5-5"
# models that take the server-side refusal fallback ("default" form, beta server-side-fallback-2026-07-01)
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5"}
RESERVED_VARS = {"environment", "python", "task_id", "task_title", "lessons", "plan", "hardware", "budget_usd",
                 "plan_why"}
SAFE_VALUE_RE = re.compile(r"^[^\"'`$\\;|&<>\n\r\x00]{0,300}$")
MAX_BUDGET = 50.0

SYSTEM = """You plan runs of scientific computing tasks for a research group's live demo. An AI agent (Claude Code) \
will do the actual work in a cloud sandbox on Modal; script steps run on the backend. You decide, before anything \
starts: the stages the run will go through, the sandbox hardware, the values of the tunable parameters, and a \
budget in USD (LLM tokens plus compute) that the run should stay under.

Choose the cheapest hardware that finishes in reasonable time: CPU unless the work clearly benefits from a GPU \
(GPU-accelerated MD, ML training or inference); more cores only when the code parallelises. Keep tunable \
parameters at their defaults unless there is a reason (lessons from earlier runs are the best reason). Only \
tune parameters from the list you are given, and give values as plain strings. Stages: 2 to 6 short items, each \
a concrete thing that will happen, with where it runs ("backend" or "modal"). `why` is one or two plain \
sentences a scientist watching the demo would understand, naming the deciding facts (e.g. a lesson, the system \
size, the price difference)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "plan": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "detail": {"type": "string"},
                           "where": {"type": "string", "enum": ["backend", "modal"]}},
            "required": ["name", "detail", "where"], "additionalProperties": False}},
        "hardware": {"type": "string", "enum": list(costs.HARDWARE)},
        "params": {"type": "array", "items": {
            "type": "object", "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
            "required": ["name", "value"], "additionalProperties": False}},
        "budget_usd": {"type": "number"},
        "why": {"type": "string"},
    },
    "required": ["plan", "hardware", "params", "budget_usd", "why"],
    "additionalProperties": False,
}


def plan_path(run_dir: Path) -> Path:
    return run_dir / "plan.json"


def load(run_dir: Path) -> dict:
    try:
        d = json.loads(plan_path(run_dir).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(run_dir: Path, rec: dict) -> None:
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        tmp = run_dir / "plan.json.tmp"
        tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False, default=str))
        tmp.replace(plan_path(run_dir))
    except OSError as e:
        log.warning("could not write plan.json: %s", e)


# ── inputs to the decision ──────────────────────────────────────────────────


def flow_outline(flow_file: Optional[Path]) -> tuple[list[dict], dict[str, Any]]:
    """(steps [{id, kind, description}], flow vars with defaults) from the task's flow YAML."""
    if not flow_file:
        return [], {}
    try:
        import yaml
        spec = yaml.safe_load(flow_file.read_text()) or {}
    except Exception:
        return [], {}
    steps = []
    for s in spec.get("steps") or []:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        kind = "script" if "run" in s else "agent" if "instruction" in s else "task"
        steps.append({"id": str(s["id"]), "kind": kind, "description": str(s.get("description") or "")})
    vars_ = spec.get("vars") if isinstance(spec.get("vars"), dict) else {}
    return steps, vars_


def tunable(task: dict, flow_vars: dict) -> dict[str, Any]:
    """Flow vars the planner may set: not task inputs (the user chose those) and not engine plumbing."""
    inputs = {i.get("name") for i in task.get("inputs") or [] if isinstance(i, dict)}
    return {k: v for k, v in flow_vars.items() if k not in inputs and k not in RESERVED_VARS
            and isinstance(v, (str, int, float)) and not isinstance(v, bool)}


def defaults(task: dict, steps: list[dict], reason: str) -> dict:
    hw = str(task.get("hardware") or costs.DEFAULT_HARDWARE)
    hw = hw if hw in costs.HARDWARE else costs.DEFAULT_HARDWARE
    try:
        budget = float(task.get("budget_usd") or 2.0)
    except (TypeError, ValueError):
        budget = 2.0
    stages = [{"name": s["id"], "detail": s["description"] or s["id"],
               "where": "backend" if s["kind"] == "script" else "modal"} for s in steps]
    return {"plan": stages, "hardware": hw, "params": {}, "budget_usd": budget, "why": reason}


def _prompt(task: dict, inputs: dict, steps: list[dict], knobs: dict, lessons: str) -> str:
    parts = [f"Task: {task.get('title') or task['id']} (id {task['id']})",
             f"What it does: {task.get('prompt') or task.get('subtitle') or ''}",
             "Inputs chosen by the user: " + (json.dumps(inputs, ensure_ascii=False) if inputs else "none"),
             "Flow steps:\n" + ("\n".join(f"- {s['id']} ({s['kind']}): {s['description']}" for s in steps)
                                or "- (unknown)"),
             "Tunable parameters (name = default):\n" + ("\n".join(f"- {k} = {v}" for k, v in knobs.items())
                                                         or "- none"),
             "Hardware options for the Modal sandbox (USD per hour):\n" + "\n".join(
                 f"- {h['id']}: {h['label']}, {h['cores']} cores, {h['mem_gib']} GiB"
                 + (f", GPU {h['gpu'].upper()}" if h["gpu"] else "") + f", ${h['usd_per_hour']}/h"
                 for h in costs.hardware_options())]
    if task.get("hardware"):
        parts.append(f"The task's default hardware: {task['hardware']}")
    if task.get("budget_usd"):
        parts.append(f"The task's default budget: ${task['budget_usd']}")
    parts.append("Lessons from earlier runs of this task:\n" + (
        safety.fence(lessons.strip(), "lessons distilled from earlier runs' traces (which quote papers and tool "
                     "output)") if lessons.strip() else "none yet"))
    return "\n\n".join(parts)


# ── the call ────────────────────────────────────────────────────────────────


def api_key() -> str:
    return (os.environ.get("MACRAE_PLANNER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def model_name() -> str:
    return os.environ.get("MACRAE_PLANNER_MODEL", "").strip() or DEFAULT_MODEL


def call_claude(system: str, prompt: str) -> tuple[dict, dict, str]:
    """One Messages API call with structured output. Returns (decision, usage, model that answered)."""
    import anthropic  # lazy: the server starts without it

    model = model_name()
    timeout = float(os.environ.get("MACRAE_PLANNER_TIMEOUT", "") or 60)
    client = anthropic.Anthropic(api_key=api_key(), timeout=timeout, max_retries=1)
    kwargs: dict[str, Any] = {}
    if costs.price_key(model)[0] == model and model in FALLBACK_MODELS:
        kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    effort = os.environ.get("MACRAE_PLANNER_EFFORT", "").strip() or "medium"
    resp = client.beta.messages.create(
        model=model, max_tokens=8000, system=system,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        **kwargs)
    usage = resp.usage.to_dict() if hasattr(resp.usage, "to_dict") else dict(resp.usage or {})
    if resp.stop_reason == "refusal":
        raise PlannerRefused("the model declined to plan this run", usage, str(resp.model or model))
    if resp.stop_reason == "max_tokens":
        raise PlannerRefused("the plan was cut off (max_tokens)", usage, str(resp.model or model))
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    return json.loads(text), usage, str(resp.model or model)


class PlannerRefused(RuntimeError):
    def __init__(self, msg: str, usage: dict, model: str):
        super().__init__(msg)
        self.usage, self.model = usage, model


def _clean(decision: dict, base: dict, knobs: dict) -> dict:
    """Keep what is valid; anything else falls back to the default."""
    out = dict(base)
    hw = str(decision.get("hardware") or "")
    if hw in costs.HARDWARE:
        out["hardware"] = hw
    try:
        b = float(decision.get("budget_usd"))
        if 0 < b <= MAX_BUDGET:
            out["budget_usd"] = round(b, 2)
    except (TypeError, ValueError):
        pass
    stages = []
    for s in decision.get("plan") or []:
        if isinstance(s, dict) and str(s.get("name") or "").strip():
            stages.append({"name": str(s["name"])[:80], "detail": str(s.get("detail") or "")[:300],
                           "where": "backend" if s.get("where") == "backend" else "modal"})
    if stages:
        out["plan"] = stages[:8]
    params = {}
    for p in decision.get("params") or []:
        if not isinstance(p, dict):
            continue
        name, value = str(p.get("name") or ""), str(p.get("value") if p.get("value") is not None else "")
        if name in knobs and SAFE_VALUE_RE.match(value) and value.strip():
            params[name] = value.strip()
    out["params"] = params
    why = str(decision.get("why") or "").strip()
    out["why"] = why[:600] or base["why"]
    return out


def make_plan(task: dict, inputs: dict, flow_file: Optional[Path], lessons: str = "",
              call: Optional[Callable[[str, str], tuple[dict, dict, str]]] = None,
              run_dir: Optional[Path] = None) -> dict:
    """Decide; never raises. Writes <run>/plan.json twice when run_dir is given: "planning" first, then the result.
    Returns the record: {status ok|default, plan, hardware, params, budget_usd, why, model, usage, cost_usd,
    started, finished, error, lessons, knobs}."""
    t0 = time.time()
    steps, flow_vars = flow_outline(flow_file)
    knobs = tunable(task, flow_vars)
    rec: dict[str, Any] = {"status": "planning", "started": t0, "finished": None, "task_id": task["id"],
                           "inputs": inputs, "lessons": lessons, "knobs": knobs, "model": "", "usage": {},
                           "cost_usd": 0.0, "error": ""}
    if run_dir:
        save(run_dir, rec)
    base = defaults(task, steps, "")
    reason = ""
    decision: Optional[dict] = None
    if os.environ.get("MACRAE_PLANNER", "").strip().lower() in ("off", "0", "false", "no"):
        reason = "the planner is switched off (MACRAE_PLANNER=off)"
    elif call is None and not api_key():
        reason = "no ANTHROPIC_API_KEY on the backend"
    elif blocked := safety.llm_block_reason():
        reason = f"no Claude calls right now: {blocked}"
    else:
        try:
            decision, usage, model = (call or call_claude)(SYSTEM, _prompt(task, inputs, steps, knobs, lessons))
            rec.update(model=model, usage=usage)
            if not isinstance(decision, dict):
                raise ValueError("the planner did not return a JSON object")
        except PlannerRefused as e:
            rec.update(model=e.model, usage=e.usage)
            reason = str(e)
            decision = None
        except Exception as e:  # API down, bad key, timeout, bad JSON: the run goes ahead with the defaults
            log.warning("planner failed: %s: %s", type(e).__name__, e)
            reason = f"the planner call failed ({type(e).__name__}: {str(e)[:160]})"
            decision = None
    if rec.get("usage"):
        rec["cost_usd"] = round(costs.llm_usd(rec.get("model") or model_name(), costs.norm_usage(rec["usage"])), 6)
    if decision is not None:
        rec.update(_clean(decision, base, knobs), status="ok")
    else:
        rec.update(defaults(task, steps, f"Used the task's defaults: {reason}."), status="default", error=reason)
    rec["finished"] = time.time()
    if run_dir:
        save(run_dir, rec)
    return rec


def flow_vars(rec: dict) -> dict[str, Any]:
    """The plan as flow vars (strings and JSON, as tasks.runner passes them)."""
    out: dict[str, Any] = dict(rec.get("params") or {})
    out.update(hardware=rec.get("hardware") or costs.DEFAULT_HARDWARE,
               budget_usd=rec.get("budget_usd") if rec.get("budget_usd") is not None else 2.0,
               plan=json.dumps(rec.get("plan") or [], ensure_ascii=False),
               plan_why=str(rec.get("why") or ""))
    return out


# ── events ──────────────────────────────────────────────────────────────────


def decision_title(rec: dict) -> str:
    n = len(rec.get("plan") or [])
    hw = costs.hardware_label(str(rec.get("hardware") or ""))
    what = f"{hw}, {n} stage{'s' if n != 1 else ''}, budget ${float(rec.get('budget_usd') or 0):.2f}"
    return ("Decided: " if rec.get("status") == "ok" else "Using the task's defaults: ") + what


def decision_detail(rec: dict) -> str:
    lines = [str(rec.get("why") or "")]
    for i, s in enumerate(rec.get("plan") or [], 1):
        lines.append(f"{i}. {s.get('name')}: {s.get('detail')} ({s.get('where')})")
    if rec.get("params"):
        lines.append("Parameters: " + ", ".join(f"{k} = {v}" for k, v in rec["params"].items()))
    n_lessons = sum(1 for ln in str(rec.get("lessons") or "").splitlines() if ln.strip().startswith(("-", "*")))
    if n_lessons:
        lines.append(f"Used {n_lessons} lesson{'s' if n_lessons != 1 else ''} from earlier runs.")
    return "\n".join(x for x in lines if x)
