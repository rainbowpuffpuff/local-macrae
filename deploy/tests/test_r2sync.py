"""deploy/r2sync.py: index pull, state restore/push, interrupted runs; against the real Worker handler on Node."""

import importlib.util
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent
REPO = DEPLOY.parent
spec = importlib.util.spec_from_file_location("macrae_r2sync", DEPLOY / "r2sync.py")
r2sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r2sync)

NODE = shutil.which("node")


class FakeRemote:
    """Same contract as r2sync.Remote, in memory (keys → (bytes, mtime))."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, float | None]] = {}
        self.puts: list[str] = []

    def list(self, prefix):
        return [{"key": k, "size": len(v[0]), "mtime": v[1]} for k, v in sorted(self.objects.items())
                if k.startswith(prefix)]

    def get(self, key):
        return self.objects.get(key)

    def put(self, key, data, mtime=None):
        assert key.startswith("state/")
        self.objects[key] = (data, mtime)
        self.puts.append(key)


def make_syncer(tmp_path, remote, name="a"):
    home = tmp_path / name
    dirs = r2sync.state_dirs({"AGENT_RUNNER_HOME": str(home)})
    return r2sync.Syncer(remote, dirs, home / "index", baked_index=tmp_path / "baked", logger=lambda m: None)


def write_index(d: Path, created: float, papers=2, stamp="1"):
    d.mkdir(parents=True, exist_ok=True)
    files = {"papers": f"papers-{stamp}.json", "chunks": f"chunks-{stamp}.jsonl", "embeddings": f"emb-{stamp}.npy"}
    for n in files.values():
        (d / n).write_text(f"data {n}")
    (d / "manifest.json").write_text(json.dumps({"version": 1, "created": created, "papers": papers, "files": files}))
    return files


def write_run(home: Path, run_id: str, status: str, pid=None):
    d = home / "runs" / run_id
    (d / "logs").mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps({"id": run_id, "status": status, "pid": pid, "steps": {}}))
    (d / "logs" / "card.log").write_text("[12:00:00] started\n")
    (d / ".state.json.x.tmp").write_text("half written")  # never synced
    job = home / "jobs" / f"{run_id}-card-a1" / "trial-1" / "agent"
    job.mkdir(parents=True, exist_ok=True)
    (job / "trajectory.json").write_text('{"steps": []}')
    (home / "macrae" / "events").mkdir(parents=True, exist_ok=True)
    (home / "macrae" / "events" / f"{run_id}.jsonl").write_text('{"seq": 1}\n')
    (home / "leases" / "main").mkdir(parents=True, exist_ok=True)
    (home / "leases" / "main" / "x").write_text("123")  # not synced


def test_push_is_incremental_and_skips_temp_and_leases(tmp_path):
    remote = FakeRemote()
    s = make_syncer(tmp_path, remote)
    home = tmp_path / "a"
    write_run(home, "r1", "ok")
    assert s.push() == 4
    assert sorted(remote.objects) == [
        "state/jobs/r1-card-a1/trial-1/agent/trajectory.json",
        "state/macrae/events/r1.jsonl",
        "state/runs/r1/logs/card.log",
        "state/runs/r1/state.json",
    ]
    assert s.push() == 0  # nothing changed
    log = home / "runs" / "r1" / "logs" / "card.log"
    log.write_text(log.read_text() + "[12:01:00] finished\n")
    os.utime(log, (time.time() + 5, time.time() + 5))
    assert s.push() == 1 and remote.puts[-1] == "state/runs/r1/logs/card.log"


def test_push_skips_big_files_and_survives_errors(tmp_path):
    remote = FakeRemote()
    s = make_syncer(tmp_path, remote)
    s.max_bytes = 10
    write_run(tmp_path / "a", "r1", "ok")
    (tmp_path / "a" / "jobs" / "big.bin").write_bytes(b"x" * 11)
    s.push()
    assert "state/jobs/big.bin" not in remote.objects

    class Down(FakeRemote):
        def put(self, key, data, mtime=None):
            raise r2sync.SyncError("unreachable")
    s2 = make_syncer(tmp_path, Down())
    assert s2.push() == 0 and s2.pushed == {}  # retried next round


def test_restore_into_a_fresh_container_marks_interrupted_runs(tmp_path):
    remote = FakeRemote()
    old = make_syncer(tmp_path, remote, "old")
    write_run(tmp_path / "old", "done", "ok")
    write_run(tmp_path / "old", "cut", "running", pid=4242)
    old.push()

    new = make_syncer(tmp_path, remote, "new")
    home = tmp_path / "new"
    (home / "macrae" / "events").mkdir(parents=True)
    (home / "macrae" / "events" / "done.jsonl").write_text("local wins\n")  # never overwritten
    assert new.restore() == 7
    assert (home / "macrae" / "events" / "done.jsonl").read_text() == "local wins\n"
    assert json.loads((home / "runs" / "done" / "state.json").read_text())["status"] == "ok"
    cut = json.loads((home / "runs" / "cut" / "state.json").read_text())
    assert cut["status"] == "crashed" and cut["pid"] is None and "restarted" in cut["error"] and cut["finished"]
    assert (home / "jobs" / "cut-card-a1" / "trial-1" / "agent" / "trajectory.json").exists()
    assert not (home / "leases").exists()
    remote.puts.clear()
    # restored files aren't uploaded again; the fixed record and the local file that won are
    assert new.push() == 2 and sorted(remote.puts) == ["state/macrae/events/done.jsonl", "state/runs/cut/state.json"]


def test_restore_never_writes_outside_its_folders(tmp_path):
    remote = FakeRemote()
    remote.objects["state/runs/../../evil"] = (b"x", None)
    remote.objects["state/runs/a//b"] = (b"x", None)
    s = make_syncer(tmp_path, remote)
    assert s.restore() == 0
    assert not (tmp_path / "evil").exists()


def test_pull_index_only_when_newer_and_manifest_last(tmp_path):
    remote = FakeRemote()
    s = make_syncer(tmp_path, remote)
    assert s.pull_index() is False  # nothing in R2

    src = tmp_path / "src"
    files = write_index(src, created=200.0, papers=5, stamp="2")
    for n in [*files.values(), "manifest.json"]:
        remote.objects[f"index/{n}"] = ((src / n).read_bytes(), None)
    write_index(s.index_dir, created=100.0, papers=1, stamp="1")
    order = []
    real_get = remote.get
    remote.get = lambda k: (order.append(k), real_get(k))[1]
    assert s.pull_index() is True
    assert order[0] == "index/manifest.json" and set(order[1:]) == {f"index/{n}" for n in files.values()}
    assert json.loads((s.index_dir / "manifest.json").read_text())["papers"] == 5
    assert sorted(p.name for p in s.index_dir.iterdir()) == sorted([*files.values(), "manifest.json"])  # old removed
    assert s.pull_index() is False  # now current

    del remote.objects["index/emb-2.npy"]
    write_index(s.index_dir, created=50.0, stamp="1")
    with pytest.raises(r2sync.SyncError, match="missing"):
        s.pull_index()
    assert json.loads((s.index_dir / "manifest.json").read_text())["created"] == 50.0  # manifest not swapped


def test_seed_index_from_the_image(tmp_path):
    s = make_syncer(tmp_path, FakeRemote())
    assert s.seed_index() is False  # no baked index
    write_index(tmp_path / "baked", created=10.0, papers=7)
    assert s.seed_index() is True
    assert json.loads((s.index_dir / "manifest.json").read_text())["papers"] == 7
    assert s.seed_index() is False  # never over an existing index


def test_run_loop_pushes_when_a_step_changes(tmp_path):
    remote = FakeRemote()
    s = make_syncer(tmp_path, remote)
    s.interval = 3600  # only state.json changes trigger a push in this test
    home = tmp_path / "a"
    write_run(home, "r1", "running")
    stop = threading.Event()
    t = threading.Thread(target=s.run, args=(stop, 0.05))
    t.start()
    try:
        deadline = time.time() + 5
        while "state/runs/r1/state.json" not in remote.objects and time.time() < deadline:
            time.sleep(0.05)
        assert "state/runs/r1/state.json" in remote.objects
        st = home / "runs" / "r1" / "state.json"
        st.write_text(json.dumps({"id": "r1", "status": "ok", "steps": {}}))
        os.utime(st, (time.time() + 10, time.time() + 10))
        deadline = time.time() + 5
        while b'"ok"' not in remote.objects["state/runs/r1/state.json"][0] and time.time() < deadline:
            time.sleep(0.05)
        assert b'"ok"' in remote.objects["state/runs/r1/state.json"][0]
    finally:
        stop.set()
        t.join(5)


# ── against cloudflare/worker.js dataHandler (Node, folder-backed bucket) ──────────────────────────────────────
@pytest.fixture
def data_server(tmp_path):
    if not NODE:
        pytest.skip("node not installed")
    bucket = tmp_path / "bucket"
    p = subprocess.Popen([NODE, str(REPO / "cloudflare/dev/data-server.mjs"), "--port", "0", "--dir", str(bucket)],
                         stdout=subprocess.PIPE, text=True)
    info = json.loads(p.stdout.readline())
    yield f"http://127.0.0.1:{info['port']}", bucket
    p.terminate()
    p.wait(5)


def test_round_trip_through_the_worker_handler(tmp_path, data_server):
    url, bucket = data_server
    remote = r2sync.Remote(url, timeout=10)
    old = make_syncer(tmp_path, remote, "old")
    write_run(tmp_path / "old", "20261008-1 é run", "running", pid=1)
    weird = tmp_path / "old" / "jobs" / "x" / "name with spaces & % #.txt"
    weird.parent.mkdir(parents=True)
    weird.write_text("odd name")
    mtime = time.time() - 3600
    os.utime(weird, (mtime, mtime))
    assert old.push() == 5
    assert (bucket / "state" / "jobs" / "x" / "name with spaces & % #.txt").read_text() == "odd name"

    new = make_syncer(tmp_path, remote, "new")
    assert new.restore() == 5
    restored = tmp_path / "new" / "jobs" / "x" / "name with spaces & % #.txt"
    assert restored.read_text() == "odd name"
    assert abs(restored.stat().st_mtime - mtime) < 0.01  # mtime kept as R2 metadata
    assert json.loads((tmp_path / "new" / "runs" / "20261008-1 é run" / "state.json").read_text())["status"] == "crashed"

    with pytest.raises(r2sync.SyncError, match="HTTP 400"):
        remote.put("index/manifest.json", b"{}")  # the container can't overwrite the index
    files = write_index(bucket / "index", created=300.0, papers=9)  # as deploy.sh uploads it
    assert new.pull_index() is True
    assert json.loads((new.index_dir / "manifest.json").read_text())["papers"] == 9
    assert all((new.index_dir / n).exists() for n in files.values())


def test_cli_status(tmp_path, data_server):
    url, _ = data_server
    r = subprocess.run(["python3", str(DEPLOY / "r2sync.py"), "status"], env={**os.environ, "MACRAE_SYNC_URL": url,
                       "AGENT_RUNNER_HOME": str(tmp_path)}, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "state/runs/: 0 objects" in r.stdout and "index/: none" in r.stdout
    r = subprocess.run(["python3", str(DEPLOY / "r2sync.py"), "push"], env={k: v for k, v in os.environ.items()
                       if k != "MACRAE_SYNC_URL"}, capture_output=True, text=True)
    assert r.returncode == 2 and "MACRAE_SYNC_URL" in r.stderr


def test_what_macrae_learned_survives_a_new_container(tmp_path):
    """Lessons and the capability registry (evolve's home) go to state/evolve/ and come back on the next start."""
    remote = FakeRemote()
    old = make_syncer(tmp_path, remote, "old")
    ev = old.dirs["evolve"]
    (ev / "capabilities" / "calc-image").mkdir(parents=True)
    (ev / "lessons.jsonl").write_text('{"lesson": "use the pinned image"}\n')
    (ev / "capabilities" / "calc-image" / "manifest.json").write_text('{"name": "calc-image"}')
    old.push()
    assert "state/evolve/lessons.jsonl" in remote.objects
    new = make_syncer(tmp_path, remote, "new")
    new.restore()
    assert (new.dirs["evolve"] / "lessons.jsonl").read_text() == '{"lesson": "use the pinned image"}\n'
    assert (new.dirs["evolve"] / "capabilities" / "calc-image" / "manifest.json").exists()
