"""What the next run gets: the lessons block for its agent steps and the planner, plus hints from past runs.

    evolve.context("small-calc", k=8)   → the block the planner reads ("" when nothing has been learned yet)
    evolve.flow_vars("small-calc")      → {"lessons": block naming /app/<task_id>/ for scripts, "tools_dir": path|""}
                                          (the server merges these into the flow vars when it starts a run)
    evolve.hints("small-calc")          → past runs per hardware, best run, suggested hardware/budget (rule-based
                                          planner fallback, and extra facts for the planner's Claude call)
"""

from __future__ import annotations

import statistics
from typing import Optional

from . import store, tools

HEADER = ("LESSONS FROM EARLIER RUNS OF THIS TASK (learned from their traces; follow them unless they contradict "
          "the instructions above):")
MAX_LESSON_CHARS = 400


def _lesson_line(x: dict) -> str:
    text = " ".join(x["lesson"].split())
    if len(text) > MAX_LESSON_CHARS:
        text = text[: MAX_LESSON_CHARS - 1] + "…"
    hits = int(x.get("hits") or 1)
    seen = f" (seen in {hits} runs)" if hits > 1 else ""
    return f"- [{x['id']}] {str(x.get('kind') or 'do').upper()}: {text}{seen}"


def top_lessons(task_id: str, k: int = 8) -> list[dict]:
    return store.active(store.load(), task_id)[: max(0, k)]


def context(task_id: str, k: int = 8, tools_path: Optional[str] = None) -> str:
    """The best recent lessons + the reusable scripts, as one block of text. "" if there are none.
    tools_path: where the scripts are inside the sandbox (flow_vars passes /app/<task_id>); None = just list them."""
    lessons = top_lessons(task_id, k)
    scripts = tools.listing(task_id)
    if not lessons and not scripts:
        return ""
    lines = []
    if lessons:
        lines.append(HEADER)
        lines += [_lesson_line(x) for x in lessons]
    if scripts:
        where = (f" are in {tools_path.rstrip('/')}/: copy and adapt them instead of starting from scratch"
                 if tools_path else "")
        lines.append(f"REUSABLE SCRIPTS from earlier runs that passed the check{where}:")
        for t in scripts[:8]:
            n = len(t.get("runs") or [])
            passed = f" (passed the check in {n} runs)" if n > 1 else ""
            lines.append(f"- {t['name']}: {t.get('description') or 'script'}{passed}")
    return "\n".join(lines)


def flow_vars(task_id: str, k: int = 8) -> dict[str, str]:
    """Vars for a new run of the task: `lessons` (instruction block) and `tools_dir` (extra agent input path)."""
    tdir = tools.tools_dir(task_id)
    sandbox = f"/app/{tools.task_dir(task_id).name}" if tdir else None
    text = context(task_id, k, tools_path=sandbox)
    if not tdir and text and "REUSABLE SCRIPTS" in text:
        text = context(task_id, k, tools_path=None)
    return {"lessons": text, "tools_dir": tdir}


def rule_plan(task: dict | str, hardware_options: Optional[list[str]] = None,
              default_hardware: str = "cpu-2") -> dict:
    """The planner's answer without Claude, in the contract's shape: {"plan", "hardware", "params", "budget_usd",
    "why"}. Stages are the flow's steps; hardware and budget come from past runs of the task (hints()); a time-limit
    or out-of-memory lesson moves a task that never passed to the next larger hardware option."""
    task = task if isinstance(task, dict) else {"id": str(task)}
    tid = str(task.get("id") or "")
    h = hints(tid)
    opts = [str(o) for o in hardware_options or []]
    stages = []
    flow = task.get("flow")
    if flow:
        try:
            import yaml
            from pathlib import Path
            from .config import REPO_ROOT
            p = Path(flow) if Path(flow).is_absolute() else REPO_ROOT / flow
            spec = yaml.safe_load(p.read_text()) or {}
            stages = [str(s.get("description") or s.get("id")) for s in spec.get("steps") or [] if isinstance(s, dict)]
        except Exception:
            stages = []
    hw = h["suggested_hardware"] if h["suggested_hardware"] and (not opts or h["suggested_hardware"] in opts) else (
        default_hardware if not opts or default_hardware in opts else opts[0])
    why = []
    if h["ok"]:
        why.append(f"{h['ok']} of {h['runs']} earlier runs passed; the fastest took "
                   f"{(h['best'] or {}).get('wall_s') or 0:.0f} s on {(h['best'] or {}).get('hardware') or hw}")
    limits = [x for x in top_lessons(tid, 20) if str(x.get("key") or "").startswith(("timeout:", "oom:"))]
    if limits and not h["ok"] and opts and hw in opts and opts.index(hw) + 1 < len(opts):
        hw = opts[opts.index(hw) + 1]
        why.append(f"an earlier run hit a limit ({limits[0]['lesson'][:120]}), so one size up")
    if not why:
        why.append("no earlier runs of this task: the task's defaults")
    return {"plan": stages, "hardware": hw, "params": {}, "budget_usd": h["suggested_budget_usd"],
            "why": "Rule-based plan (no Claude call): " + "; ".join(why) + ".", "lessons": context(tid)}


def _median(xs: list[float]) -> Optional[float]:
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs), 3) if xs else None


def hints(task_id: str) -> dict:
    """Facts from past runs of the task for the planner. Suggestions only use passing runs."""
    from .metrics import task_rows
    rows = task_rows(task_id)
    ok = [r for r in rows if r.get("ok")]
    by_hw: dict[str, dict] = {}
    for r in rows:
        hw = r.get("hardware") or "default"
        h = by_hw.setdefault(hw, {"runs": 0, "ok": 0, "wall_s": [], "total_usd": []})
        h["runs"] += 1
        if r.get("ok"):
            h["ok"] += 1
            h["wall_s"].append(r.get("wall_s"))
            h["total_usd"].append(r.get("total_usd"))
    hw_out = {hw: {"runs": h["runs"], "ok": h["ok"], "median_wall_s": _median(h["wall_s"]),
                   "median_usd": _median(h["total_usd"])} for hw, h in by_hw.items()}
    best = min(ok, key=lambda r: (r.get("wall_s") or 1e18)) if ok else None
    # most reliable hardware, then fastest
    ranked = sorted(((hw, h) for hw, h in hw_out.items() if h["ok"]),
                    key=lambda kv: (-kv[1]["ok"] / kv[1]["runs"], kv[1]["median_wall_s"] or 1e18))
    suggested_hw = ranked[0][0] if ranked and ranked[0][0] != "default" else None
    usd = [r.get("total_usd") for r in ok if isinstance(r.get("total_usd"), (int, float))]
    budget = round(max(usd) * 1.5, 2) if usd else None
    return {
        "task_id": task_id, "runs": len(rows), "ok": len(ok),
        "ok_rate": round(len(ok) / len(rows), 3) if rows else None,
        "median_wall_s": _median([r.get("wall_s") for r in ok]),
        "median_usd": _median(usd),
        "best": {k: best.get(k) for k in ("run_id", "wall_s", "total_usd", "hardware")} if best else None,
        "by_hardware": hw_out,
        "suggested_hardware": suggested_hw,
        "suggested_budget_usd": budget,
        "settings": [x["lesson"] for x in top_lessons(task_id, 20) if x.get("kind") == "setting"][:6],
        "context": context(task_id),
    }
