"""export_dataset(out) → out/: every finished agent trajectory (ATIF) as chat JSONL, for fine-tuning.

    out/all.jsonl        one line per trial: {"messages": [...], "reward": r, "run_id", "task_id", "step",
                         "attempt", "trial", "model", "check_passed", "check_output", "lessons_used", "status"}
    out/sft.jsonl        the same, only reward >= 1 (attempts that passed their check)
    out/manifest.json    counts and where it came from

messages use the common function-calling chat shape: {"role": "user", "content"} (the instruction, with the lessons
it was given), {"role": "assistant", "content", "tool_calls": [{"id", "type": "function", "function": {"name",
"arguments": "<json>"}}]}, {"role": "tool", "tool_call_id", "content"}.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from . import store
from .runs import Run, Step, Trial, list_run_dirs, load, text_of


def atif_messages(atif: dict) -> list[dict]:
    msgs: list[dict] = []
    for s in atif.get("steps") or []:
        if not isinstance(s, dict):
            continue
        src = s.get("source")
        text = text_of(s.get("message"))
        if src in ("user", "system"):
            if text.strip():
                msgs.append({"role": src, "content": text})
            continue
        if src != "agent":
            continue
        m: dict[str, Any] = {"role": "assistant", "content": text}
        calls = [c for c in s.get("tool_calls") or [] if isinstance(c, dict)]
        if calls:
            m["tool_calls"] = [{"id": str(c.get("tool_call_id") or f"call_{i}"), "type": "function",
                                "function": {"name": str(c.get("function_name") or ""),
                                             "arguments": c["arguments"] if isinstance(c.get("arguments"), str)
                                             else json.dumps(c.get("arguments") or {}, ensure_ascii=False)}}
                               for i, c in enumerate(calls)]
        if text.strip() or calls:
            msgs.append(m)
        results = [r for r in ((s.get("observation") or {}).get("results") or []) if isinstance(r, dict)]
        for j, r in enumerate(results):
            cid = r.get("source_call_id")
            if cid is None and j < len(calls):
                cid = calls[j].get("tool_call_id") or f"call_{j}"
            msgs.append({"role": "tool", "tool_call_id": str(cid or ""), "content": text_of(r.get("content"))})
    return msgs


def _attempt_outcome(step: Step, tr: Trial) -> tuple[Optional[float], Optional[bool], str]:
    a = next((x for x in step.attempts if x["n"] == tr.attempt), None)
    ck = next((c for c in step.checks if c.attempt == tr.attempt), None)
    if a is not None:
        reward, passed = a.get("reward"), bool(a.get("passed"))
    elif step.status in ("ok", "failed") and tr.attempt == step.attempt:
        reward, passed = step.reward, step.status == "ok"
    else:
        reward, passed = tr.reward, None
    if reward is None and tr.reward is not None:
        reward = tr.reward
    return reward, passed, ck.text if ck else ""


def records(run: Run) -> list[dict]:
    out = []
    for step in run.agent_steps:
        for tr in step.trials:
            if not tr.atif:
                continue
            msgs = atif_messages(tr.atif)
            if not any(m["role"] == "assistant" for m in msgs):
                continue
            reward, passed, check = _attempt_outcome(step, tr)
            ids = store.ids_in(tr.instruction) or run.lessons_used
            out.append({"messages": msgs, "reward": reward, "run_id": run.id, "task_id": run.task_id,
                        "step": step.key, "attempt": tr.attempt, "trial": tr.dir.name, "model": tr.model,
                        "check_passed": passed, "check_output": check[-1500:], "lessons_used": ids,
                        "status": step.status, "exception": tr.exception, "created": tr.finished or run.finished})
    return out


def export_dataset(out: str | Path) -> Path:
    out = Path(out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    n_all = n_sft = n_runs = 0
    by_task: dict[str, int] = {}
    with open(out / "all.jsonl.tmp", "w") as fa, open(out / "sft.jsonl.tmp", "w") as fs:
        for d in list_run_dirs():
            st = store.read_json(d / "state.json", {})
            if not isinstance(st, dict) or st.get("status") not in ("ok", "failed", "cancelled", "crashed"):
                continue
            try:
                recs = records(load(d))
            except Exception:
                continue
            if recs:
                n_runs += 1
            for r in recs:
                line = json.dumps(r, ensure_ascii=False, default=str) + "\n"
                fa.write(line)
                n_all += 1
                by_task[r["task_id"]] = by_task.get(r["task_id"], 0) + 1
                if isinstance(r["reward"], (int, float)) and r["reward"] >= 1:
                    fs.write(line)
                    n_sft += 1
    (out / "all.jsonl.tmp").replace(out / "all.jsonl")
    (out / "sft.jsonl.tmp").replace(out / "sft.jsonl")
    (out / "manifest.json").write_text(json.dumps(
        {"created": time.time(), "runs": n_runs, "all": n_all, "sft": n_sft, "by_task": by_task,
         "files": {"all": "all.jsonl", "sft": "sft.jsonl"}}, indent=1))
    return out
