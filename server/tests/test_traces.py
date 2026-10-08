"""GET /api/runs/{id}/trace.zip and /report.html (server/traces.py, server/report.py).

Uses evolve's fixture runs (real-shaped agent_runner + Harbor folders: two attempts, a failed then a passing check,
ATIF trajectories, claude-code.txt, artifacts) installed into a temporary AGENT_RUNNER_HOME.
"""

import hashlib
import importlib.util
import io
import json
import re
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import ROOT, SECRET, make_agent_run

from server import report, traces

_spec = importlib.util.spec_from_file_location("trace_fixtures", ROOT / "evolve" / "tests" / "trace_fixtures.py")
fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixtures)

A = fixtures.A  # small-calc Na+: attempt 1 rejected by the check, attempt 2 ok
TOP = f"macrae-trace-{A}/"
LIVE_TOKEN = "live-tok-9f8e7d6c5b4a"


def install(env, run=A):
    fixtures.install(env.home, [run])
    run_dir = env.runs / run
    live = run_dir / "live"
    live.mkdir(exist_ok=True)
    (live / "token").write_text(LIVE_TOKEN)
    (live / "calc.jsonl").write_text(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}) + "\n")
    return run_dir, env.jobs / run


def unzip(resp) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(resp.content))


def all_text(z: zipfile.ZipFile) -> str:
    return "\n".join(z.read(n).decode("utf-8", errors="replace") for n in z.namelist())


# ── trace.zip ────────────────────────────────────────────────────────────────


def test_zip_has_the_whole_run(env, client):
    install(env)
    r = client.get(f"/api/runs/{A}/trace.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert f'filename="macrae-trace-{A}.zip"' in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"
    z = unzip(r)
    names = set(z.namelist())
    assert all(n.startswith(TOP) for n in names)
    for n in ["report.html", "README.md", "run.json", "events.json", "manifest.json",
              "run/state.json", "run/flow.yaml", "run/logs/calc.log", "run/outputs/prepare-a1.txt", "run/live/calc.jsonl",
              "jobs/calc-a1/calc-a1__Xk2pQ/agent/trajectory.json", "jobs/calc-a2/calc-a2__Ru7Lm/agent/trajectory.json",
              "jobs/calc-a2/calc-a2__Ru7Lm/agent/claude-code.txt", "jobs/calc-a2/calc-a2__Ru7Lm/result.json",
              "jobs/calc-a2/calc-a2__Ru7Lm/artifacts/app/calc/result.json", "jobs/calc-a2/result.json"]:
        assert TOP + n in names, n
    # the run's live token never leaves the server, as a file or as a value
    assert TOP + "run/live/token" not in names
    assert LIVE_TOKEN not in all_text(z)
    # the files are the files (no redaction needed in the fixture's trajectory)
    src = env.jobs / A / "calc-a2" / "calc-a2__Ru7Lm" / "agent" / "trajectory.json"
    assert z.read(TOP + "jobs/calc-a2/calc-a2__Ru7Lm/agent/trajectory.json") == src.read_bytes()
    # manifest: every packed file with its real size and hash; the token listed as skipped
    man = json.loads(z.read(TOP + "manifest.json"))
    assert man["run_id"] == A and man["source"] == "local"
    for f in man["files"]:
        data = z.read(TOP + f["path"])
        assert len(data) == f["size"] and hashlib.sha256(data).hexdigest() == f["sha256"]
    assert {"path": "run/live/token", "why": "secret"} in man["skipped"]
    # run.json is the run view's object; events.json the page's events
    run = json.loads(z.read(TOP + "run.json"))
    assert run["run_id"] == A and run["status"] == "ok" and run["costs"]["llm_usd"] > 0
    evs = json.loads(z.read(TOP + "events.json"))["events"]
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    assert evs == client.get(f"/api/runs/{A}/events").json()["events"]


def test_zip_redacts_secrets(env, client, monkeypatch):
    run_dir, jobs = install(env)
    monkeypatch.setenv("AGENT_RUNNER_TOKEN_MAIN", "sk-ant-oat01-AbCdEf123456789")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "as-ZZmodalsecretZZ")
    cfg = jobs / "calc-a2" / "config.json"
    cfg.write_text(json.dumps({"agents": [{"name": "claude-code", "env": {
        "MACRAE_LIVE_TOKEN": LIVE_TOKEN, "CLAUDE_CODE_OAUTH_TOKEN": "oauth-value-1234567", "MACRAE_RUN_ID": A}}]}))
    log = run_dir / "logs" / "calc.log"
    log.write_text(log.read_text() + f"[12:00:00] env ANTHROPIC_API_KEY=sk-ant-api03-xyzxyzxyz123 secret {SECRET}\n"
                   "[12:00:01] Authorization: Bearer abcdefghijklmnop\n"
                   "[12:00:02] modal as-ZZmodalsecretZZ and the main login sk-ant-oat01-AbCdEf123456789\n")
    z = unzip(client.get(f"/api/runs/{A}/trace.zip"))
    text = all_text(z)
    for secret in (LIVE_TOKEN, "oauth-value-1234567", "sk-ant-api03-xyzxyzxyz123", SECRET, "abcdefghijklmnop",
                   "as-ZZmodalsecretZZ", "sk-ant-oat01-AbCdEf123456789"):
        assert secret not in text, secret
    cfg_out = json.loads(z.read(TOP + "jobs/calc-a2/config.json"))
    assert cfg_out["agents"][0]["env"] == {"MACRAE_LIVE_TOKEN": "***", "CLAUDE_CODE_OAUTH_TOKEN": "***",
                                           "MACRAE_RUN_ID": A}
    man = json.loads(z.read(TOP + "manifest.json"))
    redacted = {f["path"] for f in man["files"] if f.get("redacted")}
    assert {"jobs/calc-a2/config.json", "run/logs/calc.log"} <= redacted
    # what isn't secret stays as it was (citation keys, token counts, file paths)
    assert '"key": "[1]"' in z.read(TOP + "run/outputs/prepare-a1.txt").decode() or \
        "[1]" in z.read(TOP + "run/outputs/prepare-a1.txt").decode()
    assert "n_input_tokens" in z.read(TOP + "jobs/calc-a2/calc-a2__Ru7Lm/result.json").decode()


def test_zip_size_limits(env, client, monkeypatch):
    _, jobs = install(env)
    big = jobs / "calc-a2" / "calc-a2__Ru7Lm" / "artifacts" / "app" / "calc" / "traj.xtc"
    big.write_bytes(b"\x01" * 300_000)
    monkeypatch.setenv("MACRAE_TRACE_MAX_FILE_MB", "0.2")
    z = unzip(client.get(f"/api/runs/{A}/trace.zip"))
    man = json.loads(z.read(TOP + "manifest.json"))
    skipped = {s["path"]: s for s in man["skipped"]}
    arc = "jobs/calc-a2/calc-a2__Ru7Lm/artifacts/app/calc/traj.xtc"
    assert arc in skipped and skipped[arc]["size"] == 300_000 and "MAX_FILE" in skipped[arc]["why"]
    assert TOP + arc not in z.namelist()


def test_zip_skips_symlinks_out_of_the_run(env, client, tmp_path):
    run_dir, _ = install(env)
    outside = tmp_path / "outside.txt"
    outside.write_text("not part of the run")
    (run_dir / "outputs" / "link.txt").symlink_to(outside)
    (run_dir / "linkdir").symlink_to(tmp_path)
    z = unzip(client.get(f"/api/runs/{A}/trace.zip"))
    assert not any("link" in n for n in z.namelist())
    assert "not part of the run" not in all_text(z)


def test_unknown_bad_and_unauthorized(env, client):
    install(env)
    assert client.get("/api/runs/nope/trace.zip").status_code == 404
    assert client.get("/api/runs/nope/report.html").status_code == 404
    assert client.get("/api/runs/..%2F..%2Fetc/trace.zip").status_code == 404
    assert client.get(f"/api/runs/{A}/trace.zip", headers={"X-Macrae-Secret": "wrong"}).status_code == 401
    assert client.get(f"/api/runs/{A}/report.html", headers={"X-Macrae-Secret": ""}).status_code == 401


def test_jobs_dir_must_belong_to_the_run(env, client):
    """A state.json pointing jobs_dir at some other tree doesn't get that tree packed."""
    run_dir, _ = install(env)
    st = json.loads((run_dir / "state.json").read_text())
    st["jobs_dir"] = str(env.tmp)
    (run_dir / "state.json").write_text(json.dumps(st))
    z = unzip(client.get(f"/api/runs/{A}/trace.zip"))
    # falls back to <jobs root>/<run id>, which is the run's real jobs folder
    assert TOP + "jobs/calc-a2/calc-a2__Ru7Lm/agent/trajectory.json" in z.namelist()
    assert not any("tasks.json" in n for n in z.namelist())


# ── report.html ──────────────────────────────────────────────────────────────


def test_report_is_self_contained_and_complete(env, client):
    _, jobs = install(env)
    art = jobs / "calc-a2" / "calc-a2__Ru7Lm" / "artifacts" / "app" / "calc"
    (art / "manuscript.md").write_text("# Na+ binding\n\nThe ECC energy is **lower** [1].\n\n- item <script>x</script>\n")
    r = client.get(f"/api/runs/{A}/report.html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-disposition"] == f'inline; filename="macrae-report-{A}.html"'
    h = r.text
    # offline and CSP-safe: no scripts, no external resources (links to DOIs are fine, nothing is loaded)
    assert "<script" not in h.lower()
    assert not re.search(r"""(src|href)=["']?(//|https?:)[^"']*\.(js|css|png|svg|woff2?)""", h)
    assert not re.search(r"<link\b", h) and not re.search(r"@import|url\(", h)
    # the sections
    for sec in ("summary", "steps", "timeline", "tools", "checks", "results", "manuscript", "citations", "files"):
        assert f'id="{sec}"' in h, sec
    assert "Ion–water binding" in h  # the task's title, from macrae.json/tasks
    evs = client.get(f"/api/runs/{A}/events").json()["events"]
    for ev in evs:  # every trace event is on the timeline
        assert f'id="ev-{ev["seq"]}"' in h
    # tool calls with their inputs and outputs, from both attempts' trajectories
    traj = json.loads((jobs / "calc-a2" / "calc-a2__Ru7Lm" / "agent" / "trajectory.json").read_text())
    calls = [c for s in traj["steps"] for c in s.get("tool_calls") or []]
    assert calls and all(f'<b class="mono">{c["function_name"]}</b>' in h for c in calls)
    assert h.count('class="trial"') >= 2
    # the check: attempt 1 failed with the checker's reason, attempt 2 passed
    assert "e_coulomb_ecc_kcal_mol" in h and "check exit" not in h.split('id="checks"')[1].split("</section>")[0]
    checks = h.split('id="checks"')[1].split("</section>")[0]
    assert checks.count("st-failed") == 1 and checks.count("st-ok") == 1
    # costs, results and the manuscript (rendered, and escaped)
    assert "Total cost" in h and "$" in h
    assert "artifacts/app/calc/result.json" in h
    assert "<h3>Na+ binding</h3>" in h and "<b>lower</b>" in h and '<span class="ref">[1]</span>' in h
    assert "&lt;script&gt;x&lt;/script&gt;" in h
    # citations, linked to their DOI
    assert 'href="https://doi.org/10.1021/acs.jpcb.0c09009"' in h or "doi.org/10.1021/acs.jpcb.0c09009" in h


def test_report_download_and_zip_copy(env, client):
    install(env)
    r = client.get(f"/api/runs/{A}/report.html?download=1")
    assert r.headers["content-disposition"].startswith("attachment;")
    z = unzip(client.get(f"/api/runs/{A}/trace.zip"))
    zh = z.read(TOP + "report.html").decode()
    assert zh.count('class="ev ') == r.text.count('class="ev ')


def test_report_of_a_running_run(env, client):
    run_id = "20261008-100000-methods-card-ab12"
    make_agent_run(env, run_id=run_id, status="running", step_status="running")
    r = client.get(f"/api/runs/{run_id}/report.html")
    assert r.status_code == 200
    assert "snapshot of a running run" in r.text
    assert "xtb dimer.xyz" in r.text  # the agent's call so far
    z = unzip(client.get(f"/api/runs/{run_id}/trace.zip"))
    assert f"macrae-trace-{run_id}/jobs/card-0-a1/card-0__abc/agent/trajectory.json" in z.namelist()


def test_report_of_a_run_without_agent_steps(env, client):
    fixtures.install(env.home, [fixtures.D])  # failed before the agent started: no Claude login
    r = client.get(f"/api/runs/{fixtures.D}/report.html")
    assert r.status_code == 200
    assert "failed" in r.text and 'id="tools"' not in r.text


# ── R2 ───────────────────────────────────────────────────────────────────────


class FakeR2:
    """The Worker's R2 endpoint (cloudflare/worker.js dataHandler) over a dict, as deploy/tests uses it."""

    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects
        self.gets: list[str] = []
        store = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                if u.path == "/":
                    q = urllib.parse.parse_qs(u.query)
                    prefix = q.get("prefix", [""])[0]
                    objs = [{"key": k, "size": len(v), "mtime": 1791450000.0} for k, v in sorted(store.objects.items())
                            if k.startswith(prefix)]
                    page, cursor = (objs[:2], "2") if "cursor" not in q and len(objs) > 2 else (
                        objs[int(q["cursor"][0]):] if "cursor" in q else objs, None)
                    body = json.dumps({"objects": page, "cursor": cursor}).encode()
                    self.send_response(200)
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                key = urllib.parse.unquote(u.path[1:])
                store.gets.append(key)
                if key not in store.objects:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = store.objects[key]
                self.send_response(200)
                self.send_header("content-length", str(len(body)))
                self.send_header("x-macrae-mtime", "1791450000.000")
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def r2_run(env, tmp_path, monkeypatch):
    """Run A only in R2 (as deploy/r2sync.py pushes it), not on this disk."""
    staging = tmp_path / "staging"
    fixtures.install(staging, [A])
    live = staging / "runs" / A / "live"
    live.mkdir()
    (live / "token").write_text(LIVE_TOKEN)
    objects = {}
    for part in ("runs", "jobs"):
        for p in (staging / part).rglob("*"):
            if p.is_file():
                objects[f"state/{part}/{p.relative_to(staging / part).as_posix()}"] = p.read_bytes()
    objects[f"state/macrae/runs/{A}.json"] = json.dumps({"run_id": A, "task_id": "small-calc",
                                                         "title": "Ion–water binding", "inputs": {"ion": "Na+"}}).encode()
    objects["state/runs/other-run/state.json"] = b"{}"
    fake = FakeR2(objects)
    monkeypatch.setenv("MACRAE_SYNC_URL", fake.url)
    monkeypatch.setenv("MACRAE_TRACE_CACHE", str(tmp_path / "cache"))
    yield fake
    fake.close()


def test_zip_from_r2(env, client, r2_run, tmp_path):
    assert not (env.runs / A).exists()
    r = client.get(f"/api/runs/{A}/trace.zip")
    assert r.status_code == 200
    z = unzip(r)
    names = set(z.namelist())
    assert TOP + "jobs/calc-a2/calc-a2__Ru7Lm/agent/trajectory.json" in names
    assert TOP + "run/logs/calc.log" in names and TOP + "server/run.json" in names
    assert TOP + "run/live/token" not in names and LIVE_TOKEN not in all_text(z)
    man = json.loads(z.read(TOP + "manifest.json"))
    assert man["source"] == "r2"
    evs = json.loads(z.read(TOP + "events.json"))["events"]
    assert any(e["type"] == "calc" or e["type"] == "write" for e in evs) and evs[-1]["title"] == "Run finished"
    assert "(R2)" in z.read(TOP + "README.md").decode()
    assert "Ion–water binding" in z.read(TOP + "report.html").decode()
    # read into the cache, never into the runner's folders; other runs aren't fetched
    assert not (env.runs / A).exists() and not (env.jobs / A).exists()
    assert (tmp_path / "cache" / A / "runs" / A / "state.json").is_file()
    assert not any(k.startswith("state/runs/other-run") for k in r2_run.gets)
    # a second download only fetches what changed (nothing)
    n = len(r2_run.gets)
    assert client.get(f"/api/runs/{A}/report.html").status_code == 200
    assert len(r2_run.gets) == n


def test_r2_missing_run_and_r2_down(env, client, r2_run, monkeypatch):
    assert client.get("/api/runs/20991231-000000-nothing-0000/trace.zip").status_code == 404
    monkeypatch.setenv("MACRAE_SYNC_URL", "http://127.0.0.1:9")  # nothing listens
    r = client.get(f"/api/runs/{A}/trace.zip")
    assert r.status_code == 503 and "R2" in r.json()["detail"]
    monkeypatch.delenv("MACRAE_SYNC_URL")
    assert client.get(f"/api/runs/{A}/trace.zip").status_code == 404


# ── units ────────────────────────────────────────────────────────────────────


def test_checks_from_log(env):
    run_dir, _ = install(env)
    st = json.loads((run_dir / "state.json").read_text())
    checks = traces.checks_from_log(run_dir / "logs" / "calc.log", float(st["started"]))
    assert [(c["attempt"], c["exit"], c["passed"], c["reward"]) for c in checks] == [(1, 1, False, "0.0"),
                                                                                    (2, 0, True, "1.0")]
    assert "tasks/checks.py" in checks[0]["command"] and checks[0]["cwd"].endswith("artifacts/app/calc")
    assert "cites [4]" in checks[0]["output"]


def test_redactor_leaves_ordinary_values(monkeypatch):
    monkeypatch.setenv("SOME_TOKEN_MODE", "disabled")
    monkeypatch.setenv("TOKEN_FILE_PASSWORD", "/root/.secret")
    monkeypatch.setenv("MY_API_KEY", "k-123456789")
    red = traces.Redactor()
    s = red.text('mode disabled at /root/.secret; key k-123456789; {"key": "[1]", "tokens": {"in": 5}}')
    assert s == 'mode disabled at /root/.secret; key ***; {"key": "[1]", "tokens": {"in": 5}}'
    assert red.text("MACRAE_LIVE_TOKEN=***") == "MACRAE_LIVE_TOKEN=***"


def test_markdown_subset_is_escaped():
    h = report.markdown("## T <img src=x onerror=1>\n\nA [link](javascript:alert(1)) and [ok](https://x.org) [2, 3].\n"
                        "```\n<b>code</b>\n```\n1. one\n2. two\n")
    assert "<img" not in h and "&lt;img" in h
    assert "javascript:" not in h.replace("[link](javascript:alert(1))", "") or 'href="javascript' not in h
    assert 'href="javascript' not in h and '<a href="https://x.org" rel="noopener">ok</a>' in h
    assert '<span class="ref">[2, 3]</span>' in h and "<pre>&lt;b&gt;code&lt;/b&gt;</pre>" in h
    assert "<ol>" in h and "<li>two</li>" in h
