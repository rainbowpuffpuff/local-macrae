#!/usr/bin/env python3
"""Start the macrae backend (FastAPI app from server/) under uvicorn.

Used as the container entrypoint (deploy/Dockerfile) and by `make serve` for local development:

    python deploy/start.py                 # 0.0.0.0:$PORT (default 8080), production settings
    python deploy/start.py --reload        # 127.0.0.1, auto-reload on code changes
    python deploy/start.py --check         # print the resolved app and exit

On Cloudflare (MACRAE_SYNC_URL set by the Worker, see cloudflare/index.js) it doesn't exec uvicorn but supervises
it: the index baked into the image is copied in, deploy/r2sync.py restores runs/traces and the index from R2 and
keeps pushing them up, and on SIGTERM (sleep, rollout, restart) new task starts are refused while running flows get
up to MACRAE_DRAIN_SECONDS (840, under Cloudflare's 15 min before SIGKILL) to finish, then state is pushed once more.

Before starting it:
  - drops contract variables that are set but empty (an env file copied from env.example with blanks must
    behave like "unset", so the server's defaults apply),
  - creates the papers / index / agent-runner directories,
  - inside the container, copies the index baked into the image (/app/index) to an empty $MACRAE_INDEX_DIR,
  - inside the container, writes ~/.modal.toml for $MODAL_PROFILE from MODAL_TOKEN_ID/MODAL_TOKEN_SECRET, so
    Harbor's Modal environment and the `modal` CLI find the login under the profile name agent_runner exports,
  - finds the ASGI app: $MACRAE_APP if set, else server.app:app, server.main:app, server:app
    (or a `create_app` factory in those modules).
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent

# Every backend variable from CONTRACT.md. Empty values are removed so defaults apply.
CONTRACT_VARS = [
    "MACRAE_TOOL_SECRET", "MACRAE_PAPERS_DIR", "MACRAE_INDEX_DIR", "MACRAE_TASKS_FILE",
    "MODAL_PROFILE", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "ANTHROPIC_API_KEY", "AGENT_RUNNER_HOME",
]
TOKEN_PREFIX = "AGENT_RUNNER_TOKEN_"
# Inside the container, data lives on the /data volume (also set as ENV in the Dockerfile; repeated here so a blank
# value from an env file falls back to the volume, not to a path inside the image).
CONTAINER_DEFAULTS = {"MACRAE_PAPERS_DIR": "/data/papers", "MACRAE_INDEX_DIR": "/data/index",
                      "AGENT_RUNNER_HOME": "/data/agent-runner"}
DEFAULT_MODAL_PROFILE = "acalincarol"
APP_CANDIDATES = ["server.app", "server.main", "server"]


def clean_env(env: dict[str, str]) -> list[str]:
    """Remove contract variables (and AGENT_RUNNER_TOKEN_*) whose value is blank. Returns the removed names."""
    removed = []
    for k in list(env):
        if (k in CONTRACT_VARS or k.startswith(TOKEN_PREFIX)) and not env[k].strip():
            del env[k]
            removed.append(k)
    return sorted(removed)


def data_dirs(env: dict[str, str], repo: Path = REPO) -> list[Path]:
    """Directories the backend writes to or reads from, resolved like the server does (relative to the repo)."""
    out = []
    for var, default in [("MACRAE_PAPERS_DIR", "papers"), ("MACRAE_INDEX_DIR", "index")]:
        p = Path(env.get(var) or default).expanduser()
        out.append(p if p.is_absolute() else repo / p)
    if env.get("AGENT_RUNNER_HOME"):
        out.append(Path(env["AGENT_RUNNER_HOME"]).expanduser())
    return out


def ensure_dirs(paths: list[Path]) -> None:
    for p in paths:
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:  # read-only mount etc.: the server reports it, don't refuse to start
            print(f"start: cannot create {p}: {e}", file=sys.stderr)


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def modal_config(env: dict[str, str], existing: str = "") -> Optional[str]:
    """New ~/.modal.toml text with a profile for the env tokens, or None if nothing should change.

    Only adds a profile that isn't there yet; never touches other profiles."""
    tid, secret = env.get("MODAL_TOKEN_ID", "").strip(), env.get("MODAL_TOKEN_SECRET", "").strip()
    if not (tid and secret):
        return None
    profile = env.get("MODAL_PROFILE", "").strip() or DEFAULT_MODAL_PROFILE
    header = f"[{profile}]"
    if any(line.strip() == header for line in existing.splitlines()):
        return None
    active = "" if "active = true" in existing else "active = true\n"
    block = f"{header}\ntoken_id = {_toml_str(tid)}\ntoken_secret = {_toml_str(secret)}\n{active}"
    return (existing.rstrip() + "\n\n" if existing.strip() else "") + block


def write_modal_config(env: dict[str, str], path: Path) -> bool:
    try:
        existing = path.read_text()
    except OSError:
        existing = ""
    text = modal_config(env, existing)
    if text is None:
        return False
    path.write_text(text)
    path.chmod(0o600)
    return True


def in_container(env: dict[str, str]) -> bool:
    return env.get("MACRAE_CONTAINER") == "1" or Path("/.dockerenv").exists()


def resolve_app(env: dict[str, str], candidates: list[str] = APP_CANDIDATES) -> tuple[str, bool]:
    """(import string, is_factory). Raises SystemExit with a clear message if no app is found."""
    if env.get("MACRAE_APP"):
        target = env["MACRAE_APP"].strip()  # "pkg.mod:create_app()" = factory
        return target.removesuffix("()"), target.endswith("()") or env.get("MACRAE_APP_FACTORY") == "1"
    errors = []
    for mod_name in candidates:
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError as e:
            # only "this candidate doesn't exist" is skipped; a missing dependency inside it is a real error
            if e.name and (mod_name == e.name or mod_name.startswith(e.name + ".")):
                errors.append(f"{mod_name}: not found")
                continue
            raise
        if getattr(mod, "app", None) is not None:
            return f"{mod_name}:app", False
        if callable(getattr(mod, "create_app", None)):
            return f"{mod_name}:create_app", True
        errors.append(f"{mod_name}: no `app` or `create_app`")
    raise SystemExit("start: no FastAPI app found (" + "; ".join(errors) + "). Set MACRAE_APP=module:attr.")


def uvicorn_args(app: str, factory: bool, *, reload: bool, host: str, port: int) -> list[str]:
    args = [sys.executable, "-m", "uvicorn", app, "--host", host, "--port", str(port)]
    if factory:
        args.append("--factory")
    if reload:
        args += ["--reload", "--reload-dir", str(REPO)]
    else:
        # behind Caddy (or the Worker): trust X-Forwarded-* from the proxy; one worker keeps run state in-process
        args += ["--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-keep-alive", "75", "--no-server-header"]
    return args


def seed_index(env: dict[str, str], repo: Path = REPO) -> bool:
    """Copy the index baked into the image (<repo>/index) into an empty MACRAE_INDEX_DIR (the /data volume)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import r2sync

    index_dir = Path(env.get("MACRAE_INDEX_DIR") or repo / "index").expanduser()
    baked = repo / "index"
    if not index_dir.is_absolute() or baked.resolve() == index_dir.resolve():
        return False
    try:
        return r2sync.Syncer(None, {}, index_dir, baked_index=baked).seed_index()
    except OSError as e:
        print(f"start: could not copy the baked index: {e}", file=sys.stderr)
        return False


def active_runs(runs_dir: Path) -> list[str]:
    """Runs whose engine process is alive in this container (running/starting state.json with a live pid)."""
    out = []
    if not runs_dir.is_dir():
        return out
    for d in runs_dir.iterdir():
        try:
            st = json.loads((d / "state.json").read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(st, dict) or st.get("status") not in ("running", "starting"):
            continue
        try:
            os.kill(int(st.get("pid")), 0)
        except (ProcessLookupError, ValueError, TypeError, OverflowError):
            continue
        except PermissionError:
            pass
        out.append(d.name)
    return sorted(out)


def supervise(args: list[str], env: dict, drain_seconds: float = 840.0) -> int:
    """Run uvicorn as a child, sync state with R2 (deploy/r2sync.py) and drain on SIGTERM. Returns the exit code."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import r2sync

    syncer = r2sync.Syncer.from_env(env, REPO)
    drain_file = Path(env.get("MACRAE_DRAIN_FILE") or Path(tempfile.gettempdir()) / "macrae-draining")
    drain_file.unlink(missing_ok=True)
    env["MACRAE_DRAIN_FILE"] = str(drain_file)  # the server refuses new task starts while it exists

    term = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: term.set())
    child = subprocess.Popen(args, env=env)
    stop = threading.Event()
    sync_thread = threading.Thread(target=syncer.run, args=(stop,), name="r2sync", daemon=True)
    sync_thread.start()

    while child.poll() is None and not term.is_set():
        term.wait(1)
    if term.is_set() and child.poll() is None:
        drain_file.touch()
        runs_dir = syncer.dirs["runs"]
        deadline = time.monotonic() + drain_seconds
        busy = active_runs(runs_dir)
        while busy and time.monotonic() < deadline and child.poll() is None:
            print(f"start: stopping; waiting for {len(busy)} run(s) to finish: {', '.join(busy)}", file=sys.stderr)
            time.sleep(10)
            busy = active_runs(runs_dir)
        if busy:
            print(f"start: {len(busy)} run(s) still going at the deadline, they will be cut off: {', '.join(busy)}",
                  file=sys.stderr)
        child.terminate()
        try:
            child.wait(20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    stop.set()
    sync_thread.join(60)
    try:
        print(f"start: pushed {syncer.push()} file(s) to R2 before exit", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — report and exit anyway
        print(f"start: final push failed: {e}", file=sys.stderr)
    return 0 if term.is_set() else (child.returncode or 0)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reload", action="store_true", help="development: bind 127.0.0.1 and reload on changes")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--check", action="store_true", help="resolve the app, print it and exit")
    a = ap.parse_args(argv)

    env = os.environ
    removed = clean_env(env)  # os.environ: also affects the uvicorn process we exec
    if removed:
        print(f"start: ignoring empty {', '.join(removed)}", file=sys.stderr)
    if in_container(dict(env)):
        for k, v in CONTAINER_DEFAULTS.items():
            env.setdefault(k, v)
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    os.chdir(REPO)  # contract defaults (papers/, index/, tasks/tasks.json) are relative to the repo
    env["PYTHONPATH"] = os.pathsep.join(p for p in [str(REPO), env.get("PYTHONPATH", "")] if p)

    if not a.check:
        ensure_dirs(data_dirs(dict(env)))
        if in_container(dict(env)) and write_modal_config(dict(env), Path.home() / ".modal.toml"):
            print(f"start: wrote Modal profile {env.get('MODAL_PROFILE') or DEFAULT_MODAL_PROFILE} to "
                  "~/.modal.toml", file=sys.stderr)
        if in_container(dict(env)):
            seed_index(dict(env))

    app, factory = resolve_app(dict(env))
    if a.check:
        print(app + (" (factory)" if factory else ""))
        return 0

    host = a.host or ("127.0.0.1" if a.reload else "0.0.0.0")
    port = a.port or int(env.get("PORT") or 8080)
    args = uvicorn_args(app, factory, reload=a.reload, host=host, port=port)
    print("start: " + " ".join(args[1:]), file=sys.stderr)
    if env.get("MACRAE_SYNC_URL") and not a.reload:
        return supervise(args, env, float(env.get("MACRAE_DRAIN_SECONDS") or 840))
    os.execv(args[0], args)
    return 0  # not reached


if __name__ == "__main__":
    sys.exit(main())
