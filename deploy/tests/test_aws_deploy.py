"""aws_deploy.sh: pure helpers, and the whole up/stop/up/down cycle against fake aws/ssh/curl (tests/fakes)."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent
SCRIPT = DEPLOY / "aws_deploy.sh"
FAKES = Path(__file__).resolve().parent / "fakes"


def bash_fn(expr: str) -> subprocess.CompletedProcess:
    """Source the script (main doesn't run when sourced) and evaluate a helper."""
    return subprocess.run(["bash", "-c", f'source "{SCRIPT}"; {expr}'], capture_output=True, text=True)


@pytest.mark.parametrize("ip,host", [("3.120.5.7", "3-120-5-7.sslip.io"), ("18.0.255.1", "18-0-255-1.sslip.io")])
def test_sslip_host(ip, host):
    assert bash_fn(f"sslip_host {ip}").stdout.strip() == host


def test_sslip_host_rejects_garbage():
    assert bash_fn("sslip_host None").returncode != 0
    assert bash_fn("sslip_host ''").returncode != 0


@pytest.mark.parametrize("itype,arch", [
    ("t3.small", "amd64"), ("t3a.medium", "amd64"), ("m7i.large", "amd64"), ("g5.xlarge", "amd64"),
    ("t4g.small", "arm64"), ("m7gd.large", "arm64"), ("c6gn.xlarge", "arm64"), ("g5g.xlarge", "arm64"),
])
def test_ami_arch(itype, arch):
    assert bash_fn(f"ami_arch {itype}").stdout.strip() == arch


def test_ami_param_is_canonical_ubuntu_2404():
    out = bash_fn("ami_param t4g.small").stdout.strip()
    assert out == "/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id"


def test_key_name_is_stable_and_depends_on_key(tmp_path):
    a, b = tmp_path / "a.pub", tmp_path / "b.pub"
    a.write_text("ssh-ed25519 AAAAone me@host\n")
    b.write_text("ssh-ed25519 AAAAtwo me@host\n")
    na, na2, nb = (bash_fn(f'key_name_for "{p}"').stdout.strip() for p in (a, a, b))
    assert na == na2 and na != nb
    assert na.startswith("macrae-") and len(na) == len("macrae-") + 12


def test_bash_syntax():
    for f in ("aws_deploy.sh", "cloud-init.sh", "docker-shim.sh"):
        assert subprocess.run(["bash", "-n", str(DEPLOY / f)]).returncode == 0, f


def test_docker_shim_fails_cleanly():
    r = subprocess.run(["sh", str(DEPLOY / "docker-shim.sh"), "image", "inspect", "x"], capture_output=True, text=True)
    assert r.returncode == 1 and "Modal" in r.stderr


# ── full cycle against fakes ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def world(tmp_path):
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for f in FAKES.iterdir():
        shutil.copy2(f, bindir / f.name)
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "p.pdf").write_bytes(b"%PDF-fake")
    env_file = tmp_path / ".env"
    env_file.write_text("MACRAE_TOOL_SECRET=s3cret-s3cret-s3cret\nMODAL_TOKEN_ID=ak-1\nMODAL_TOKEN_SECRET=as-1\n"
                        f"ANTHROPIC_API_KEY=sk-ant-x\nMACRAE_PAPERS_DIR={tmp_path / 'papers'}\n"
                        f"MACRAE_INDEX_DIR={tmp_path / 'no-index'}\n")
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "FAKE_DIR": str(fake_dir),
        "ENV_FILE": str(env_file),
        "MACRAE_STATE_DIR": str(tmp_path / "state"),
        "MACRAE_SSH_KEY": str(tmp_path / "home" / ".ssh" / "macrae_deploy"),
        "AWS_REGION": "eu-central-1",
        "PYTHON": sys.executable,
    }

    def run(*args, check=True, extra=None):
        r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env={**env, **(extra or {})},
                           stdin=subprocess.DEVNULL, timeout=120)
        if check:
            assert r.returncode == 0, r.stdout + r.stderr
        return r

    def calls(op=None):
        lines = (fake_dir / "calls.log").read_text().splitlines() if (fake_dir / "calls.log").exists() else []
        cs = [json.loads(x) for x in lines]
        return [c for c in cs if op is None or op in c]

    def ssh_cmds():
        p = fake_dir / "ssh.log"
        return [json.loads(x)["cmd"] for x in p.read_text().splitlines()] if p.exists() else []

    def aws_state():
        return json.loads((fake_dir / "aws.json").read_text())

    return type("World", (), dict(run=staticmethod(run), calls=staticmethod(calls), ssh=staticmethod(ssh_cmds),
                                  state=staticmethod(aws_state), fake=fake_dir, tmp=tmp_path, env=env))


def test_up_creates_everything_once_and_is_idempotent(world):
    r = world.run("up")
    assert r.stdout.strip().splitlines()[-1] == "https://3-120-5-7.sslip.io"
    assert len(world.calls("run-instances")) == 1
    assert len(world.calls("allocate-address")) == 1
    assert len(world.calls("create-security-group")) == 1
    assert len(world.calls("import-key-pair")) == 1

    run = world.calls("run-instances")[0]
    assert run[run.index("--instance-type") + 1] == "t3.small"
    assert run[run.index("--image-id") + 1] == "ami-amd64"
    assert run[run.index("--user-data") + 1] == f"file://{SCRIPT.parent / 'cloud-init.sh'}"
    assert "HttpTokens=required" in run[run.index("--metadata-options") + 1]
    assert "VolumeSize=20" in run[run.index("--block-device-mappings") + 1]
    perms = world.state()["sgs"]["macrae-backend"]["rules"]
    assert any("FromPort=443" in p and "0.0.0.0/0" in p for p in perms)
    assert any("FromPort=22" in p and "1.2.3.4/32" in p for p in perms)
    assert not any("FromPort=8080" in p for p in perms)

    # the server's env: secrets kept, local paths dropped, domain + data dir set
    remote_env = (world.fake / "remote_env").read_text()
    assert "MACRAE_TOOL_SECRET=s3cret-s3cret-s3cret" in remote_env
    assert "MACRAE_DOMAIN=3-120-5-7.sslip.io" in remote_env
    assert "MACRAE_DATA_DIR=/srv/macrae/data" in remote_env
    assert "MACRAE_PAPERS_DIR" not in remote_env and "MACRAE_INDEX_DIR" not in remote_env

    cmds = world.ssh()
    assert any("cloud-init status --wait" in c for c in cmds)
    assert any("tar -xzf - -C /srv/macrae/app.new" in c for c in cmds)
    assert any("mv app.new app" in c for c in cmds)
    assert any("docker compose --profile https up -d --build" in c for c in cmds)
    papers_synced = (world.fake / "rsync.log").exists() or any("/srv/macrae/data/papers" in c for c in cmds)
    assert papers_synced
    assert "no-index" in r.stderr  # missing local index is skipped with a warning, not an error

    state = (world.tmp / "state" / "aws.env").read_text()
    assert "URL=https://3-120-5-7.sslip.io" in state and "INSTANCE_ID=i-1" in state

    # second run: nothing new is created, duplicate SG rules are fine
    world.run("up")
    assert len(world.calls("run-instances")) == 1
    assert len(world.calls("allocate-address")) == 1
    assert len(world.calls("create-security-group")) == 1
    assert len(world.calls("import-key-pair")) == 1
    assert len(world.calls("associate-address")) == 1
    assert world.run("url").stdout.strip() == "https://3-120-5-7.sslip.io"


def test_stop_then_up_restarts_same_instance(world):
    world.run("up")
    world.run("stop")
    assert world.state()["instances"]["i-1"]["state"] == "stopped"
    world.run("up")
    assert world.state()["instances"]["i-1"]["state"] == "running"
    assert len(world.calls("run-instances")) == 1
    assert len(world.calls("start-instances")) == 1


def test_custom_domain_and_graviton(world):
    world.run("up", extra={"MACRAE_DOMAIN": "macrae.example.org", "MACRAE_INSTANCE_TYPE": "t4g.small"})
    run = world.calls("run-instances")[0]
    assert run[run.index("--image-id") + 1] == "ami-arm64"
    assert "MACRAE_DOMAIN=macrae.example.org" in (world.fake / "remote_env").read_text()


def test_status_and_down_remove_everything(world):
    world.run("up")
    st = world.run("status").stdout
    assert "i-1 (running, t3.small)" in st and '"ok": true' in st
    world.run("down", "--yes")
    s = world.state()
    assert s["instances"]["i-1"]["state"] == "terminated"
    assert s["eips"] == {} and s["sgs"] == {} and s["keys"] == []
    assert not (world.tmp / "state" / "aws.env").exists()
    assert "no instance" in world.run("status").stdout


def test_down_asks_first(world):
    world.run("up")
    r = world.run("down", check=False)
    assert r.returncode != 0 and "aborted" in r.stderr
    assert world.state()["instances"]["i-1"]["state"] == "running"


def test_up_refuses_without_secret_before_touching_aws(world):
    Path(world.env["ENV_FILE"]).write_text("MACRAE_TOOL_SECRET=\n")
    r = world.run("up", check=False)
    assert r.returncode != 0 and "MACRAE_TOOL_SECRET" in r.stderr
    assert world.calls() == []


def test_code_needs_existing_instance(world):
    r = world.run("code", check=False)
    assert r.returncode != 0 and "no instance" in r.stderr


def test_cost_mentions_totals(world):
    out = world.run("cost").stdout
    assert "$20.43" in out and "$23.07" in out and "Modal" in out
