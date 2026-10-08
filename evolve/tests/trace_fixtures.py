"""Install the fixture runs (fixtures/home, written by fixtures/build.py) into a temporary AGENT_RUNNER_HOME."""

from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
REPO = HERE.parents[1]

A = "20261008-201851-small-calc-71d4"   # small-calc Na+, ok on attempt 2 (module error, check rejected attempt 1)
B = "20261008-214406-small-calc-9c2e"   # small-calc K+, 3 lessons injected, ok on attempt 1
C = "20261008-223015-bff-charges-e1f0"  # BFF, gmx missing, MCMC timeout on attempt 1, ok on attempt 2
D = "20261008-180522-methods-card-ab56"  # failed before the agent started: no Claude login


def _render(text: str, home: Path, base: float) -> str:
    text = re.sub(r'"@E\+([\d.]+)@"', lambda m: repr(base + float(m.group(1))), text)
    text = re.sub(r"@ISO\+([\d.]+)@", lambda m: datetime.fromtimestamp(base + float(m.group(1)), timezone.utc)
                  .isoformat().replace("+00:00", "Z"), text)
    text = re.sub(r"@T\+([\d.]+)@", lambda m: time.strftime("%H:%M:%S", time.localtime(base + float(m.group(1)))),
                  text)
    return text.replace("@HOME@", str(home)).replace("@REPO@", str(REPO))


def install(home: Path, runs: list[str] | None = None, now: float | None = None) -> dict[str, float]:
    """Copy the fixture runs into home (runs/ and jobs/), placeholders filled. Returns run id → start epoch."""
    now = now or time.time()
    starts = json.loads((FIXTURES / "runs.json").read_text())
    out = {}
    for rid, ago in starts.items():
        if runs is not None and rid not in runs:
            continue
        base = float(int(now - ago))
        out[rid] = base
        for sub in ("runs", "jobs"):
            src = FIXTURES / "home" / sub / rid
            if not src.is_dir():
                continue
            dst = home / sub / rid
            shutil.copytree(src, dst)
            for p in dst.rglob("*"):
                if p.is_file():
                    try:
                        p.write_text(_render(p.read_text(), home, base))
                    except UnicodeDecodeError:
                        pass
    return out


def clone_run(home: Path, rid: str, new_id: str, shift: float = 600.0) -> str:
    """A copy of an installed run under another id (a later run that hits the same problems)."""
    for sub in ("runs", "jobs"):
        src = home / sub / rid
        if src.is_dir():
            shutil.copytree(src, home / sub / new_id)
            for p in (home / sub / new_id).rglob("*"):
                if p.is_file():
                    p.write_text(p.read_text().replace(rid, new_id))
    st = json.loads((home / "runs" / new_id / "state.json").read_text())
    st["started"] += shift
    st["finished"] += shift
    (home / "runs" / new_id / "state.json").write_text(json.dumps(st))
    return new_id
