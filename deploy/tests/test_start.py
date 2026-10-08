"""deploy/start.py: env cleanup, data dirs, Modal profile, app discovery, uvicorn command."""

import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("macrae_start", DEPLOY / "start.py")
start = importlib.util.module_from_spec(spec)
spec.loader.exec_module(start)


def test_clean_env_drops_blank_contract_vars_only():
    env = {"MACRAE_PAPERS_DIR": "", "MODAL_PROFILE": "  ", "AGENT_RUNNER_TOKEN_ALICE": "", "MACRAE_TOOL_SECRET": "x",
           "UNRELATED": ""}
    assert start.clean_env(env) == ["AGENT_RUNNER_TOKEN_ALICE", "MACRAE_PAPERS_DIR", "MODAL_PROFILE"]
    assert env == {"MACRAE_TOOL_SECRET": "x", "UNRELATED": ""}


def test_data_dirs_defaults_and_absolute(tmp_path):
    assert start.data_dirs({}, tmp_path) == [tmp_path / "papers", tmp_path / "index"]
    got = start.data_dirs({"MACRAE_PAPERS_DIR": "/data/papers", "MACRAE_INDEX_DIR": "idx",
                           "AGENT_RUNNER_HOME": str(tmp_path / "ar")}, tmp_path)
    assert got == [Path("/data/papers"), tmp_path / "idx", tmp_path / "ar"]


def test_modal_config_new_file():
    text = start.modal_config({"MODAL_TOKEN_ID": "ak-1", "MODAL_TOKEN_SECRET": "as-1"})
    assert text.startswith("[acalincarol]\n")
    assert 'token_id = "ak-1"' in text and 'token_secret = "as-1"' in text and "active = true" in text


def test_modal_config_keeps_existing_profiles():
    existing = '[other]\ntoken_id = "a"\ntoken_secret = "b"\nactive = true\n'
    text = start.modal_config({"MODAL_TOKEN_ID": "ak", "MODAL_TOKEN_SECRET": "as", "MODAL_PROFILE": "demo"}, existing)
    assert text.startswith(existing.rstrip()) and "[demo]" in text
    assert text.count("active = true") == 1
    assert start.modal_config({"MODAL_TOKEN_ID": "ak", "MODAL_TOKEN_SECRET": "as", "MODAL_PROFILE": "other"},
                              existing) is None


def test_modal_config_needs_both_tokens():
    assert start.modal_config({"MODAL_TOKEN_ID": "ak"}) is None


def test_write_modal_config_parses_as_toml(tmp_path):
    tomllib = pytest.importorskip("tomllib")
    p = tmp_path / ".modal.toml"
    assert start.write_modal_config({"MODAL_TOKEN_ID": 'a"k', "MODAL_TOKEN_SECRET": "s\\x"}, p)
    data = tomllib.loads(p.read_text())
    assert data["acalincarol"] == {"token_id": 'a"k', "token_secret": "s\\x", "active": True}
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert not start.write_modal_config({"MODAL_TOKEN_ID": "x", "MODAL_TOKEN_SECRET": "y"}, p)  # already there


@pytest.fixture
def pkgs(tmp_path, monkeypatch):
    """Importable fake server packages under tmp_path."""
    def make(name, body):
        d = tmp_path / name
        d.mkdir()
        (d / "__init__.py").write_text("")
        (d / "app.py").write_text(textwrap.dedent(body))
        return name
    monkeypatch.syspath_prepend(str(tmp_path))
    yield make
    for m in [m for m in sys.modules if m.startswith("fakesrv")]:
        del sys.modules[m]


def test_resolve_app_finds_app(pkgs):
    pkgs("fakesrv_a", "app = object()\n")
    assert start.resolve_app({}, ["fakesrv_missing.app", "fakesrv_a.app"]) == ("fakesrv_a.app:app", False)


def test_resolve_app_factory(pkgs):
    pkgs("fakesrv_b", "def create_app():\n    return object()\n")
    assert start.resolve_app({}, ["fakesrv_b.app"]) == ("fakesrv_b.app:create_app", True)


def test_resolve_app_surfaces_broken_dependency(pkgs):
    pkgs("fakesrv_c", "import definitely_not_installed_xyz\napp = 1\n")
    with pytest.raises(ModuleNotFoundError):
        start.resolve_app({}, ["fakesrv_c.app"])


def test_resolve_app_none_found():
    with pytest.raises(SystemExit, match="MACRAE_APP"):
        start.resolve_app({}, ["fakesrv_nope.app"])


def test_resolve_app_override():
    assert start.resolve_app({"MACRAE_APP": "x.y:app"}) == ("x.y:app", False)
    assert start.resolve_app({"MACRAE_APP": "x.y:make()"}) == ("x.y:make", True)


def test_uvicorn_args():
    prod = start.uvicorn_args("server.app:app", False, reload=False, host="0.0.0.0", port=8080)
    assert prod[1:6] == ["-m", "uvicorn", "server.app:app", "--host", "0.0.0.0"]
    assert "--proxy-headers" in prod and "--reload" not in prod and "--workers" not in prod
    dev = start.uvicorn_args("m:create_app", True, reload=True, host="127.0.0.1", port=9000)
    assert "--factory" in dev and "--reload" in dev and "9000" in dev


def test_check_cli_in_a_fake_repo(tmp_path):
    """`start.py --check` from a repo copy: resolves server.app:app relative to the repo, not the cwd."""
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    (repo / "server").mkdir()
    (repo / "server" / "__init__.py").write_text("")
    (repo / "server" / "app.py").write_text("app = 'asgi'\n")
    (repo / "deploy" / "start.py").write_text((DEPLOY / "start.py").read_text())
    r = subprocess.run([sys.executable, str(repo / "deploy" / "start.py"), "--check"], capture_output=True,
                       text=True, cwd=tmp_path, env={"PATH": "/usr/bin:/bin", "MACRAE_PAPERS_DIR": ""})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "server.app:app"
    assert "ignoring empty MACRAE_PAPERS_DIR" in r.stderr


# ── Cloudflare mode: supervisor, R2 sync, drain ─────────────────────────────────────────────────────────────────

def test_seed_index_copies_the_baked_index_once(tmp_path):
    repo = tmp_path / "repo"
    (repo / "index").mkdir(parents=True)
    (repo / "index" / "papers-1.json").write_text("[]")
    (repo / "index" / "manifest.json").write_text('{"created": 1, "papers": 4, "files": {"papers": "papers-1.json"}}')
    data = tmp_path / "data" / "index"
    assert start.seed_index({"MACRAE_INDEX_DIR": str(data)}, repo) is True
    assert (data / "papers-1.json").exists() and '"papers": 4' in (data / "manifest.json").read_text()
    assert start.seed_index({"MACRAE_INDEX_DIR": str(data)}, repo) is False
    assert start.seed_index({}, repo) is False  # index dir is the repo's own index/ (make serve)


def test_active_runs_needs_a_live_pid(tmp_path):
    for rid, status, pid in (("a", "running", os.getpid()), ("b", "running", 999999), ("c", "ok", os.getpid()),
                             ("d", "starting", os.getpid())):
        (tmp_path / rid).mkdir()
        (tmp_path / rid / "state.json").write_text(json.dumps({"status": status, "pid": pid}))
    (tmp_path / "e").mkdir()
    assert start.active_runs(tmp_path) == ["a", "d"]
    assert start.active_runs(tmp_path / "missing") == []


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(pred, timeout=30.0, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = pred()
            if v:
                return v
        except Exception:  # noqa: BLE001 — server not up yet
            pass
        time.sleep(step)
    raise AssertionError("timed out")


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_supervisor_restores_syncs_and_drains(tmp_path):
    """start.py as on Cloudflare: real server, R2 through the Worker's handler (on Node), SIGTERM with a run going."""
    pytest.importorskip("uvicorn")
    pytest.importorskip("fastapi")
    bucket = tmp_path / "bucket"
    old = bucket / "state" / "runs" / "20261008-000000-old"
    old.mkdir(parents=True)
    (old / "state.json").write_text(json.dumps({"id": old.name, "name": "small-calc", "status": "running", "pid": 1,
                                                "started": 1760000000.0, "steps": {}, "order": []}))
    data = subprocess.Popen(["node", str(DEPLOY.parent / "cloudflare/dev/data-server.mjs"), "--port", "0",
                             "--dir", str(bucket)], stdout=subprocess.PIPE, text=True)
    sync_url = f"http://127.0.0.1:{json.loads(data.stdout.readline())['port']}"
    port, home, secret = _free_port(), tmp_path / "home", "test-secret"
    env = {**os.environ, "MACRAE_SYNC_URL": sync_url, "MACRAE_TOOL_SECRET": secret, "AGENT_RUNNER_HOME": str(home),
           "MACRAE_INDEX_DIR": str(tmp_path / "index"), "MACRAE_SYNC_INTERVAL": "1", "MACRAE_DRAIN_SECONDS": "60",
           "MACRAE_DRAIN_FILE": str(tmp_path / "draining"), "PYTHONUNBUFFERED": "1"}
    env.pop("MACRAE_CONTAINER", None)
    proc = subprocess.Popen([sys.executable, str(DEPLOY / "start.py"), "--port", str(port), "--host", "127.0.0.1"],
                            env=env, stderr=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{port}"

    def get(path):
        req = urllib.request.Request(base + path, headers={"X-Macrae-Secret": secret})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    worker = None
    try:
        _wait(lambda: get("/api/health")["ok"])
        # the run from the previous container came back, recorded as failed (its engine is gone)
        runs = _wait(lambda: get("/api/runs")["runs"])
        assert runs[0]["run_id"] == old.name and runs[0]["status"] == "failed"
        assert '"crashed"' in _wait(lambda: (bucket / "state/runs" / old.name / "state.json").read_text()
                                    if "crashed" in (bucket / "state/runs" / old.name / "state.json").read_text()
                                    else None)

        # a run in progress in this container: its engine is a live process
        worker = subprocess.Popen(["sleep", "60"])
        live = home / "runs" / "20261008-000100-live"
        live.mkdir(parents=True)
        (live / "state.json").write_text(json.dumps({"id": live.name, "status": "running", "pid": worker.pid,
                                                     "started": time.time(), "steps": {}, "order": []}))
        _wait(lambda: (bucket / "state/runs" / live.name / "state.json").exists())  # pushed on the state change

        proc.send_signal(signal.SIGTERM)  # Cloudflare: sleep, rollout or restart
        _wait(lambda: (tmp_path / "draining").exists(), timeout=10)

        def refused():
            req = urllib.request.Request(base + "/api/tasks/small-calc/start", data=b"{}", method="POST",
                                         headers={"X-Macrae-Secret": secret, "content-type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as e:
                return e.code == 503 and "restarting" in json.loads(e.read())["detail"]
        assert _wait(refused, timeout=10)
        assert get(f"/api/runs/{live.name}")["status"] == "running"  # still serving while it drains
        time.sleep(2)
        assert proc.poll() is None, "must wait for the running flow"

        (live / "state.json").write_text(json.dumps({"id": live.name, "status": "ok", "pid": worker.pid,
                                                     "started": time.time() - 5, "finished": time.time(),
                                                     "steps": {}, "order": []}))
        worker.terminate()
        worker.wait()
        assert proc.wait(60) == 0
        err = proc.stderr.read()
        assert "waiting for 1 run(s) to finish" in err and "before exit" in err
        assert '"ok"' in (bucket / "state/runs" / live.name / "state.json").read_text()
    finally:
        for p in (proc, data, worker):
            if p and p.poll() is None:
                p.kill()
                p.wait()
