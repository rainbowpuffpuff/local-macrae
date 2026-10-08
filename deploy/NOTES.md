# deploy: notes

> **Cloudflare is now the deployment** (CLOUDFLARE.md, deploy/cloudflare.md): `make deploy` runs
> `cloudflare/deploy.sh`, and the backend is the container built from `deploy/Dockerfile` inside the `macrae` Worker.
> New here for that: `r2sync.py` (state ↔ R2 through the Worker), the supervisor and drain mode in `start.py`
> (when `MACRAE_SYNC_URL` is set), `envtool.py cf-secrets`, the root `.dockerignore` (wrangler builds with
> `-f -`, so only the context's ignore file applies), and `index/` is baked into the image when present.
> Tests: `test_r2sync.py`, `test_cloudflare_deploy.py` (fakes in `tests/fakes_cf/`), plus new cases in
> `test_start.py` / `test_envtool.py` / `test_artifacts.py`. The AWS route below still works (`make aws-deploy`,
> `make backend-*`).

## What I built
| file | what |
|---|---|
| `deploy/Dockerfile` | Backend image: python:3.11-slim, uv, Harbor CLI (`harbor[modal]==0.24.0` as a uv tool on Python 3.12, which Harbor needs), every `*/requirements.txt` that exists (server, rag, tasks, deploy), `pip install -e .` (agent_runner), fastembed model prefetched, non-root user `macrae` (uid 1000), tini, healthcheck on `/api/health`, uvicorn on 8080 via `deploy/start.py`. Data on the `/data` volume. |
| `deploy/Dockerfile.dockerignore` | BuildKit's per-Dockerfile ignore list (context is the repo root, whose `.dockerignore` I don't own): no `.git`, papers, index, `.env`, `.dev.vars`, `.reference`. |
| `deploy/start.py` | Entrypoint for both the container and `make serve`. It drops blank contract env vars so defaults apply, creates the data dirs, writes `~/.modal.toml` for `$MODAL_PROFILE` from `MODAL_TOKEN_ID/SECRET` (container only), finds the FastAPI app, and execs uvicorn (proxy headers, 1 worker; `--reload` for dev). |
| `deploy/compose.yaml`, `deploy/Caddyfile` | Backend (port bound to 127.0.0.1) plus Caddy under profile `https`, with automatic Let's Encrypt for `$MACRAE_DOMAIN`. Same file locally (`make docker-run`) and on EC2. |
| `deploy/aws_deploy.sh` | One EC2 instance with HTTPS. Commands: `up`, `code`, `data`, `ingest`, `status`, `url`, `logs`, `ssh`, `stop`, `start`, `down`, `cost`. Idempotent (finds resources by tag). No local Docker: the image is built on the instance. |
| `deploy/cloud-init.sh` | First boot: Docker, compose, buildx, 2 GB swap, `/srv/macrae/{app,data}` owned by uid 1000. |
| `deploy/envtool.py` | stdlib helper for `deploy/.env`: `get`, `export` (safe `eval` for Makefile recipes), `check` (deploy preflight), `render` (the server's copy of the env file), `worker-secrets` (`wrangler secret put` for the Worker, URL taken from `deploy/.state`). |
| `deploy/docker-shim.sh` | Installed as `/usr/local/bin/docker` in the image. It exits 1 with a message, so any leftover `docker image inspect` in agent_runner fails cleanly instead of raising `FileNotFoundError`. |
| `deploy/env.example` | Every contract variable (backend + Worker) without values, plus the optional deploy settings. |
| `deploy/aws.md` | The AWS choice and trade-offs (EC2 + Caddy vs ECS Express vs App Runner), **cost** (≈ $20.43/month us-east-1, ≈ $23.07 eu-central-1; ≈ $5.50 stopped), deploy/operate/troubleshoot. |
| `Makefile` | `index`, `serve`, `test`, `web-dev`, `deploy-web`, `deploy-backend` (contract), plus `install`, `search`, `web-secrets`, `deploy` (all three), `backend-*` (code/data/ingest/status/url/logs/ssh/stop/start/down), `cost`, `voice-setup`, `docker-build`, `docker-run`, `smoke`. `make help` lists them. |
| `deploy/tests/` | 54 pytest tests (see below). |

## Run it
```bash
cp deploy/env.example deploy/.env       # fill in; blank = unset
make install                            # pip install -e ".[test]" + module requirements
make index                              # papers/ → index/
make serve                              # backend on http://127.0.0.1:8080 (reload)
make web-dev                            # Worker + web/ via wrangler dev, /api → the local backend
make deploy-backend                     # AWS: prints https://<ip>.sslip.io (see deploy/aws.md)
make deploy-web web-secrets voice-setup # Worker, its secrets (BACKEND_URL from deploy/.state), ElevenLabs agent
make smoke                              # health + tasks on the deployed URL
```
Local container (needs Docker): `make docker-run` → http://127.0.0.1:8080, with data in `deploy/data/`.

## Test
`make test` runs pytest (default import mode, like a bare `pytest` at the root; see INTEGRATION.md) over every
module's `tests/` folder that exists, plus `npm test` in `web/` and `cloudflare/` when their package.json has a test script.
Only the deploy tests: `python3 -m pytest -q deploy/tests`. Current result: 54 passed; `make test` with the
repo's existing `tests/` gives 59 passed.

- `test_aws_deploy.py`: the bash helpers (sslip host, AMI architecture per instance type, key-pair name), and the
  full `up → up → stop → up → status → down` cycle against fake `aws`/`ssh`/`curl`/`ssh-keygen`/`rsync`
  (`deploy/tests/fakes/`, state in JSON). It checks:
  - nothing is created twice, and duplicate SG rules are tolerated;
  - the run-instances flags (IMDSv2, gp3 size, user data, AMI arch);
  - the SG opens 80/443 to the world, 22 only to your IP, and never 8080;
  - the uploaded server env has the secrets and the domain but not the local paths;
  - the remote commands run in order;
  - `down` asks for confirmation and removes everything;
  - `up` refuses to touch AWS without `MACRAE_TOOL_SECRET`.
- `test_start.py`: env cleanup, data dirs, `~/.modal.toml` writing (valid TOML, 0600, other profiles kept), app
  discovery (app / factory / missing / broken dependency surfaces), uvicorn args, `--check` in a fake repo.
- `test_envtool.py`: dotenv parsing, precedence, shell quoting, deploy checks, server env rendering (Modal pair
  from `~/.modal.toml`), wrangler `[vars]` detection, Worker secrets.
- `test_artifacts.py`: env.example vs the CONTRACT.md variable list, plus the Dockerfile, dockerignore, compose,
  Caddyfile and Makefile contract targets (`make -n`).

Verified outside the tests:
- `uv tool install --python 3.12 "harbor[modal]==0.24.0"` installs, and `harbor exec --help` has `-e/--env` and
  `--task-template`. Modal 1.6.1 is importable in Harbor's environment.
- `caddy validate` accepts the Caddyfile.

**Not verified here (no Docker or AWS in this sandbox):** an actual `docker build` and a real AWS deploy. The
AWS calls are standard CLI v2 EC2/SSM calls. The first real `make deploy-backend` takes about 8 minutes:
boot, then Docker install, then the image build on the instance.

## Assumptions about other modules
- **server**: the FastAPI app is `server.app:app`; `server.main:app`, `server:app` and a `create_app` factory are
  also found, or set `MACRAE_APP=module:attr`. It reads the contract env vars and serves `GET /api/health` without
  auth (used by the Docker healthcheck, Caddy and `aws_deploy.sh`). It runs as one uvicorn worker, so in-process
  state is fine. Its deps include fastapi/uvicorn; `deploy/requirements.txt` adds them anyway.
- **rag**: `python -m rag ingest` with no arguments uses `MACRAE_PAPERS_DIR`/`MACRAE_INDEX_DIR` (the Makefile
  and `aws_deploy.sh ingest` call it like that). fastembed's cache honours `FASTEMBED_CACHE_PATH`
  (`/opt/fastembed` in the image, prefetched). If rag passes its own `cache_dir`, the model downloads on first
  use instead. An index built on a laptop works on the server: `deploy/aws_deploy.sh data` copies `index/`
  verbatim, so it must not store absolute paths to PDFs. If it does, run `deploy/aws_deploy.sh ingest` on the
  server instead.
- **tasks / agent_runner**: in the container, flows use `environment: modal`, since there is no Docker daemon
  (`docker` is the failing shim). Script steps run inside the container with `PYTHONPATH=/app` and cwd `/app`, so
  `python -m rag search` works. `harbor` is on PATH (`/usr/local/bin/harbor`). Modal login comes from
  `MODAL_TOKEN_ID/SECRET`; `start.py` also writes them as profile `$MODAL_PROFILE` (default `acalincarol`) in
  `~/.modal.toml`. `AGENT_RUNNER_HOME=/data/agent-runner` (persistent).
  - Possible issue to check: `agent_runner/harbor.py` sets `IMAGE_DIR = Path(__file__).resolve().parent.parent / "image"`.
    That resolves to `<repo>/image`, not `agent_runner/image`, so the Modal task template should copy from the
    right path.
- **voice**: `voice/setup_agent.py` takes `BACKEND_URL` and `ELEVENLABS_API_KEY` from the environment.
  `make voice-setup` exports `deploy/.env` and, if `BACKEND_URL` is empty, the deployed URL.
- **web / cloudflare**:
  - `make deploy-web` runs `cloudflare/deploy.sh` (fallback: `npx wrangler@4 deploy`).
  - `make web-secrets` puts `BACKEND_URL`, `MACRAE_TOOL_SECRET`, `ELEVENLABS_API_KEY` and `ELEVENLABS_AGENT_ID` as
    Worker secrets. It skips any name defined under `[vars]` in `cloudflare/wrangler.toml`, because a secret can't
    share a name with a var.
  - `make web-dev` passes the same four with `wrangler dev --var`, with `BACKEND_URL=http://127.0.0.1:8080`.
- Secrets live only in `deploy/.env` (git-ignored by the root `.gitignore` and `deploy/.gitignore`) and in
  `/srv/macrae/app/deploy/.env` (mode 600) on the server. Local AWS state is cached in `deploy/.state/` (ignored).
