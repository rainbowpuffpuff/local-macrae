"""Cross-module: rag index → tasks flow through the real agent_runner engine → the server's API, as the page sees it.

Harbor (Modal + Claude) is tasks/tests/fake_harbor.py; everything else is the real code. Skips if the server or rag
dependencies aren't installed.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOI = "10.1039/d6sm00560h"  # rag/tests/fixtures/vesicle.pdf, a group paper
SECRET = "integration-secret"

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("fitz")


def _env(tmp_path: Path) -> dict:
    papers, index, home = tmp_path / "papers", tmp_path / "index", tmp_path / "home"
    papers.mkdir()
    home.mkdir()
    for pdf in (ROOT / "rag" / "tests" / "fixtures").glob("*.pdf"):
        shutil.copy(pdf, papers / pdf.name)
    (home / "settings.json").write_text('{"agent_image": ""}')
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "harbor"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "tasks" / "tests" / "fake_harbor.py"}" "$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    # an empty keyring: the developer's own stored Claude accounts must not log the test in
    keyring = bindir / "secret-tool"
    keyring.write_text("#!/bin/sh\nexit 1\n")
    keyring.chmod(keyring.stat().st_mode | stat.S_IEXEC)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("MODAL_", "AGENT_RUNNER_", "CLAUDE", "MACRAE_")) and k != "ANTHROPIC_API_KEY"}
    env.update(PATH=f"{bindir}{os.pathsep}{env.get('PATH', '')}", FAKE_HARBOR_LOG=str(tmp_path / "harbor.jsonl"),
               AGENT_RUNNER_HOME=str(home), ANTHROPIC_API_KEY="sk-ant-api03-test", MACRAE_OFFLINE="1",
               MACRAE_PAPERS_DIR=str(papers), MACRAE_INDEX_DIR=str(index), MACRAE_EMBEDDER="hash",
               PYTHONPATH=str(ROOT))
    return env


def test_methods_card_run_is_traced_end_to_end(tmp_path, monkeypatch):
    env = _env(tmp_path)
    ing = subprocess.run([sys.executable, "-m", "rag", "ingest"], env=env, cwd=ROOT, capture_output=True,
                         text=True, timeout=120)
    assert ing.returncode == 0 and json.loads(ing.stdout)["papers"] == 2, ing.stderr

    r = subprocess.run([sys.executable, "-m", "agent_runner", "flow", "run", str(ROOT / "tasks/flows/methods-card.yaml"),
                        "--var", f"python={sys.executable}", "--var", f"doi={DOI}"],
                       env=env, cwd=ROOT, capture_output=True, text=True, timeout=240)
    assert r.stdout.strip().endswith("ok"), r.stdout + r.stderr

    for k in ("AGENT_RUNNER_HOME", "MACRAE_PAPERS_DIR", "MACRAE_INDEX_DIR", "MACRAE_EMBEDDER"):
        monkeypatch.setenv(k, env[k])
    monkeypatch.setenv("MACRAE_TOOL_SECRET", SECRET)
    monkeypatch.setenv("MACRAE_SERVER_DATA", str(tmp_path / "server-data"))
    monkeypatch.setenv("MODAL_CONFIG_PATH", str(tmp_path / "no-modal.toml"))
    monkeypatch.delenv("MACRAE_TASKS_FILE", raising=False)
    from fastapi.testclient import TestClient

    from server import events
    from server.app import app

    events.reset_cache()
    c = TestClient(app, headers={"X-Macrae-Secret": SECRET})
    runs = c.get("/api/runs").json()["runs"]
    assert len(runs) == 1 and runs[0]["task_id"] == "methods-card" and runs[0]["status"] == "ok"
    res = c.get(f"/api/runs/{runs[0]['run_id']}/events").json()
    assert res["done"] is True
    evs = res["events"]
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    by = lambda t: [e for e in evs if e["type"] == t]  # noqa: E731

    # script step `sources` (python -m tasks.prepare_methods): searches and citations, not a calculation
    searches = [e for e in by("search") if e["step"] == "sources"]
    assert len(searches) == 6 and all(e["title"].startswith("Searched papers: ") for e in searches), searches
    assert not [e for e in by("calc") if e["step"] in ("sources", "collect")]
    started = next(e for e in evs if e["step"] == "sources" and e["detail"].startswith("$ "))
    assert started["type"] == "status" and started["title"].startswith("Pulled the paper's passages")
    cites = [e for e in by("cite") if e["step"] == "sources"]
    assert cites and any((e["citation"] or {}).get("doi") == DOI for e in cites)
    # agent step: the fake trial's ATIF trajectory has a Read of paper/context.md and a final message
    card = [e["type"] for e in evs if e["step"] == "card"]
    assert "read" in card and "think" in card and card[-1] == "result", card
    assert evs[-1]["type"] == "result" and evs[-1]["step"] == ""

    st = c.post("/api/tools/run_status", json={"run_id": runs[0]["run_id"]}).json()
    assert st["status"] == "ok" and "finished" in st["summary"]


def test_failed_run_explains_itself_in_order(tmp_path, monkeypatch):
    """No Claude login: the agent step fails, collect is skipped; the page shows why, in the order it happened."""
    env = _env(tmp_path)
    del env["ANTHROPIC_API_KEY"]
    r = subprocess.run([sys.executable, "-m", "agent_runner", "flow", "run", str(ROOT / "tasks/flows/methods-card.yaml"),
                        "--var", f"python={sys.executable}"], env=env, cwd=ROOT, capture_output=True, text=True,
                       timeout=240)
    assert r.stdout.strip().endswith("failed"), r.stdout + r.stderr

    monkeypatch.setenv("AGENT_RUNNER_HOME", env["AGENT_RUNNER_HOME"])
    monkeypatch.setenv("MACRAE_TOOL_SECRET", SECRET)
    monkeypatch.setenv("MACRAE_SERVER_DATA", str(tmp_path / "server-data"))
    monkeypatch.delenv("MACRAE_TASKS_FILE", raising=False)
    from fastapi.testclient import TestClient

    from server import events
    from server.app import app

    events.reset_cache()
    c = TestClient(app, headers={"X-Macrae-Secret": SECRET})
    run_id = c.get("/api/runs").json()["runs"][0]["run_id"]
    evs = c.get(f"/api/runs/{run_id}/events").json()["events"]
    titles = [e["title"] for e in evs]
    assert titles.index("Skipped collect") > titles.index("card failed") > titles.index("Finished sources"), titles
    failed = next(e for e in evs if e["title"] == "card failed")
    assert failed["type"] == "error" and "agent-runner accounts set NAME" in failed["detail"]
    assert evs[-1]["title"] == "Run failed"
