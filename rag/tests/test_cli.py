import json
import os
import subprocess
import sys

from rag import config
from rag.__main__ import main


def run_cli(*args, cwd=None):
    env = {**os.environ, "PYTHONPATH": str(config.REPO_ROOT)}
    return subprocess.run([sys.executable, "-m", "rag", *args], capture_output=True, text=True, env=env,
                          cwd=cwd or config.REPO_ROOT, timeout=120)


def test_cli_search_empty_index_prints_json(rag_env, tmp_path):
    r = run_cli("search", "ion", "water", cwd=tmp_path)  # not a TTY -> JSON
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"passages": []}
    r = run_cli("search", "ion", "--format", "text")
    assert r.returncode == 0 and "No passages" in r.stdout


def test_cli_ingest_search_stats(papers_dir, rag_env, tmp_path, capsys):
    r = run_cli("ingest", cwd=tmp_path)  # from another folder, like a flow step
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["papers"] == 2
    r = run_cli("search", "velocity-rescaling thermostat", "-k", "1", "--doi", "10.1039/d6sm00560h")
    data = json.loads(r.stdout)
    assert len(data["passages"]) == 1 and data["passages"][0]["citation"]["key"] == "[1]"

    assert main(["search", "iduronic acid", "--format", "context", "-k", "2"]) == 0
    out = capsys.readouterr().out
    context, cits = out.split("\nCITATIONS_JSON ")
    assert "[1] Riopedre Fernández et al. 2026" in context
    assert json.loads(cits)[0]["doi"] == "10.1093/glycob/cwag064"

    assert main(["search", "iduronic acid", "--format", "text"]) == 0
    assert "Riopedre Fernández et al. 2026" in capsys.readouterr().out

    assert main(["stats"]) == 0
    assert json.loads(capsys.readouterr().out)["papers"] == 2


def test_cli_ingest_no_embed(papers_dir, capsys):
    assert main(["ingest", "--no-embed"]) == 0
    assert json.loads(capsys.readouterr().out)["embedder"] is None
