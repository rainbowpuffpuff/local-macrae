"""cloudflare/deploy.sh against fake wrangler/docker/node/npm/curl (tests/fakes_cf): checks, secrets, index, deploy."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent
REPO = DEPLOY.parent
FAKES = Path(__file__).resolve().parent / "fakes_cf"
ALL_SECRETS = ["MACRAE_TOOL_SECRET", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "AGENT_RUNNER_TOKEN_MAIN",
               "ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID"]


@pytest.fixture
def cf(tmp_path):
    """A repo-shaped folder with the real deploy.sh, envtool.py and wrangler.toml; returns run(args, env, dotenv)."""
    repo = tmp_path / "repo"
    (repo / "cloudflare").mkdir(parents=True)
    (repo / "deploy").mkdir()
    for f in ("cloudflare/deploy.sh", "cloudflare/wrangler.toml", "deploy/envtool.py", "deploy/Dockerfile.dockerignore"):
        shutil.copy2(REPO / f, repo / f)
    fake = tmp_path / "fake"
    fake.mkdir()

    def run(*args, env=None, dotenv=None):
        if dotenv is not None:
            (repo / "deploy" / ".env").write_text(dotenv)
        e = {"PATH": f"{FAKES}:/usr/bin:/bin", "HOME": str(tmp_path), "FAKE_DIR": str(fake), "MACRAE_HEALTH_POLL": "0",
             "PY": shutil.which("python3")}
        e.update(env or {})
        return subprocess.run(["bash", str(repo / "cloudflare" / "deploy.sh"), *args], env=e, capture_output=True,
                              text=True, timeout=60)

    run.repo, run.fake = repo, fake
    run.log = lambda name: (fake / name).read_text().splitlines() if (fake / name).exists() else []
    run.state = lambda: json.loads((fake / "cf.json").read_text()) if (fake / "cf.json").exists() else {}
    return run


def index(repo: Path, created: float, stamp: str):
    d = repo / "index"
    d.mkdir(exist_ok=True)
    files = {"papers": f"papers-{stamp}.json", "chunks": f"chunks-{stamp}.jsonl", "embeddings": f"emb-{stamp}.npy"}
    for n in files.values():
        (d / n).write_text(n)
    (d / "manifest.json").write_text(json.dumps({"version": 1, "created": created, "papers": 2, "chunks": 9,
                                                  "files": files}))
    return files


def test_first_deploy_refuses_without_secrets_and_changes_nothing(cf):
    r = cf()
    assert r.returncode == 1
    for name in ("MACRAE_TOOL_SECRET", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"):
        assert f"{name} is missing: npx wrangler secret put {name}" in r.stderr
    assert "no Claude login" in r.stderr and "AGENT_RUNNER_TOKEN_" in r.stderr
    assert "nothing was deployed" in r.stderr
    assert [line for line in cf.log("wrangler.log") if not line.startswith(("whoami", "secret list"))] == []


@pytest.mark.parametrize("env,expect", [
    ({"FAKE_NODE_MAJOR": "18"}, "Node 22 or newer"),
    ({"FAKE_DOCKER_DOWN": "1"}, "Docker is installed but not running"),
    ({"FAKE_LOGGED_OUT": "1"}, "npx wrangler login"),
])
def test_preflight_failures_are_explained(cf, env, expect):
    r = cf(env=env)
    assert r.returncode == 1 and expect in r.stderr
    assert not any(line.startswith("deploy") for line in cf.log("wrangler.log"))


def test_backend_url_on_the_worker_is_refused(cf):
    r = cf("check", env={"FAKE_WORKER_SECRETS": json.dumps(ALL_SECRETS + ["BACKEND_URL"])})
    assert r.returncode == 1 and "npx wrangler secret delete BACKEND_URL" in r.stderr


def test_check_with_worker_secrets_only(cf):
    r = cf("check", env={"FAKE_WORKER_SECRETS": json.dumps(["MACRAE_TOOL_SECRET", "MODAL_TOKEN_ID",
                                                             "MODAL_TOKEN_SECRET", "AGENT_RUNNER_TOKEN_CAROL"])})
    assert r.returncode == 0, r.stderr
    assert "secret AGENT_RUNNER_TOKEN_CAROL: on the Worker" in r.stderr
    assert "ELEVENLABS_AGENT_ID not set: voice stays off" in r.stderr
    assert "check: ok" in r.stderr


def test_full_deploy_from_env_file(cf):
    files = index(cf.repo, created=100.0, stamp="1")
    dotenv = ("MACRAE_TOOL_SECRET=a-long-enough-shared-secret\nMODAL_TOKEN_ID=ak-1\nMODAL_TOKEN_SECRET=as-1\n"
              "AGENT_RUNNER_TOKEN_MAIN=tok-main\nAGENT_RUNNER_TOKEN_CAROL=tok-carol\nANTHROPIC_API_KEY=\n"
              "BACKEND_URL=http://127.0.0.1:8080\n")
    r = cf("--containers-rollout=immediate", dotenv=dotenv, env={"FAKE_HEALTH_AFTER": "2"})
    assert r.returncode == 0, r.stderr
    st = cf.state()
    assert st["buckets"] == ["macrae-data"]
    # index: data files, then the manifest, last
    puts = [line.split()[3] for line in cf.log("wrangler.log") if line.startswith("r2 object put")]
    assert puts[-1] == "macrae-data/index/manifest.json"
    assert sorted(puts[:-1]) == sorted(f"macrae-data/index/{n}" for n in files.values())
    # one wrangler deploy with the .env secrets (never BACKEND_URL), plus the extra flag
    (dep,) = st["deploys"]
    assert dep["secrets"] == {"MACRAE_TOOL_SECRET": "a-long-enough-shared-secret", "MODAL_TOKEN_ID": "ak-1",
                              "MODAL_TOKEN_SECRET": "as-1", "AGENT_RUNNER_TOKEN_CAROL": "tok-carol",
                              "AGENT_RUNNER_TOKEN_MAIN": "tok-main"}
    assert "--containers-rollout=immediate" in dep["args"]
    assert "npm ci" not in "\n".join(cf.log("npm.log")) or (cf.repo / "cloudflare/package-lock.json").exists()
    assert cf.log("node.log") and "test/" in cf.log("node.log")[0]
    assert (cf.repo / "deploy/.state/cloudflare.env").read_text() == "WORKER_URL=https://macrae.owner.workers.dev\n"
    assert "Deployed: https://macrae.owner.workers.dev" in r.stderr
    # wrangler pipes the Dockerfile in, so only the root .dockerignore applies: created before the build
    assert (cf.repo / ".dockerignore").read_text() == (REPO / "deploy/Dockerfile.dockerignore").read_text()
    assert "tok-main" not in r.stdout + r.stderr and "a-long-enough" not in r.stdout + r.stderr  # values never printed
    health = [line for line in cf.log("curl.log") if "/api/health" in line]
    assert len(health) == 3

    # second deploy: same index → not uploaded again; a newer index replaces the old files
    r = cf(env={"FAKE_WORKER_SECRETS": json.dumps(ALL_SECRETS)})
    assert r.returncode == 0, r.stderr
    assert "R2 already has this index" in r.stderr
    new = index(cf.repo, created=200.0, stamp="2")
    for n in files.values():
        (cf.repo / "index" / n).unlink()
    r = cf("index", env={"FAKE_WORKER_SECRETS": json.dumps(ALL_SECRETS)})
    assert r.returncode == 0, r.stderr
    keys = set(cf.state()["objects"])
    assert keys == {f"macrae-data/index/{n}" for n in [*new.values(), "manifest.json"]}


def test_failed_deploy_or_tests_stop(cf):
    secrets = {"FAKE_WORKER_SECRETS": json.dumps(ALL_SECRETS)}
    r = cf(env={**secrets, "FAKE_TESTS_FAIL": "1"})
    assert r.returncode == 1 and "tests failed, not deploying" in r.stderr
    assert not cf.state().get("deploys")
    r = cf(env={**secrets, "FAKE_DEPLOY_FAIL": "1"})
    assert r.returncode == 1 and "wrangler deploy failed" in r.stderr


def test_unhealthy_backend_after_deploy_says_where_to_look(cf):
    r = cf(env={"FAKE_WORKER_SECRETS": json.dumps(ALL_SECRETS), "FAKE_HEALTH_AFTER": "99", "MACRAE_HEALTH_WAIT": "1",
                "MACRAE_HEALTH_POLL": "1"})
    assert r.returncode == 1 and "wrangler containers list" in r.stderr


def test_restart_and_status(cf):
    r = cf("restart", env={"MACRAE_TOOL_SECRET": "s"})
    assert r.returncode == 1 and "deploy first" in r.stderr
    (cf.repo / "deploy/.state").mkdir(parents=True)
    (cf.repo / "deploy/.state/cloudflare.env").write_text("WORKER_URL=https://macrae.owner.workers.dev\n")
    r = cf("restart")
    assert r.returncode == 1 and "MACRAE_TOOL_SECRET is needed" in r.stderr
    r = cf("restart", env={"FAKE_SECRET": "s3"}, dotenv="MACRAE_TOOL_SECRET=s3\n")
    assert r.returncode == 0, r.stderr
    assert "https://macrae.owner.workers.dev/admin/restart" in cf.log("curl.log")[-1]
    assert "-X POST" in cf.log("curl.log")[-1]
    r = cf("status", env={"FAKE_SECRET": "s3"})
    assert r.returncode == 0 and '"healthy"' in r.stderr and '"ok": true' in r.stderr


def test_secrets_uploads_env_values_without_deploying(cf):
    r = cf("secrets", env={"FAKE_WORKER_SECRETS": json.dumps(["MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"])},
           dotenv="MACRAE_TOOL_SECRET=0123456789abcdef0123\nANTHROPIC_API_KEY=sk-ant-x\n")
    assert r.returncode == 0, r.stderr
    assert cf.state()["bulk"] == [{"MACRAE_TOOL_SECRET": "0123456789abcdef0123", "ANTHROPIC_API_KEY": "sk-ant-x"}]
    assert not cf.state().get("deploys") and "cloudflare/deploy.sh restart" in r.stderr


def test_a_different_root_dockerignore_stops_the_deploy(cf):
    (cf.repo / ".dockerignore").write_text("node_modules\n")  # would let deploy/.env into the image
    r = cf(env={"FAKE_WORKER_SECRETS": json.dumps(ALL_SECRETS)})
    assert r.returncode == 1 and "cp deploy/Dockerfile.dockerignore .dockerignore" in r.stderr
    assert not cf.state().get("deploys")
