"""The server against the real agent_runner engine: a script-only flow (no Docker/Harbor), started detached the
way tasks.runner does, followed through the HTTP API until done."""

import json
import os
import subprocess
import sys
import textwrap
import time

import pytest
from conftest import ROOT

pytest.importorskip("yaml")

FAKE_RAG_MAIN = '''
import json, sys
q = sys.argv[2] if len(sys.argv) > 2 else ""
print(json.dumps({"passages": [{"id": "p1", "text": "Ions pair in water.", "score": 0.9, "citation": {
    "key": "[1]", "title": "Ion pairing", "authors": "V. Košťál; P. Jungwirth", "year": 2026, "journal": "JCTC",
    "doi": "10.1021/acs.jctc.5c02051", "page": 3, "url": "https://doi.org/10.1021/acs.jctc.5c02051",
    "quote": "Ions pair in water."}}]}))
'''

FLOW = """
name: e2e-demo
steps:
  - id: passages
    run: python3 -m rag search "{{ vars.topic }}" --json
    outputs: json
  - id: calc
    needs: [passages]
    run: python3 -c "import math; print(round(math.pi, 6), round(math.e, 6), 1.414214)"
    outputs: text
  - id: maybe
    when: "{{ False }}"
    run: echo never
"""


def test_detached_script_flow_end_to_end(env, client):
    work = env.tmp / "work"
    (work / "rag").mkdir(parents=True)
    (work / "rag" / "__init__.py").write_text("")
    (work / "rag" / "__main__.py").write_text(FAKE_RAG_MAIN)
    flow = work / "flow.yaml"
    flow.write_text(textwrap.dedent(FLOW))
    proc_env = dict(os.environ, PYTHONPATH=str(ROOT))
    # what tasks.runner.start does: agent_runner.flows.start_detached(flow, vars)
    out = subprocess.run([sys.executable, "-c", textwrap.dedent(f"""
        from pathlib import Path
        from agent_runner import flows
        print(flows.start_detached(Path({str(flow)!r}), {{"topic": "ion pairing"}}))
    """)], env=proc_env, capture_output=True, text=True, timeout=60, cwd=str(work))
    assert out.returncode == 0, out.stderr
    run_id = out.stdout.strip().splitlines()[-1]

    seen, after, done = [], 0, False
    deadline = time.time() + 60
    while time.time() < deadline and not done:
        r = client.get(f"/api/runs/{run_id}/events", params={"after": after})
        assert r.status_code == 200
        body = r.json()
        for e in body["events"]:
            assert e["seq"] == after + 1  # no gaps, no repeats
            after = e["seq"]
            seen.append(e)
        done = body["done"]
        if not done:
            time.sleep(0.3)
    assert done, (run_id, [e["title"] for e in seen], (env.runs / run_id / "engine.log").read_text())

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "ok"
    assert {s["key"]: s["status"] for s in run["steps"]} == {"passages": "ok", "calc": "ok", "maybe": "skipped"}
    types = [(e["step"], e["type"]) for e in seen]
    assert ("passages", "search") in types
    assert ("passages", "cite") in types
    assert ("calc", "calc") in types
    assert ("maybe", "status") in types
    cite = next(e for e in seen if e["type"] == "cite")
    assert cite["citation"]["doi"] == "10.1021/acs.jctc.5c02051" and cite["title"].startswith("Cited Košťál 2026")
    assert seen[-1]["type"] == "result" and seen[-1]["title"] == "Run finished"
    summary = next(x for x in client.get("/api/runs").json()["runs"] if x["run_id"] == run_id)
    assert summary["title"] == "e2e-demo" and summary["finished"] is not None
    status = client.post("/api/tools/run_status", json={"run_id": run_id}).json()
    assert status["status"] == "ok" and "finished" in status["summary"]
    print(json.dumps([(e["seq"], e["step"], e["type"], e["title"]) for e in seen], ensure_ascii=False, indent=0))
