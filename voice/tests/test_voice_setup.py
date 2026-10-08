"""setup_agent.py against an in-memory fake of the ElevenLabs API (no network)."""
import importlib.util
import json
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

VOICE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("macrae_voice_setup", VOICE / "setup_agent.py")
sa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sa)

BACKEND = "https://backend.example.com"
SECRET = "s3cret-s3cret-s3cret-0123"
ENV = {"ELEVENLABS_API_KEY": "xi-test", "BACKEND_URL": BACKEND + "/", "MACRAE_TOOL_SECRET": SECRET}


class FakeElevenLabs:
    """Just enough of /v1/convai to exercise setup_agent: agents, tools, secrets, plus a fake backend."""

    def __init__(self):
        self.agents, self.tools, self.secrets = {}, {}, {}
        self.calls, self.fail = [], {}  # fail: (method, path) -> list of statuses to return first
        self.backend_status = {"/api/health": 200, "/api/tools/search_papers": 200}
        self.n = 0

    def _id(self, prefix):
        self.n += 1
        return f"{prefix}_{self.n}"

    def __call__(self, method, url, headers, body, timeout):
        u = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        data = json.loads(body) if body else None
        self.calls.append((method, u.path, data))
        if url.startswith(BACKEND):
            return self.backend_status.get(u.path, 404), b"{}"
        assert headers["xi-api-key"] == "xi-test"
        queued = self.fail.get((method, u.path))
        if queued:
            status = queued.pop(0)
            return status, json.dumps({"detail": [{"loc": ["body", "x"], "msg": "boom"}]}).encode()
        status, out = self.route(method, u.path, q, data)
        return status, json.dumps(out).encode()

    def route(self, method, path, q, data):
        parts = path.strip("/").split("/")[2:]  # after v1/convai
        if parts == ["secrets"] and method == "GET":
            found = [{"type": "stored", "secret_id": k, "name": v["name"], "used_by": {}}
                     for k, v in self.secrets.items() if v["name"].startswith(q.get("search", ""))]
            return 200, {"secrets": found}
        if parts == ["secrets"] and method == "POST":
            assert data["type"] == "new"
            sid = self._id("sec")
            self.secrets[sid] = data
            return 200, {"type": "stored", "secret_id": sid, "name": data["name"]}
        if parts[0] == "secrets" and method == "PATCH":
            assert data["type"] == "update"
            self.secrets[parts[1]] = data
            return 200, {"type": "stored", "secret_id": parts[1], "name": data["name"]}
        if parts == ["tools"] and method == "POST":
            tid = self._id("tool")
            self.tools[tid] = data["tool_config"]
            return 200, {"id": tid, "tool_config": data["tool_config"]}
        if parts[0] == "tools" and len(parts) == 2:
            tid = parts[1]
            if tid not in self.tools:
                return 404, {"detail": "not found"}
            if method == "PATCH":
                self.tools[tid] = data["tool_config"]
            if method == "DELETE":
                del self.tools[tid]
                return 200, {}
            return 200, {"id": tid, "tool_config": self.tools[tid]}
        if parts == ["agents", "create"] and method == "POST":
            aid = self._id("agent")
            self.agents[aid] = {"agent_id": aid, **data}
            return 200, {"agent_id": aid}
        if parts == ["agents"] and method == "GET":
            found = [{"agent_id": k, "name": v["name"], "archived": v.get("archived", False)}
                     for k, v in self.agents.items() if q.get("search", "") in v["name"]]
            return 200, {"agents": found, "has_more": False, "next_cursor": None}
        if parts[0] == "agents" and len(parts) == 2:
            aid = parts[1]
            if aid not in self.agents:
                return 404, {"detail": {"status": "agent_not_found", "message": "not found"}}
            if method == "PATCH":
                assert data["version_description"]
                self.agents[aid].update({k: v for k, v in data.items() if k != "version_description"})
            return 200, self.agents[aid]
        return 404, {"detail": f"no route {method} {path}"}

    def count(self, method, prefix):
        return sum(1 for m, p, _ in self.calls if m == method and p.startswith(prefix))


def args(*argv):
    return sa.parse_args(["--tasks-file", "/nonexistent/tasks.json", *argv])


def run(fake, *argv, env=ENV):
    return sa.run(args(*argv), dict(env), transport=fake, sleep=lambda s: None)


def agent_tool_ids(fake, aid):
    return fake.agents[aid]["conversation_config"]["agent"]["prompt"]["tool_ids"]


# --------------------------------------------------------------------------------------- pure helpers

def test_resolve_placeholders():
    obj = {"url": "{BACKEND_URL}/api/x/{item_id}", "dyn": "{{user_name}}", "voice": "{VOICE}", "n": 3,
           "list": ["a {BACKEND_URL}"]}
    out = sa.resolve_placeholders(obj, {"BACKEND_URL": "https://b", "VOICE": None})
    assert out == {"url": "https://b/api/x/{item_id}", "dyn": "{{user_name}}", "n": 3, "list": ["a https://b"]}
    with pytest.raises(sa.SetupError, match="unknown placeholder {NOPE}"):
        sa.resolve_placeholders({"a": "{NOPE}"}, {})
    with pytest.raises(sa.SetupError, match="has no value"):
        sa.resolve_placeholders({"a": "x {VOICE}"}, {"VOICE": None})


def test_check_backend_url(capsys):
    assert sa.check_backend_url("https://b.example.com/") == "https://b.example.com"
    assert sa.check_backend_url(" https://b.example.com/api/ ") == "https://b.example.com"
    assert sa.check_backend_url("https://b.example.com/macrae") == "https://b.example.com/macrae"
    for bad in (None, "", "b.example.com", "ftp://b", "https://b.example.com/?x=1"):
        with pytest.raises(sa.SetupError, match="BACKEND_URL"):
            sa.check_backend_url(bad)
    capsys.readouterr()
    sa.check_backend_url("http://localhost:8080")
    assert "not reachable from ElevenLabs" in capsys.readouterr().err
    sa.check_backend_url("http://backend.example.com")
    assert "plain http" in capsys.readouterr().err


def test_load_tasks(tmp_path):
    assert sa.load_tasks(tmp_path / "missing.json") == sa.FALLBACK_TASKS
    (tmp_path / "bad.json").write_text("{nope")
    assert sa.load_tasks(tmp_path / "bad.json") == sa.FALLBACK_TASKS
    tasks = [{"id": "methods-card", "title": "Methods card", "subtitle": "cited methods",
              "inputs": [{"name": "doi", "label": "DOI", "default": "10.1021/x"}]}, {"title": "no id"}]
    (tmp_path / "list.json").write_text(json.dumps(tasks))
    (tmp_path / "obj.json").write_text(json.dumps({"tasks": tasks}))
    assert sa.load_tasks(tmp_path / "list.json") == tasks[:1] == sa.load_tasks(tmp_path / "obj.json")
    text = sa.task_list_text(tasks[:1])
    assert text == "- methods-card: Methods card (cited methods). Inputs come from the page: DOI defaults to 10.1021/x."


def test_build_payloads_resolves_everything():
    tasks = [{"id": "a-task", "title": "A"}, {"id": "b-task", "prompt": "do\nb"}]
    agent, tools = sa.build_payloads(BACKEND, tasks, "sec_9", voice_id="voice123", llm="claude-haiku-4-5")
    blob = json.dumps([agent, tools])
    assert not sa.PLACEHOLDER.search(blob)
    assert agent["conversation_config"]["tts"]["voice_id"] == "voice123"
    assert agent["conversation_config"]["agent"]["prompt"]["llm"] == "claude-haiku-4-5"
    assert "- a-task: A.\n- b-task (do b)." in agent["conversation_config"]["agent"]["prompt"]["prompt"]
    webhooks = [t for t in tools if t["type"] == "webhook"]
    assert all(t["api_schema"]["request_headers"]["X-Macrae-Secret"] == {"secret_id": "sec_9"} for t in webhooks)
    assert webhooks[0]["api_schema"]["url"] == BACKEND + "/api/tools/search_papers"
    assert "a-task, b-task" in webhooks[1]["api_schema"]["request_body_schema"]["properties"]["task_id"]["description"]

    agent, tools = sa.build_payloads(BACKEND, tasks, inline_secret=SECRET)
    assert "voice_id" not in agent["conversation_config"]["tts"]  # no voice -> ElevenLabs default
    assert tools[0]["api_schema"]["request_headers"]["X-Macrae-Secret"] == SECRET

    with pytest.raises(sa.SetupError, match="dynamic variable"):
        sa.build_payloads(BACKEND, tasks, agent_cfg={"conversation_config": {"agent": {"first_message": "hi {{x}}"}}})


# --------------------------------------------------------------------------------------- full runs

def test_missing_env_is_a_clear_error():
    fake = FakeElevenLabs()
    for var, msg in [("MACRAE_TOOL_SECRET", "MACRAE_TOOL_SECRET is not set"),
                     ("ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY is not set"),
                     ("BACKEND_URL", "BACKEND_URL is not set")]:
        env = {k: v for k, v in ENV.items() if k != var}
        with pytest.raises(sa.SetupError, match=msg):
            run(fake, env=env)
    assert fake.calls == []


def test_dry_run_calls_nothing_and_masks_secret(capsys):
    def no_network(*a):
        raise AssertionError("dry run must not call anything")
    env = {k: v for k, v in ENV.items() if k != "ELEVENLABS_API_KEY"}
    assert sa.run(args("--dry-run", "--inline-secret"), env, transport=no_network) is None
    out = capsys.readouterr().out
    assert SECRET not in out
    data = json.loads(out)
    assert data["tools"][0]["api_schema"]["request_headers"]["X-Macrae-Secret"] == "***"
    assert data["agent"]["name"] == "macrae"


def test_create_then_update_in_place():
    fake = FakeElevenLabs()
    aid = run(fake)
    assert aid in fake.agents and len(fake.tools) == 5 and len(fake.secrets) == 1
    (sid, secret), = fake.secrets.items()
    assert secret == {"type": "new", "name": "macrae_tool_secret", "value": SECRET}
    ids = agent_tool_ids(fake, aid)
    assert [fake.tools[t]["name"] for t in ids] == ["search_papers", "start_task", "run_status",
                                                    "show_citations", "open_run"]
    for t in ids[:3]:
        assert fake.tools[t]["api_schema"]["request_headers"] == {"X-Macrae-Secret": {"secret_id": sid}}
        assert fake.tools[t]["api_schema"]["url"].startswith(BACKEND + "/api/tools/")
    assert SECRET not in json.dumps([fake.tools, fake.agents])  # only the secret store has the value
    assert fake.count("GET", "/api/health") == 1

    fake.calls.clear()
    assert run(fake, "--skip-backend-check") == aid  # found by name
    assert len(fake.agents) == 1 and len(fake.tools) == 5 and len(fake.secrets) == 1
    assert fake.count("POST", "/v1/convai") == 0
    assert fake.count("PATCH", "/v1/convai/tools/") == 5
    assert fake.count("PATCH", f"/v1/convai/secrets/{sid}") == 1
    assert fake.count("PATCH", f"/v1/convai/agents/{aid}") == 1
    assert agent_tool_ids(fake, aid) == ids
    assert fake.count("GET", "/api/health") == 0


def test_update_by_agent_id_recreates_missing_tool_and_keeps_others_tools():
    fake = FakeElevenLabs()
    aid = run(fake, "--skip-backend-check")
    old = agent_tool_ids(fake, aid)
    other = fake.route("POST", "/v1/convai/tools", {}, {"tool_config": {"type": "webhook", "name": "search_papers"}})
    del fake.tools[old[0]]  # someone deleted search_papers in the dashboard
    fake.agents[aid]["name"] = "renamed"
    new_id = run(fake, "--skip-backend-check", env={**ENV, "ELEVENLABS_AGENT_ID": aid})
    assert new_id == aid and fake.agents[aid]["name"] == "macrae"
    ids = agent_tool_ids(fake, aid)
    assert ids[1:] == old[1:] and ids[0] not in old and ids[0] != other[1]["id"]
    assert fake.tools[other[1]["id"]] == {"type": "webhook", "name": "search_papers"}  # untouched


def test_unknown_agent_id_and_ambiguous_name():
    fake = FakeElevenLabs()
    with pytest.raises(sa.SetupError, match="agent_x .* was not found"):
        run(fake, "--agent-id", "agent_x", "--skip-backend-check")
    fake.agents = {"a1": {"name": "macrae"}, "a2": {"name": "macrae"}, "a3": {"name": "macrae", "archived": True}}
    with pytest.raises(sa.SetupError, match="2 agents are named 'macrae' \\(a1, a2\\)"):
        run(fake, "--skip-backend-check")


def test_rejected_config_rolls_back_new_tools():
    fake = FakeElevenLabs()
    fake.fail[("POST", "/v1/convai/agents/create")] = [422]
    with pytest.raises(sa.SetupError, match=r"rejected the config:\n  x: boom"):
        run(fake, "--skip-backend-check")
    assert fake.tools == {} and fake.agents == {}
    assert fake.count("DELETE", "/v1/convai/tools/") == 5


def test_auth_errors_and_retries():
    fake = FakeElevenLabs()
    fake.fail[("GET", "/v1/convai/agents")] = [401]
    with pytest.raises(sa.SetupError, match="ELEVENLABS_API_KEY was rejected"):
        run(fake, "--skip-backend-check")

    fake = FakeElevenLabs()
    fake.fail[("GET", "/v1/convai/agents")] = [503, 429]  # transient: retried
    assert run(fake, "--skip-backend-check")

    fake = FakeElevenLabs()
    fake.fail[("POST", "/v1/convai/secrets")] = [500, 500]  # POST is not retried (could duplicate)
    with pytest.raises(sa.SetupError, match="HTTP 500"):
        run(fake, "--skip-backend-check")
    assert fake.count("POST", "/v1/convai/secrets") == 1


def test_network_down():
    def down(*a):
        raise urllib.error.URLError("Name or service not known")
    client = sa.ElevenLabs("k", transport=down, sleep=lambda s: None)
    with pytest.raises(sa.SetupError, match="can't reach https://api.elevenlabs.io"):
        client.request("GET", "/v1/convai/agents")


def test_backend_check_warns_but_continues(capsys):
    fake = FakeElevenLabs()
    fake.backend_status["/api/tools/search_papers"] = 401
    assert run(fake)
    assert "rejected MACRAE_TOOL_SECRET" in capsys.readouterr().err
    fake.backend_status["/api/health"] = 502
    assert run(fake)
    assert "returned HTTP 502" in capsys.readouterr().err


def test_main_prints_only_the_agent_id(monkeypatch, capsys):
    fake = FakeElevenLabs()
    monkeypatch.setattr(sa, "urllib_transport", fake)
    monkeypatch.setattr(sa.run, "__defaults__", (fake, lambda s: None))
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ELEVENLABS_AGENT_ID", raising=False)
    assert sa.main(["--tasks-file", "/nonexistent.json"]) == 0
    out, err = capsys.readouterr()
    assert out.strip() in fake.agents and "created agent macrae" in err
    monkeypatch.delenv("MACRAE_TOOL_SECRET")
    assert sa.main([]) == 1
    assert "error: MACRAE_TOOL_SECRET is not set" in capsys.readouterr().err
