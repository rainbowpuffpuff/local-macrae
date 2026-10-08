# tasks/common: the Modal agent image with the live-trace `claude` wrapper

Owned by the backend module (CONTRACT v2, "Live events").

| file | what |
|---|---|
| `Dockerfile` | the agent image: `agent_runner/image` contents (Ubuntu 24.04, Claude Code, python3, node) with the real CLI moved to `/usr/local/lib/macrae/claude-real` and the wrapper installed as `/usr/local/bin/claude` **and** `/root/.local/bin/claude` (Harbor runs `export PATH="$HOME/.local/bin:$PATH"; claude …`) |
| `claude` | the POSIX-sh shim: with `MACRAE_LIVE_URL` + `MACRAE_LIVE_TOKEN` set and python3 present it runs `claude_live.py`, else it `exec`s the real CLI |
| `claude_live.py` | the forwarder (stdlib, Python ≥ 3.8): runs the real CLI, passes stdout through byte for byte, posts each stream-json line to `POST {MACRAE_LIVE_URL}/api/live/{MACRAE_RUN_ID}/{MACRAE_STEP}` (header `X-Macrae-Live`), batched ≤ 1 s |
| `build.sh` | `tasks/common/build.sh ghcr.io/<you>/macrae-agent:latest --push` |

How the env gets there: the server writes `<run>/live/token` (and `url`) when it starts a task; agent_runner adds
`--ae MACRAE_LIVE_URL=… --ae MACRAE_LIVE_TOKEN=… --ae MACRAE_RUN_ID=… --ae MACRAE_STEP=…` to `harbor exec`/`run`
(`agent_runner/harbor.py: live_agent_env`), and Harbor sets them on the agent's `claude` command.

Rules the wrapper keeps:
- stdout is exactly the CLI's (Harbor still tees it to `agent/claude-code.txt`); nothing of ours on stderr;
- the exit code (and signals: TERM/INT/HUP/QUIT are relayed) are the CLI's;
- only `--output-format stream-json` invocations are forwarded; `claude --version` (Harbor's install check) goes
  straight to the real binary;
- forwarding can't break the run: network errors are retried with the next batch (the server de-duplicates by
  stream + offset), 400/401/403/404/410/413 turn forwarding off, the tail gets at most ~6 s after the CLI exits;
- lines > 1 MB are replaced by a small `{"type": "macrae_truncated", …}` stub; > 32 MB unsent → oldest dropped.
- `MACRAE_LIVE_DEBUG=/tmp/live.log` logs what it does (to a file, never to stdout/stderr).

**Use it**: build and push the image, then set `AGENT_RUNNER_MODAL_IMAGE=<image>` for the backend (on Cloudflare:
`cd cloudflare && npx wrangler secret put AGENT_RUNNER_MODAL_IMAGE`, then `cloudflare/deploy.sh restart`). Without
it, Harbor 0.24 runs agent steps on `ubuntu` and installs Claude Code at trial start; then there is no wrapper, the
run works as before, and the page only gets the agent's events when the trial ends.

Tests: `server/tests/test_claude_wrapper.py` (fake `claude` printing stream-json → wrapper → the real backend on
uvicorn), `server/tests/test_costs_evolution.py::test_engine_passes_live_env_to_harbor_and_masks_the_token`.
