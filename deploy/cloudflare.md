# macrae on Cloudflare

Everything runs on Cloudflare as **one Worker called `macrae`**:

```
browser ──► Worker "macrae" (cloudflare/index.js + worker.js)
              ├─ /, /app.js, …        web/ (static assets)
              ├─ /voice/signed-url    ElevenLabs signed URL (ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID)
              ├─ /api/*               ──► container "macrae-backend" (Durable Object MacraeBackend, deploy/Dockerfile)
              │                            FastAPI :8080 · rag · tasks · agent_runner · harbor --env modal ──► Modal
              └─ /admin/*             status / graceful restart of the container (needs X-Macrae-Secret)
ElevenLabs tools ──► Worker /api/tools/* (with X-Macrae-Secret) ──► container
container ──► http://r2.macrae/* ──► Worker outbound handler ──► R2 bucket "macrae-data" (index/, state/)
```

- **One container instance**, named `macrae-backend`, shared by everyone (`max_instances = 1`). It starts on the
  first request, and **sleeps after 2 hours without requests** (`sleepAfter = "2h"`). If a task run is still going
  at that point, it stays up and checks again 2 hours later. Sleeping never cuts a flow off.
- **Secrets** are Worker secrets. The Worker passes the backend's secrets to the container as environment variables
  each time the container starts.
- **State lives in R2.** The container's disk is wiped on every restart (sleep, deploy, platform maintenance). Run
  records, traces and the paper index are kept in the R2 bucket `macrae-data` (see [State](#state-r2)).
- `BACKEND_URL` is no longer used in production. On the Worker it is only an **override for local development**,
  sending `/api/*` to a backend you run yourself (`make serve`).

## 0. Once: account and tools

| what | why |
|---|---|
| Cloudflare account on the **Workers Paid** plan ($5/month) | Containers and Durable Objects need it |
| **R2** enabled (dash.cloudflare.com → R2 → enable; 10 GB free) | the `macrae-data` bucket |
| **Node 22+** (`node -v`) | wrangler 4 needs it; `nvm install 22` |
| **Docker running** (Docker Desktop, Colima or dockerd) | `wrangler deploy` builds `deploy/Dockerfile` locally for linux/amd64 and pushes it to Cloudflare's registry. On Apple Silicon this runs under emulation, so the first build is slow. |
| Python 3 | `deploy/envtool.py` and `deploy/r2sync.py` are stdlib-only |
| `cd cloudflare && npx wrangler login` | opens the browser once. In CI, set `CLOUDFLARE_API_TOKEN` instead. |

`cloudflare/deploy.sh check` (or `make cf-check`) checks all of this and changes nothing.

No local Docker? Connect the repo to **Workers Builds** instead (dashboard → Workers & Pages → macrae → Settings →
Builds; root directory `cloudflare`, deploy command `npx wrangler deploy`). It builds the Dockerfile in the cloud
on every push to the production branch. It skips `deploy.sh`'s checks, so first commit the root `.dockerignore`
(`cp deploy/Dockerfile.dockerignore .dockerignore`; `deploy.sh` creates it on its first run) and set the secrets
with `wrangler secret put`. Upload the index with `cloudflare/deploy.sh index`.

## 1. Secrets

Required: `MACRAE_TOOL_SECRET`, `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, and at least one Claude login
(`AGENT_RUNNER_TOKEN_<NAME>`, as many as you like, and/or `ANTHROPIC_API_KEY`). Voice needs
`ELEVENLABS_API_KEY` and `ELEVENLABS_AGENT_ID`; without them the page works, but the talk button says voice isn't
configured. The deploy stops before changing anything if a required secret is missing, and it prints the command
that fixes it.

**Way A: Worker secrets, typed in once.** Run in `cloudflare/`. Each command prompts for the value, which never
lands in a file or your shell history.
```bash
cd cloudflare
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # a value for MACRAE_TOOL_SECRET; keep it, ElevenLabs needs it too
npx wrangler secret put MACRAE_TOOL_SECRET
npx wrangler secret put MODAL_TOKEN_ID          # modal.com → Settings → API tokens (or ~/.modal.toml: token_id)
npx wrangler secret put MODAL_TOKEN_SECRET      #                                       (token_secret)
npx wrangler secret put AGENT_RUNNER_TOKEN_MAIN # `claude setup-token`; repeat with other names: AGENT_RUNNER_TOKEN_CAROL, …
npx wrangler secret put ANTHROPIC_API_KEY       # optional (API billing, account "api-key")
npx wrangler secret put ELEVENLABS_API_KEY      # optional until you set up voice
npx wrangler secret put ELEVENLABS_AGENT_ID     # after `make voice-setup` (step 4)
npx wrangler secret list                        # names only
```
Before the very first deploy the Worker doesn't exist yet. `wrangler secret put` then asks whether to create it;
answer yes.

**Way B: `deploy/.env`.** `cp deploy/env.example deploy/.env`, fill in the same names, and `cloudflare/deploy.sh`
uploads the values with the deploy (`wrangler deploy --secrets-file`). The file is git-ignored and Docker-ignored.
If `MODAL_TOKEN_ID/SECRET` are empty, the Modal login is taken from `~/.modal.toml`, profile `$MODAL_PROFILE`
(default `acalincarol`). `make web-secrets` uploads the file's secrets without deploying.

You can mix both ways: the deploy checks the union. Never set **`BACKEND_URL`** on the Worker. The deploy refuses
if it is there, because it would send `/api` past the container (`npx wrangler secret delete BACKEND_URL`).

**Changing a secret later:** `npx wrangler secret put NAME`, then `cloudflare/deploy.sh restart`. A running
container keeps the values it started with.

## 2. Paper index (optional before the first deploy)

```bash
cp -r /path/to/pdfs papers/          # or put the PDFs there any way you like
make index                           # papers/ → index/ (about 25–30 min the first time for ~480 papers; incremental after)
```
The deploy uploads `index/` to R2 when it is newer than R2's copy. If `index/` exists at build time, it is also
baked into the image as a fallback. Papers themselves never leave your machine. Without an index, search
answers "the papers here don't cover it" and tasks still run.

## 3. Deploy

```bash
cloudflare/deploy.sh                 # = make deploy (also make deploy-web / make deploy-backend)
```
What it does, in order (it stops at the first problem and changes nothing before step f):

| step | command it runs | fails with |
|---|---|---|
| a. tools | `node -v` ≥ 22, `docker info`, `wrangler whoami` | which tool is missing and how to get it |
| b. secrets | `wrangler secret list` + `deploy/envtool.py cf-secrets deploy/.env` | each missing secret, with its `wrangler secret put` command |
| c. dependencies | `npm ci` in cloudflare/ (`@cloudflare/containers` 0.3.7, pinned in package-lock.json) | |
| d. tests | `node --test cloudflare/test/*.test.js web/tests/*.test.js` | "tests failed, not deploying" |
| e. build hygiene | makes sure the repo-root `.dockerignore` equals `deploy/Dockerfile.dockerignore` (creates it if missing) | if a different `.dockerignore` is there |
| f. bucket | `wrangler r2 bucket info macrae-data` → `wrangler r2 bucket create macrae-data` | R2 not enabled |
| g. index | `wrangler r2 object put macrae-data/index/<file> --remote`: data files first, `manifest.json` last; old files deleted | |
| h. deploy | `wrangler deploy [--secrets-file …] [your flags]`: Worker + assets, then `docker build` of deploy/Dockerfile (context = repo root), push, container rollout | wrangler's error |
| i. wait | polls `https://macrae.<you>.workers.dev/api/health` until `"ok": true` (up to 15 min; the first deploy provisions for several minutes) | where to look |

The Worker URL is saved in `deploy/.state/cloudflare.env` (`WORKER_URL=…`), and `make voice-setup`,
`make smoke` and `deploy.sh restart|status` read it from there. With a custom domain, set
`MACRAE_PUBLIC_URL=https://…`. Extra arguments go to `wrangler deploy`, for example
`cloudflare/deploy.sh --containers-rollout=immediate`.

**Deploying new code** is the same command. The Worker goes live at once. If anything the image contains changed
(server, rag, tasks, agent_runner, deploy, data), Cloudflare rolls the container: it sends **SIGTERM**, the backend
refuses new task starts (503 "the backend is restarting"), lets running flows finish for **up to 14 minutes** while
still serving the page, pushes its state to R2 and exits. The new container restores everything from R2. Changes
to `web/` or `cloudflare/` alone don't touch the image, so they don't restart the container.

## 4. Voice (after the first deploy)

```bash
make voice-setup                                    # ElevenLabs agent with tools at https://macrae.<you>.workers.dev/api/tools/…
cd cloudflare && npx wrangler secret put ELEVENLABS_AGENT_ID   # the id it printed
```
`voice-setup` needs `ELEVENLABS_API_KEY` and `MACRAE_TOOL_SECRET` in your shell or `deploy/.env`; it stores the
secret in ElevenLabs. The tools call the Worker, which lets `/api/tools/*` through only with that secret. No
restart is needed: the Worker reads `ELEVENLABS_*` itself. Run `make voice-setup` again after editing
`tasks/tasks.json`.

## Day to day

| command | does |
|---|---|
| `cloudflare/deploy.sh` / `make deploy` | full deploy (above) |
| `cloudflare/deploy.sh check` / `make cf-check` | preflight + secret list, no changes |
| `cloudflare/deploy.sh secrets` / `make web-secrets` | upload `deploy/.env`'s secrets to the Worker (`wrangler secret bulk`), no deploy |
| `cloudflare/deploy.sh index` / `make cf-index` | upload a new `index/` to R2 (then `restart`, or wait for the next start) |
| `cloudflare/deploy.sh restart` / `make cf-restart` | `POST /admin/restart`: graceful stop; the next request starts it with current secrets and index |
| `cloudflare/deploy.sh status` / `make cf-status` | `/api/health` + `/admin/status` (container state: running, healthy, stopped…) |
| `make cf-logs` | `wrangler tail`: Worker logs, including "keeping the backend up" / "stopping" decisions |
| `make smoke` | curl `/api/health` and `/api/tasks` through the Worker |
| `npx wrangler containers list` | the container application, its id and instance count |
| `npx wrangler containers instances <APP_ID>` | the instance: state, location |
| dashboard → Workers & Pages → macrae → Containers | the same, plus the container's logs (stdout/stderr of start.py, uvicorn, r2sync) |
| `npx wrangler r2 object get macrae-data/index/manifest.json --remote --pipe` | which index R2 has |

`restart` and `status` need `MACRAE_TOOL_SECRET` in your shell or in `deploy/.env`.

For a shell in the container, add your ed25519 key to `[[containers.authorized_keys]]` in
`cloudflare/wrangler.toml`, deploy, then run `npx wrangler containers ssh <INSTANCE_ID>`. Inside,
`python deploy/r2sync.py status|push|restore|pull-index` runs the sync by hand.

## State (R2)

Bucket `macrae-data`, bound to the Worker as `DATA`:

| key | local path in the container | direction |
|---|---|---|
| `index/manifest.json`, `index/{papers,chunks,emb}-<stamp>.*` | `/data/index` | down when the container starts (only if R2's `created` is newer). The data files arrive before the manifest, so the running server switches over atomically. |
| `state/runs/<run>/…` | `/data/agent-runner/runs` | up and down (agent_runner records: state.json, logs, outputs, results) |
| `state/jobs/<step>-a<n>/<trial>/…` | `/data/agent-runner/jobs` | up and down (Harbor trials: the traces, trajectory.json) |
| `state/macrae/…` | `/data/agent-runner/macrae` | up and down (the server's run→task sidecars and event logs, so `seq` stays stable across restarts) |

**How the container reaches R2.** This is the simplest route that works from inside the container: no S3 keys and
no extra secret. The container makes plain HTTP requests to `http://r2.macrae/…`. `MacraeBackend.outboundByHost`
maps that host to `dataHandler` in worker.js, which runs in the Worker next to the R2 binding (Cloudflare's
"outbound handlers"). Only the container can reach it. The protocol is three calls:
`GET /?prefix=` (list), `GET /<key>`, `PUT /<key>`. Writes are allowed under `state/` only; objects can be up to 100 MB.
The container gets `MACRAE_SYNC_URL=http://r2.macrae` from the Worker. R2's S3 API would also work, but it would
need an R2 API token as two more secrets plus an S3 client in the image.

**When it syncs** (`deploy/r2sync.py`, started by `deploy/start.py` when `MACRAE_SYNC_URL` is set):
- **on start**, in the background: the index comes down, then `state/` files missing locally are restored.
  uvicorn starts at once, so the Worker's readiness check on port 8080 isn't held up. Runs that were still
  `running` when the old container died are marked `crashed`; the page shows them as failed with the reason.
- **after each run step**: every 5 s it compares each run's `state.json` mtime, and any change (step started,
  finished, retried, run ended) uploads the changed files.
- **on a timer**: every 60 s (`MACRAE_SYNC_INTERVAL`) it uploads whatever changed, for example a trajectory
  written mid-step.
- **on stop**: after the drain, once more.
Uploads are incremental (size + mtime). Files over 50 MB (`MACRAE_SYNC_MAX_MB`) and temp files are skipped, and
leases and cooldowns are not synced. Deletions are not mirrored; runs are append-only records. At most about a
minute of trace detail can be lost if the host dies without a SIGTERM.

The image also bakes in the repo's `index/` when present (`deploy/Dockerfile.dockerignore` no longer excludes
it). `start.py` copies it into an empty `/data/index` before the server starts, and R2's copy replaces it when
R2's is newer.

## Sleep, restarts and cost

- Instance type **standard-1** (1/2 vCPU, 4 GiB, 8 GB disk) in `cloudflare/wrangler.toml`. Use `standard-2` if
  several runs at once feel slow. Changing it rolls the container.
- Billing is per 10 ms while the container runs: memory and disk as provisioned, CPU as used, plus $5/month
  Workers Paid. Rough numbers for standard-1 at October 2026 prices:
  - about **$29/month** if it never sleeps;
  - about **$4–5/month** if it runs about 4 h a day.
  The page polls `/api/health` every 20 s while its tab is visible, so a visible tab left open keeps the
  container awake; hidden tabs stop polling. R2 use stays in the free tier.
- Stops come from: 2 h without requests (and no running task), a deploy that changes the image, `deploy.sh
  restart`, or Cloudflare host maintenance. Every stop is SIGTERM with up to 15 min before SIGKILL; start.py uses
  14 of them (`MACRAE_DRAIN_SECONDS=840`).
- A cold start (after sleep) takes the image start plus about 5 s. The first request after a long sleep may show
  "agent offline" for a moment, and the page retries by itself.

## Local development (unchanged)

```bash
make serve          # backend on :8080 with reload. No supervisor, no R2: MACRAE_SYNC_URL is unset locally.
make web-dev        # wrangler dev; BACKEND_URL=http://127.0.0.1:8080 overrides the container ([dev] enable_containers = false)
node cloudflare/dev/serve.mjs              # same on plain Node (reads cloudflare/.dev.vars)
```
To try the container's sync locally: `node cloudflare/dev/data-server.mjs --port 8789 --dir /tmp/r2` (the real
`dataHandler` over a folder), then `MACRAE_SYNC_URL=http://127.0.0.1:8789 python deploy/start.py --port 8080`.
To run the real container under wrangler: `cd cloudflare && npx wrangler dev --enable-containers` (needs Docker).

## Troubleshooting

| symptom | look at |
|---|---|
| deploy: "Docker is installed but not running" | start Docker; wrangler builds the image locally |
| deploy waits, then "backend didn't answer" | first deploy: provisioning can take minutes, so run `deploy.sh status` again. Otherwise check the dashboard → Containers logs; a crash in `start.py` shows there |
| page: "Agent offline", `/api/health` → 503 `"the backend container is not ready (…)"` | the container is starting or failed to start; the text after "not ready" is the Container class's reason |
| tasks fail with `AuthError: Modal …` | `MODAL_TOKEN_ID/SECRET` wrong or missing → `wrangler secret put …` then `deploy.sh restart` |
| tasks fail with "no Claude login" | no `AGENT_RUNNER_TOKEN_*` / `ANTHROPIC_API_KEY` reached the container → set one, restart |
| `/api/health` shows `papers: 0` after an index upload | the container pulls the index on start: `deploy.sh restart` |
| 503 "the backend is restarting" on task start | a deploy or restart is draining running flows; try again in a few minutes |
| voice tools get 403 from the Worker | ElevenLabs' stored secret differs from the Worker's `MACRAE_TOOL_SECRET` → `make voice-setup` again |

The AWS route (`deploy/aws.md`, `make aws-deploy`, `make backend-*`) still works as an alternative.
