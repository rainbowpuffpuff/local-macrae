"""Account pool for Harbor runs: leases (slots per account), cooldowns after limit hits, auto-spread.

Leases are files leases/<account>/<id> holding the owner pid, so several flow processes share
one view of who is busy. A dead pid's lease is ignored and cleaned up.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from . import tokens
from .config import COOLDOWN_FILE, LEASES_DIR, load_settings


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def active_leases(account: str) -> int:
    d = LEASES_DIR / account
    if not d.is_dir():
        return 0
    n = 0
    for f in d.iterdir():
        try:
            pid = int(f.read_text().split()[0])
        except (OSError, ValueError, IndexError):
            continue
        if _alive(pid):
            n += 1
        else:
            f.unlink(missing_ok=True)
    return n


def cooldowns() -> dict[str, dict]:
    try:
        data = json.loads(COOLDOWN_FILE.read_text())
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {k: v for k, v in data.items() if v.get("until", 0) > now}


def set_cooldown(account: str, until: float, reason: str) -> None:
    COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = cooldowns()
    data[account] = {"until": until, "reason": reason[:200], "at": time.time()}
    tmp = COOLDOWN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(COOLDOWN_FILE)


def clear_cooldown(account: str) -> None:
    data = cooldowns()
    if data.pop(account, None) is not None:
        COOLDOWN_FILE.write_text(json.dumps(data, indent=2))


def usable_accounts() -> list[str]:
    """Accounts with a login that aren't cooling down."""
    cool = cooldowns()
    return [n for n in tokens.list_accounts() if n not in cool and tokens.has_token(n)]


@contextmanager
def _lock() -> Iterator[None]:
    LEASES_DIR.mkdir(parents=True, exist_ok=True)
    with open(LEASES_DIR / ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


class Lease:
    def __init__(self, account: str, path: Path):
        self.account = account
        self.path = path

    def release(self) -> None:
        self.path.unlink(missing_ok=True)


def try_lease(want: str, cap: Optional[int] = None) -> Optional[Lease]:
    """want = account name or 'auto'. Returns a lease or None if nothing is free right now."""
    cap = cap or int(load_settings().get("per_account", 2))
    with _lock():
        if want == "auto":
            cands = [(active_leases(a), a) for a in usable_accounts()]
            cands = [c for c in cands if c[0] < cap]
            if not cands:
                return None
            account = min(cands)[1]
        else:
            if want in cooldowns() or active_leases(want) >= cap:
                return None
            account = want
        d = LEASES_DIR / account
        d.mkdir(parents=True, exist_ok=True)
        p = d / uuid.uuid4().hex[:12]
        p.write_text(f"{os.getpid()} {time.time():.0f}\n")
        return Lease(account, p)


async def acquire(want: str, cap: Optional[int] = None, on_wait=None) -> Lease:
    waited = False
    while True:
        lease = try_lease(want, cap)
        if lease:
            return lease
        if want == "auto" and not usable_accounts():
            if not tokens.list_accounts():
                raise RuntimeError("no Claude login: `agent-runner token set NAME` (token from `claude setup-token`), "
                                   "or set AGENT_RUNNER_TOKEN_<NAME> / ANTHROPIC_API_KEY")
        if on_wait and not waited:
            on_wait()
            waited = True
        await asyncio.sleep(5)
