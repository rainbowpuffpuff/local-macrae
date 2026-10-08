"""deploy/envtool.py: dotenv parsing, deploy checks, the server env file, Worker secrets."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("macrae_envtool", DEPLOY / "envtool.py")
envtool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(envtool)

MODAL_TOML = '[acalincarol]\ntoken_id = "ak-from-toml"\ntoken_secret = "as-from-toml"\nactive = true\n'


def test_parse():
    text = """
# comment
export A=1
B = "two words"
C='single # not a comment'
D=plain # trailing comment
E=
B=override
not a line
"""
    assert envtool.parse(text) == {"A": "1", "B": "override", "C": "single # not a comment", "D": "plain", "E": ""}


def test_get_prefers_process_env_and_treats_blank_as_unset(monkeypatch):
    monkeypatch.setenv("X_TEST_KEY", "from-env")
    assert envtool.get({"X_TEST_KEY": "from-file"}, "X_TEST_KEY") == "from-env"
    monkeypatch.setenv("X_TEST_KEY", "")
    assert envtool.get({"X_TEST_KEY": "from-file"}, "X_TEST_KEY") == "from-file"
    assert envtool.get({"X_TEST_KEY": " "}, "X_TEST_KEY", "dflt") == "dflt"


def test_export_lines_quote_and_skip(monkeypatch):
    monkeypatch.setenv("ALREADY", "set")
    lines = envtool.export_lines({"A": "it's $HOME", "B": "", "ALREADY": "file"})
    assert lines == ["export A='it'\"'\"'s $HOME'"]
    out = subprocess.run(["bash", "-c", lines[0] + '; printf %s "$A"'], capture_output=True, text=True).stdout
    assert out == "it's $HOME"


def test_check_errors_and_warnings(tmp_path):
    none = tmp_path / "missing.toml"
    errors, warnings = envtool.check({}, none)
    assert any("MACRAE_TOOL_SECRET" in e for e in errors)
    assert any("Modal" in w for w in warnings) and any("Claude" in w for w in warnings)

    ok = {"MACRAE_TOOL_SECRET": "x" * 32, "MODAL_TOKEN_ID": "a", "MODAL_TOKEN_SECRET": "b",
          "AGENT_RUNNER_TOKEN_ALICE": "sk-ant-oat01-x"}
    assert envtool.check(ok, none) == ([], [])
    assert envtool.check({**ok, "MACRAE_TOOL_SECRET": "short"}, none)[1]


def test_check_accepts_modal_profile_from_toml(tmp_path):
    toml = tmp_path / "modal.toml"
    toml.write_text(MODAL_TOML)
    env = {"MACRAE_TOOL_SECRET": "x" * 32, "ANTHROPIC_API_KEY": "sk"}
    assert envtool.check(env, toml) == ([], [])
    assert envtool.check({**env, "MODAL_PROFILE": "other"}, toml)[1]


def test_render_for_server(tmp_path):
    toml = tmp_path / "modal.toml"
    toml.write_text(MODAL_TOML)
    src = ("# keep comments\nMACRAE_TOOL_SECRET=abc\nMACRAE_PAPERS_DIR=papers\nMACRAE_DOMAIN=old.example\n"
           "MODAL_TOKEN_ID=\nMODAL_TOKEN_SECRET=\nANTHROPIC_API_KEY=sk\n")
    out = envtool.render(src, domain="1-2-3-4.sslip.io", data_dir="/srv/macrae/data", modal_toml=toml)
    env = envtool.parse(out)
    assert env["MACRAE_TOOL_SECRET"] == "abc" and env["ANTHROPIC_API_KEY"] == "sk"
    assert env["MACRAE_DOMAIN"] == "1-2-3-4.sslip.io" and env["MACRAE_DATA_DIR"] == "/srv/macrae/data"
    assert env["MODAL_TOKEN_ID"] == "ak-from-toml" and env["MODAL_TOKEN_SECRET"] == "as-from-toml"
    assert "MACRAE_PAPERS_DIR" not in env and "old.example" not in out and "# keep comments" in out


def test_render_keeps_explicit_modal_tokens(tmp_path):
    toml = tmp_path / "modal.toml"
    toml.write_text(MODAL_TOML)
    out = envtool.render("MODAL_TOKEN_ID=mine\nMODAL_TOKEN_SECRET=mine2\n", domain="d", data_dir="/x", modal_toml=toml)
    env = envtool.parse(out)
    assert env["MODAL_TOKEN_ID"] == "mine" and "from-toml" not in out


def test_wrangler_vars_toml_and_jsonc(tmp_path):
    (tmp_path / "wrangler.toml").write_text('name = "macrae"\n[vars]\nELEVENLABS_AGENT_ID = "agent_1"\n')
    assert envtool.wrangler_vars(tmp_path) == {"ELEVENLABS_AGENT_ID"}
    j = tmp_path / "j"
    j.mkdir()
    (j / "wrangler.jsonc").write_text('{\n  // comment\n  "name": "macrae",\n  "vars": {"BACKEND_URL": "x"}\n}\n')
    assert envtool.wrangler_vars(j) == {"BACKEND_URL"}
    assert envtool.wrangler_vars(tmp_path / "none") == set()


def test_worker_secrets_url_from_state_and_skips(monkeypatch):
    for k in envtool.WORKER_SECRETS:
        monkeypatch.delenv(k, raising=False)
    env = {"MACRAE_TOOL_SECRET": "abc", "ELEVENLABS_API_KEY": "xi", "ELEVENLABS_AGENT_ID": "agent_1"}
    secrets, msgs = envtool.worker_secrets(env, {"URL": "https://1-2-3-4.sslip.io"}, {"ELEVENLABS_AGENT_ID"})
    assert secrets == {"BACKEND_URL": "https://1-2-3-4.sslip.io", "MACRAE_TOOL_SECRET": "abc", "ELEVENLABS_API_KEY": "xi"}
    assert any("ELEVENLABS_AGENT_ID" in m and "[vars]" in m for m in msgs)
    secrets, msgs = envtool.worker_secrets({}, {}, set())
    assert secrets == {} and len(msgs) == 4


def test_cli_get_and_check(tmp_path):
    f = tmp_path / ".env"
    f.write_text("MACRAE_TOOL_SECRET=\nAWS_REGION=us-east-1\n")
    env = {k: v for k, v in os.environ.items() if k != "AWS_REGION"}
    run = lambda *a: subprocess.run([sys.executable, str(DEPLOY / "envtool.py"), *a], capture_output=True, text=True,
                                    env=env)
    assert run("get", str(f), "AWS_REGION").stdout.strip() == "us-east-1"
    assert run("get", str(f), "MISSING", "dflt").stdout.strip() == "dflt"
    r = run("check", str(f), "--modal-toml", str(tmp_path / "none"))
    assert r.returncode == 1 and "MACRAE_TOOL_SECRET" in r.stderr
    assert run("check", str(tmp_path / "nope")).returncode == 1


def test_cli_worker_secrets_dry_run(tmp_path):
    f = tmp_path / ".env"
    f.write_text("MACRAE_TOOL_SECRET=abc\n")
    state = tmp_path / "aws.env"
    state.write_text("URL=https://x.sslip.io\n")
    env = {k: v for k, v in os.environ.items() if k not in envtool.WORKER_SECRETS}
    r = subprocess.run([sys.executable, str(DEPLOY / "envtool.py"), "worker-secrets", str(f), "--state", str(state),
                        "--wrangler-dir", str(tmp_path), "--dry-run"], capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert "secret put BACKEND_URL" in r.stderr and "secret put MACRAE_TOOL_SECRET" in r.stderr


# ── Cloudflare secrets (cloudflare/deploy.sh) ───────────────────────────────────────────────────────────────────

def test_cf_secrets_union_of_worker_and_env_file(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith(("AGENT_RUNNER_TOKEN_", "MODAL_", "MACRAE_", "ANTHROPIC", "ELEVENLABS")):
            monkeypatch.delenv(k)
    env = {"MACRAE_TOOL_SECRET": "x" * 20, "AGENT_RUNNER_TOKEN_ALICE": "ta", "AGENT_RUNNER_TOKEN_EMPTY": "",
           "BACKEND_URL": "http://127.0.0.1:8080", "ELEVENLABS_API_KEY": "xi"}
    values, errors, notes = envtool.cf_secrets(env, {"MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "AGENT_RUNNER_TOKEN_BOB"},
                                               set(), tmp_path / "none.toml")
    assert errors == []
    assert values == {"MACRAE_TOOL_SECRET": "x" * 20, "AGENT_RUNNER_TOKEN_ALICE": "ta", "ELEVENLABS_API_KEY": "xi"}
    assert "secret AGENT_RUNNER_TOKEN_BOB: on the Worker" in notes
    assert "secret MACRAE_TOOL_SECRET: from deploy/.env, uploaded with this deploy" in notes
    assert any("ELEVENLABS_AGENT_ID not set" in n for n in notes)


def test_cf_secrets_errors_and_modal_profile_fallback(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith(("AGENT_RUNNER_TOKEN_", "MODAL_", "MACRAE_", "ANTHROPIC", "ELEVENLABS")):
            monkeypatch.delenv(k)
    values, errors, _ = envtool.cf_secrets({}, {"BACKEND_URL"}, set(), None)
    assert len(errors) == 5 and values == {}
    assert any("secret delete BACKEND_URL" in e for e in errors)

    toml = tmp_path / "modal.toml"
    toml.write_text('[acalincarol]\ntoken_id = "ak-9"\ntoken_secret = "as-9"\n')
    values, errors, notes = envtool.cf_secrets({"MACRAE_TOOL_SECRET": "short", "ANTHROPIC_API_KEY": "sk"}, set(),
                                               {"ANTHROPIC_API_KEY"}, toml)
    assert values["MODAL_TOKEN_ID"] == "ak-9" and values["MODAL_TOKEN_SECRET"] == "as-9"
    assert any("Modal login: profile [acalincarol]" in n for n in notes)
    assert any("shorter than 16" in n for n in notes)
    assert errors == ["ANTHROPIC_API_KEY is under [vars] in cloudflare/wrangler.toml; a secret can't share its name: "
                      "remove it there"]
