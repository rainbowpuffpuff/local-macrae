"""Live agent output: the `claude` wrapper in the Modal image (tasks/common/claude_live.py) posts Claude Code's
stream-json lines here while the agent runs, so the page shows each read, command and calculation as it happens
instead of when the trial ends.

    POST /api/live/{run_id}/{step}      header X-Macrae-Live: <the run's token>   (no X-Macrae-Secret)
    body {"stream": str, "offset": int, "sent": float, "lines": [str], "times": [float]}
         or NDJSON (one stream-json line per line; headers X-Macrae-Live-Stream / X-Macrae-Live-Offset optional)

Files, in the run folder ($AGENT_RUNNER_HOME/runs/<id>/live/):
    token                    the per-run token, created by the server when it starts the task (mode 600)
    url                      the public base URL the agent posts to, when MACRAE_LIVE_URL isn't set in the env
    <step>.jsonl             the raw lines, appended in arrival order (<step> = sanitized step key, as logs/<step>.log)
    <step>.batches.jsonl     one record per accepted batch: {"stream", "offset", "n", "t", "times"}; it gives each
                             line its invocation (stream) and time, so the raw file stays plain stream-json

A batch carries its stream (one per `claude` invocation) and the index of its first line, so a retried batch is
never stored twice. Times are the wrapper's clock shifted by (our receive time − its send time): a sandbox clock
that is off doesn't put events in the wrong order.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import config, runs
from .trace import sanitize

MAX_BODY = 4 * 1024 * 1024  # per request (the Worker allows the same)
MAX_LINES = 20000  # per request
MAX_STEP_BYTES = 96 * 1024 * 1024  # per step file; beyond that we refuse (413) and the wrapper stops
ACCEPT_AFTER_END = 30 * 60  # seconds after the run ended that late batches are still taken
STREAM_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
STEP_RE = re.compile(r"^[A-Za-z0-9_.\-\[\]]{1,160}$")


class LiveError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def live_dir(run_dir: Path) -> Path:
    return run_dir / "live"


# ── token + url (written at task start) ──────────────────────────────────────


def setup_run(run_dir: Path, url: str = "") -> str:
    """Create the run's live token (and record the URL the agent should post to). Returns the token."""
    d = live_dir(run_dir)
    d.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    p = d / "token"
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    if url:
        (d / "url").write_text(url.rstrip("/"))
    return token


def run_token(run_dir: Path) -> str:
    try:
        return (live_dir(run_dir) / "token").read_text().strip()
    except OSError:
        return ""


def public_url(header_origin: Optional[str]) -> str:
    """Where the sandbox reaches us: MACRAE_LIVE_URL, else the public origin the Worker reported (X-Macrae-Origin)."""
    env = os.environ.get("MACRAE_LIVE_URL", "").strip()
    if env:
        return env.rstrip("/")
    o = (header_origin or "").strip()
    if re.match(r"^https?://[A-Za-z0-9.\-]+(:\d+)?$", o) and not re.search(r"//(localhost|127\.|0\.0\.0\.0|\[)", o):
        return o
    return ""


# ── ingest ──────────────────────────────────────────────────────────────────

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_counts: dict[str, dict[str, int]] = {}  # batches file → {stream: lines stored}


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _stream_counts(batches: Path) -> dict[str, int]:
    cached = _counts.get(str(batches))
    if cached is not None:
        return cached
    counts: dict[str, int] = {}
    for rec in _read_jsonl(batches):
        s, off, n = str(rec.get("stream") or ""), int(rec.get("offset") or 0), int(rec.get("n") or 0)
        counts[s] = max(counts.get(s, 0), off + n)
    _counts[str(batches)] = counts
    return counts


def _parse_body(body: bytes, content_type: str, headers: dict[str, str]) -> tuple[str, Optional[int], list, list,
                                                                                    Optional[float]]:
    if "json" in content_type and "ndjson" not in content_type:
        try:
            d = json.loads(body or b"{}")
        except ValueError as e:
            raise LiveError(400, f"body is not JSON: {e}") from e
        if not isinstance(d, dict) or not isinstance(d.get("lines"), list):
            raise LiveError(400, 'body must be {"stream", "offset", "lines": [...]}')
        off = d.get("offset")
        sent = d.get("sent")
        return (str(d.get("stream") or "default"), int(off) if isinstance(off, int) and off >= 0 else None,
                d["lines"], d.get("times") if isinstance(d.get("times"), list) else [],
                float(sent) if isinstance(sent, (int, float)) else None)
    text = body.decode("utf-8", errors="replace")
    off = headers.get("x-macrae-live-offset", "")
    return (headers.get("x-macrae-live-stream") or "default", int(off) if off.isdigit() else None,
            text.splitlines(), [], None)


def ingest(run_id: str, step: str, token: Optional[str], body: bytes, content_type: str = "application/json",
           headers: Optional[dict[str, str]] = None) -> dict:
    """Store one batch. Raises LiveError(status, detail) when refused."""
    if not runs.valid_run_id(run_id) or not STEP_RE.match(step or ""):
        raise LiveError(404, "no such run or step")
    run_dir = runs.run_dir(run_id)
    want = run_token(run_dir) if run_dir.is_dir() else ""
    if not want:
        raise LiveError(404, "no such run, or live forwarding is off for it")
    if not token or not hmac.compare_digest(token.encode(), want.encode()):
        raise LiveError(401, "missing or wrong X-Macrae-Live token")
    if len(body) > MAX_BODY:
        raise LiveError(413, "batch too large")
    st = config.read_json(run_dir / "state.json")
    if isinstance(st, dict) and runs.public_status(str(st.get("status") or "")) in runs.TERMINAL:
        if time.time() - float(st.get("finished") or time.time()) > ACCEPT_AFTER_END:
            raise LiveError(410, "the run is over")
    stream, offset, lines, times, sent = _parse_body(body, content_type or "", {k.lower(): v for k, v in
                                                                              (headers or {}).items()})
    if not STREAM_RE.match(stream):
        raise LiveError(400, "bad stream id")
    if len(lines) > MAX_LINES:
        raise LiveError(413, "too many lines in one batch")
    now = time.time()
    skew = (now - sent) if sent and abs(now - sent) < 6 * 3600 else 0.0
    clean: list[tuple[str, float]] = []
    for i, ln in enumerate(lines):
        if not isinstance(ln, str):
            ln = json.dumps(ln, ensure_ascii=False)
        ln = ln.replace("\r", " ").replace("\n", " ").strip()
        t = times[i] if i < len(times) and isinstance(times[i], (int, float)) else None
        t = (float(t) + skew) if t is not None else now
        clean.append((ln, min(t, now)))
    name = sanitize(step)
    d = live_dir(run_dir)
    raw, batches = d / f"{name}.jsonl", d / f"{name}.batches.jsonl"
    with _lock(str(raw)):
        counts = _stream_counts(batches)
        have = counts.get(stream, 0)
        start = offset if offset is not None else have
        skip = max(0, have - start)
        new = clean[skip:]
        kept = [(ln, t) for ln, t in new if ln]
        if not new:
            return {"ok": True, "accepted": 0, "next": have}
        try:
            size = raw.stat().st_size
        except OSError:
            size = 0
        if size + sum(len(ln) + 1 for ln, _ in kept) > MAX_STEP_BYTES:
            raise LiveError(413, "this step's live log is full")
        d.mkdir(parents=True, exist_ok=True)
        if kept:
            with open(raw, "a", encoding="utf-8") as f:
                f.write("".join(ln + "\n" for ln, _ in kept))
        # n counts every line of the batch we consumed (empty ones too), so offsets stay aligned with the wrapper's
        rec = {"stream": stream, "offset": start + skip, "n": len(new), "stored": len(kept), "t": round(now, 3),
               "times": [round(t, 3) for ln, t in new if ln]}
        with open(batches, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        counts[stream] = max(have, start + skip + len(new))
    return {"ok": True, "accepted": len(kept), "next": counts[stream]}


# ── reading ─────────────────────────────────────────────────────────────────


@dataclass
class Line:
    stream: str
    t: float
    data: dict


def _read_jsonl(p: Path) -> list[dict]:
    out = []
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for ln in f:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    out.append(d)
    except OSError:
        pass
    return out


_read_cache: dict[str, tuple[tuple, list[Line]]] = {}
_read_guard = threading.Lock()


def _sig(*paths: Path) -> tuple:
    out = []
    for p in paths:
        try:
            st = p.stat()
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def read_step(run_dir: Path, key: str) -> list[Line]:
    """Every stored line of a step, with its stream and time (non-JSON lines skipped). [] if none."""
    name = sanitize(key)
    d = live_dir(run_dir)
    raw, batches = d / f"{name}.jsonl", d / f"{name}.batches.jsonl"
    sig = _sig(raw, batches)
    if sig[0] is None:
        return []
    with _read_guard:
        hit = _read_cache.get(str(raw))
        if hit and hit[0] == sig:
            return hit[1]
    try:
        with open(raw, encoding="utf-8", errors="replace") as f:
            raw_lines = f.read().split("\n")  # not splitlines(): JSON may hold U+2028 and friends
    except OSError:
        return []
    owners: list[tuple[str, float]] = []
    for rec in _read_jsonl(batches):
        times = rec.get("times") or []
        n = int(rec.get("stored", len(times)) or 0)
        for i in range(n):
            owners.append((str(rec.get("stream") or "default"), float(times[i]) if i < len(times) else
                           float(rec.get("t") or 0)))
    out: list[Line] = []
    for i, ln in enumerate(raw_lines):
        if i >= len(owners):
            break  # a batch record not written yet (crash between the two writes): wait for the next read
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            data = json.loads(ln)
        except ValueError:
            continue
        if isinstance(data, dict):
            out.append(Line(owners[i][0], owners[i][1], data))
    with _read_guard:
        _read_cache[str(raw)] = (sig, out)
        if len(_read_cache) > 200:
            _read_cache.pop(next(iter(_read_cache)))
    return out


def streams(run_dir: Path, key: str) -> dict[str, list[tuple[float, dict]]]:
    """The step's lines grouped by `claude` invocation, in arrival order."""
    out: dict[str, list[tuple[float, dict]]] = {}
    for ln in read_step(run_dir, key):
        out.setdefault(ln.stream, []).append((ln.t, ln.data))
    return out


def has_live(run_dir: Path) -> bool:
    return bool(run_token(run_dir))


def reset_cache() -> None:
    with _read_guard:
        _read_cache.clear()
    _counts.clear()

