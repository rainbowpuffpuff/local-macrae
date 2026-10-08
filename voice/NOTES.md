# voice/: NOTES

The ElevenLabs voice agent ("ElevenAgents", formerly Conversational AI) for the macrae page: its config, its
tools, and a script that creates or updates it through the ElevenLabs API.

## What's here
| file | what |
|---|---|
| `agent.json` | Agent create/update body in the API's own shape: name `macrae`, first message, system prompt, LLM `claude-sonnet-5-5` (Claude via ElevenLabs), TTS `eleven_v4_turbo` with voice placeholder `{ELEVENLABS_VOICE_ID}`, ASR keywords (Jungwirth, xtb, OpenMM, …), client events, `platform_settings.auth.enable_auth = true` |
| `tools.json` | List of five `tool_config` objects (body of `POST /v1/convai/tools`): webhook tools `search_papers`, `start_task`, `run_status` → `POST {BACKEND_URL}/api/tools/<name>` with header `X-Macrae-Secret`; client tools `show_citations(citations)`, `open_run(run_id)` |
| `setup_agent.py` | Creates or updates the secret, tools and agent; prints the agent id. Stdlib only |
| `tests/` | `test_voice_config.py` (configs vs CONTRACT.md), `test_voice_setup.py` (full runs against a fake ElevenLabs API) |

Placeholders `{NAME}` (capitals only) are filled in by `setup_agent.py`: `BACKEND_URL`, `ELEVENLABS_VOICE_ID`,
`TASK_LIST` / `TASK_IDS` (from `tasks/tasks.json`), `MACRAE_TOOL_SECRET_ID`. ElevenLabs' own `{path_param}` and
`{{dynamic_variable}}` syntax is never touched. The prompt deliberately has no `{{…}}`, so the page doesn't
have to send any dynamic variables.

## ElevenLabs API used (checked against the live OpenAPI spec, api.elevenlabs.io/openapi.json, 2026-10-08)
- Tools are standalone workspace resources: `POST /v1/convai/tools` and `PATCH /v1/convai/tools/{id}`, both
  with body `{"tool_config": …}`. The agent refers to them in `conversation_config.agent.prompt.tool_ids`. The
  inline `prompt.tools` field is deprecated and not used.
- Agent: `POST /v1/convai/agents/create` → `{agent_id}`, `PATCH /v1/convai/agents/{id}`,
  `GET /v1/convai/agents?search=` (find by name), `GET /v1/convai/agents/{id}`.
- Shared secret: workspace secret `macrae_tool_secret` (`POST /v1/convai/secrets` `{"type":"new",…}`, or
  `PATCH /v1/convai/secrets/{id}` `{"type":"update",…}` when it already exists). The tools send it as
  `request_headers: {"X-Macrae-Secret": {"secret_id": "…"}}`, so the value never sits in a tool config.
  Use `--inline-secret` to put the plain value in the header instead, e.g. if the key can't manage secrets.
- I resolved the payloads `setup_agent.py` sends and validated them with jsonschema against the spec's
  `Body_Create_Agent…`, `ConversationalConfigAPIModel-Input`, `ToolRequestModel`, `WebhookToolConfig-Input` and
  `ClientToolConfig-Input` schemas, with `additionalProperties: false` forced on so misspelled fields fail too.
  All pass. That was a one-off check; the spec (2 MB) isn't committed.

## Run it
```bash
export ELEVENLABS_API_KEY=…            # needs ElevenAgents write access (and workspace secrets)
export BACKEND_URL=https://<backend>   # public URL of server/, without /api; ElevenLabs calls it
export MACRAE_TOOL_SECRET=…            # same value as on the backend and the Worker
export ELEVENLABS_VOICE_ID=…           # optional; unset = ElevenLabs' default voice
python voice/setup_agent.py --dry-run  # print resolved agent + tools (secret masked), call nothing
export ELEVENLABS_AGENT_ID=$(python voice/setup_agent.py)
cd cloudflare && npx wrangler secret put ELEVENLABS_AGENT_ID   # the Worker needs it for /voice/signed-url
```
- Only the agent id goes to stdout; progress, warnings and errors go to stderr. Exit code 1 on error, with a
  message saying what to fix.
- Re-running updates in place: the agent is found by `--agent-id` / `ELEVENLABS_AGENT_ID`, otherwise by its
  exact name. Its existing tools are PATCHed by name, and missing tools are created. Same-named tools that
  belong to other agents in the workspace are never touched.
- **Re-run after editing `tasks/tasks.json` or the prompt**, because the task list is baked into the prompt.
- Before touching ElevenLabs, it probes `GET {BACKEND_URL}/api/health` and `POST /api/tools/search_papers` with
  the secret, and warns if the backend is down or rejects the secret (`--skip-backend-check` turns this off).
  It also warns if `BACKEND_URL` is localhost or a private IP, which ElevenLabs can't reach (use a tunnel).
- Being defensive: config validation happens before any network call; clear messages on 401, 403 and 422 (422
  shows each field and its message); GET/PATCH are retried on 429/5xx but POST is never retried after a 5xx
  (it could create a duplicate); newly created tools are deleted again if saving the agent fails; afterwards
  the agent is read back to check its `tool_ids`, LLM and auth.
- Optional env: `ELEVENLABS_LLM` (overrides the LLM, e.g. `claude-haiku-4-5` for lower latency),
  `ELEVENLABS_API_BASE` (e.g. `https://api.eu.residency.elevenlabs.io`), `MACRAE_TASKS_FILE`.

## Test
```bash
pip install pytest && python -m pytest -q voice/tests   # 21 tests, no network, no key needed
```
Ran here: 21 passed in voice/tests; 26 passed for `pytest` at the repo root together with `tests/test_flows.py`.
I also ran the script against the real API with an invalid key and got the expected clear 401 error. **Not done
here: a real create**, because there is no `ELEVENLABS_API_KEY` in this environment. The first run with a real
key is the real test. If ElevenLabs rejects something, the 422 message names the field.

## What I assumed about other modules
- **server/**: `/api/tools/search_papers|start_task|run_status` take exactly `{"query"}`, `{"task_id"}` and
  `{"run_id"}` and check `X-Macrae-Secret` (401/403 otherwise). `search_papers.answer_context` numbers passages
  `[1]…[n]` with the same keys as `citations[].key`. On errors (unknown task, unknown run) the routes return
  4xx with a short JSON `detail`; the tools use `tool_error_handling_mode: passthrough`, so the agent can say
  why. Calls time out after 30 s (search_papers, start_task) and 20 s (run_status), so `start_task` must return
  as soon as the run is detached.
- **web/**: since `enable_auth` is on, the page must start sessions with the Worker's signed URL (as the
  contract says); a bare agent id won't connect. It registers client tools with these exact names and object
  parameters: `clientTools: { show_citations: ({citations}) => …, open_run: ({run_id}) => … }`. `citations`
  items are a subset of the contract's Citation (`key, title, authors, year, journal, doi, page, url, quote`;
  only `key` and `title` are guaranteed). Neither tool needs to return anything (`expects_response: false`).
  The agent also has the `agent_tool_response_full_payload` client event on, so `onAgentToolResponse` receives
  the full `search_papers` result (`full_tool_result`, a JSON string). The page can render citation cards from
  that directly, a more reliable fallback than waiting for the LLM to call `show_citations`.
- **web/** (optional): a run started by clicking a card is unknown to the agent. The prompt handles that by
  saying it can only check runs it started. If the page sends `conversation.sendContextualUpdate("User started
  run <run_id> (<task title>)")`, the agent can narrate that run too.
- **tasks/**: `tasks/tasks.json` is a list of Task objects, or `{"tasks": [...]}`; both are accepted. The
  prompt uses `id`, `title`, `subtitle` (or `prompt`), and `inputs[].label/default`. If the file is missing,
  the prompt describes the contract's `methods-card` and `small-calc`.
- **deploy/**: `deploy/env.example` should also list the voice setup variables `ELEVENLABS_API_KEY`,
  `ELEVENLABS_VOICE_ID` and, optionally, `ELEVENLABS_LLM` / `ELEVENLABS_API_BASE`. A `make voice` target would be
  `python voice/setup_agent.py`. The backend needs no ElevenLabs variables; ElevenLabs calls it.
- The voice config needs no pip packages, so there is no `voice/requirements.txt`.

## Owner decisions / knobs
- Voice: set `ELEVENLABS_VOICE_ID` (from the Voice Library) and re-run.
- Spoken citations follow the contract: "Košťál and colleagues, 2026 [1]". TTS uses
  `text_normalisation_type: elevenlabs`, so the transcript keeps `[1]` and `2026` as written. Whether the bracket
  number sounds natural in speech is worth a listen in the demo. If not, drop the "[n] when speaking" rule and
  rely on the citation cards.
- `max_duration_seconds` is 900 (15 min per conversation) and is billed. Change it in `agent.json`.
