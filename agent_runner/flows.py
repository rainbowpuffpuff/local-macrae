"""Flow engine: a mesh of agent runs (through Harbor) and deterministic script steps.

A flow is YAML:

    name: fix-modules
    workdir: ~/proj                  # relative paths resolve here (default: the flow file's folder)
    concurrency: 6                   # steps running at once (default from settings)
    vars: {target: src}
    defaults: {agent: claude-code, account: auto, model: ""}
    steps:
      - id: list                     # script step: runs on this machine
        run: python3 list_modules.py {{ vars.target }}
        outputs: json                # stdout parsed as json / lines / text
      - id: fix                      # agent step: harbor exec in a container
        foreach: "{{ list.output }}" # one patch per item, run concurrently
        path: "{{ item }}"
        instruction: Make the tests in {{ item }} pass.
        retry: {max: 2, until: "reward >= 1", escalate: [{model: claude-opus-5-5}]}
      - id: report
        when: "{{ any(not f.ok for f in fix) }}"
        run: echo "{{ [f.error for f in fix if not f.ok] }}"

Step kinds: `run:` (shell), `instruction:` (harbor exec), `task:`/`dataset:` (harbor run).
Dependencies come from `needs:` plus every step id mentioned in a {{ }} expression.
Every result has: status ok failed skipped cancelled, ok, output, error, attempts, and for agent
steps reward, rewards, account, job_dir, trials, artifacts (artifacts/app), files (the copied
folder after the agent's changes), cost_usd. A foreach step's result is a list of these.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import yaml

from . import events, harbor, pool
from .config import RUNS_DIR, jobs_dir, load_settings

ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TPL_RE = re.compile(r"\{\{(.*?)\}\}", re.S)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
REPO_ROOT = Path(__file__).resolve().parent.parent  # so `python -m agent_runner` works from a checkout too
STEP_KEYS = {"id", "needs", "when", "foreach", "run", "instruction", "task", "dataset", "path", "paths",
             "agent", "model", "account", "attempts", "image", "workdir", "artifacts", "scan", "retry",
             "outputs", "always", "cwd", "timeout", "agent_kwargs", "skills", "tasks", "n_tasks", "env",
             "concurrency", "description", "check"}


DICT_METHODS = {n for n in dir(dict) if not n.startswith("_")}


class AttrDict(dict):
    """dict with attribute access, so expressions can say fix.reward or fix['reward']."""

    def __getattr__(self, k: str) -> Any:
        try:
            return self[k]
        except KeyError:
            return None


def _wrap(v: Any) -> Any:
    if isinstance(v, dict) and not isinstance(v, AttrDict):
        return AttrDict({k: _wrap(x) for k, x in v.items()})
    if isinstance(v, list):
        return [_wrap(x) for x in v]
    return v


SAFE = {n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n) for n in (
    "len", "sum", "min", "max", "any", "all", "sorted", "round", "abs", "str", "int", "float", "bool",
    "list", "dict", "set", "tuple", "zip", "enumerate", "range", "isinstance", "reversed", "map", "filter")}
SAFE.update({"json": json, "re": re, "Path": Path, "None": None, "True": True, "False": False})


def evaluate(expr: str, ctx: dict) -> Any:
    return eval(expr.strip(), {"__builtins__": SAFE}, ctx)  # flows are the user's own files


def render(value: Any, ctx: dict) -> Any:
    """Fill {{ }} in strings (recursively). A string that is exactly one {{ }} keeps the value's type."""
    if isinstance(value, str):
        m = TPL_RE.fullmatch(value.strip())
        if m:
            return evaluate(m.group(1), ctx)
        return TPL_RE.sub(lambda mm: _to_text(evaluate(mm.group(1), ctx)), value)
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    return value


def _to_text(v: Any) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return "" if v is None else str(v)


def _names_in(expr: str) -> set[str]:
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def _exprs(value: Any) -> list[str]:
    if isinstance(value, str):
        return TPL_RE.findall(value)
    if isinstance(value, list):
        return [e for v in value for e in _exprs(v)]
    if isinstance(value, dict):
        return [e for v in value.values() for e in _exprs(v)]
    return []


def step_kind(s: dict) -> str:
    if "run" in s:
        return "run"
    if "task" in s or "dataset" in s:
        return "task"
    return "agent"


# ── loading & validating ────────────────────────────────────────────────────


class FlowError(Exception):
    pass


def load_flow(text_or_path: str | Path) -> tuple[dict, Path]:
    if isinstance(text_or_path, Path) or (isinstance(text_or_path, str) and "\n" not in text_or_path
                                          and Path(text_or_path).expanduser().is_file()):
        p = Path(text_or_path).expanduser().resolve()
        text, base = p.read_text(), p.parent
    else:
        text, base = str(text_or_path), Path.cwd()
    try:
        spec = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise FlowError(f"YAML: {e}") from e
    if not isinstance(spec, dict):
        raise FlowError("flow must be a mapping with name/steps")
    return spec, base


def analyze(spec: dict) -> tuple[list[dict], dict[str, set[str]], list[str]]:
    """Normalized steps, deps per step id, and problems (empty list = valid)."""
    problems: list[str] = []
    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        return [], {}, ["no steps"]
    ids: list[str] = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            problems.append(f"step {i + 1} is not a mapping")
            continue
        sid = str(s.get("id", ""))
        if not ID_RE.match(sid):
            problems.append(f"step {i + 1}: id {sid!r} must be a python-style name (letters, digits, _)")
        if sid in ids:
            problems.append(f"duplicate step id {sid}")
        ids.append(sid)
        unknown = set(s) - STEP_KEYS
        if unknown:
            problems.append(f"{sid}: unknown keys {sorted(unknown)}")
        k = step_kind(s)
        if k == "agent" and not s.get("instruction"):
            problems.append(f"{sid}: needs one of run / instruction / task / dataset")
        for e in _exprs(s) + [x for x in (s.get("when"), s.get("foreach")) if isinstance(x, str)
                               and not TPL_RE.search(x)]:
            try:
                ast.parse(e.strip(), mode="eval")
            except SyntaxError as err:
                problems.append(f"{sid}: bad expression {{{{ {e.strip()} }}}}: {err.msg}")
        r = s.get("retry")
        if r is not None and not isinstance(r, (int, dict)):
            problems.append(f"{sid}: retry must be a number or {{max, until, escalate}}")
    clash = sorted(k for k in (spec.get("vars") or {}) if k in DICT_METHODS)
    if clash:
        problems.append(f"vars {clash} share a name with a dict method, so vars.<name> would mean the method; "
                        "rename them (vars['name'] also works)")
    idset = set(ids)
    deps: dict[str, set[str]] = {}
    for s in steps:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id", ""))
        d = set(s.get("needs") or [])
        exprs = _exprs(s)
        for key in ("when", "foreach"):
            if isinstance(s.get(key), str) and not TPL_RE.search(s[key]):
                exprs.append(s[key])
        for e in exprs:
            d |= _names_in(e) & idset
        r = s.get("retry")
        if isinstance(r, dict) and isinstance(r.get("until"), str):
            d |= _names_in(r["until"]) & idset
        d.discard(sid)
        for x in d - idset:
            problems.append(f"{sid}: needs unknown step {x}")
        deps[sid] = d & idset
    # cycles
    state: dict[str, int] = {}

    def visit(n: str, path: list[str]) -> None:
        if state.get(n) == 1:
            problems.append("cycle: " + " → ".join(path + [n]))
            return
        if state.get(n) == 2:
            return
        state[n] = 1
        for m in deps.get(n, ()):
            visit(m, path + [n])
        state[n] = 2

    for n in deps:
        visit(n, [])
    return [s for s in steps if isinstance(s, dict)], deps, problems


def levels(deps: dict[str, set[str]]) -> list[list[str]]:
    """Steps grouped by depth, for showing the mesh."""
    depth: dict[str, int] = {}

    def d(n: str, seen: frozenset = frozenset()) -> int:
        if n in depth:
            return depth[n]
        if n in seen:
            return 0
        depth[n] = 0 if not deps.get(n) else 1 + max(d(m, seen | {n}) for m in deps[n])
        return depth[n]

    for n in deps:
        d(n)
    out: list[list[str]] = []
    for n, k in sorted(depth.items(), key=lambda kv: kv[1]):
        while len(out) <= k:
            out.append([])
        out[k].append(n)
    return out


# ── running ─────────────────────────────────────────────────────────────────


def _now() -> float:
    return time.time()


def _sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")[:60] or "step"


class FlowRun:
    def __init__(self, spec: dict, base: Path, run_id: Optional[str] = None, vars_: Optional[dict] = None,
                 flow_file: Optional[Path] = None):
        self.spec = spec
        self.steps, self.deps, problems = analyze(spec)
        if problems:
            raise FlowError("; ".join(problems))
        self.name = str(spec.get("name") or (flow_file.stem if flow_file else "flow"))
        self.id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{_sanitize(self.name)}-{uuid.uuid4().hex[:4]}"
        self.dir = RUNS_DIR / self.id
        self.logs = self.dir / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        wd = Path(os.path.expanduser(str(spec.get("workdir") or ".")))
        self.workdir = (wd if wd.is_absolute() else base / wd).resolve()
        self.jobs = jobs_dir() / self.id
        settings = load_settings()
        self.sem = asyncio.Semaphore(int(spec.get("concurrency") or settings.get("max_concurrent", 8)))
        self.defaults = {"agent": settings.get("default_agent", "claude-code"),
                         "model": settings.get("default_model", ""), "account": "auto"}
        self.defaults.update(spec.get("defaults") or {})
        self.vars = AttrDict(dict(spec.get("vars") or {}, **(vars_ or {})))
        self.results: dict[str, Any] = {}
        self.events = {s["id"]: asyncio.Event() for s in self.steps}
        self.state: dict[str, Any] = {
            "id": self.id, "name": self.name, "file": str(flow_file) if flow_file else "",
            "status": "running", "started": _now(), "finished": None, "pid": os.getpid(),
            "workdir": str(self.workdir), "jobs_dir": str(self.jobs), "vars": dict(self.vars),
            "levels": levels(self.deps), "steps": {}, "order": [],
        }
        (self.dir / "flow.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
        self.save()

    # state
    def save(self) -> None:
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps(self.state, indent=1, default=str))
        tmp.replace(self.dir / "state.json")

    def mark(self, key: str, **kw: Any) -> None:
        st = self.state["steps"].setdefault(key, {"key": key})
        if key not in self.state["order"]:
            self.state["order"].append(key)
        st.update(kw)
        self.save()

    def log(self, key: str, msg: str) -> None:
        with open(self.logs / f"{_sanitize(key)}.log", "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

    def ctx(self, **extra: Any) -> dict:
        c: dict[str, Any] = {"vars": self.vars}
        c.update(self.results)
        c.update(extra)
        return c

    # main
    async def run(self) -> str:
        events.log("run_begin", run_id=self.id, name=self.name, steps=len(self.steps), file=self.state["file"])
        tasks = [asyncio.create_task(self._step(s)) for s in self.steps]
        try:
            await asyncio.gather(*tasks)
            failed = [k for k, v in self.state["steps"].items() if v.get("status") == "failed"]
            self.state["status"] = "failed" if failed else "ok"
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.state["status"] = "cancelled"
        except Exception as e:  # engine bug: record it, don't lose the state
            self.state["status"] = "failed"
            self.state["error"] = repr(e)
        self.state["finished"] = _now()
        self.save()
        sts = [v.get("status") for v in self.state["steps"].values()]
        events.log("run_end", run_id=self.id, name=self.name, status=self.state["status"],
                   seconds=round(self.state["finished"] - self.state["started"], 1),
                   counts={k: sts.count(k) for k in set(sts)}, error=self.state.get("error"))
        return self.state["status"]

    async def _step(self, s: dict) -> None:
        sid = s["id"]
        try:
            for d in self.deps[sid]:
                await self.events[d].wait()
            dep_bad = [d for d in self.deps[sid] if not _all_ok(self.results.get(d))]
            if dep_bad and not s.get("always"):
                self.results[sid] = AttrDict(status="skipped", ok=False, error=f"upstream not ok: {dep_bad}")
                self.mark(sid, id=sid, kind=step_kind(s), status="skipped", note=f"upstream: {', '.join(dep_bad)}")
                return
            ctx = self.ctx()
            if "when" in s:
                w = s["when"]
                cond = render(w, ctx) if isinstance(w, str) and TPL_RE.search(w) else (
                    evaluate(w, ctx) if isinstance(w, str) else w)
                if not cond:
                    self.results[sid] = AttrDict(status="skipped", ok=False, output=None, error="when was false")
                    self.mark(sid, id=sid, kind=step_kind(s), status="skipped", note="when → false")
                    return
            if "foreach" in s:
                f = s["foreach"]
                items = render(f, ctx) if isinstance(f, str) and TPL_RE.search(f) else (
                    evaluate(f, ctx) if isinstance(f, str) else f)
                if isinstance(items, dict):
                    items = list(items.items())
                items = list(items or [])
                self.mark(sid, id=sid, kind=step_kind(s), status="running", fanout=len(items))
                subs = [self._instance(s, f"{sid}[{i}]", item=it, index=i) for i, it in enumerate(items)]
                res = await asyncio.gather(*subs)
                self.results[sid] = res
                ok = all(r.ok for r in res)
                self.mark(sid, status="ok" if ok else "failed", finished=_now(),
                          note=f"{sum(r.ok for r in res)}/{len(res)} ok")
            else:
                self.results[sid] = await self._instance(s, sid)
        except asyncio.CancelledError:
            self.results.setdefault(sid, AttrDict(status="cancelled", ok=False))
            self.mark(sid, id=sid, status="cancelled", finished=_now())
            raise
        except Exception as e:
            self.results[sid] = AttrDict(status="failed", ok=False, error=f"{type(e).__name__}: {e}")
            self.mark(sid, id=sid, kind=step_kind(s), status="failed", error=f"{type(e).__name__}: {e}",
                      finished=_now())
        finally:
            self.events[sid].set()

    async def _instance(self, s: dict, key: str, item: Any = None, index: Optional[int] = None) -> AttrDict:
        kind = step_kind(s)
        r = s.get("retry")
        retry = {"max": r} if isinstance(r, int) else dict(r or {})
        max_extra = int(retry.get("max", 0))
        until = retry.get("until")
        escalate = list(retry.get("escalate") or [])
        feedback = retry.get("feedback", True)
        self.mark(key, id=s["id"], kind=kind, status="queued", item=_short(item), queued=_now())
        self.log(key, f"queued ({kind})")
        prev: Optional[AttrDict] = None
        res = AttrDict(status="failed", ok=False)
        for attempt in range(1, max_extra + 2):
            overrides = escalate[attempt - 2] if attempt >= 2 and attempt - 2 < len(escalate) else {}
            step = dict(s, **overrides)
            ctx = self.ctx(item=_wrap(item), index=index, attempt=attempt, prev=prev)
            async with self.sem:
                self.mark(key, status="running", attempt=attempt, started=_now())
                if kind == "run":
                    res = await self._run_script(step, key, ctx, attempt, prev)
                else:
                    res = await self._run_harbor(step, key, ctx, attempt, prev if feedback else None, kind)
            res["attempts"] = attempt
            passed = res.ok
            if until and res.status not in ("cancelled",):
                try:
                    passed = bool(evaluate(until, self.ctx(this=res, item=_wrap(item), index=index, **res)))
                except Exception as e:
                    self.log(key, f"until {until!r} raised {e}")
                    passed = False
            self.log(key, f"attempt {attempt}: {res.status} reward={res.get('reward')} passed={passed}")
            if passed:
                res["ok"] = True
                res["status"] = "ok"
                break
            res["ok"] = False
            if res.status == "ok":
                res["status"] = "failed"
                res["error"] = res.get("error") or f"until not met: {until}"
            prev = res
            if attempt <= max_extra:
                self.mark(key, status="retrying", attempt=attempt, error=res.get("error", ""))
        events.log("step_end", run_id=self.id, step=key, kind=kind, status=res.status, attempts=res.get("attempts"),
                   account=res.get("account"), reward=res.get("reward"), cost_usd=res.get("cost_usd"),
                   seconds=round(res.get("duration") or 0, 1), error=(res.get("error") or "")[:300])
        self.mark(key, status=res.status, finished=_now(), error=res.get("error", ""),
                  reward=res.get("reward"), account=res.get("account"), job_dir=res.get("job_dir"),
                  cost_usd=res.get("cost_usd"), output=_short(res.get("output"), 400))
        return res

    async def _run_script(self, s: dict, key: str, ctx: dict, attempt: int, prev: Optional[AttrDict]) -> AttrDict:
        cmd = render(s["run"], ctx)
        cwd = Path(os.path.expanduser(str(render(s.get("cwd") or self.workdir, ctx))))
        ctx_file = self.dir / "context" / f"{_sanitize(key)}.json"
        ctx_file.parent.mkdir(parents=True, exist_ok=True)
        ctx_file.write_text(json.dumps({k: v for k, v in ctx.items() if k != "prev"}, default=str, indent=1))
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
        env.update({"FLOW_RUN_DIR": str(self.dir), "FLOW_JOBS_DIR": str(self.jobs), "FLOW_STEP": key,
                    "FLOW_ATTEMPT": str(attempt), "FLOW_CONTEXT": str(ctx_file),
                    "FLOW_ITEM": json.dumps(ctx.get("item"), default=str)})
        env.update({k: str(render(v, ctx)) for k, v in (s.get("env") or {}).items()})
        out_file = self.dir / "outputs" / f"{_sanitize(key)}-a{attempt}.txt"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        self.log(key, f"$ {cmd}  (cwd {cwd})")
        t0 = _now()
        with open(out_file, "wb") as out:
            proc = await asyncio.create_subprocess_shell(cmd, cwd=str(cwd), env=env, stdout=out,
                                                         stderr=asyncio.subprocess.PIPE, start_new_session=True,
                                                         stdin=asyncio.subprocess.DEVNULL)
            try:
                timeout = s.get("timeout")
                _, err = await asyncio.wait_for(proc.communicate(), float(timeout) if timeout else None)
            except asyncio.CancelledError:
                _killpg(proc.pid)
                raise
            except asyncio.TimeoutError:
                _killpg(proc.pid)
                await proc.wait()
                err = b"timeout"
        stdout = out_file.read_text(errors="replace")
        if err:
            self.log(key, "stderr: " + err.decode(errors="replace")[-3000:])
        mode = s.get("outputs", "auto")
        output: Any = stdout.strip()
        if mode in ("json", "auto"):
            try:
                output = _wrap(json.loads(stdout))
            except ValueError:
                if mode == "json":
                    return AttrDict(status="failed", ok=False, rc=proc.returncode, stdout=stdout,
                                    error="output is not valid JSON", output=None, duration=_now() - t0)
        elif mode == "lines":
            output = [ln for ln in stdout.splitlines() if ln.strip()]
        ok = proc.returncode == 0
        return AttrDict(status="ok" if ok else "failed", ok=ok, rc=proc.returncode, output=output, stdout=stdout,
                        error="" if ok else f"exit {proc.returncode}: "
                                            f"{ANSI_RE.sub('', (err or b'').decode(errors='replace'))[-300:]}",
                        duration=_now() - t0)

    async def _run_harbor(self, s: dict, key: str, ctx: dict, attempt: int, prev: Optional[AttrDict],
                          kind: str) -> AttrDict:
        agent = str(render(s.get("agent") or self.defaults["agent"], ctx))
        model = str(render(s.get("model") if "model" in s else self.defaults.get("model", ""), ctx) or "")
        want = str(render(s.get("account") or self.defaults.get("account", "auto"), ctx))
        reroutes = 0
        while True:
            lease = None
            if agent == "claude-code" and want != "none":
                self.mark(key, status="waiting-account")
                lease = await pool.acquire(want, on_wait=lambda: self.log(key, "waiting for a free account"))
                self.mark(key, status="running", account=lease.account)
            account = lease.account if lease else None
            try:
                res = await self._harbor_once(s, key, ctx, attempt, prev, kind, agent, model, account, reroutes)
            finally:
                if lease:
                    lease.release()
            if res.get("auth_failed") and account:
                pool.set_cooldown(account, _now() + 7 * 86400, "token rejected (401) — paste a new token")
                events.log("auth_failed", run_id=self.id, step=key, account=account)
                self.log(key, f"{account}: token rejected; parked until a new token is saved")
                if want == "auto" and reroutes < 5:
                    reroutes += 1
                    continue
                return res
            if res.get("limit_hit") and account and want == "auto" and reroutes < 5:
                until = res.get("limit_reset") or _now() + 30 * 60
                pool.set_cooldown(account, until, f"limit hit in {self.id}/{key}")
                events.log("limit_hit", run_id=self.id, step=key, account=account, until=until, rerouted=True)
                self.log(key, f"{account} hit its limit; cooling until {time.strftime('%H:%M', time.localtime(until))}, "
                              "re-running on another account")
                reroutes += 1
                continue
            if res.get("limit_hit") and account:
                pool.set_cooldown(account, res.get("limit_reset") or _now() + 30 * 60, f"limit hit in {self.id}/{key}")
            return res

    async def _harbor_once(self, s: dict, key: str, ctx: dict, attempt: int, prev: Optional[AttrDict], kind: str,
                           agent: str, model: str, account: Optional[str], reroute: int) -> AttrDict:
        job_name = f"{_sanitize(key)}-a{attempt}" + (f"-r{reroute}" if reroute else "")
        common = dict(agent=agent, model=model, jobs_dir=self.jobs, job_name=job_name,
                      attempts=int(s.get("attempts") or 1), agent_kwargs=render(s.get("agent_kwargs") or {}, ctx))
        paths: list[str] = []
        if kind == "agent":
            instruction = str(render(s["instruction"], ctx))
            if prev is not None:
                instruction += _feedback(prev)
            rp = render(s.get("paths") or s.get("path") or [], ctx)
            paths = [str(p) for p in (rp if isinstance(rp, list) else [rp]) if p]
            arts = render(s.get("artifacts") or [], ctx)
            if not arts and paths:
                # bring back each whole input folder; Harbor's own guess only takes paths named in the instruction
                base = str(render(s.get("workdir") or "", ctx)) or "/app"
                arts = [f"{base.rstrip('/')}/{Path(p).name}" for p in paths]
            image = str(render(s.get("image") or "", ctx))
            if not image:
                default_image = load_settings().get("agent_image") or ""
                if default_image and harbor.image_exists(default_image):
                    image = default_image
            cmd = harbor.exec_cmd(paths=paths, instruction=instruction, image=image,
                                  workdir=str(render(s.get("workdir") or "", ctx)), scan=bool(s.get("scan")),
                                  artifacts=arts if isinstance(arts, list) else [arts],
                                  agent_timeout=s.get("timeout"), **common)
        else:
            tp = render(s.get("task") or "", ctx)
            if tp:
                tp = str(Path(os.path.expanduser(str(tp))) if os.path.isabs(os.path.expanduser(str(tp)))
                         else (self.workdir / str(tp)))
            cmd = harbor.run_cmd(task_path=tp, dataset=str(render(s.get("dataset") or "", ctx)),
                                 task_names=render(s.get("tasks") or [], ctx), n_tasks=s.get("n_tasks"),
                                 n_concurrent=int(s.get("concurrency") or 1),
                                 skills=render(s.get("skills") or [], ctx), **common)
        env = harbor.harbor_env(agent, account)
        log_path = self.logs / f"{_sanitize(key)}.log"
        shown = [c if len(c) < 200 else c[:200] + "…" for c in cmd]
        self.log(key, f"harbor ({account or 'no account'}): {' '.join(shown)}")
        job_dir = self.jobs / job_name
        self.mark(key, job_dir=str(job_dir), account=account)
        t0 = _now()
        rc = await harbor.run_process(cmd, env, log_path, cwd=self.workdir)
        trials = harbor.read_trials(job_dir) if job_dir.is_dir() else []
        rewards = [t.reward for t in trials if t.reward is not None]
        errors = [t.exception for t in trials if t.exception]
        first = trials[0] if trials else None
        files = None
        if first and kind == "agent":
            app = first.artifacts_app
            cands = [app / Path(p).name for p in paths]
            files = next((str(c) for c in cands if c.exists()), str(app) if app.exists() else None)
        ok = rc == 0 and bool(trials) and not errors
        verifier = harbor.tail(first.dir / "verifier" / "test-stdout.txt", 3000) if first else ""
        reward = (sum(rewards) / len(rewards)) if rewards else None
        if ok and s.get("check") and files:
            reward, verifier = await self._check(s, key, ctx, files)
        if errors:
            error = "; ".join(errors)[:600]
        elif rc != 0:
            last = harbor.tail(log_path, 600).strip().splitlines()
            error = f"harbor exit {rc}" + (f": {ANSI_RE.sub('', last[-1])[:300]}" if last else "")
        elif not trials:
            error = "harbor produced no trials"
        else:
            error = ""
        return AttrDict(
            status="ok" if ok else "failed", ok=ok, rc=rc, account=account, agent=agent, model=model,
            reward=reward, rewards=[t.rewards for t in trials],
            job_dir=str(job_dir), trials=[str(t.dir) for t in trials],
            artifacts=str(first.artifacts_app) if first else None, files=files,
            output=harbor.final_message(first.dir) if first else "",
            cost_usd=sum(t.cost_usd or 0 for t in trials) or None,
            limit_hit=any(t.limit_hit for t in trials), auth_failed=any(t.auth_failed for t in trials),
            limit_reset=max((t.limit_reset or 0 for t in trials), default=0) or None,
            verifier=verifier, error=error, duration=_now() - t0)

    async def _check(self, s: dict, key: str, ctx: dict, files: str) -> tuple[float, str]:
        """`check:` runs on this machine inside the returned folder. Exit 0 → reward 1.0. Output = feedback."""
        cmd = str(render(s["check"], ctx))
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
        env.update({"FLOW_FILES": files, "FLOW_RUN_DIR": str(self.dir), "FLOW_STEP": key})
        self.log(key, f"check $ {cmd}  (in {files})")
        proc = await asyncio.create_subprocess_shell(cmd, cwd=files, env=env, stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.STDOUT, start_new_session=True,
                                                     stdin=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 600)
        except asyncio.TimeoutError:
            _killpg(proc.pid)
            return 0.0, "check timed out after 600 s"
        except asyncio.CancelledError:
            _killpg(proc.pid)
            raise
        text = ANSI_RE.sub("", out.decode(errors="replace"))[-3000:]
        self.log(key, f"check exit {proc.returncode}: {text[-500:]}")
        return (1.0 if proc.returncode == 0 else 0.0), f"check `{cmd}` exit {proc.returncode}\n{text}"


def _killpg(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _all_ok(r: Any) -> bool:
    if r is None:
        return False
    if isinstance(r, list):
        return all(x.get("ok") or x.get("status") == "skipped" for x in r)
    return bool(r.get("ok")) or r.get("status") == "skipped"


def _short(v: Any, n: int = 160) -> Any:
    if v is None or isinstance(v, (int, float, bool)):
        return v
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s if len(s) <= n else s[:n] + "…"


def _feedback(prev: AttrDict) -> str:
    parts = [f"\n\n---\nA previous attempt at this task did not pass (reward={prev.get('reward')})."]
    if prev.get("error"):
        parts.append(f"Error: {prev['error'][:800]}")
    if prev.get("verifier"):
        parts.append("Verifier output (end):\n" + prev["verifier"][-2000:])
    if prev.get("output"):
        parts.append("Its final message was:\n" + str(prev["output"])[-1500:])
    parts.append("Take this into account and fix what failed.")
    return "\n".join(parts)


# ── run management (used by CLI and GUI) ────────────────────────────────────


def run_flow_blocking(path: Path, vars_: Optional[dict] = None, run_id: Optional[str] = None) -> str:
    spec, base = load_flow(path)

    async def main() -> str:
        fr = FlowRun(spec, base, run_id=run_id, vars_=vars_, flow_file=path)
        print(f"run {fr.id}  ({fr.dir})", flush=True)
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        try:
            return await fr.run()
        except asyncio.CancelledError:
            return fr.state["status"]

    return asyncio.run(main())


def start_detached(path: Path, vars_: Optional[dict] = None) -> str:
    """Start a flow in its own process (survives the window closing). Returns the run id."""
    spec, _ = load_flow(path)
    _, _, problems = analyze(spec)
    if problems:
        raise FlowError("; ".join(problems))
    name = str(spec.get("name") or path.stem)
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{_sanitize(name)}-{uuid.uuid4().hex[:4]}"
    (RUNS_DIR / run_id).mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "agent_runner", "flow", "run", str(path), "--run-id", run_id]
    for k, v in (vars_ or {}).items():
        cmd += ["--var", f"{k}={v}"]
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["PYTHONPATH"] = os.pathsep.join(x for x in (str(REPO_ROOT), env.get("PYTHONPATH", "")) if x)
    with open(RUNS_DIR / run_id / "engine.log", "ab") as log:
        subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL, env=env,
                         start_new_session=True, cwd=str(path.parent))
    return run_id


def write_single_flow(*, instruction: str, paths: list[str], agent: str, model: str, account: str,
                      attempts: int, workdir: str, task: str = "", dataset: str = "", retry: int = 0,
                      until: str = "", name: str = "") -> Path:
    """A quick run from the 'New run' dialog is just a one-step flow."""
    step: dict[str, Any] = {"id": "run", "agent": agent, "account": account, "attempts": attempts}
    if model:
        step["model"] = model
    if task or dataset:
        if task:
            step["task"] = task
        if dataset:
            step["dataset"] = dataset
    else:
        step["instruction"] = instruction
        step["paths"] = paths
    if retry:
        step["retry"] = {"max": retry, **({"until": until} if until else {})}
    spec = {"name": name or (f"task-{Path(task).name}" if task else (dataset or "quick-run")), "workdir": workdir,
            "steps": [step]}
    d = RUNS_DIR / "_quick"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}.yaml"
    p.write_text(yaml.safe_dump(spec, sort_keys=False))
    return p


def _pid_alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True


def list_runs() -> list[dict]:
    out = []
    if not RUNS_DIR.is_dir():
        return out
    for d in RUNS_DIR.iterdir():
        if not d.is_dir() or d.name.startswith("_"):
            continue
        try:
            st = json.loads((d / "state.json").read_text())
        except (OSError, ValueError):
            # started but engine hasn't written state yet (or died before)
            st = {"id": d.name, "name": d.name, "status": "starting", "started": d.stat().st_mtime, "steps": {},
                  "order": []}
            if time.time() - d.stat().st_mtime > 120:
                st["status"] = "crashed"
                st["error"] = harbor.tail(d / "engine.log", 800)
        if st.get("status") == "running" and not _pid_alive(st.get("pid")):
            st["status"] = "crashed"
        st["dir"] = str(d)
        out.append(st)
    out.sort(key=lambda s: s.get("started") or 0, reverse=True)
    return out


def cancel_run(run_id: str) -> bool:
    try:
        st = json.loads((RUNS_DIR / run_id / "state.json").read_text())
        os.kill(int(st["pid"]), signal.SIGTERM)
        return True
    except (OSError, ValueError, KeyError, ProcessLookupError):
        return False


EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"


def list_examples() -> list[Path]:
    return sorted(EXAMPLES_DIR.glob("*.yaml"))
