import json
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import bridges, catalog, events  # noqa: E402

SECRET = "test-secret"

TASKS = [
    {"id": "methods-card", "title": "Methods card", "subtitle": "Cited methods summary", "icon": "flask",
     "prompt": "Pull passages and write a cited methods card.", "flow": "tasks/flows/methods-card.yaml",
     "inputs": [{"name": "doi", "label": "DOI", "default": "10.1093/glycob/cwag064"}]},
    {"id": "small-calc", "title": "Small calculation", "subtitle": "Ion–water energy with xtb", "icon": "atom",
     "prompt": "Run a tiny calculation.", "flow": "tasks/flows/small-calc.yaml", "inputs": []},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "runner"
    (home / "runs").mkdir(parents=True)
    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text(json.dumps({"tasks": TASKS}))
    monkeypatch.setenv("AGENT_RUNNER_HOME", str(home))
    monkeypatch.setenv("MACRAE_SERVER_DATA", str(tmp_path / "server-data"))
    monkeypatch.setenv("MACRAE_TASKS_FILE", str(tasks_file))
    monkeypatch.setenv("MACRAE_TOOL_SECRET", SECRET)
    monkeypatch.setenv("MACRAE_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("MACRAE_PAPERS_DIR", str(tmp_path / "papers"))
    monkeypatch.setenv("MODAL_CONFIG_PATH", str(tmp_path / "no-modal.toml"))
    for k in ("MACRAE_AUTH_DISABLED", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "MACRAE_MAX_ACTIVE_RUNS",
              # v2: never call the real Anthropic API or post anywhere from tests
              "ANTHROPIC_API_KEY", "MACRAE_PLANNER_API_KEY", "MACRAE_PLANNER", "MACRAE_LIVE_URL",
              "MACRAE_COMPUTE_RATES", "MACRAE_EVOLVE",
              # public safety (server/safety.py): defaults unless a test sets them
              "MACRAE_KILL", "MACRAE_ADMIN_SECRET", "MACRAE_DAILY_BUDGET_USD", "MACRAE_RUN_RESERVE_USD",
              "MACRAE_BUDGET_HARD_STOP", "MACRAE_RATE_LIMITS"):
        monkeypatch.delenv(k, raising=False)
    events.reset_cache()
    from server import safety
    safety.reset()  # rate-limit counters and cost cache are per process
    bridges._stats_cache.update(key=None)
    catalog._tasks_cache.update(key=None)
    return types.SimpleNamespace(tmp=tmp_path, home=home, runs=home / "runs", jobs=home / "jobs",
                                 tasks_file=tasks_file)


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient

    from server.app import app
    c = TestClient(app)
    c.headers.update({"X-Macrae-Secret": SECRET})
    return c


@pytest.fixture
def fake_modules(monkeypatch):
    """Stand-ins for rag and tasks.runner (the real ones belong to other modules)."""
    started = []

    rag = types.ModuleType("rag")

    def search(query, k=6):
        if "nothing" in query:
            return []
        cit = {"key": "", "title": "Ion pairing in water", "authors": "V. Košťál; P. Jungwirth", "year": 2026,
               "journal": "JCTC", "doi": "10.1021/acs.jctc.5c02051", "page": 3,
               "url": "https://doi.org/10.1021/acs.jctc.5c02051", "quote": "Ions pair strongly."}
        return [{"id": f"doi:10.1021/acs.jctc.5c02051#p3c{i}", "text": f"Passage {i} about {query}.",
                 "score": 0.9 - i / 10, "citation": dict(cit, key=f"[{i + 1}]")} for i in range(min(k, 2))]

    def format_context(passages):
        cits = [p["citation"] for p in passages]
        return "\n".join(f"{c['key']} {p['text']}" for p, c in zip(passages, cits)), cits

    rag.search = search
    rag.format_context = format_context
    rag.stats = lambda: {"papers": 3, "chunks": 42}

    tasks_pkg = types.ModuleType("tasks")
    tasks_pkg.__path__ = []
    runner = types.ModuleType("tasks.runner")

    def start(task_id, inputs):
        rid = f"20261008-100000-{task_id}-{len(started):04d}"
        (Path(__import__("os").environ["AGENT_RUNNER_HOME"]) / "runs" / rid).mkdir(parents=True, exist_ok=True)
        started.append((task_id, inputs, rid))
        return rid

    runner.start = start
    tasks_pkg.runner = runner
    monkeypatch.setitem(sys.modules, "rag", rag)
    monkeypatch.setitem(sys.modules, "tasks", tasks_pkg)
    monkeypatch.setitem(sys.modules, "tasks.runner", runner)
    return types.SimpleNamespace(started=started, rag=rag, runner=runner)


# ── a realistic run on disk ──────────────────────────────────────────────────

T0 = 1791450000.0  # 2026-10-08 ~


def iso(t):
    frac = f"{t % 1:.3f}"[1:]
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + frac + "Z"


def hms(t):
    return time.strftime("%H:%M:%S", time.localtime(t))


def write_state(run_dir: Path, **over):
    st = {"id": run_dir.name, "name": "methods-card", "file": str(ROOT / "tasks/flows/methods-card.yaml"),
          "status": "running", "started": T0, "finished": None, "pid": __import__("os").getpid(),
          "workdir": str(ROOT), "jobs_dir": str(run_dir.parent.parent / "jobs" / run_dir.name),
          "vars": {"doi": "10.1093/glycob/cwag064"}, "levels": [["passages"], ["card"]], "steps": {}, "order": []}
    st.update(over)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(json.dumps(st))
    return st


def atif(steps):
    return {"schema_version": "ATIF-v1.2", "session_id": "s1",
            "agent": {"name": "claude-code", "version": "2.0.0", "model_name": "claude-opus-5-5"}, "steps": steps}


TRAJECTORY = atif([
    {"step_id": 1, "timestamp": iso(T0 + 10), "source": "user", "message": "Write a cited methods card."},
    {"step_id": 2, "timestamp": iso(T0 + 11), "source": "agent", "message": "I'll start by reading the paper.",
     "tool_calls": [{"tool_call_id": "toolu_1", "function_name": "Read",
                     "arguments": {"file_path": "/app/paper/paper.json"}}],
     "observation": {"results": [{"source_call_id": "toolu_1",
                                  "content": "{\"doi\": \"10.1093/glycob/cwag064\", \"title\": \"GAGs\"}"}]}},
    {"step_id": 3, "timestamp": iso(T0 + 12), "source": "agent", "message": "",
     "tool_calls": [{"tool_call_id": "toolu_2", "function_name": "Bash",
                     "arguments": {"command": "xtb dimer.xyz --gfn 2", "description": "Run water dimer energy"}}],
     "observation": {"results": [{"source_call_id": "toolu_2", "content": "TOTAL ENERGY -10.123456 Eh"}]}},
    {"step_id": 4, "timestamp": iso(T0 + 12.4), "source": "agent", "message": "",
     "tool_calls": [{"tool_call_id": "toolu_3", "function_name": "Write",
                     "arguments": {"file_path": "/app/paper/card.md", "content": "# Methods\nMD with [1]."}}],
     "observation": {"results": [{"source_call_id": "toolu_3", "content": "File created"}]}},
    {"step_id": 5, "timestamp": iso(T0 + 13), "source": "agent",
     "message": "Done. The card is in card.md and every claim is cited."},
])


def make_agent_run(env, run_id="20261008-100000-methods-card-ab12", status="running", step_status="running",
                   trajectory=True, stream_lines=None, result_json=True):
    run_dir = env.runs / run_id
    jobs = env.jobs / run_id
    job = jobs / "card-0-a1"
    trial = job / "card-0__abc"
    (trial / "agent").mkdir(parents=True)
    (job / "config.json").write_text("{}")
    (trial / "config.json").write_text("{}")
    if trajectory:
        (trial / "agent" / "trajectory.json").write_text(json.dumps(TRAJECTORY))
    if stream_lines is not None:
        (trial / "agent" / "claude-code.txt").write_text("\n".join(json.dumps(x) for x in stream_lines) + "\n")
    if result_json:
        (trial / "result.json").write_text(json.dumps({"task_name": "x", "started_at": iso(T0 + 9),
                                                       "finished_at": iso(T0 + 14) if step_status != "running"
                                                       else None, "verifier_result": {"rewards": {"reward": 1.0}}}))
    steps = {
        "passages": {"key": "passages", "id": "passages", "kind": "run", "status": "ok", "started": T0 + 1,
                     "finished": T0 + 3, "attempt": 1, "output": "[...]"},
        "card": {"key": "card", "id": "card", "kind": "agent", "status": "running", "fanout": 1},
        "card[0]": {"key": "card[0]", "id": "card", "kind": "agent", "status": step_status, "attempt": 1,
                    "started": T0 + 5, "account": "alice", "job_dir": str(job),
                    **({"finished": T0 + 15, "reward": 1.0, "output": "Done. The card is in card.md."}
                       if step_status == "ok" else {})},
    }
    st = write_state(run_dir, status=status, steps=steps, order=["passages", "card", "card[0]"],
                     finished=T0 + 16 if status != "running" else None)
    logs = run_dir / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "passages.log").write_text(
        f"[{hms(T0 + 1)}] queued (run)\n"
        f"[{hms(T0 + 1)}] $ python -m rag search \"glycosaminoglycan methods\" --k 6 --json  (cwd {ROOT})\n"
        f"[{hms(T0 + 3)}] attempt 1: ok reward=None passed=True\n")
    (logs / "card-0.log").write_text(
        f"[{hms(T0 + 5)}] queued (agent)\n"
        f"[{hms(T0 + 5)}] harbor (alice): /usr/bin/harbor exec -a claude-code --jobs-dir {jobs} --job-name card-0-a1 "
        f"-k 1 -q -i Write a cited methods card for the paper. -p /tmp/x --no-scan -e modal\n")
    outs = run_dir / "outputs"
    outs.mkdir(exist_ok=True)
    (outs / "passages-a1.txt").write_text(json.dumps({"passages": [
        {"id": "p1", "text": "We used MD with CHARMM36.", "score": 0.8,
         "citation": {"key": "[1]", "title": "GAG structure", "authors": "M. Riopedre Fernández; D. Biriukov",
                      "year": 2026, "journal": "Glycobiology", "doi": "10.1093/glycob/cwag064", "page": 2,
                      "url": "https://doi.org/10.1093/glycob/cwag064", "quote": "We used MD."}}]}))
    return run_dir, st, trial
