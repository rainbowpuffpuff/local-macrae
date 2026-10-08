"""Static checks that the deploy files match CONTRACT.md and each other."""

import re
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent
REPO = DEPLOY.parent


def contract_env_vars() -> set[str]:
    text = (REPO / "CONTRACT.md").read_text()
    section = text.split("## Environment variables")[1].split("\n## ")[0]
    names = set(re.findall(r"`([A-Z][A-Z0-9_]*(?:<NAME>)?)`", section))
    names |= set(re.findall(r"\b([A-Z][A-Z0-9_]{3,})\b", section.split("Worker vars/secrets:")[1]))
    return names


def test_env_example_lists_every_contract_variable_without_values():
    example = (DEPLOY / "env.example").read_text()
    names = contract_env_vars()
    assert {"MACRAE_TOOL_SECRET", "MODAL_PROFILE", "AGENT_RUNNER_TOKEN_<NAME>", "BACKEND_URL",
            "ELEVENLABS_AGENT_ID"} <= names
    for name in names:
        assert re.search(rf"^#?\s*{re.escape(name)}=", example, re.M), f"{name} missing from env.example"
    for line in example.splitlines():
        m = re.match(r"^\s*#?\s*([A-Z][A-Z0-9_<>]*)=(.*)$", line)
        if m:
            assert m.group(2) == "", f"env.example must not carry values: {line}"


def test_dockerfile_meets_contract():
    df = (DEPLOY / "Dockerfile").read_text()
    assert "python:3.11" in df
    assert "astral-sh/uv" in df
    assert "harbor" in df and "uv tool install" in df
    assert "-e ." in df
    for m in ("server", "rag", "tasks", "deploy"):
        assert m in df.split("FROM ${PYTHON_IMAGE} AS reqs")[1].split("FROM")[0]
    assert "EXPOSE 8080" in df and "deploy/start.py" in df
    assert "USER macrae" in df and "HEALTHCHECK" in df and "/api/health" in df


def test_dockerignore_keeps_secrets_and_data_out():
    ignore = (DEPLOY / "Dockerfile.dockerignore").read_text().split()
    for p in ("papers", "**/.env", ".git", "**/.dev.vars", ".reference", "deploy/.state", "web", "cloudflare"):
        assert p in ignore
    assert "index" not in ignore  # baked into the image when present (Cloudflare: used when R2 has no newer one)


def test_root_dockerignore_matches_for_wranglers_stdin_build():
    """wrangler runs `docker build -f - <context>`: only the context's .dockerignore applies, so it must be the same.
    (cloudflare/deploy.sh creates it when missing and refuses to build when it differs.)"""
    root = REPO / ".dockerignore"
    if not root.exists():
        pytest.skip("no root .dockerignore yet; cloudflare/deploy.sh creates it")
    assert root.read_text() == (DEPLOY / "Dockerfile.dockerignore").read_text()


def test_wrangler_config_builds_the_backend_container():
    tomllib = pytest.importorskip("tomllib")
    cfg = tomllib.loads((REPO / "cloudflare" / "wrangler.toml").read_text())
    (c,) = cfg["containers"]
    assert c["image"] == "../deploy/Dockerfile" and c["image_build_context"] == ".."
    assert c["class_name"] == "MacraeBackend" and c["max_instances"] == 1
    assert {"name": "BACKEND", "class_name": "MacraeBackend"} in cfg["durable_objects"]["bindings"]
    assert cfg["exports"]["MacraeBackend"] == {"type": "durable-object", "storage": "sqlite"}
    assert {"binding": "DATA", "bucket_name": "macrae-data"} in cfg["r2_buckets"]
    assert "vars" not in cfg or "BACKEND_URL" not in cfg["vars"]
    index_js = (REPO / "cloudflare" / "index.js").read_text()
    assert "defaultPort = 8080" in index_js and 'sleepAfter = "2h"' in index_js
    assert "EXPOSE 8080" in (DEPLOY / "Dockerfile").read_text() and "PORT=8080" in (DEPLOY / "Dockerfile").read_text()


def test_compose_file():
    yaml = pytest.importorskip("yaml")
    c = yaml.safe_load((DEPLOY / "compose.yaml").read_text())
    be = c["services"]["backend"]
    assert be["build"] == {"context": "..", "dockerfile": "deploy/Dockerfile"}
    assert be["ports"] == ["127.0.0.1:${MACRAE_PORT:-8080}:8080"]
    assert be["environment"]["AGENT_RUNNER_HOME"] == "/data/agent-runner"
    caddy = c["services"]["caddy"]
    assert caddy["profiles"] == ["https"] and "443:443" in caddy["ports"]
    assert "{$MACRAE_DOMAIN}" in (DEPLOY / "Caddyfile").read_text()
    assert "reverse_proxy backend:8080" in (DEPLOY / "Caddyfile").read_text()


def test_makefile_has_contract_targets():
    mk = (REPO / "Makefile").read_text()
    for t in ("index", "serve", "test", "web-dev", "deploy-web", "deploy-backend"):
        assert re.search(rf"^{t}:", mk, re.M), t


@pytest.mark.skipif(subprocess.run(["which", "make"], capture_output=True).returncode != 0, reason="no make")
def test_makefile_dry_runs():
    r = subprocess.run(["make", "-n", "index", "serve", "test", "web-dev", "deploy-web", "deploy-backend"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "python3 -m rag ingest" in r.stdout and "bash cloudflare/deploy.sh" in r.stdout
    cf = subprocess.run(["make", "-n", "aws-deploy", "cf-index", "cf-restart", "web-secrets"], cwd=REPO,
                        capture_output=True, text=True)
    assert cf.returncode == 0, cf.stderr
    assert "deploy/aws_deploy.sh up" in cf.stdout and "cloudflare/deploy.sh index" in cf.stdout
    assert "cloudflare/deploy.sh restart" in cf.stdout and "cloudflare/deploy.sh secrets" in cf.stdout
    assert "-m pytest -q tests server/tests rag/tests tasks/tests voice/tests deploy/tests" in r.stdout


def test_gitignore_covers_secrets():
    gi = (DEPLOY / ".gitignore").read_text().split()
    assert ".env" in gi and ".state/" in gi and "!env.example" in gi
