"""Usage event log: one JSON line per thing that happened in the app, for retrospectives.

$AGENT_RUNNER_HOME/events.jsonl. Local only. Never pass secrets in here (tokens, keys).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .config import DATA_DIR

EVENTS_FILE = DATA_DIR / "events.jsonl"
_SECRETISH = ("token", "secret", "password", "key")


def log(event: str, **data: Any) -> None:
    """Best effort: never raises, drops any field whose name looks secret."""
    clean = {k: v for k, v in data.items() if not any(s in k.lower() for s in _SECRETISH)}
    rec = {"t": round(time.time(), 3), "event": event, "pid": os.getpid(), **clean}
    try:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENTS_FILE, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass
