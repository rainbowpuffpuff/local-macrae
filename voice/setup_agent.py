#!/usr/bin/env python3
"""Create or update the macrae ElevenLabs voice agent (ElevenAgents / Conversational AI).

    ELEVENLABS_API_KEY=... BACKEND_URL=https://... MACRAE_TOOL_SECRET=... python voice/setup_agent.py

Reads voice/agent.json (the agent: prompt, LLM, voice) and voice/tools.json (three webhook tools that call the
backend and two client tools the page implements), then, through the ElevenLabs API:

1. stores MACRAE_TOOL_SECRET as a workspace secret, so the webhook tools send it as header X-Macrae-Secret
   without the value living in the tool config;
2. creates or updates the five tools (POST/PATCH /v1/convai/tools);
3. creates or updates the agent (POST /v1/convai/agents/create, PATCH /v1/convai/agents/{id}) with those tools
   in conversation_config.agent.prompt.tool_ids.

The agent id is the only thing printed to stdout (logs go to stderr), so this works:
    export ELEVENLABS_AGENT_ID=$(python voice/setup_agent.py)
Running it again updates the same agent (found by ELEVENLABS_AGENT_ID or --agent-id, else by its name) and its
tools in place. --dry-run prints the resolved payloads without calling ElevenLabs.

Stdlib only (no pip install needed).
"""
from __future__ import annotations

import argparse
import copy
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_API_BASE = "https://api.elevenlabs.io"
SECRET_NAME = "macrae_tool_secret"
SECRET_HEADER = "X-Macrae-Secret"
# Used in the prompt and the start_task description when tasks/tasks.json can't be read (ids from CONTRACT.md).
FALLBACK_TASKS = [
    {"id": "methods-card", "title": "Methods card", "subtitle": "a cited summary of the methods of one paper"},
    {"id": "small-calc", "title": "Small calculation", "subtitle": "a tiny real computation on Modal, explained with citations"},
]
# Placeholders look like {NAME} with NAME in capitals; ElevenLabs path params ({lowercase}) and dynamic
# variables ({{name}}) are never touched.
PLACEHOLDER = re.compile(r"(?<!\{)\{([A-Z][A-Z0-9_]*)\}(?!\})")

Transport = Callable[[str, str, dict, "bytes | None", float], "tuple[int, bytes]"]


class SetupError(Exception):
    """A problem the user can fix; printed without a traceback."""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------------------------- config files

def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SetupError(f"{path} not found")
    except json.JSONDecodeError as e:
        raise SetupError(f"{path} is not valid JSON: {e}")


def resolve_placeholders(obj: Any, values: dict[str, str | None], where: str = "") -> Any:
    """Replace {NAME} placeholders in every string of obj.

    A string that is exactly one placeholder whose value is None is dropped from its dict (so the API default
    applies, e.g. no ELEVENLABS_VOICE_ID -> ElevenLabs' default voice). Unknown placeholders are an error.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            m = PLACEHOLDER.fullmatch(v) if isinstance(v, str) else None
            if m and m.group(1) in values and values[m.group(1)] is None:
                continue
            out[k] = resolve_placeholders(v, values, f"{where}.{k}" if where else k)
        return out
    if isinstance(obj, list):
        return [resolve_placeholders(v, values, f"{where}[{i}]") for i, v in enumerate(obj)]
    if isinstance(obj, str):
        def sub(m: re.Match) -> str:
            name = m.group(1)
            if name not in values:
                raise SetupError(f"unknown placeholder {{{name}}} in {where}; known: {', '.join(sorted(values))}")
            if values[name] is None:
                raise SetupError(f"placeholder {{{name}}} in {where} has no value (set {name})")
            return values[name]
        return PLACEHOLDER.sub(sub, obj)
    return obj


def load_tasks(path: Path) -> list[dict]:
    """Tasks from tasks/tasks.json (list, or {"tasks": [...]}); falls back to the contract's two ids."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log(f"note: {path} not found; describing the contract's default tasks (methods-card, small-calc)")
        return FALLBACK_TASKS
    except (OSError, json.JSONDecodeError) as e:
        log(f"warning: can't read {path} ({e}); describing the contract's default tasks")
        return FALLBACK_TASKS
    tasks = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(tasks, list):
        log(f"warning: {path} has no task list; describing the contract's default tasks")
        return FALLBACK_TASKS
    good = [t for t in tasks if isinstance(t, dict) and isinstance(t.get("id"), str) and t["id"].strip()]
    if not good:
        log(f"warning: {path} lists no tasks with an id; describing the contract's default tasks")
        return FALLBACK_TASKS
    return good


def _one_line(text: Any, limit: int = 300) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def task_list_text(tasks: list[dict]) -> str:
    """One line per task for the system prompt: id, title, what it does, its inputs."""
    lines = []
    for t in tasks:
        line = f"- {t['id']}"
        title = _one_line(t.get("title"), 120)
        if title:
            line += f": {title}"
        what = _one_line(t.get("subtitle") or t.get("prompt"))
        if what:
            line += f" ({what})"
        inputs = t.get("inputs") or []
        named = [i for i in inputs if isinstance(i, dict) and i.get("name")]
        if named:
            parts = [f"{i.get('label') or i['name']} defaults to {i['default']}" if i.get("default") not in (None, "")
                     else str(i.get("label") or i["name"]) for i in named]
            line += ". Inputs come from the page: " + "; ".join(parts)
        lines.append(line + ".")
    return "\n".join(lines)


def check_backend_url(raw: str | None) -> str:
    """Normalize BACKEND_URL to an origin (+ optional base path) without trailing slash or /api."""
    if not raw or not raw.strip():
        raise SetupError("BACKEND_URL is not set: the public https URL of the backend (server/), e.g. "
                         "https://macrae.example.com. ElevenLabs calls the tools there.")
    url = raw.strip().rstrip("/")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SetupError(f"BACKEND_URL={raw!r} is not an http(s) URL")
    if parsed.query or parsed.fragment:
        raise SetupError(f"BACKEND_URL={raw!r} must not have a query string or fragment")
    if url.endswith("/api"):
        url = url[: -len("/api")]
        log(f"note: BACKEND_URL ends with /api; using {url} (the tools add /api/tools/...)")
    host = parsed.hostname or ""
    local = host in ("localhost", "0.0.0.0") or host.endswith(".local")
    try:
        ip = ipaddress.ip_address(host)
        local = local or ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        pass
    if local:
        log(f"warning: BACKEND_URL host {host} is not reachable from ElevenLabs' servers; the tools will fail "
            "until it points at the public backend (or a tunnel such as cloudflared/ngrok)")
    elif parsed.scheme != "https":
        log("warning: BACKEND_URL is plain http; the shared secret would cross the internet unencrypted")
    return url


def validate_tools(tools: Any) -> None:
    if not isinstance(tools, list) or not tools:
        raise SetupError("tools.json must be a non-empty JSON list of tool configs")
    names = set()
    for i, t in enumerate(tools):
        if not isinstance(t, dict):
            raise SetupError(f"tools.json[{i}] is not an object")
        for key in ("type", "name", "description"):
            if not isinstance(t.get(key), str) or not t[key].strip():
                raise SetupError(f"tools.json[{i}] needs a non-empty {key!r}")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", t["name"]):
            raise SetupError(f"tool name {t['name']!r}: use letters, digits, _ or - (max 64)")
        if t["name"] in names:
            raise SetupError(f"tool name {t['name']!r} appears twice in tools.json")
        names.add(t["name"])
        if t["type"] == "webhook":
            api = t.get("api_schema")
            if not isinstance(api, dict) or not isinstance(api.get("url"), str):
                raise SetupError(f"webhook tool {t['name']} needs api_schema.url")
        elif t["type"] != "client":
            raise SetupError(f"tool {t['name']}: type {t['type']!r} is not supported here (webhook or client)")


# ---------------------------------------------------------------------------------------------- payloads

def build_payloads(backend_url: str, tasks: list[dict], secret_id: str | None = None,
                   inline_secret: str | None = None, voice_id: str | None = None, llm: str | None = None,
                   agent_cfg: Any = None, tools_cfg: Any = None) -> tuple[dict, list[dict]]:
    """Resolve agent.json and tools.json into API bodies.

    The X-Macrae-Secret header references the workspace secret `secret_id`, or with inline_secret carries the
    plain value instead."""
    agent_cfg = load_json(HERE / "agent.json") if agent_cfg is None else copy.deepcopy(agent_cfg)
    tools_cfg = load_json(HERE / "tools.json") if tools_cfg is None else copy.deepcopy(tools_cfg)
    validate_tools(tools_cfg)
    if not isinstance(agent_cfg, dict) or not isinstance(agent_cfg.get("conversation_config"), dict):
        raise SetupError("agent.json must be an object with a conversation_config")

    values: dict[str, str | None] = {
        "BACKEND_URL": backend_url,
        "MACRAE_TOOL_SECRET_ID": secret_id or "(created by setup_agent.py)",
        "TASK_LIST": task_list_text(tasks),
        "TASK_IDS": ", ".join(t["id"] for t in tasks),
        "ELEVENLABS_VOICE_ID": voice_id or None,
    }
    tools = resolve_placeholders(tools_cfg, values, "tools.json")
    if inline_secret:
        for t in tools:
            headers = t.get("api_schema", {}).get("request_headers", {})
            for name, value in headers.items():
                if isinstance(value, dict) and "secret_id" in value:
                    headers[name] = inline_secret
    agent = resolve_placeholders(agent_cfg, values, "agent.json")
    prompt = agent["conversation_config"].setdefault("agent", {}).setdefault("prompt", {})
    if llm:
        prompt["llm"] = llm
    if "{{" in json.dumps(agent, ensure_ascii=False):
        raise SetupError("agent.json contains '{{': ElevenLabs treats that as a dynamic variable the page "
                         "would have to supply; remove it")
    return agent, tools


# ---------------------------------------------------------------------------------------------- HTTP

def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""


def _detail(raw: bytes) -> str:
    """Human-readable error body: FastAPI-style 422 details become 'loc: msg' lines."""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")[:600]
    detail = data.get("detail", data) if isinstance(data, dict) else data
    if isinstance(detail, list):
        lines = []
        for d in detail[:12]:
            if isinstance(d, dict):
                loc = ".".join(str(x) for x in d.get("loc", []) if x != "body")
                lines.append(f"  {loc or '(body)'}: {d.get('msg', d)}")
            else:
                lines.append(f"  {d}")
        return "\n" + "\n".join(lines)
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("status") or json.dumps(detail))[:600]
    return str(detail)[:600]


class ElevenLabs:
    def __init__(self, api_key: str, base: str = DEFAULT_API_BASE, transport: Transport = urllib_transport,
                 timeout: float = 30.0, retries: int = 3, sleep: Callable[[float], None] = time.sleep):
        self.api_key, self.base = api_key, base.rstrip("/")
        self.transport, self.timeout, self.retries, self.sleep = transport, timeout, retries, sleep

    def request(self, method: str, path: str, body: Any = None, params: dict | None = None,
                ok404: bool = False) -> Any:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        headers = {"xi-api-key": self.api_key, "Accept": "application/json", "User-Agent": "macrae-voice-setup/1"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        # POST creates things: retrying it after a 5xx or a dropped connection could create duplicates.
        idempotent = method != "POST"
        for attempt in range(self.retries + 1):
            try:
                status, raw = self.transport(method, url, headers, data, self.timeout)
            except (urllib.error.URLError, OSError) as e:
                if idempotent and attempt < self.retries:
                    self.sleep(2 ** attempt)
                    continue
                raise SetupError(f"{method} {path}: can't reach {self.base} ({getattr(e, 'reason', e)}); check "
                                 "the network or ELEVENLABS_API_BASE")
            if (status == 429 or (status >= 500 and idempotent)) and attempt < self.retries:
                self.sleep(2 ** attempt)
                continue
            break
        if 200 <= status < 300:
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except ValueError:
                raise SetupError(f"{method} {path}: ElevenLabs returned non-JSON ({raw[:200]!r})")
        if status == 404 and ok404:
            return None
        what = f"{method} {path} failed with HTTP {status}"
        if status == 401:
            raise SetupError(f"{what}: ELEVENLABS_API_KEY was rejected. Check the key (Settings > API keys) "
                             f"and that it isn't a restricted key without ElevenAgents access. {_detail(raw)}")
        if status == 403:
            raise SetupError(f"{what}: the API key lacks permission. Give it 'ElevenAgents: write' "
                             f"(and workspace secrets access) or use an unrestricted key. {_detail(raw)}")
        if status == 422:
            raise SetupError(f"{what}: ElevenLabs rejected the config:{_detail(raw)}")
        raise SetupError(f"{what}: {_detail(raw)}")


def _paged(client: ElevenLabs, path: str, key: str, params: dict) -> list[dict]:
    items, cursor = [], None
    for _ in range(50):
        page = client.request("GET", path, params={**params, "cursor": cursor}) or {}
        items += page.get(key) or []
        cursor = page.get("next_cursor")
        if not cursor or page.get("has_more") is False:
            break
    return items


# ---------------------------------------------------------------------------------------------- steps

def ensure_secret(client: ElevenLabs, value: str, name: str = SECRET_NAME) -> str:
    """Create or update the workspace secret holding MACRAE_TOOL_SECRET; returns its secret_id."""
    found = [s for s in _paged(client, "/v1/convai/secrets", "secrets", {"search": name, "page_size": 100})
             if s.get("name") == name]
    if found:
        secret_id = found[0]["secret_id"]
        client.request("PATCH", f"/v1/convai/secrets/{secret_id}", {"type": "update", "name": name, "value": value})
        log(f"updated workspace secret {name} ({secret_id})")
        return secret_id
    created = client.request("POST", "/v1/convai/secrets", {"type": "new", "name": name, "value": value})
    secret_id = (created or {}).get("secret_id")
    if not secret_id:
        raise SetupError(f"creating secret {name}: no secret_id in response {created!r}")
    log(f"created workspace secret {name} ({secret_id})")
    return secret_id


def find_agent(client: ElevenLabs, agent_id: str | None, name: str) -> dict | None:
    """The existing agent: by id if given (must exist), else the one non-archived agent with exactly this name."""
    if agent_id:
        agent = client.request("GET", f"/v1/convai/agents/{urllib.parse.quote(agent_id)}", ok404=True)
        if agent is None:
            raise SetupError(f"agent {agent_id} (ELEVENLABS_AGENT_ID / --agent-id) was not found in this "
                             "workspace. Unset it to create a new agent, or fix the id.")
        return agent
    matches = [a for a in _paged(client, "/v1/convai/agents", "agents", {"search": name, "page_size": 100})
               if a.get("name") == name and not a.get("archived")]
    if not matches:
        return None
    if len(matches) > 1:
        ids = ", ".join(a["agent_id"] for a in matches)
        raise SetupError(f"{len(matches)} agents are named {name!r} ({ids}); pick one with --agent-id or "
                         "ELEVENLABS_AGENT_ID")
    return client.request("GET", f"/v1/convai/agents/{matches[0]['agent_id']}")


def current_tool_ids(agent: dict | None) -> list[str]:
    if not agent:
        return []
    prompt = ((agent.get("conversation_config") or {}).get("agent") or {}).get("prompt") or {}
    return list(prompt.get("tool_ids") or [])


def ensure_tools(client: ElevenLabs, tools: list[dict], attached_ids: list[str],
                 created: list[str]) -> list[str]:
    """Update the agent's tools that have our names in place, create the rest; returns ids in tools.json order.

    Only tools already attached to this agent are reused, so same-named tools of other agents in the workspace
    are never touched. Ids of newly created tools are appended to `created` (for rollback)."""
    existing: dict[str, dict] = {}
    for tid in attached_ids:
        tool = client.request("GET", f"/v1/convai/tools/{urllib.parse.quote(tid)}", ok404=True)
        cfg = (tool or {}).get("tool_config") or {}
        if cfg.get("name"):
            existing.setdefault(cfg["name"], {"id": tid, "type": cfg.get("type")})
    ids = []
    for cfg in tools:
        old = existing.get(cfg["name"])
        if old and old["type"] == cfg["type"]:
            client.request("PATCH", f"/v1/convai/tools/{old['id']}", {"tool_config": cfg})
            log(f"updated {cfg['type']} tool {cfg['name']} ({old['id']})")
            ids.append(old["id"])
        else:
            res = client.request("POST", "/v1/convai/tools", {"tool_config": cfg})
            tid = (res or {}).get("id")
            if not tid:
                raise SetupError(f"creating tool {cfg['name']}: no id in response {res!r}")
            created.append(tid)
            log(f"created {cfg['type']} tool {cfg['name']} ({tid})")
            ids.append(tid)
    dropped = [t for t in attached_ids if t not in ids]
    if dropped:
        log(f"note: detaching tools not in tools.json: {', '.join(dropped)}")
    return ids


def upsert_agent(client: ElevenLabs, agent_body: dict, existing: dict | None) -> str:
    if existing:
        agent_id = existing["agent_id"]
        body = {**agent_body, "version_description": "voice/setup_agent.py"}
        client.request("PATCH", f"/v1/convai/agents/{agent_id}", body)
        log(f"updated agent {agent_body.get('name')} ({agent_id})")
        return agent_id
    res = client.request("POST", "/v1/convai/agents/create", agent_body)
    agent_id = (res or {}).get("agent_id")
    if not agent_id:
        raise SetupError(f"creating the agent: no agent_id in response {res!r}")
    log(f"created agent {agent_body.get('name')} ({agent_id})")
    return agent_id


def verify_agent(client: ElevenLabs, agent_id: str, agent_body: dict, tool_ids: list[str]) -> None:
    got = client.request("GET", f"/v1/convai/agents/{agent_id}")
    have = current_tool_ids(got)
    if sorted(have) != sorted(tool_ids):
        raise SetupError(f"agent {agent_id} saved, but its tool_ids are {have}, expected {tool_ids}")
    want_llm = agent_body["conversation_config"]["agent"]["prompt"].get("llm")
    got_llm = ((got.get("conversation_config") or {}).get("agent") or {}).get("prompt", {}).get("llm")
    if want_llm and got_llm and got_llm != want_llm:
        log(f"warning: agent LLM is {got_llm}, expected {want_llm}")
    auth = ((got.get("platform_settings") or {}).get("auth") or {}).get("enable_auth")
    if auth is False:
        log("warning: agent auth is off; anyone with the agent id can start (paid) conversations")


def check_backend(backend_url: str, secret: str, transport: Transport = urllib_transport) -> None:
    """Non-fatal: warn if the backend isn't up or rejects the shared secret."""
    try:
        status, raw = transport("GET", backend_url + "/api/health", {"Accept": "application/json"}, None, 8)
    except (urllib.error.URLError, OSError) as e:
        log(f"warning: backend {backend_url} unreachable ({getattr(e, 'reason', e)}); the agent is configured, "
            "but its tools fail until the backend is up")
        return
    if status != 200:
        log(f"warning: {backend_url}/api/health returned HTTP {status}; the tools may fail")
        return
    body = json.dumps({"query": "methods"}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json", SECRET_HEADER: secret}
    try:
        status, raw = transport("POST", backend_url + "/api/tools/search_papers", headers, body, 30)
    except (urllib.error.URLError, OSError) as e:
        log(f"warning: search_papers on the backend failed ({getattr(e, 'reason', e)})")
        return
    if status in (401, 403):
        log("warning: the backend rejected MACRAE_TOOL_SECRET; set the same value here and on the backend")
    elif status != 200:
        log(f"warning: backend search_papers returned HTTP {status}: {_detail(raw)}")
    else:
        log(f"backend {backend_url} is up and accepts the tool secret")


def mask(obj: Any, secret: str) -> Any:
    if isinstance(obj, dict):
        return {k: mask(v, secret) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask(v, secret) for v in obj]
    if isinstance(obj, str) and secret and secret in obj:
        return obj.replace(secret, "***")
    return obj


def run(args: argparse.Namespace, env: dict[str, str], transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep) -> str | None:
    secret = (env.get("MACRAE_TOOL_SECRET") or "").strip()
    if not secret:
        raise SetupError("MACRAE_TOOL_SECRET is not set: the shared secret the backend expects in header "
                         f"{SECRET_HEADER} (the same value the backend and the Worker use)")
    if len(secret) < 16:
        log("warning: MACRAE_TOOL_SECRET is short; use something like `openssl rand -hex 32`")
    api_key = (env.get("ELEVENLABS_API_KEY") or "").strip()
    if not api_key and not args.dry_run:
        raise SetupError("ELEVENLABS_API_KEY is not set (ElevenLabs > Settings > API keys)")

    tasks_file = Path(args.tasks_file or env.get("MACRAE_TASKS_FILE") or "tasks/tasks.json")
    if not tasks_file.is_absolute() and not tasks_file.exists():
        tasks_file = REPO / tasks_file
    tasks = load_tasks(tasks_file)
    backend_url = check_backend_url(env.get("BACKEND_URL"))
    voice_id = (env.get("ELEVENLABS_VOICE_ID") or "").strip() or None
    llm = (env.get("ELEVENLABS_LLM") or "").strip() or None
    inline = secret if args.inline_secret else None

    def payloads(secret_id: str | None = None) -> tuple[dict, list[dict]]:
        return build_payloads(backend_url, tasks, secret_id, inline, voice_id, llm)

    agent_body, tools = payloads()  # validates the config before anything touches ElevenLabs
    if not voice_id:
        log("note: ELEVENLABS_VOICE_ID not set; the agent uses ElevenLabs' default voice")
    if args.dry_run:
        print(json.dumps(mask({"agent": agent_body, "tools": tools}, secret), indent=2, ensure_ascii=False))
        return None

    if not args.skip_backend_check:
        check_backend(backend_url, secret, transport)

    client = ElevenLabs(api_key, env.get("ELEVENLABS_API_BASE") or DEFAULT_API_BASE, transport, sleep=sleep)
    agent_id_hint = (args.agent_id or env.get("ELEVENLABS_AGENT_ID") or "").strip() or None
    existing = find_agent(client, agent_id_hint, agent_body.get("name") or "macrae")
    if not inline:
        agent_body, tools = payloads(ensure_secret(client, secret))

    created: list[str] = []
    try:
        tool_ids = ensure_tools(client, tools, current_tool_ids(existing), created)
        agent_body["conversation_config"]["agent"]["prompt"]["tool_ids"] = tool_ids
        agent_id = upsert_agent(client, agent_body, existing)
    except Exception:
        for tid in created:  # don't leave orphan tools behind when the agent couldn't be saved
            try:
                client.request("DELETE", f"/v1/convai/tools/{tid}", ok404=True)
                log(f"rolled back new tool {tid}")
            except SetupError as e:
                log(f"warning: could not delete tool {tid}: {e}")
        raise
    verify_agent(client, agent_id, agent_body, tool_ids)
    if agent_id != agent_id_hint:
        log(f"set ELEVENLABS_AGENT_ID={agent_id} for the Worker: cd cloudflare && npx wrangler secret put "
            "ELEVENLABS_AGENT_ID")
    return agent_id


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create or update the macrae ElevenLabs voice agent; prints its id.")
    p.add_argument("--agent-id", help="update this agent (default: $ELEVENLABS_AGENT_ID, else find by name)")
    p.add_argument("--tasks-file", help="tasks list for the prompt (default: $MACRAE_TASKS_FILE or tasks/tasks.json)")
    p.add_argument("--dry-run", action="store_true", help="print the resolved agent and tool configs, call nothing")
    p.add_argument("--inline-secret", action="store_true",
                   help="put MACRAE_TOOL_SECRET in the tool headers as plain text instead of a workspace secret")
    p.add_argument("--skip-backend-check", action="store_true", help="don't probe BACKEND_URL first")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        agent_id = run(args, dict(os.environ))
    except SetupError as e:
        log(f"error: {e}")
        return 1
    except KeyboardInterrupt:
        log("interrupted")
        return 130
    if agent_id:
        print(agent_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
