"""Harbor glue: build exec/run commands, launch with the right account, read jobs, trials and traces."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import tokens
from .config import HOME

HARBOR_BIN = shutil.which("harbor") or str(HOME / ".local/bin/harbor")

# Claude Code's limit messages ("Claude AI usage limit reached|1760000000", "You've hit your limit · resets 3pm")
AUTH_RE = re.compile(r"Invalid bearer token|Failed to authenticate|api_error_status\"?:\s*401|OAuth token has expired",
                     re.I)
LIMIT_RE = re.compile(r"usage limit reached\|?(\d{9,})?|hit your (?:usage )?limit|rate_limit_error|"
                      r"Claude AI usage limit", re.I)


def _ts(v: Any) -> Optional[float]:
    if not v:
        return None
    try:
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt.timestamp()
    except ValueError:
        return None


def _json(p: Path) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


# ── launching ───────────────────────────────────────────────────────────────

IMAGE_DIR = Path(__file__).resolve().parent.parent / "image"
_image_cache: dict[str, tuple[float, bool]] = {}


def image_exists(tag: str) -> bool:
    hit = _image_cache.get(tag)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    import subprocess
    ok = subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0
    _image_cache[tag] = (time.time(), ok)
    return ok


def build_image(tag: str) -> int:
    import subprocess
    return subprocess.run(["docker", "build", "--pull", "-t", tag, str(IMAGE_DIR)]).returncode



def harbor_env(agent: str, account: Optional[str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["PATH"] = env.get("PATH", "/usr/bin:/bin") + f":{HOME}/.local/bin"
    for k in [k for k in env if k.startswith(tokens.ENV_PREFIX)]:
        del env[k]  # other accounts' tokens never reach a run
    if agent == "claude-code" and account:
        cred = tokens.credential(account)
        if not cred:
            raise RuntimeError(f"account {account} has no login")
        kind, secret = cred
        if kind == "api_key":
            env["ANTHROPIC_API_KEY"] = secret
        else:
            env.pop("ANTHROPIC_API_KEY", None)
            env["CLAUDE_CODE_OAUTH_TOKEN"] = secret
            env["CLAUDE_FORCE_OAUTH"] = "1"
    return env


def exec_cmd(*, paths: list[str], instruction: str, agent: str, model: str = "", jobs_dir: Path,
             job_name: str, attempts: int = 1, image: str = "", workdir: str = "",
             artifacts: Optional[list[str]] = None, scan: bool = False, agent_kwargs: Optional[dict] = None,
             agent_timeout: Optional[float] = None, extra: Optional[list[str]] = None) -> list[str]:
    cmd = [HARBOR_BIN, "exec", "-a", agent, "--jobs-dir", str(jobs_dir), "--job-name", job_name,
           "-k", str(attempts), "-q", "-i", instruction]
    for p in paths:
        cmd += ["-p", p]
    cmd.append("--scan" if scan else "--no-scan")
    if model:
        cmd += ["-m", model]
    if image:
        cmd += ["--image", image]
    if workdir:
        cmd += ["--workdir", workdir]
    for a in artifacts or []:
        cmd += ["-f", a]
    for k, v in (agent_kwargs or {}).items():
        cmd += ["--ak", f"{k}={v}"]
    if agent_timeout:
        cmd += ["--agent-timeout", str(agent_timeout)]
    return cmd + list(extra or [])


def run_cmd(*, task_path: str = "", dataset: str = "", agent: str, model: str = "", jobs_dir: Path,
            job_name: str, attempts: int = 1, n_concurrent: int = 1, task_names: Optional[list[str]] = None,
            n_tasks: Optional[int] = None, agent_kwargs: Optional[dict] = None,
            skills: Optional[list[str]] = None, extra: Optional[list[str]] = None) -> list[str]:
    cmd = [HARBOR_BIN, "run", "-a", agent, "-o", str(jobs_dir), "--job-name", job_name,
           "-k", str(attempts), "-n", str(n_concurrent), "-q", "-y"]
    if task_path:
        cmd += ["-p", task_path]
    if dataset:
        cmd += ["-d", dataset]
    for t in task_names or []:
        cmd += ["-i", t]
    if n_tasks:
        cmd += ["-l", str(n_tasks)]
    if model:
        cmd += ["-m", model]
    for k, v in (agent_kwargs or {}).items():
        cmd += ["--ak", f"{k}={v}"]
    for s in skills or []:
        cmd += ["--skill", s]
    return cmd + list(extra or [])


async def run_process(cmd: list[str], env: dict[str, str], log_path: Path, cwd: Optional[Path] = None,
                      shell: bool = False) -> int:
    """Run to completion, output to log_path. On cancel: SIGINT the group (harbor cleans up containers)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        if shell:
            proc = await asyncio.create_subprocess_shell(cmd[0], stdout=log, stderr=log, env=env,
                                                         cwd=str(cwd) if cwd else None, start_new_session=True,
                                                         stdin=asyncio.subprocess.DEVNULL)
        else:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=log, stderr=log, env=env,
                                                        cwd=str(cwd) if cwd else None, start_new_session=True,
                                                        stdin=asyncio.subprocess.DEVNULL)
        try:
            return await proc.wait()
        except asyncio.CancelledError:
            for sig, wait in ((signal.SIGINT, 30), (signal.SIGTERM, 10), (signal.SIGKILL, 5)):
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    await asyncio.wait_for(proc.wait(), wait)
                    break
                except asyncio.TimeoutError:
                    continue
            raise


# ── reading jobs / trials / traces ──────────────────────────────────────────


@dataclass
class Trial:
    name: str
    dir: Path
    task: str = ""
    reward: Optional[float] = None
    rewards: dict = field(default_factory=dict)
    exception: str = ""
    agent: str = ""
    model: str = ""
    started: Optional[float] = None
    finished: Optional[float] = None
    cost_usd: Optional[float] = None
    limit_hit: bool = False
    limit_reset: Optional[float] = None
    auth_failed: bool = False

    @property
    def duration(self) -> Optional[float]:
        return self.finished - self.started if self.started and self.finished else None

    @property
    def artifacts_app(self) -> Path:
        return self.dir / "artifacts" / "app"


@dataclass
class Job:
    name: str
    dir: Path
    started: Optional[float] = None
    finished: Optional[float] = None
    n_trials: int = 0
    n_done: int = 0
    n_errors: int = 0
    n_running: int = 0
    mean_reward: Optional[float] = None
    agents: list[str] = field(default_factory=list)

    updated: Optional[float] = None

    @property
    def running(self) -> bool:
        """Unfinished and touched in the last 10 min. An unfinished job that went quiet was killed/cancelled."""
        return self.finished is None and (time.time() - (self.updated or 0)) < 600

    @property
    def stopped(self) -> bool:
        return self.finished is None and not self.running


def _limit_info(trial_dir: Path, exception: str) -> tuple[bool, Optional[float], Optional[float]]:
    """(limit_hit, reset_epoch, cost_usd) from the agent stream + exception text."""
    hit, reset, cost = False, None, None
    texts = [exception]
    stream = trial_dir / "agent" / "claude-code.txt"
    if stream.is_file():
        try:
            tail = stream.read_text(errors="replace")[-20000:]
        except OSError:
            tail = ""
        texts.append(tail)
        for line in reversed(tail.splitlines()):
            if '"type":"result"' in line.replace(" ", ""):
                try:
                    d = json.loads(line)
                    cost = d.get("total_cost_usd")
                    if d.get("is_error"):
                        texts.append(str(d.get("result", "")))
                except ValueError:
                    pass
                break
    for t in texts:
        m = LIMIT_RE.search(t or "")
        if m:
            hit = True
            if m.group(1):
                reset = float(m.group(1))
    return hit, reset, cost


def read_trial(d: Path) -> Trial:
    r = _json(d / "result.json") or {}
    t = Trial(name=d.name, dir=d, task=r.get("task_name") or "")
    vr = r.get("verifier_result") or {}
    t.rewards = vr.get("rewards") or {}
    if "reward" in t.rewards:
        try:
            t.reward = float(t.rewards["reward"])
        except (TypeError, ValueError):
            pass
    elif (d / "verifier" / "reward.txt").is_file():
        try:
            t.reward = float((d / "verifier" / "reward.txt").read_text().strip())
        except (OSError, ValueError):
            pass
    ex = r.get("exception_info") or {}
    if ex:
        t.exception = f"{ex.get('exception_type', '')}: {ex.get('exception_message', '')}".strip(": ")
    ai = r.get("agent_info") or {}
    t.agent = ai.get("name") or ""
    t.model = (ai.get("model_info") or {}).get("name") or ""
    t.started, t.finished = _ts(r.get("started_at")), _ts(r.get("finished_at"))
    t.limit_hit, t.limit_reset, t.cost_usd = _limit_info(d, t.exception)
    stream = d / "agent" / "claude-code.txt"
    t.auth_failed = bool(AUTH_RE.search(t.exception) or (stream.is_file() and AUTH_RE.search(tail(stream, 20000))))
    if t.auth_failed:
        t.exception = "Claude login rejected (401): the Harbor token for this account is wrong or expired"
    return t


def is_job_dir(d: Path) -> bool:
    return (d / "config.json").is_file() and ((d / "result.json").is_file() or (d / "lock.json").is_file())


def read_job(d: Path) -> Job:
    r = _json(d / "result.json") or {}
    j = Job(name=d.name, dir=d, started=_ts(r.get("started_at")), finished=_ts(r.get("finished_at")))
    if j.started is None:
        try:
            j.started = (d / "config.json").stat().st_mtime
        except OSError:
            pass
    try:
        j.updated = max(p.stat().st_mtime for p in d.rglob("*.log"))
    except (OSError, ValueError):
        j.updated = j.started
    st = r.get("stats") or {}
    j.n_trials = int(r.get("n_total_trials") or 0)
    j.n_done = int(st.get("n_completed_trials") or 0)
    j.n_errors = int(st.get("n_errored_trials") or 0)
    j.n_running = int(st.get("n_running_trials") or 0)
    means = []
    for name, ev in (st.get("evals") or {}).items():
        j.agents.append(name)
        for m in ev.get("metrics") or []:
            if isinstance(m, dict) and m.get("mean") is not None:
                means.append(float(m["mean"]))
    j.mean_reward = sum(means) / len(means) if means else None
    return j


def trial_dirs(job_dir: Path) -> list[Path]:
    return sorted(p for p in job_dir.iterdir() if p.is_dir() and (p / "config.json").is_file())


def read_trials(job_dir: Path) -> list[Trial]:
    return [read_trial(p) for p in trial_dirs(job_dir)]


def scan_jobs(roots: list[Path], depth: int = 2) -> list[Job]:
    """Jobs directly in each root, or one level down (flow runs keep their jobs in <jobs_dir>/<run-id>/)."""
    found: list[Job] = []

    def walk(d: Path, left: int) -> None:
        try:
            kids = [p for p in d.iterdir() if p.is_dir()]
        except OSError:
            return
        for k in kids:
            if is_job_dir(k):
                found.append(read_job(k))
            elif left > 1:
                walk(k, left - 1)

    for r in roots:
        if r.is_dir():
            walk(r, depth)
    uniq: dict[Path, Job] = {}
    for j in found:
        uniq.setdefault(j.dir.resolve(), j)
    found = list(uniq.values())
    found.sort(key=lambda j: j.started or 0, reverse=True)
    return found


def load_trajectory(trial_dir: Path) -> list[dict]:
    t = _json(trial_dir / "agent" / "trajectory.json") or {}
    return t.get("steps") or []


def final_message(trial_dir: Path) -> str:
    """Last agent message in the trajectory: the natural 'output' of an agent step."""
    for s in reversed(load_trajectory(trial_dir)):
        if s.get("source") == "agent" and s.get("message"):
            return str(s["message"])
    return ""


def tail(path: Path, n: int = 6000) -> str:
    try:
        data = path.read_bytes()[-n:]
    except OSError:
        return ""
    return data.decode(errors="replace")
