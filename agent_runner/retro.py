"""Retrospective input: everything about how the app was used, condensed into one folder for agents to read.

    retro/input/SUMMARY.md       numbers: runs, steps, failures, retries, accounts, cost, events, errors seen
    retro/input/runs/*.json      run states
    retro/input/events.jsonl     usage events
    retro/input/sessions/*.md    Claude Code conversations (user + assistant text, tool errors), condensed
    retro/input/source/          the app's source, so findings can point at code
"""

from __future__ import annotations

import json
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from .config import HOME, RUNS_DIR
from .events import EVENTS_FILE

PKG = Path(__file__).resolve().parent
REPO_ROOT = PKG.parent  # a git checkout when installed with -e; site-packages otherwise


def default_sessions() -> Path:
    """Claude Code keeps a project's sessions in ~/.claude/projects/<cwd with / → ->: the project you run from."""
    return HOME / ".claude/projects" / str(Path.cwd().resolve()).replace("/", "-")


def _since(days: float) -> float:
    return time.time() - days * 86400


def _load_runs(since: float) -> list[dict]:
    out = []
    if not RUNS_DIR.is_dir():
        return out
    for d in RUNS_DIR.iterdir():
        f = d / "state.json"
        if d.name.startswith("_") or not f.is_file():
            continue
        try:
            st = json.loads(f.read_text())
        except ValueError:
            continue
        if (st.get("started") or 0) >= since:
            out.append(st)
    out.sort(key=lambda s: s.get("started") or 0)
    return out


def _load_events(since: float) -> list[dict]:
    out = []
    try:
        for line in EVENTS_FILE.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("t", 0) >= since:
                e.setdefault("event", e.get("kind"))  # early events used "kind"
                out.append(e)
    except OSError:
        pass
    return out


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif c.get("type") == "tool_use":
                inp = c.get("input") or {}
                desc = inp.get("description") or inp.get("command") or inp.get("file_path") or ""
                parts.append(f"[tool {c.get('name')}: {str(desc)[:160]}]")
            elif c.get("type") == "tool_result" and c.get("is_error"):
                parts.append(f"[tool error: {_text_of(c.get('content'))[:300]}]")
        return "\n".join(p for p in parts if p)
    return ""


def condense_session(path: Path, max_chars: int = 60000) -> str:
    """User messages in full, assistant text and tool calls shortened, tool errors kept."""
    lines = [f"# session {path.stem}\n"]
    total = 0
    for raw in path.read_text(errors="replace").splitlines():
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        role = d.get("type")
        msg = d.get("message") or {}
        if role not in ("user", "assistant") or not isinstance(msg, dict):
            continue
        text = _text_of(msg.get("content")).strip()
        if not text:
            continue
        if role == "assistant":
            text = text[:1200]
        else:
            text = text[:4000]
        chunk = f"\n**{role}** {d.get('timestamp', '')[:16]}\n{text}\n"
        total += len(chunk)
        if total > max_chars:
            lines.append("\n… (truncated)\n")
            break
        lines.append(chunk)
    return "".join(lines)


def _fmt_t(t: Optional[float]) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(t)) if t else "?"


def summarize(runs: list[dict], events: list[dict]) -> str:
    L = ["# Usage summary\n", f"generated {time.strftime('%Y-%m-%d %H:%M')}\n"]
    status = Counter(r.get("status") for r in runs)
    L.append(f"\n## Runs ({len(runs)})\n" + ", ".join(f"{k}: {v}" for k, v in status.items()) + "\n")
    L.append("\n| run | status | steps | took | started |\n|---|---|---|---|---|\n")
    for r in runs:
        took = (r.get("finished") or time.time()) - (r.get("started") or time.time())
        L.append(f"| {r['id']} | {r.get('status')} | {len(r.get('steps', {}))} | {int(took)}s | {_fmt_t(r.get('started'))} |\n")

    steps = [(r["id"], k, s) for r in runs for k, s in r.get("steps", {}).items()]
    by_kind = Counter((s.get("kind"), s.get("status")) for _, _, s in steps)
    L.append("\n## Steps by kind and status\n" + "\n".join(f"- {k} {st}: {n}" for (k, st), n in sorted(by_kind.items(), key=str)) + "\n")
    retried = [(rid, k, s) for rid, k, s in steps if (s.get("attempt") or 1) > 1]
    L.append(f"\n## Retries: {len(retried)} step(s) needed more than one attempt\n")
    for rid, k, s in retried[:40]:
        L.append(f"- {rid}/{k}: attempt {s.get('attempt')} → {s.get('status')} {(s.get('error') or '')[:200]}\n")
    failed = [(rid, k, s) for rid, k, s in steps if s.get("status") == "failed"]
    L.append(f"\n## Failed steps ({len(failed)})\n")
    errs = Counter((s.get("error") or "?")[:120] for _, _, s in failed)
    for e, n in errs.most_common(25):
        L.append(f"- ×{n} {e}\n")
    accts = Counter(s.get("account") for _, _, s in steps if s.get("account"))
    cost = sum(s.get("cost_usd") or 0 for _, _, s in steps)
    L.append("\n## Accounts used\n" + ("\n".join(f"- {a}: {n} step(s)" for a, n in accts.items()) or "- none") + "\n")
    L.append(f"\nTotal reported cost (API-equivalent): ${cost:.2f}\n")

    ev = Counter(e.get("event") for e in events)
    L.append(f"\n## App events ({len(events)})\n" + "\n".join(f"- {k}: {n}" for k, n in ev.most_common()) + "\n")
    toasts = [e for e in events if e.get("event") == "toast"]
    L.append(f"\n## Messages the user saw ({len(toasts)}, last 40)\n")
    for e in toasts[-40:]:
        L.append(f"- {_fmt_t(e['t'])} [{e.get('page')}] {e.get('msg')}\n")
    probs = [e for e in events if e.get("event") in ("flow_check_failed", "flow_start_failed", "auth_failed", "limit_hit")]
    L.append(f"\n## Problems logged ({len(probs)})\n")
    for e in probs[-40:]:
        L.append(f"- {_fmt_t(e['t'])} {e.get('event')} {json.dumps({k: v for k, v in e.items() if k not in ('t', 'pid', 'event')}, default=str)[:300]}\n")
    return "".join(L)


def gather(out: Path, days: float = 7.0, sessions_dir: Optional[Path] = None, max_sessions: int = 6) -> Path:
    since = _since(days)
    inp = out / "input"
    if inp.exists():
        shutil.rmtree(inp)
    (inp / "runs").mkdir(parents=True)
    (inp / "sessions").mkdir()
    runs, events = _load_runs(since), _load_events(since)
    (inp / "SUMMARY.md").write_text(summarize(runs, events))
    for r in runs:
        (inp / "runs" / f"{r['id']}.json").write_text(json.dumps(r, indent=1, default=str))
    with open(inp / "events.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e, default=str) + "\n")
    sd = sessions_dir or default_sessions()
    if sd.is_dir():
        sess = sorted((p for p in sd.glob("*.jsonl") if p.stat().st_mtime >= since),
                      key=lambda p: p.stat().st_mtime, reverse=True)[:max_sessions]
        for p in sess:
            (inp / "sessions" / f"{p.stem}.md").write_text(condense_session(p))
    src = inp / "source"
    (src / "agent_runner").mkdir(parents=True)
    for p in PKG.glob("*.py"):
        shutil.copy2(p, src / "agent_runner" / p.name)
    for rel in ("README.md", ".claude/skills/agent-runner/SKILL.md"):
        p = REPO_ROOT / rel
        if p.is_file():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, src / rel)
    (out / "findings").mkdir(exist_ok=True)
    return out
