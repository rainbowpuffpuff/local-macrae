"""evolve: the agent gets better run over run by learning from its own traces.

    import evolve
    evolve.distill(run_dir)            # after a run ends: lessons → $AGENT_RUNNER_HOME/evolve/lessons.jsonl,
                                       # passing scripts → tools/<task_id>/ (Claude with ANTHROPIC_API_KEY, else rules)
    evolve.distill_in_background(id)   # the same, in a daemon thread (the server's run-end hook)
    evolve.context(task_id, k=8)       # the best lessons + scripts as text (for the planner)
    evolve.flow_vars(task_id)          # {"lessons", "tools_dir"}: merge into the flow vars when starting a run
    evolve.hints(task_id)              # past runs by hardware, best run, suggested hardware/budget
    evolve.rule_plan(task, options)    # the planner's JSON without Claude (its fallback), from past runs
    evolve.metrics()                   # GET /api/evolution
    evolve.export_dataset(out)         # out/all.jsonl + out/sft.jsonl (ATIF trajectories as chat JSONL)

CLI: python -m evolve distill|context|metrics|export|lessons|hints|add|watch|fixtures|check-flows (see --help).
"""

from __future__ import annotations

import time
from pathlib import Path

from .context import context, flow_vars, hints, rule_plan
from .dataset import export_dataset
from .distill import distill, distill_in_background, distill_pending, pending
from .metrics import metrics

__all__ = ["distill", "distill_in_background", "distill_pending", "pending", "context", "flow_vars", "hints",
           "rule_plan", "metrics", "export_dataset", "lessons", "add_lesson", "check_flows"]


def lessons(task_id: str = "", include_retired: bool = False) -> list[dict]:
    """Stored lessons (one task, or all), best first."""
    from . import store
    xs = store.load()
    if task_id:
        xs = [x for x in xs if x.get("task_id") in (task_id, "*")]
    if not include_retired:
        xs = [x for x in xs if x.get("status", "active") == "active"]
    now = time.time()
    return sorted(xs, key=lambda x: -store.score(x, now))


def add_lesson(task_id: str, text: str, kind: str = "do", evidence: list[str] | None = None) -> dict:
    """A lesson written by a person (task_id "*" = every task). Repeating an existing one reinforces it."""
    from . import store
    if kind not in store.KINDS:
        raise ValueError(f"kind must be one of {store.KINDS}")
    with store.locked():
        xs = store.load()
        lesson, _ = store.merge(xs, {"task_id": task_id, "lesson": text, "kind": kind, "evidence": evidence or [],
                                     "confidence": 0.9, "source": "manual"})
        store.save(xs)
    return dict(lesson)


def check_flows(paths: list[str | Path]) -> list[str]:
    """Problems with the lesson-injection hook in flow files (empty = every agent step gets the lessons)."""
    import yaml
    problems = []
    for p in paths:
        p = Path(p)
        try:
            spec = yaml.safe_load(p.read_text()) or {}
        except (OSError, yaml.YAMLError) as e:
            problems.append(f"{p}: {e}")
            continue
        vars_ = spec.get("vars") or {}
        agent_steps = [s for s in spec.get("steps") or [] if isinstance(s, dict) and s.get("instruction")]
        if not agent_steps:
            continue
        for v in ("lessons", "tools_dir"):
            if v not in vars_:
                problems.append(f"{p.name}: vars has no default for {v!r} (add `{v}: \"\"`)")
        for s in agent_steps:
            if "vars.lessons" not in str(s.get("instruction")):
                problems.append(f"{p.name}: step {s.get('id')} instruction doesn't include {{{{ vars.lessons }}}}")
            paths_ = s.get("paths") if "paths" in s else s.get("path")
            if "vars.tools_dir" not in str(paths_):
                problems.append(f"{p.name}: step {s.get('id')} doesn't pass {{{{ vars.tools_dir }}}} in paths")
    return problems
