#!/usr/bin/env python3
"""Keep the backend's state in R2 (bucket macrae-data) when it runs as a Cloudflare container. Stdlib only.

The container has no R2 credentials. It talks plain HTTP to $MACRAE_SYNC_URL (http://r2.macrae), and the Worker
answers from its DATA binding (cloudflare/worker.js `dataHandler`, wired up in cloudflare/index.js):

    GET /?prefix=P[&cursor=C]   → {"objects": [{"key", "size", "mtime"}], "cursor": str|null}
    GET /KEY                    → bytes (header x-macrae-mtime), 404 if missing
    PUT /KEY                    → store (header x-macrae-mtime); only keys under state/

What goes where:

    index/<file>    → $MACRAE_INDEX_DIR      down on start, only when R2's manifest is newer than the local one;
                                             data files first, manifest.json last (rag reloads on its mtime)
    state/runs/…    ↔ $AGENT_RUNNER_HOME/runs                 agent_runner run records (state.json, logs, outputs)
    state/jobs/…    ↔ $AGENT_RUNNER_JOBS (…/jobs)             Harbor trial folders: the traces
    state/macrae/…  ↔ $MACRAE_SERVER_DATA (…/macrae)          the server's run→task sidecars and event logs
    state/evolve/…  ↔ $MACRAE_EVOLVE_HOME (…/evolve)          what Macrae learned: lessons, tools, the capability
                                                             registry and its ledger, dusk → dawn benchmarks

state/ is restored on start (only files that don't exist locally) and pushed up incrementally: whenever a run's
state.json changes (a step started or finished), every $MACRAE_SYNC_INTERVAL seconds (60), and once more when the
container stops. Runs that were still going when the old container died are marked crashed on restore.
Leases and cooldowns are not synced: they belong to processes that no longer exist.

    python deploy/r2sync.py status|pull-index|restore|push      (by hand, with MACRAE_SYNC_URL set)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

STATE_PARTS = ("macrae", "runs", "jobs", "evolve")  # restore order: event logs before the runs they describe
INDEX_PREFIXES = ("papers-", "chunks-", "emb-")
DEFAULT_INTERVAL = 60.0
POLL = 5.0
MAX_MB = 50.0


def log(msg: str) -> None:
    print(f"r2sync: {msg}", file=sys.stderr, flush=True)


class SyncError(RuntimeError):
    pass


class Remote:
    """HTTP client for the Worker's R2 endpoint."""

    def __init__(self, base: str, timeout: float = 30.0, retries: int = 3):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.retries = retries

    def _request(self, method: str, path: str, data: Optional[bytes] = None,
                 headers: Optional[dict] = None) -> tuple[int, dict, bytes]:
        url = self.base + path
        last: Exception | None = None
        for attempt in range(self.retries):
            req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return r.status, dict(r.headers), r.read()
            except urllib.error.HTTPError as e:
                body = e.read()
                if e.code < 500:
                    return e.code, dict(e.headers), body
                last = SyncError(f"{method} {path}: HTTP {e.code} {body[:200]!r}")
            except (urllib.error.URLError, OSError) as e:
                last = SyncError(f"{method} {path}: {e}")
            time.sleep(0.5 * 2 ** attempt)
        raise last or SyncError(f"{method} {path} failed")

    @staticmethod
    def _key_path(key: str) -> str:
        return "/" + urllib.parse.quote(key, safe="/")

    def list(self, prefix: str) -> list[dict]:
        out, cursor = [], None
        while True:
            q = {"prefix": prefix, **({"cursor": cursor} if cursor else {})}
            status, _, body = self._request("GET", "/?" + urllib.parse.urlencode(q))
            if status != 200:
                raise SyncError(f"list {prefix}: HTTP {status} {body[:200]!r}")
            page = json.loads(body)
            out += page.get("objects") or []
            cursor = page.get("cursor")
            if not cursor:
                return out

    def get(self, key: str) -> Optional[tuple[bytes, Optional[float]]]:
        status, headers, body = self._request("GET", self._key_path(key))
        if status == 404:
            return None
        if status != 200:
            raise SyncError(f"get {key}: HTTP {status} {body[:200]!r}")
        mtime = {k.lower(): v for k, v in headers.items()}.get("x-macrae-mtime")
        try:
            return body, float(mtime) if mtime else None
        except ValueError:
            return body, None

    def put(self, key: str, data: bytes, mtime: Optional[float] = None) -> None:
        headers = {"content-type": "application/octet-stream"}
        if mtime:
            headers["x-macrae-mtime"] = f"{mtime:.3f}"
        status, _, body = self._request("PUT", self._key_path(key), data=data, headers=headers)
        if status != 200:
            raise SyncError(f"put {key}: HTTP {status} {body[:200]!r}")


def _atomic_write(path: Path, data: bytes, mtime: Optional[float] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if mtime:
            os.utime(tmp, (mtime, mtime))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> Optional[dict]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _inside(root: Path, rel: str) -> Optional[Path]:
    """root/rel if it stays inside root (keys come from the bucket; never trust them with a path)."""
    parts = rel.split("/")
    if not rel or any(p in ("", ".", "..") for p in parts):
        return None
    return root.joinpath(*parts)


def _skip(name: str) -> bool:
    return name.endswith(".tmp") or name.startswith(".") or name == "__pycache__"


def state_dirs(env: dict) -> dict[str, Path]:
    home = Path(env.get("AGENT_RUNNER_HOME") or Path.home() / ".local/share/agent-runner").expanduser()
    return {
        "macrae": Path(env.get("MACRAE_SERVER_DATA") or home / "macrae").expanduser(),
        "runs": home / "runs",
        "jobs": Path(env.get("AGENT_RUNNER_JOBS") or home / "jobs").expanduser(),
        "evolve": Path(env.get("MACRAE_EVOLVE_HOME") or home / "evolve").expanduser(),
    }


def mark_interrupted(state_file: Path) -> bool:
    """A run restored as running/starting belonged to a container that is gone: record it as crashed."""
    st = _read_json(state_file)
    if not st or st.get("status") not in ("running", "starting"):
        return False
    try:
        finished = state_file.stat().st_mtime
    except OSError:
        finished = time.time()
    st.update(status="crashed", pid=None, finished=st.get("finished") or finished,
              error=(st.get("error") or "") + ("\n" if st.get("error") else "") +
              "the backend container restarted while this run was going (record restored from R2)")
    _atomic_write(state_file, json.dumps(st, indent=1).encode())
    return True


class Syncer:
    def __init__(self, remote: Remote, dirs: dict[str, Path], index_dir: Path, baked_index: Optional[Path] = None,
                 interval: float = DEFAULT_INTERVAL, max_bytes: int = int(MAX_MB * 1024 * 1024),
                 logger: Callable[[str], None] = log):
        self.remote = remote
        self.dirs = dirs
        self.index_dir = index_dir
        self.baked_index = baked_index
        self.interval = interval
        self.max_bytes = max_bytes
        self.log = logger
        self.pushed: dict[str, tuple[int, int]] = {}  # key → (size, mtime_ns) of what R2 has
        self._lock = threading.Lock()
        self._warned: set[str] = set()

    @classmethod
    def from_env(cls, env: dict, repo: Optional[Path] = None) -> "Syncer":
        repo = repo or Path(__file__).resolve().parent.parent
        index_dir = Path(env.get("MACRAE_INDEX_DIR") or repo / "index").expanduser()
        if not index_dir.is_absolute():
            index_dir = repo / index_dir
        baked = repo / "index"
        return cls(Remote(env["MACRAE_SYNC_URL"]), state_dirs(env), index_dir,
                   baked_index=baked if baked.resolve() != index_dir.resolve() else None,
                   interval=float(env.get("MACRAE_SYNC_INTERVAL") or DEFAULT_INTERVAL),
                   max_bytes=int(float(env.get("MACRAE_SYNC_MAX_MB") or MAX_MB) * 1024 * 1024))

    # ── index ────────────────────────────────────────────────────────────────
    def seed_index(self) -> bool:
        """Copy the index baked into the image (repo index/) when the index dir has none. Local, fast."""
        src = self.baked_index
        if not src or (self.index_dir / "manifest.json").exists():
            return False
        manifest = _read_json(src / "manifest.json") if src else None
        if not manifest:
            return False
        names = [n for n in (manifest.get("files") or {}).values() if n]
        if not all((src / n).is_file() for n in names):
            self.log(f"baked index in {src} is incomplete, not using it")
            return False
        self.index_dir.mkdir(parents=True, exist_ok=True)
        for n in names:
            shutil.copy2(src / n, self.index_dir / n)
        shutil.copy2(src / "manifest.json", self.index_dir / ".manifest.json.seed.tmp")
        os.replace(self.index_dir / ".manifest.json.seed.tmp", self.index_dir / "manifest.json")
        self.log(f"index: {manifest.get('papers', '?')} papers from the image ({src})")
        return True

    def pull_index(self) -> bool:
        """Download R2's index if it is newer than the local one. True if the local index changed."""
        got = self.remote.get("index/manifest.json")
        if got is None:
            self.log("index: none in R2 (upload one with cloudflare/deploy.sh index)")
            return False
        raw, _ = got
        remote = json.loads(raw)
        local = _read_json(self.index_dir / "manifest.json") or {}
        if local and float(local.get("created") or 0) >= float(remote.get("created") or 0):
            self.log(f"index: local copy is current ({local.get('papers', '?')} papers)")
            return False
        names = [n for n in (remote.get("files") or {}).values() if n]
        for n in names:
            dest = _inside(self.index_dir, n)
            if dest is None or "/" in n:
                raise SyncError(f"index manifest names a bad file: {n!r}")
            data = self.remote.get(f"index/{n}")
            if data is None:
                raise SyncError(f"index/{n} is named in R2's manifest but missing (re-upload the index)")
            _atomic_write(dest, data[0])
        _atomic_write(self.index_dir / "manifest.json", raw)
        for p in self.index_dir.iterdir():  # what rag would remove after a swap
            if p.is_file() and p.name.startswith(INDEX_PREFIXES) and p.name not in names:
                p.unlink(missing_ok=True)
        self.log(f"index: {remote.get('papers', '?')} papers, {remote.get('chunks', '?')} chunks from R2")
        return True

    # ── state ────────────────────────────────────────────────────────────────
    def restore(self) -> int:
        """Download state/ files that don't exist locally. Returns how many."""
        total = 0
        for part, root in self.dirs.items():
            prefix = f"state/{part}/"
            todo = []
            for obj in self.remote.list(prefix):
                dest = _inside(root, obj["key"][len(prefix):])
                if dest is None or dest.exists():
                    continue
                todo.append((obj["key"], dest))

            def fetch(item):
                key, dest = item
                got = self.remote.get(key)
                if got is None:
                    return None
                data, mtime = got
                _atomic_write(dest, data, mtime)
                st = dest.stat()
                with self._lock:
                    self.pushed[key] = (st.st_size, st.st_mtime_ns)
                return dest

            with ThreadPoolExecutor(max_workers=8) as pool:
                done = [d for d in pool.map(fetch, todo) if d]
            if part == "runs":
                fixed = [d.parent.name for d in done if d.name == "state.json" and d.parent.parent == root
                         and mark_interrupted(d)]
                if fixed:
                    self.log(f"marked {len(fixed)} interrupted run(s) as crashed: {', '.join(fixed[:5])}")
            total += len(done)
        if total:
            self.log(f"restored {total} file(s) of runs and traces from R2")
        return total

    def _files(self):
        for part, root in self.dirs.items():
            if not root.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not _skip(d)]
                for name in filenames:
                    if _skip(name):
                        continue
                    p = Path(dirpath) / name
                    rel = p.relative_to(root).as_posix()
                    yield f"state/{part}/{rel}", p

    def push(self) -> int:
        """Upload new or changed state files. Returns how many were uploaded."""
        n, errors = 0, []
        with self._lock:
            for key, p in self._files():
                try:
                    st = p.lstat()
                except OSError:
                    continue
                if not p.is_file() or p.is_symlink():
                    continue
                sig = (st.st_size, st.st_mtime_ns)
                if self.pushed.get(key) == sig:
                    continue
                if st.st_size > self.max_bytes:
                    if key not in self._warned:
                        self._warned.add(key)
                        self.log(f"skip {key}: {st.st_size / 1e6:.0f} MB is over MACRAE_SYNC_MAX_MB")
                    continue
                try:
                    data = p.read_bytes()
                    self.remote.put(key, data, st.st_mtime)
                except (OSError, SyncError) as e:
                    errors.append(str(e))
                    if len(errors) >= 3:  # R2 unreachable: try again next round
                        break
                    continue
                self.pushed[key] = sig
                n += 1
        if errors:
            self.log(f"push: {len(errors)} error(s), retrying later; first: {errors[0]}")
        return n

    def run_signature(self) -> dict[str, int]:
        """state.json mtimes; a change means a run started, a step moved on, or a run ended."""
        runs = self.dirs["runs"]
        sig = {}
        if runs.is_dir():
            for d in runs.iterdir():
                try:
                    sig[d.name] = (d / "state.json").stat().st_mtime_ns
                except OSError:
                    pass
        return sig

    def run(self, stop: threading.Event, poll: float = POLL) -> None:
        """Startup (index down, state restore), then push on run changes and on the timer until `stop` is set."""
        for name, step in (("index", self.pull_index), ("restore", self.restore)):
            try:
                step()
            except Exception as e:  # never take the server down over sync
                self.log(f"{name} failed: {e}")
        last_sig, last_push = None, 0.0
        while not stop.is_set():
            sig = self.run_signature()
            if sig != last_sig or time.monotonic() - last_push >= self.interval:
                try:
                    n = self.push()
                    if n:
                        self.log(f"pushed {n} file(s)")
                except Exception as e:
                    self.log(f"push failed: {e}")
                last_sig, last_push = sig, time.monotonic()
            stop.wait(poll)


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("status", "pull-index", "restore", "push"):
        print(__doc__.split("\n\n")[0] + "\nusage: r2sync.py status|pull-index|restore|push", file=sys.stderr)
        return 2
    if not os.environ.get("MACRAE_SYNC_URL"):
        print("r2sync: MACRAE_SYNC_URL is not set (inside the container it is http://r2.macrae)", file=sys.stderr)
        return 2
    s = Syncer.from_env(dict(os.environ))
    if argv[0] == "status":
        for part in STATE_PARTS:
            objs = s.remote.list(f"state/{part}/")
            print(f"state/{part}/: {len(objs)} objects, {sum(o.get('size') or 0 for o in objs) / 1e6:.1f} MB")
        m = s.remote.get("index/manifest.json")
        print("index/: " + (f"{json.loads(m[0]).get('papers')} papers" if m else "none"))
    elif argv[0] == "pull-index":
        s.pull_index()
    elif argv[0] == "restore":
        s.restore()
    else:
        print(f"pushed {s.push()} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
