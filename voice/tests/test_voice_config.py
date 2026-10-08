"""Static checks: voice/agent.json and voice/tools.json match CONTRACT.md and the ElevenLabs config rules."""
import importlib.util
import json
import re
from pathlib import Path

VOICE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("macrae_voice_setup_cfg", VOICE / "setup_agent.py")
setup_agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_agent)

AGENT = json.loads((VOICE / "agent.json").read_text(encoding="utf-8"))
TOOLS = json.loads((VOICE / "tools.json").read_text(encoding="utf-8"))
BY_NAME = {t["name"]: t for t in TOOLS}
CITATION_KEYS = {"key", "title", "authors", "year", "journal", "doi", "page", "url", "quote"}
KNOWN_PLACEHOLDERS = {"BACKEND_URL", "MACRAE_TOOL_SECRET_ID", "TASK_LIST", "TASK_IDS", "ELEVENLABS_VOICE_ID"}


def walk_props(schema, path="params"):
    """Yield (path, property schema) for every nested property / array item."""
    for name, prop in (schema.get("properties") or {}).items():
        yield f"{path}.{name}", prop
        yield from walk_props(prop, f"{path}.{name}")
    if isinstance(schema.get("items"), dict):
        yield f"{path}[]", schema["items"]
        yield from walk_props(schema["items"], f"{path}[]")


def test_tool_set_matches_contract():
    assert [t["name"] for t in TOOLS if t["type"] == "webhook"] == ["search_papers", "start_task", "run_status"]
    assert sorted(t["name"] for t in TOOLS if t["type"] == "client") == ["open_run", "show_citations"]
    setup_agent.validate_tools(TOOLS)


def test_webhooks_call_backend_with_secret_header():
    body_fields = {"search_papers": ["query"], "start_task": ["task_id"], "run_status": ["run_id"]}
    for name, fields in body_fields.items():
        api = BY_NAME[name]["api_schema"]
        assert api["url"] == "{BACKEND_URL}/api/tools/" + name
        assert api["method"] == "POST"
        assert api["request_headers"] == {"X-Macrae-Secret": {"secret_id": "{MACRAE_TOOL_SECRET_ID}"}}
        body = api["request_body_schema"]
        assert body["type"] == "object"
        assert sorted(body["properties"]) == fields and body["required"] == fields
        assert 5 <= BY_NAME[name]["response_timeout_secs"] <= 300


def test_client_tool_parameters_match_page_contract():
    cites = BY_NAME["show_citations"]["parameters"]
    assert cites["required"] == ["citations"]
    item = cites["properties"]["citations"]["items"]
    assert set(item["properties"]) <= CITATION_KEYS
    assert set(item["required"]) <= set(item["properties"])
    run = BY_NAME["open_run"]["parameters"]
    assert list(run["properties"]) == ["run_id"] and run["required"] == ["run_id"]
    for name in ("show_citations", "open_run"):
        assert BY_NAME[name]["expects_response"] is False


def test_every_parameter_is_described_and_typed():
    for t in TOOLS:
        root = t.get("parameters") or t.get("api_schema", {}).get("request_body_schema")
        assert root, t["name"]
        for path, prop in [("root", root), *walk_props(root)]:
            assert prop.get("description", "").strip(), f"{t['name']} {path} needs a description (the LLM reads it)"
            assert prop.get("type") in {"string", "integer", "number", "boolean", "object", "array"}, path
            if prop["type"] == "object":
                assert set(prop.get("required", [])) <= set(prop.get("properties", {})), path


def test_placeholders_are_known():
    found = set(setup_agent.PLACEHOLDER.findall(json.dumps(AGENT) + json.dumps(TOOLS)))
    assert found <= KNOWN_PLACEHOLDERS
    assert "{TASK_LIST}" in AGENT["conversation_config"]["agent"]["prompt"]["prompt"]
    assert AGENT["conversation_config"]["tts"]["voice_id"] == "{ELEVENLABS_VOICE_ID}"


def test_agent_config():
    agent = AGENT["conversation_config"]["agent"]
    assert agent["first_message"].strip()
    assert agent["prompt"]["llm"].startswith("claude-")
    assert "tool_ids" not in agent["prompt"] and "tools" not in agent["prompt"]  # filled by setup_agent.py
    assert AGENT["platform_settings"]["auth"]["enable_auth"] is True  # page connects via the Worker's signed URL
    events = AGENT["conversation_config"]["conversation"]["client_events"]
    for needed in ("audio", "interruption", "user_transcript", "agent_response", "client_tool_call"):
        assert needed in events
    assert "{{" not in json.dumps(AGENT), "{{...}} is an ElevenLabs dynamic variable the page would have to send"


def test_prompt_covers_contract_rules():
    prompt = AGENT["conversation_config"]["agent"]["prompt"]["prompt"]
    for tool in ("search_papers", "show_citations", "start_task", "open_run", "run_status"):
        assert tool in prompt
    assert re.search(r"only from the passages", prompt, re.I)            # answer from search_papers only
    assert "first author" in prompt and "[1]" in prompt and "year" in prompt  # cite [n] + first author + year
    assert "don't cover" in prompt                                        # say when papers don't cover it
    assert "one sentence" in prompt                                       # narrate run progress briefly
    assert "Available tasks" in prompt                                    # offer the clickable tasks
