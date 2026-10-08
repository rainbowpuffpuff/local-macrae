# macrae on Cloudflare: what changed, the secrets, what was verified

The backend no longer needs AWS. `cloudflare/deploy.sh` (or `make deploy`) deploys **one Worker, `macrae`**:
- it serves `web/`;
- it answers `/voice/signed-url` as before;
- it sends `/api/*` to a **backend container** built from `deploy/Dockerfile`.

The container's runs, traces and paper index are kept in the R2 bucket **`macrae-data`**. Every command is
explained in **deploy/cloudflare.md**. `make serve` and the rest of local development work as before.

## What changed

**Worker (`cloudflare/`)**
- `index.js` (new; the Worker's `main`) defines `MacraeBackend extends Container` from `@cloudflare/containers`
  0.3.7 (pinned in `package.json` and `package-lock.json`):
  - `defaultPort = 8080`, the port `deploy/start.py` already used.
  - `sleepAfter = "2h"`.
  - `envVars` is built from the Worker's secrets.
  - `onActivityExpired`: when the 2 h run out, it asks the backend for `/api/runs`. While any run is `running` the
    container stays up and is checked again 2 h later, so a flow is never cut off by sleep.
  - `fetch` gives a cold start 30 s + 60 s instead of the library's 8 s + 20 s.
  - `restart()` is used by `/admin/restart`.
  - It also exports `ContainerProxy` and maps the made-up host `r2.macrae` to the R2 handler.
- `worker.js`:
  - `/api/*` goes to the single instance `macrae-backend` through the Durable Object binding `BACKEND`. If
    `BACKEND_URL` is set (now only a local-dev override), it goes there instead.
  - The Container class's plain-text "not ready" answers become the page's usual
    `{"error":"agent offline","offline":true}` (503).
  - New `/admin/status` and `/admin/restart` (both need `X-Macrae-Secret`).
  - `containerEnv()` forwards `MACRAE_TOOL_SECRET`, `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `ANTHROPIC_API_KEY`,
    **every `AGENT_RUNNER_TOKEN_<NAME>`**, and a few optional knobs. It also sets `MACRAE_SYNC_URL=http://r2.macrae`.
    The ElevenLabs keys stay in the Worker.
  - `dataHandler()` is the container's R2 endpoint: list, get, and put under `state/` only, at most 100 MB per
    object, with path checks.
  - `/voice/signed-url`, the security headers, rate limits, the `/api/tools` secret rule and the body limits are
    unchanged.
- `wrangler.toml`:
  - `main = "index.js"`;
  - `[[containers]]` with `image = "../deploy/Dockerfile"`, `image_build_context = ".."`,
    `instance_type = "standard-1"` (1/2 vCPU, 4 GiB, 8 GB disk), `max_instances = 1`;
  - Durable Object binding `BACKEND` and `[exports.MacraeBackend]` (SQLite);
  - `[[r2_buckets]]` binding `DATA` → `macrae-data`;
  - `[dev] enable_containers = false`, so `make web-dev` needs no Docker.
- `deploy.sh` does the whole deploy, and also has `check`, `secrets`, `index`, `restart` and `status`. See
  [Deploy](#deploy-and-voice).
- `dev/data-server.mjs` (new) runs the real `dataHandler` on Node over a folder, for local and cross-language
  tests.

**Backend (`deploy/`, `server/`)**
- `deploy/r2sync.py` (new, stdlib only) pulls the index from R2 when R2's is newer. Data files come first and
  `manifest.json` last, so the running server switches over atomically. It also:
  - restores `runs/`, `jobs/` (traces) and `macrae/` (event logs, run→task sidecars) when the container starts;
  - marks runs that were cut off by a dead container as `crashed`;
  - pushes changed files up when any run's `state.json` changes (each step), every 60 s, and once more on stop.

  **Why through the Worker and not R2's S3 API:** the container makes plain HTTP requests to `http://r2.macrae`,
  and a Cloudflare outbound handler answers them next to the R2 binding. That needs no S3 keys, no extra secrets
  and no S3 client in the image.
- `deploy/start.py`: when `MACRAE_SYNC_URL` is set (only the Worker sets it), it supervises uvicorn instead of
  exec'ing it:
  - it starts the sync thread;
  - on **SIGTERM** (sleep, a rollout from a deploy, a restart, host maintenance) it creates `MACRAE_DRAIN_FILE`,
    so task starts get 503 "the backend is restarting" while the page keeps working;
  - it waits up to 14 min for running flows (Cloudflare kills at 15), pushes state, and exits.

  In any container it also copies an index **baked into the image** (`/app/index`) into an empty `/data/index`.
  Without `MACRAE_SYNC_URL` (`make serve`, AWS/compose) it execs uvicorn exactly as before.
- `server/app.py` and `server/config.py`: the drain check (`config.draining()`) on task starts. That is the only
  server change.
- `deploy/Dockerfile`: comments only. It already listened on 8080, had no Docker inside (a stub `docker`) and ran
  Harbor with `--env modal`.
- `deploy/Dockerfile.dockerignore`:
  - `index/` is no longer excluded, so it gets baked in when present;
  - `web/`, `cloudflare/`, `docs/` and `.claude/` are now excluded, so a page-only deploy doesn't change the image
    and doesn't restart the container.
- **`.dockerignore` at the repo root (new) is a security fix.** The wrangler dry run showed that wrangler builds
  with `docker build -f - <repo>`, the Dockerfile on stdin. Then BuildKit **ignores `deploy/Dockerfile.dockerignore`**,
  and without a root `.dockerignore` the image would have contained `deploy/.env`, `papers/` and `.git`. The root
  copy is identical to the deploy one (a test checks this). `deploy.sh` creates it if it's missing (your workflow's
  `apply` step doesn't copy root files) and refuses to build if a different one exists.
- `deploy/envtool.py cf-secrets` holds the secret check: Worker secrets plus `deploy/.env`. It writes the
  `--secrets-file` (mode 600) and never prints values.
- `deploy/env.example` and `deploy/cloudflare.md` (new) are updated. `deploy/aws.md` and `aws_deploy.sh` stay as an
  alternative.

**Makefile**
- `make deploy`, `deploy-web` and `deploy-backend` all run `cloudflare/deploy.sh`.
- `web-secrets` uploads `deploy/.env`'s secrets.
- New `cf-check`, `cf-index`, `cf-restart`, `cf-status`, `cf-logs`.
- AWS: `aws-deploy` and `aws-web-secrets`, plus the `backend-*` targets, now labelled (AWS).
- `voice-setup` and `smoke` default to the Worker URL saved in `deploy/.state/cloudflare.env`.

**Test commands:** `node --test <dir>` fails on Node 22 (wrangler's minimum), because arguments are now globs. The
npm scripts and `deploy.sh` use `test/*.test.js` and `tests/*.test.js`, which work on Node 18 and 22.

**Docs:** `deploy/cloudflare.md` (new), plus `deploy/NOTES.md`, `web/NOTES.md`, `web/smoke.md`, `server/NOTES.md`
and a pointer at the top of INTEGRATION.md. CONTRACT.md was not edited: its diagram and "Deploy" section still say
AWS.

**API choice:** the docs now recommend the newer "Durable Object Container API" (`ctx.container`) for *new*
applications. You asked for the `Container` class, which is still documented and supported with the default
scheduling policy. If you migrate later, Cloudflare has a guide ("Migrate from the Container class to the Durable
Object Container API").

## Secrets: the exact commands

Once:
```bash
cd cloudflare
npx wrangler login
python3 -c "import secrets; print(secrets.token_urlsafe(32))"    # value for MACRAE_TOOL_SECRET (keep it)
npx wrangler secret put MACRAE_TOOL_SECRET
npx wrangler secret put MODAL_TOKEN_ID            # Modal → Settings → API tokens, or token_id in ~/.modal.toml
npx wrangler secret put MODAL_TOKEN_SECRET
npx wrangler secret put AGENT_RUNNER_TOKEN_MAIN   # from `claude setup-token`; add more: AGENT_RUNNER_TOKEN_<NAME>
npx wrangler secret put ANTHROPIC_API_KEY         # optional
npx wrangler secret put ELEVENLABS_API_KEY        # for voice
```
Each command prompts for the value. Before the first deploy, wrangler asks whether to create the Worker `macrae`:
answer yes. Instead of these commands you can fill the same names into `deploy/.env`. `deploy.sh` uploads them with
the deploy, and takes the Modal pair from `~/.modal.toml` if they are empty. The deploy needs:
- `MACRAE_TOOL_SECRET`, `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`;
- at least one Claude login (`AGENT_RUNNER_TOKEN_*` or `ANTHROPIC_API_KEY`).

Otherwise it stops before changing anything and prints the `wrangler secret put` line for each missing name. It
also refuses if `BACKEND_URL` is a Worker secret (left over from the AWS setup):
`npx wrangler secret delete BACKEND_URL`.

After changing any secret later: `cloudflare/deploy.sh restart`. The container reads its env when it starts.

## Deploy and voice

```bash
make index                         # optional: papers/ → index/ (uploaded to R2 by the deploy)
cloudflare/deploy.sh               # Node 22+, Docker running; prints https://macrae.<you>.workers.dev
make voice-setup                   # ElevenLabs tools now point at the Worker URL
cd cloudflare && npx wrangler secret put ELEVENLABS_AGENT_ID     # the id voice-setup printed
```
If an ElevenLabs agent was already set up against an AWS URL, run `make voice-setup` again so its tools point at
the Worker.

## What I verified

Here: Python 3.12.3, Node 18.19 (repo tests), Node 22.23 + wrangler 4.148.0 (Cloudflare checks),
@cloudflare/containers 0.3.7. I read the Containers docs as of 2026-10-08 and the package's source.

- **All tests:** `make test` exits 0.
  - pytest: **277 passed** (it was 248). By folder: tests 7, server 52, rag 50, tasks 65, voice 21, deploy 82.
  - node: **39** Worker/container tests (it was 25) + **18** page tests. The same 57 pass on Node 22.
- **New tests:**
  - `cloudflare/test/container.test.js` (14). It covers `containerEnv` (token prefix, nothing extra, no ElevenLabs
    keys) and `/api` → Durable Object (path, query, secret overwrite, body). It also covers:
    - the `BACKEND_URL` override;
    - "not ready" mapped to offline;
    - the admin routes;
    - the R2 endpoint (pagination, mtime, refusing `..` hidden as `%2F..%2F`, index writes, a missing length);
    - `MacraeBackend` itself, through a fake `@cloudflare/containers` loaded with `module.register`: port, 2 h,
      env, sleeping when idle, staying up while a run is `running`, restart, cold-start budget.
  - `deploy/tests/test_r2sync.py` (9), including a round trip from Python **through the real JS `dataHandler`** on
    Node: names with spaces/é/&/%/#, mtimes kept, the index pull, crashed-marking.
  - `deploy/tests/test_start.py` (+3), including an **end-to-end supervisor run**: real `start.py`, the real
    FastAPI app, and sync to the Node data server. It checks:
    1. a run restored from R2 shows as failed;
    2. a live run's `state.json` is pushed;
    3. after SIGTERM, task starts get 503 while `/api/runs` still answers;
    4. it waits for the running flow, then exits 0 with a final push.
  - `deploy/tests/test_cloudflare_deploy.py` (12): `deploy.sh` against fake wrangler/docker/node/npm/curl. It
    covers:
    - each missing secret and the Claude-login rule, with nothing deployed;
    - Node < 22, Docker down, logged out;
    - `BACKEND_URL` on the Worker;
    - the full deploy from `deploy/.env` (index files before the manifest, `--secrets-file` contents, no secret
      values printed, URL saved, health wait);
    - re-deploy skipping an unchanged index, and a newer index replacing the old files;
    - `secrets`, `restart`, `status`;
    - a different root `.dockerignore` refused.
  - Also new: `test_envtool.py` (+2), `test_artifacts.py` (+2: wrangler config, root `.dockerignore`),
    `server/tests/test_api.py` (+1: drain).
- **wrangler 4.148 `deploy --dry-run`** (with a recording `docker` stub) bundles `index.js`, including
  `@cloudflare/containers`, and accepts the config. It lists the bindings `BACKEND (MacraeBackend)`,
  `DATA (macrae-data)`, the two rate limits and `ASSETS`, and the container `macrae-macraebackend` from
  `deploy/Dockerfile`. The build command it runs is
  `docker build --load --platform linux/amd64 --provenance=false -f - <repo>`, which is how the `.dockerignore`
  problem above turned up.
- **`wrangler dev` (real workerd), BACKEND_URL override → a real backend from `deploy/start.py`:**
  - `/api/health` and `/api/tasks` returned 200;
  - `/api/tools/*` without the secret returned 403;
  - with the secret, it reached the backend;
  - the page returned 200.
- **`wrangler dev` without BACKEND_URL** (containers disabled):
  - `/api/health` → 502 `{"error":"agent offline","offline":true}`;
  - the page → 200;
  - `/admin/*` → 403 without the secret.
- **`make serve`:** still exec's uvicorn with `--reload` (no supervisor, no R2), and health and tasks answer.
- **`deploy.sh` with the real wrangler, not logged in:** it stops with "not logged in to Cloudflare. Run: cd
  cloudflare && npx wrangler login". With Docker stopped it stops with "Docker is installed but not running".

**Not verified (no Cloudflare account, and this sandbox can't run containers: `unshare: operation not permitted`):**
- a real `wrangler deploy`;
- the image build itself (the Dockerfile is unchanged apart from comments, but it has never been built);
- the outbound-handler route to R2 on Cloudflare's network (that it resolves `r2.macrae`, and that `put` streams);
- cold-start time;
- `onActivityExpired` on the real alarm schedule;
- a real rollout's SIGTERM drain;
- any Modal, Claude or ElevenLabs call.

The first real `cloudflare/deploy.sh` is the test of those. If `/api/health` stays offline, the container logs in
the dashboard (Workers & Pages → macrae → Containers) show start.py and r2sync output. `deploy/cloudflare.md` has a
troubleshooting table.

## Worth knowing

- **Cost (standard-1, rough):**
  - about $29/month if the container never sleeps;
  - about $4–5/month at about 4 h/day.

  Both are on top of $5 Workers Paid; R2 stays in the free tier. A *visible* browser tab polls `/api/health` every
  20 s and so keeps the container awake.
- **Restarts:** any deploy that changes the backend's code rolls the container, but running flows get up to 14 min
  to finish first. Edits to `web/` or `cloudflare/` alone don't restart it.
- **What survives a crash:** at most about 60 s of trace detail can be lost if Cloudflare's host dies without
  SIGTERM. Runs that were going when a container died show as failed, with the reason.
- **Docker for deploys:** the deploy needs a local Docker, because wrangler builds the image. On Apple Silicon the
  amd64 build is emulated and slow the first time. Workers Builds can build it in the cloud instead (see
  deploy/cloudflare.md).
