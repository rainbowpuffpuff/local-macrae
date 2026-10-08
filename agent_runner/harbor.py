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
from .config import HOME, load_settings

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

# the image folder sits next to this file in the vendored copy and one level up in the upstream checkout
_HERE = Path(__file__).resolve().parent
IMAGE_DIR = next((d for d in (_HERE / "image", _HERE.parent / "image") if (d / "Dockerfile").is_file()), _HERE / "image")
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



# ── Modal ───────────────────────────────────────────────────────────────────
# `environment: modal` runs the container on Modal instead of local Docker (`harbor … -e modal`). Modal can't see the
# local agent-runner/agent-base image, so agent steps get `--image` when a registry image is configured (the step's
# `image:`, AGENT_RUNNER_MODAL_IMAGE, settings.json `modal_image`; Modal pulls and caches it), else a task template
# whose environment/Dockerfile is our image Dockerfile. Harbor 0.24's `exec -p …` still pins its default image
# (ubuntu) over the template, so there Claude Code is installed at trial start: slower, but it works.

DEFAULT_MODAL_PROFILE = "acalincarol"


def modal_env(env: dict[str, str]) -> dict[str, str]:
    """Harbor process env for Modal: MODAL_PROFILE from the environment (default acalincarol), unless the login
    comes from MODAL_TOKEN_ID/MODAL_TOKEN_SECRET, which need no profile."""
    env = dict(env)
    if env.get("MODAL_PROFILE"):
        return env
    if env.get("MODAL_TOKEN_ID") and env.get("MODAL_TOKEN_SECRET"):
        return env
    env["MODAL_PROFILE"] = DEFAULT_MODAL_PROFILE
    return env


def modal_image() -> str:
    return os.environ.get("AGENT_RUNNER_MODAL_IMAGE") or str(load_settings().get("modal_image") or "")


def modal_template(dest: Path) -> Path:
    """A Harbor task template whose environment/Dockerfile is the agent image's Dockerfile. Idempotent."""
    env_dir = dest / "environment"
    env_dir.mkdir(parents=True, exist_ok=True)
    src = (IMAGE_DIR / "Dockerfile").read_text()
    target = env_dir / "Dockerfile"
    if not target.is_file() or target.read_text() != src:
        target.write_text(src)
    return dest


# ── live trace forwarding (macrae) ──────────────────────────────────────────
# The agent image's `claude` wrapper (tasks/common/) forwards Claude Code's stream-json to
# POST {MACRAE_LIVE_URL}/api/live/{run}/{step} with the run's token. The server that started the run writes the token
# (and, when it knows its public URL, the URL) into <run>/live/ before the engine starts; we pass them to the agent
# with Harbor's `--ae`. No token or no URL: nothing is passed and the run is exactly as before.

LIVE_ENV_NAMES = ("MACRAE_LIVE_URL", "MACRAE_LIVE_TOKEN", "MACRAE_RUN_ID", "MACRAE_STEP")


def _read_small(p: Path) -> str:
    try:
        return p.read_text().strip()[:2000]
    except OSError:
        return ""


def live_agent_env(run_dir: Path, run_id: str, step: str) -> dict[str, str]:
    token = _read_small(run_dir / "live" / "token")
    url = (os.environ.get("MACRAE_LIVE_URL") or "").strip() or _read_small(run_dir / "live" / "url")
    if not token or not url.startswith(("http://", "https://")):
        return {}
    return {"MACRAE_LIVE_URL": url.rstrip("/"), "MACRAE_LIVE_TOKEN": token, "MACRAE_RUN_ID": run_id,
            "MACRAE_STEP": step}


def mask_agent_env(cmd: list[str]) -> list[str]:
    """The command as logged: the live token is replaced by ***."""
    return [re.sub(r"^(MACRAE_LIVE_TOKEN=).*", r"\1***", c, flags=re.S) for c in cmd]


# ── capabilities (macrae v3) ────────────────────────────────────────────────
# The server that starts a run writes <run>/capabilities.json (evolve.capabilities.export): which installed
# capabilities this run gets, the block appended to every agent instruction (the list + the CAPABILITY_GAP protocol),
# the folder to upload as /app/capabilities, and optionally an image capability (a Dockerfile fragment + pinned
# requirements). No file, or mode "dusk": nothing is mounted or built, and the run is exactly as before.

CAP_IMAGE_DIR = "/opt/macrae-cap"  # where an image capability's own files are copied in its Dockerfile


def capability_context(run_dir: Path) -> dict:
    info = _json(run_dir / "capabilities.json")
    return info if isinstance(info, dict) else {}


def _image_base_dir() -> Path:
    """The Dockerfile an image capability builds on: AGENT_RUNNER_IMAGE_BASE (a folder with a Dockerfile and its
    context), else the macrae agent image with the live-trace wrapper (tasks/common), else agent_runner's own."""
    env = os.environ.get("AGENT_RUNNER_IMAGE_BASE", "").strip()
    for d in ([Path(env).expanduser()] if env else []) + [_HERE.parent / "tasks" / "common", IMAGE_DIR]:
        if (d / "Dockerfile").is_file():
            return d
    return IMAGE_DIR


def image_dockerfile(cap: dict, base_image: str = "", inputs: Optional[list[str]] = None) -> str:
    """Dockerfile text for a sandbox built from an image capability: the base (a registry image if one is set,
    else our agent Dockerfile), the capability's files in /opt/macrae-cap/<name>/, its fragment, then the step's
    input folders at /app/<name> last, so everything before them is the same for every run."""
    name = str(cap["name"])
    frag_path = Path(str(cap["dir"])) / str(cap.get("fragment") or "Dockerfile.fragment")
    fragment = frag_path.read_text() if frag_path.is_file() else ""
    if base_image:
        head = f"FROM {base_image}\n"
    else:
        head = (_image_base_dir() / "Dockerfile").read_text().rstrip() + "\n"
    lines = [head,
             f"# ── image capability {name} v{cap.get('version', 1)} (sha256 {str(cap.get('sha256') or '')[:16]}) ──",
             f"COPY macrae-cap/{name}/ {CAP_IMAGE_DIR}/{name}/",
             f"WORKDIR {CAP_IMAGE_DIR}/{name}",
             fragment.strip(),
             "WORKDIR /app"]
    for p in inputs or []:
        lines.append(f"COPY inputs/{Path(p).name}/ /app/{Path(p).name}/")
    return "\n".join(x for x in lines if x is not None) + "\n"


def write_image_environment(env_dir: Path, cap: dict, base_image: str = "",
                            inputs: Optional[list[str]] = None) -> Path:
    """environment/ for a Harbor task built from an image capability (Modal builds it from the Dockerfile)."""
    if env_dir.exists():
        shutil.rmtree(env_dir)
    env_dir.mkdir(parents=True)
    if not base_image:
        base = _image_base_dir()
        for f in base.iterdir():  # the base Dockerfile's own context (the claude wrapper)
            if f.is_file() and f.name != "Dockerfile" and not f.name.startswith(".") and f.suffix != ".md":
                shutil.copy2(f, env_dir / f.name)
    shutil.copytree(Path(str(cap["dir"])), env_dir / "macrae-cap" / str(cap["name"]),
                    ignore=shutil.ignore_patterns("manifest.json"))
    for p in inputs or []:
        src = Path(p)
        dest = env_dir / "inputs" / src.name
        if src.is_dir():
            shutil.copytree(src, dest)
        elif src.is_file():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / src.name)
    (env_dir / "Dockerfile").write_text(image_dockerfile(cap, base_image, inputs))
    return env_dir


def _toml_str(s: str) -> str:
    return json.dumps(str(s))  # a JSON string is a valid TOML basic string


def compile_agent_task(dest: Path, *, instruction: str, paths: list[str], cap: dict, artifacts: list[str],
                       agent_timeout: Optional[float] = None, workdir: str = "", base_image: str = "") -> Path:
    """A Harbor task for an agent step whose sandbox is built from an image capability (`harbor run -p`, since
    `harbor exec` always uses a prebuilt image). The input folders are COPY'd into /app, the artifacts are the
    same folders (they come back as artifacts/app/<name>, as with exec), and tests/test.sh only writes reward 1:
    the flow's own `check:` decides."""
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "tests").mkdir(parents=True)
    write_image_environment(dest / "environment", cap, base_image, paths)
    (dest / "instruction.md").write_text(instruction.rstrip() + "\n")
    (dest / "tests" / "test.sh").write_text("#!/bin/bash\n# verification is the flow's check: on the backend\n"
                                            "mkdir -p /logs/verifier && echo 1 > /logs/verifier/reward.txt\n")
    (dest / "tests" / "test.sh").chmod(0o755)
    arts = ", ".join(_toml_str(a) for a in artifacts)
    toml = [
        'schema_version = "1.4"',
        f"artifacts = [{arts}]",
        "",
        "[metadata]",
        f"macrae_image_capability = {_toml_str(str(cap['name']) + ' v' + str(cap.get('version', 1)))}",
        "",
        "[agent]",
        f"timeout_sec = {float(agent_timeout or 3600):.1f}",
        "",
        "[verifier]",
        "timeout_sec = 60.0",
        "",
        "[environment]",
        "build_timeout_sec = 1800.0",
        f"workdir = {_toml_str(workdir or '/app')}",
    ]
    (dest / "task.toml").write_text("\n".join(toml) + "\n")
    return dest


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
             agent_timeout: Optional[float] = None, extra: Optional[list[str]] = None,
             environment: str = "", task_template: str = "",
             agent_env: Optional[dict[str, str]] = None) -> list[str]:
    cmd = [HARBOR_BIN, "exec", "-a", agent, "--jobs-dir", str(jobs_dir), "--job-name", job_name,
           "-k", str(attempts), "-q", "-i", instruction]
    if environment and environment != "docker":
        cmd += ["-e", environment]
    if task_template:
        cmd += ["--task-template", task_template]
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
    for k, v in (agent_env or {}).items():
        cmd += ["--ae", f"{k}={v}"]
    return cmd + list(extra or [])


def run_cmd(*, task_path: str = "", dataset: str = "", agent: str, model: str = "", jobs_dir: Path,
            job_name: str, attempts: int = 1, n_concurrent: int = 1, task_names: Optional[list[str]] = None,
            n_tasks: Optional[int] = None, agent_kwargs: Optional[dict] = None,
            skills: Optional[list[str]] = None, extra: Optional[list[str]] = None,
            environment: str = "", agent_env: Optional[dict[str, str]] = None) -> list[str]:
    cmd = [HARBOR_BIN, "run", "-a", agent, "-o", str(jobs_dir), "--job-name", job_name,
           "-k", str(attempts), "-n", str(n_concurrent), "-q", "-y"]
    if environment and environment != "docker":
        cmd += ["-e", environment]
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
    for k, v in (agent_env or {}).items():
        cmd += ["--ae", f"{k}={v}"]
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
