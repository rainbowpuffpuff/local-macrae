#!/usr/bin/env python3
"""`claude` wrapper for the Modal agent image: run the real Claude Code CLI, pass its stdout through unchanged
(Harbor tees it to agent/claude-code.txt), and forward each stream-json line to the macrae backend while it runs.

    POST {MACRAE_LIVE_URL}/api/live/{MACRAE_RUN_ID}/{MACRAE_STEP}      header X-Macrae-Live: {MACRAE_LIVE_TOKEN}
    body {"stream": "<id of this invocation>", "offset": <index of the first line>, "lines": [str], "times": [float]}

Lines are batched and sent at most 1 s apart (sooner when a batch gets big). A failed batch is retried with the
next one; the server skips lines it already has (by stream + offset), so a retry never duplicates. Forwarding can
never break the run: any error here only turns forwarding off. This file writes nothing to stdout except the real
CLI's output and nothing to stderr (Harbor merges it into claude-code.txt); set MACRAE_LIVE_DEBUG=<file> to log.

Stdlib only, Python 3.8+. Installed as /usr/local/lib/macrae/claude_live.py and started by the /usr/local/bin/claude
shim (tasks/common/claude), which falls back to the real binary when python3 or the env vars are missing.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

REAL_CANDIDATES = ("/usr/local/lib/macrae/claude-real", "/opt/claude/bin/claude")
INTERVAL = 1.0  # seconds between sends (the contract's "≤ 1 s")
MAX_BATCH_BYTES = 1_500_000  # send early above this; stays under the Worker's body limit
MAX_LINE_BYTES = 1_000_000  # a longer line (a huge tool result) is replaced by a short stub
MAX_PENDING_BYTES = 32_000_000  # backend unreachable for long: drop the oldest lines rather than grow forever
HTTP_TIMEOUT = 5.0
CLOSE_GRACE = 6.0  # after the CLI exits, how long we may keep trying to deliver the tail
PERMANENT = {400, 401, 403, 404, 410, 413}  # the backend refuses this run: stop forwarding


def _debug(msg: str) -> None:
    path = os.environ.get("MACRAE_LIVE_DEBUG")
    if not path:
        return
    try:
        with open(path, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def real_binary() -> str:
    """The real Claude Code binary, never this wrapper."""
    me = {os.path.realpath(sys.argv[0]), os.path.realpath(__file__)}
    for c in [os.environ.get("MACRAE_CLAUDE_REAL", "")] + list(REAL_CANDIDATES):
        if c and os.path.isfile(c) and os.access(c, os.X_OK) and os.path.realpath(c) not in me:
            return c
    raise FileNotFoundError("real claude binary not found (set MACRAE_CLAUDE_REAL)")


def wants_stream(argv: list[str]) -> bool:
    """Only the agent run (`--output-format stream-json`) is forwarded; `claude --version` etc. are not."""
    for i, a in enumerate(argv):
        if a == "--output-format=stream-json" or (a == "--output-format" and argv[i + 1:i + 2] == ["stream-json"]):
            return True
    return False


def live_config(env=os.environ):
    url, token = env.get("MACRAE_LIVE_URL", "").strip(), env.get("MACRAE_LIVE_TOKEN", "").strip()
    run_id, step = env.get("MACRAE_RUN_ID", "").strip(), env.get("MACRAE_STEP", "").strip()
    if not (url.startswith(("http://", "https://")) and token and run_id and step):
        return None
    endpoint = (f"{url.rstrip('/')}/api/live/{urllib.parse.quote(run_id, safe='')}/"
                f"{urllib.parse.quote(step, safe='')}")
    return endpoint, token


def _stub(line: str) -> str:
    """A line too big to forward: keep its type so the page still knows something happened."""
    head = line[:4000]
    typ = ""
    try:
        typ = str(json.loads(line).get("type") or "")
    except (ValueError, AttributeError):
        pass
    return json.dumps({"type": "macrae_truncated", "orig_type": typ, "bytes": len(line), "head": head})


class Forwarder:
    """Collects lines from the reader and posts them from a background thread."""

    def __init__(self, endpoint: str, token: str, post=None):
        self.endpoint, self.token = endpoint, token
        self.stream = uuid.uuid4().hex[:16]
        self.lines: list[str] = []
        self.times: list[float] = []
        self.offset = 0  # stream index of self.lines[0]
        self.pending_bytes = 0
        self.closed = False
        self.dead = False  # permanent refusal: stop trying
        self.sent = 0
        self.cond = threading.Condition()
        self.post = post or self._post
        self.thread = threading.Thread(target=self._loop, name="macrae-live", daemon=True)
        self.thread.start()

    def add(self, raw: bytes) -> None:
        if self.dead:
            return
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line.strip():
            return
        if len(line) > MAX_LINE_BYTES:
            line = _stub(line)
        with self.cond:
            self.lines.append(line)
            self.times.append(time.time())
            self.pending_bytes += len(line)
            while self.pending_bytes > MAX_PENDING_BYTES and len(self.lines) > 1:
                self.pending_bytes -= len(self.lines.pop(0))
                self.times.pop(0)
                self.offset += 1
            if len(self.lines) == 1 or self.pending_bytes >= MAX_BATCH_BYTES:
                self.cond.notify()  # wake the sender: first line of a batch, or a big batch

    def close(self, grace: float = CLOSE_GRACE) -> None:
        with self.cond:
            self.closed = True
            self.cond.notify()
        self.thread.join(grace)

    def _post(self, body: bytes) -> int:
        req = urllib.request.Request(self.endpoint, data=body, method="POST", headers={
            "Content-Type": "application/json", "X-Macrae-Live": self.token, "User-Agent": "macrae-claude-live/1"})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                r.read(2000)
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def _take(self) -> tuple[int, list[str], list[float]]:
        """The next batch (not removed yet): at most MAX_BATCH_BYTES, at least one line."""
        n, size = 0, 0
        for ln in self.lines:
            if n and size + len(ln) > MAX_BATCH_BYTES:
                break
            n += 1
            size += len(ln)
        return self.offset, self.lines[:n], self.times[:n]

    def _loop(self) -> None:
        backoff = 0.0
        last = 0.0
        while True:
            with self.cond:
                while not self.closed and (not self.lines or
                                           (self.pending_bytes < MAX_BATCH_BYTES and time.time() - last < INTERVAL)):
                    wait = INTERVAL - (time.time() - last) if self.lines else None
                    self.cond.wait(max(0.05, wait) if wait is not None else None)
                if self.closed and not self.lines:
                    return
                offset, batch, times = self._take()
            if backoff:
                time.sleep(backoff)
            last = time.time()
            body = json.dumps({"stream": self.stream, "offset": offset, "sent": time.time(), "lines": batch,
                               "times": times}).encode()
            try:
                status = self.post(body)
            except Exception as e:  # network down, DNS, timeout: keep the batch and try again
                _debug(f"post failed: {type(e).__name__}: {e}")
                status = 0
            if 200 <= status < 300:
                backoff = 0.0
                with self.cond:
                    k = len(batch) - (self.offset - offset)  # lines may have been dropped meanwhile
                    if k > 0:
                        del self.lines[:k]
                        del self.times[:k]
                        self.pending_bytes -= sum(len(x) for x in batch[len(batch) - k:])
                        self.offset += k
                    self.sent += len(batch)
                continue
            _debug(f"post returned {status}")
            if status in PERMANENT:
                with self.cond:
                    self.dead = True
                    self.lines.clear()
                    self.times.clear()
                    self.pending_bytes = 0
                return
            backoff = min(10.0, (backoff * 2) or 0.5)
            if self.closed:
                backoff = min(backoff, 1.0)  # little time left: retry quickly; close() stops waiting anyway


def run(argv: list[str]) -> int:
    real = real_binary()
    cfg = live_config()
    if cfg is None or not wants_stream(argv):
        os.execv(real, [real] + argv)
    try:
        fwd = Forwarder(*cfg)
    except Exception as e:
        _debug(f"forwarder failed to start: {e}")
        os.execv(real, [real] + argv)
    proc = subprocess.Popen([real] + argv, stdout=subprocess.PIPE)  # stdin and stderr are inherited

    def relay(sig, _frame):
        try:
            proc.send_signal(sig)
        except OSError:
            pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
        try:
            signal.signal(sig, relay)
        except (OSError, ValueError):
            pass
    out = sys.stdout.buffer
    out_ok = True
    assert proc.stdout is not None
    for raw in iter(proc.stdout.readline, b""):
        if out_ok:
            try:
                out.write(raw)
                out.flush()
            except (BrokenPipeError, OSError):
                out_ok = False  # whoever read our stdout is gone; keep draining so the CLI doesn't block
        try:
            fwd.add(raw)
        except Exception as e:
            _debug(f"add failed: {e}")
    rc = proc.wait()
    try:
        fwd.close()
    except Exception:
        pass
    _debug(f"claude exited {rc}; forwarded {fwd.sent} lines as stream {fwd.stream}")
    return rc if rc >= 0 else 128 - rc


def main() -> None:
    try:
        rc = run(sys.argv[1:])
    except FileNotFoundError as e:
        sys.stderr.write(f"claude: {e}\n")
        rc = 127
    try:
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        pass
    os._exit(rc)  # don't wait for a stuck forwarder thread


if __name__ == "__main__":
    main()
