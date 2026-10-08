"""Rule-based lesson extractor: what distill uses without ANTHROPIC_API_KEY (or when the Claude call fails).

Every lesson comes from something concrete in the trace, with the event it came from as evidence:
  check      a `check:` rejected an attempt → the exact problem line, and whether a later attempt fixed it
  failfix    a command failed and a later, similar one worked → "X failed with E; Y worked instead"
  missing    ModuleNotFoundError / command not found → what installed it (or that nothing did)
  limits     time limit or out-of-memory on the hardware used → a setting for the planner
  repeat     the same failing command run again unchanged → avoid
  slow       the slowest calculation in a passing run → time budget
  baseline   the best passing run so far (time, cost, hardware) → the planner's reference
Infrastructure failures (logins, Modal auth, no trials) are not lessons: the agent can't learn them.
"""

from __future__ import annotations

import re
from typing import Optional

from .runs import Call, Run, Step, evidence

INFRA_RE = re.compile(r"AuthError|Modal profile|no Claude login|login rejected|token rejected|\b401\b|"
                      r"harbor produced no trials|accounts set|usage limit|rate_limit", re.I)
TIMEOUT_RE = re.compile(r"AgentTimeoutError|timed? ?out|TimeoutError|time limit|DEADLINE", re.I)
OOM_RE = re.compile(r"MemoryError|out of memory|OOM|\bKilled\b|exit code 137|std::bad_alloc", re.I)
MODULE_RE = re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'")
NOTFOUND_RE = re.compile(r"(?:^|\s|/)([\w.+-]+): (?:command )?not found|command not found: ([\w.+-]+)", re.M)
INSTALL_RE = re.compile(r"\b(pip3?|uv pip|conda|mamba|micromamba|apt-get|apt)\b[^\n]*\binstall\b", re.I)
ERRLINE_RE = re.compile(r"^.*(?:\w+(?:Error|Exception)\b:?|command not found|No such file or directory|Killed|"
                        r"\berror:|FAILED|fatal).*$", re.M | re.I)
GENERIC = {"python", "python3", "bash", "sh", "cd", "pip", "pip3", "env", "time", "echo", "cat", "ls", "set", "source",
           "export", "uv", "sudo", "timeout", "nohup", "&&", "||", "-c", "-m", "-q", "-y"}
PIP_NAMES = {"bff": "bfflearn", "sklearn": "scikit-learn", "yaml": "pyyaml", "cv2": "opencv-python",
             "openmm": "openmm", "MDAnalysis": "MDAnalysis", "mdtraj": "mdtraj", "emcee": "emcee"}
MAX_LESSONS = 8


def short_cmd(cmd: str, n: int = 90) -> str:
    c = cmd.strip().splitlines()[0] if cmd.strip() else ""
    c = re.sub(r"^\s*cd\s+\S+\s*&&\s*", "", c)
    c = re.sub(r"\s+", " ", c).strip()
    return c if len(c) <= n else c[: n - 1] + "…"


def err_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    hits = [ln for ln in lines if ERRLINE_RE.match(ln)]
    ln = (hits or lines or [""])[-1]
    return ln if len(ln) <= 160 else ln[:159] + "…"


def _sig(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+(\.\d+)?|'[^']*'|\"[^\"]*\"|/\S+", "#", text.lower())).strip()[:100]


def _tokens(cmd: str) -> set[str]:
    """Programs and files a command uses (not the folder it cd's into, flags or bare numbers)."""
    out = set()
    for w in re.findall(r"[\w./+-]+", short_cmd(cmd, 10_000)):
        base = w.rsplit("/", 1)[-1]
        if base in GENERIC or len(base) < 3 or base.startswith("-") or re.fullmatch(r"[\d.]+", base):
            continue
        out.add(base)
    return out


def _dur(s: Optional[float]) -> str:
    if s is None:
        return ""
    return f"{s:.0f} s" if s < 120 else f"{s / 60:.1f} min"


def _hw(run: Run) -> str:
    return run.hardware or "the default Modal sandbox"


def _install_fix(failed: Call, later: list[Call], name: str) -> Optional[Call]:
    """What made a missing module/command work: the install between the failure and the first later success that
    uses it; else that success itself (e.g. the venv's python instead of the system one)."""
    toks = _tokens(failed.command) | {name}
    pkg = PIP_NAMES.get(name, name).lower()
    for j, x in enumerate(later):
        if x.failed or short_cmd(x.command) == short_cmd(failed.command):
            continue
        if INSTALL_RE.search(x.command) and (pkg in x.command.lower() or name.lower() in x.command.lower()):
            return x
        if toks & _tokens(x.command):
            installs = [y for y in later[:j] if not y.failed and INSTALL_RE.search(y.command)]
            return installs[-1] if installs else x
    return None


def _bash(calls: list[Call]) -> list[tuple[int, Call]]:
    return [(i, c) for i, c in enumerate(calls) if c.command]


def _cand(run: Run, step: Step, text: str, kind: str, conf: float, key: str, t: Optional[float] = None,
          needle: str = "", types: tuple = (), replace: bool = False) -> dict:
    return {"task_id": run.task_id, "run_id": run.id, "lesson": text, "kind": kind, "confidence": conf,
            "key": key, "evidence": [evidence(run, step.key, t, needle, types)], "source": "rules",
            "replace": replace}


# ── rules ───────────────────────────────────────────────────────────────────


def check_rules(run: Run, step: Step) -> list[dict]:
    out = []
    passed = [a["n"] for a in step.attempts if a.get("passed")]
    for ck in step.checks:
        if ck.rc == 0:
            continue
        lines = [ln.strip()[2:].strip() if ln.strip().startswith("- ") else ln.strip()
                 for ln in ck.text.splitlines()]
        problems = [ln for ln in lines if ln and not re.match(r"^(FAILED:?|ok|check `)", ln)][:2]
        fixed = next((n for n in passed if n > ck.attempt), None)
        for p in problems:
            p = p if len(p) <= 220 else p[:219] + "…"
            tail = f"; attempt {fixed} passed after fixing it" if fixed else ""
            text = f"Check before finishing: {p.rstrip('.')} (the `{step.key}` check rejected attempt {ck.attempt} for this{tail})."
            out.append(_cand(run, step, text, "avoid", 0.85 if fixed else 0.75,
                             f"check:{step.key.split('[')[0]}:{_sig(p)[:70]}", ck.t, "check", ("error",)))
    return out


def failure_rules(run: Run, step: Step) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for tr in step.trials:
        bash = _bash(tr.calls)
        fails: dict[str, int] = {}
        for pos, (i, c) in enumerate(bash):
            if not c.failed or INFRA_RE.search(c.output or ""):
                continue
            later = [x for _, x in bash[pos + 1:]]
            line = err_line(c.output)
            mm = MODULE_RE.search(c.output or "")
            nf = NOTFOUND_RE.search(c.output or "")
            same = sum(1 for x in later if short_cmd(x.command) == short_cmd(c.command) and x.failed)
            sig = _sig(line)
            fails[sig] = fails.get(sig, 0) + 1
            if mm or nf:
                name = mm.group(1).split(".")[0] if mm else (nf.group(1) or nf.group(2))
                key = f"missing:{name}"
                if key in seen:
                    continue
                seen.add(key)
                fix = _install_fix(c, later, name)
                what = f"Python module `{name}`" if mm else f"`{name}`"
                where = "is not importable" if mm else "is not on PATH"
                if fix is not None:
                    is_install = bool(INSTALL_RE.search(fix.command))
                    took = f" (took {_dur(fix.seconds)})" if is_install and fix.seconds and fix.seconds >= 5 else ""
                    how = "installed it" if is_install else "worked"
                    text = (f"{what} {where} in the sandbox with `{short_cmd(c.command, 60)}`; "
                            f"`{short_cmd(fix.command)}` {how}{took}. Do that first.")
                    out.append(_cand(run, step, text, "setting", 0.85, key, c.t, name, ("calc", "status", "error")))
                else:
                    text = (f"{what} {where} in the sandbox (`{short_cmd(c.command, 60)}` → {line}); "
                            f"install it before the first command that needs it.")
                    out.append(_cand(run, step, text, "setting", 0.7, key, c.t, name, ("calc", "status", "error")))
                continue
            key = f"fail:{sig}"
            if key in seen:
                continue
            toks = _tokens(c.command)
            fix = next((x for x in later if not x.failed and toks & _tokens(x.command)
                        and short_cmd(x.command) != short_cmd(c.command)), None)
            if fix is not None:
                seen.add(key)
                text = f"`{short_cmd(c.command, 70)}` failed with “{line}”; `{short_cmd(fix.command)}` worked instead."
                out.append(_cand(run, step, text, "do", 0.8, key, c.t, "", ("calc", "status", "error")))
            elif same:
                seen.add(key)
                text = (f"Don't re-run `{short_cmd(c.command, 70)}` unchanged after it fails with “{line}”: it failed "
                        f"again {same} more time(s). Read the error and change the command or the input first.")
                out.append(_cand(run, step, text, "avoid", 0.7, f"repeat:{sig}", c.t, "", ("calc", "status", "error")))
            elif not step.status == "ok":
                seen.add(key)
                text = f"`{short_cmd(c.command, 70)}` failed with “{line}” and the step did not recover from it."
                out.append(_cand(run, step, text, "avoid", 0.55, key, c.t, "", ("calc", "status", "error")))
    return out


def limit_rules(run: Run, step: Step) -> list[dict]:
    out = []
    texts = [step.error] + [tr.exception for tr in step.trials]
    t_fail = max([tr.finished or 0 for tr in step.trials if tr.exception] or [step.finished or 0]) or None
    passed_n = {a["n"] for a in step.attempts if a.get("passed")}
    passed_trial = next((tr for tr in reversed(step.trials) if not tr.exception
                         and (tr.attempt in passed_n or (tr.reward or 0) >= 1)), None)
    if any(TIMEOUT_RE.search(x or "") and not INFRA_RE.search(x or "") for x in texts):
        bad = next((tr for tr in step.trials if TIMEOUT_RE.search(tr.exception or "")), None)
        took = _dur(bad.seconds if bad else step.seconds)
        tail = ""
        if passed_trial:
            tail = f"; attempt {passed_trial.attempt} passed in {_dur(passed_trial.seconds)}"
            stuck = next((c for c in reversed(bad.calls) if c.command), None) if bad else None
            if stuck is not None:
                redo = next((c for c in passed_trial.calls if c.command and not c.failed
                             and _tokens(c.command) & _tokens(stuck.command)
                             and short_cmd(c.command) != short_cmd(stuck.command)), None)
                if redo is not None:
                    tail += f" with `{short_cmd(redo.command, 80)}` instead of `{short_cmd(stuck.command, 80)}`"
        text = (f"`{step.key}` hit its time limit after {took} on {_hw(run)}{tail}. Shrink the problem "
                f"(fewer samples/iterations, smaller system) or ask the planner for more time or faster hardware.")
        out.append(_cand(run, step, text, "setting", 0.8, f"timeout:{step.key.split('[')[0]}", t_fail, "",
                         ("error",), replace=True))
    oom = [c for tr in step.trials for c in tr.calls if c.failed and OOM_RE.search(c.output or "")]
    if oom or any(OOM_RE.search(x or "") for x in texts):
        c = oom[0] if oom else None
        what = f"`{short_cmd(c.command, 60)}`" if c else f"`{step.key}`"
        text = (f"{what} ran out of memory on {_hw(run)}. Use hardware with more memory (or a GPU) or reduce "
                f"the system size before running it.")
        out.append(_cand(run, step, text, "setting", 0.75, f"oom:{step.key.split('[')[0]}", c.t if c else t_fail,
                         "", ("calc", "error"), replace=True))
    return out


def slow_rules(run: Run, step: Step) -> list[dict]:
    if step.status != "ok":
        return []
    out = []
    for tr in step.trials:
        if (tr.reward or 0) < 1 and tr is not step.trials[-1]:
            continue
        total = tr.seconds or sum(c.seconds or 0 for c in tr.calls) or 0
        slow = sorted((c for c in tr.calls if c.command and not c.failed and not INSTALL_RE.search(c.command)
                       and (c.seconds or 0) >= 30
                       and ((c.seconds or 0) >= 120 or (total and (c.seconds or 0) >= 0.3 * total))),
                      key=lambda c: -(c.seconds or 0))[:2]
        for c in slow:
            label = c.description or short_cmd(c.command, 70)
            pct = f" ({100 * (c.seconds or 0) / total:.0f}% of the agent's time)" if total else ""
            text = (f"“{label}” takes about {_dur(c.seconds)} on {_hw(run)}{pct}; budget for it, run it once and "
                    f"reuse its output instead of re-running it.")
            out.append(_cand(run, step, text, "setting", 0.6, f"slow:{_sig(label)[:60]}", c.t, "", ("calc",),
                             replace=True))
    return out


def baseline_rule(run: Run, history: list[dict]) -> list[dict]:
    """The best passing run so far, as a reference for the planner (one keyed lesson per task, kept current)."""
    if not run.ok or run.wall_s is None:
        return []
    others = [h for h in history if h.get("ok") and h.get("run_id") != run.id and h.get("wall_s")]
    if any(h["wall_s"] < run.wall_s for h in others):
        return []
    total = run.costs.get("total_usd")
    calls = sum(len(tr.calls) for s in run.agent_steps for tr in s.trials)
    agent = ", ".join(f"{s.key} {_dur(s.seconds)}" for s in run.agent_steps if s.seconds)
    cost = f", ${total:.2f}" if isinstance(total, (int, float)) else ""
    text = (f"Fastest passing run so far: {_dur(run.wall_s)} end to end ({agent}; {calls} tool calls{cost}) on "
            f"{_hw(run)}. Plan time and budget from it.")
    step = run.agent_steps[-1] if run.agent_steps else (run.steps[-1] if run.steps else None)
    if step is None:
        return []
    return [_cand(run, step, text, "setting", 0.7, "baseline", run.finished, "", ("result",), replace=True)]


def extract(run: Run, history: Optional[list[dict]] = None, limit: int = MAX_LESSONS) -> list[dict]:
    cands: list[dict] = []
    for step in run.agent_steps:
        if INFRA_RE.search(step.error or "") and not step.trials:
            continue
        cands += check_rules(run, step)
        cands += failure_rules(run, step)
        cands += limit_rules(run, step)
        cands += slow_rules(run, step)
    cands += baseline_rule(run, history or [])
    # strongest first, one per key
    seen, out = set(), []
    for c in sorted(cands, key=lambda c: -c["confidence"]):
        if c["key"] in seen:
            continue
        seen.add(c["key"])
        out.append(c)
    return out[:limit]
