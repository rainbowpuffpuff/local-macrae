#!/usr/bin/env bash
# Deploy macrae to Cloudflare as ONE Worker "macrae": the page (../web), the /api proxy, /voice/signed-url and the
# backend container (built from ../deploy/Dockerfile), with runs/traces/index kept in the R2 bucket macrae-data.
# Every step is explained in deploy/cloudflare.md.
#
#   cloudflare/deploy.sh [wrangler deploy flags]   the whole deploy (make deploy)
#   cloudflare/deploy.sh check                     only the checks and the secret list; changes nothing
#   cloudflare/deploy.sh secrets                   upload deploy/.env's secrets to the deployed Worker (no deploy)
#   cloudflare/deploy.sh index                     upload ../index to R2 (the container pulls it when it starts)
#   cloudflare/deploy.sh restart                   restart the backend container gracefully (after a secret change)
#   cloudflare/deploy.sh status                    /api/health and the container's state
#
# Secrets come from the Worker (`npx wrangler secret put NAME`) and/or deploy/.env (uploaded with the deploy).
# A missing one stops the deploy before anything changes, with the command that fixes it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$HERE"

ENV_FILE="${MACRAE_ENV_FILE:-$REPO/deploy/.env}"
STATE_DIR="$REPO/deploy/.state"
STATE="$STATE_DIR/cloudflare.env"
BUCKET="macrae-data"
PY="${PY:-python3}"
WRANGLER="${WRANGLER:-npx --yes wrangler@4}"
HEALTH_WAIT="${MACRAE_HEALTH_WAIT:-900}"   # first deploy: Cloudflare provisions the image for several minutes
HEALTH_POLL="${MACRAE_HEALTH_POLL:-15}"
SECRETS_FILE=""
trap 'rm -f "${SECRETS_FILE:-}"' EXIT

die() { printf '\ndeploy: %s\n' "$*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*" >&2; }
wr() { $WRANGLER "$@"; }

need_node() {
  command -v node >/dev/null || die "node is not installed. Wrangler 4 needs Node 22 or newer (https://nodejs.org, or: nvm install 22)."
  local major; major="$(node -p 'process.versions.node.split(".")[0]')"
  [ "$major" -ge 22 ] || die "wrangler needs Node 22 or newer; this is Node $(node -v). Install it (e.g. nvm install 22 && nvm use 22) and run again."
}
need_python() { command -v "$PY" >/dev/null || die "$PY not found (needed for deploy/envtool.py)."; }
need_docker() {
  command -v docker >/dev/null || die "docker not found. wrangler deploy builds the backend image from deploy/Dockerfile with your local Docker (Docker Desktop, Colima, or the docker engine). Install it and run again."
  docker info >/dev/null 2>&1 || die "Docker is installed but not running (docker info failed). Start Docker Desktop / Colima / dockerd and run again."
}
need_login() {
  [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && return 0
  local who; who="$(wr whoami 2>&1 || true)"
  if grep -qi "not authenticated" <<<"$who"; then
    die "not logged in to Cloudflare. Run: cd cloudflare && npx wrangler login   (or set CLOUDFLARE_API_TOKEN)"
  fi
}
worker_url() {
  local u="${MACRAE_PUBLIC_URL:-}"
  [ -n "$u" ] || u="$(sed -n 's/^WORKER_URL=//p' "$STATE" 2>/dev/null | tail -1)"
  [ -n "$u" ] || die "don't know the Worker's URL yet: deploy first (cloudflare/deploy.sh), or set MACRAE_PUBLIC_URL=https://…"
  printf '%s' "${u%/}"
}
# The operator's header for /admin/*: X-Macrae-Admin when MACRAE_ADMIN_SECRET is known (env or deploy/.env), else the
# shared tool secret.
op_header() {
  local a="${MACRAE_ADMIN_SECRET:-}"
  [ -n "$a" ] || a="$("$PY" "$REPO/deploy/envtool.py" get "$ENV_FILE" MACRAE_ADMIN_SECRET 2>/dev/null || true)"
  if [ -n "$a" ]; then printf 'X-Macrae-Admin: %s' "$a"; return; fi
  local s; s="$(tool_secret)" || return 1  # (errexit is off inside $(…))
  printf 'X-Macrae-Secret: %s' "$s"
}
tool_secret() {
  local s="${MACRAE_TOOL_SECRET:-}"
  [ -n "$s" ] || s="$("$PY" "$REPO/deploy/envtool.py" get "$ENV_FILE" MACRAE_TOOL_SECRET)"
  [ -n "$s" ] || die "MACRAE_TOOL_SECRET is needed for this (the same value as the Worker secret): export it or put it in deploy/.env"
  printf '%s' "$s"
}

# Secret names on the Worker, as JSON. A Worker that doesn't exist yet (first deploy) has none.
existing_secrets() {
  local out err; err="$(mktemp)"
  if out="$(wr secret list --format json 2>"$err")"; then
    sed -n '/^\[/,$p' <<<"$out"
  elif grep -qiE '10007|does not exist|not found|could not find' "$err" <<<"$out"; then
    echo "[]"
  else
    cat "$err" >&2
    rm -f "$err"
    die "could not list the Worker's secrets (see the wrangler output above)"
  fi
  rm -f "$err"
}

check_secrets() {  # $1 = file to write the deploy/.env values to (JSON, for --secrets-file)
  step "Secrets (Worker + ${ENV_FILE#$REPO/})"
  local existing; existing="$(existing_secrets)"
  "$PY" "$REPO/deploy/envtool.py" cf-secrets "$ENV_FILE" --existing "$existing" --out "$1" --wrangler-dir "$HERE" \
    || die "fix the secrets above, then run cloudflare/deploy.sh again (deploy/cloudflare.md lists every command)"
}

# wrangler builds with `docker build -f - <repo>` (Dockerfile on stdin), so BuildKit can't find
# deploy/Dockerfile.dockerignore: only <repo>/.dockerignore keeps deploy/.env, papers/ and .git out of the image.
ensure_dockerignore() {
  local want="$REPO/deploy/Dockerfile.dockerignore" have="$REPO/.dockerignore"
  [ -f "$want" ] || die "deploy/Dockerfile.dockerignore is missing; it keeps secrets and papers out of the image"
  if [ ! -f "$have" ]; then
    cp "$want" "$have"
    echo "created .dockerignore (copy of deploy/Dockerfile.dockerignore) so the image build skips secrets and papers" >&2
  elif ! cmp -s "$want" "$have"; then
    die ".dockerignore differs from deploy/Dockerfile.dockerignore. wrangler's image build only reads .dockerignore, so make them the same (cp deploy/Dockerfile.dockerignore .dockerignore) or deploy/.env and papers/ could end up in the image."
  fi
}

ensure_bucket() {
  step "R2 bucket $BUCKET"
  if wr r2 bucket info "$BUCKET" >/dev/null 2>&1; then
    echo "exists" >&2
  else
    wr r2 bucket create "$BUCKET" || die "could not create the R2 bucket $BUCKET (is R2 enabled on the account? dash.cloudflare.com → R2)"
  fi
}

# Upload ../index to R2 when it is newer than R2's copy: data files first, manifest.json last, then remove the files
# the old manifest named. The container pulls it on its next start (cloudflare/deploy.sh restart to do that now).
upload_index() {
  step "Paper index → R2"
  local idx="$REPO/index"
  if [ ! -f "$idx/manifest.json" ]; then
    echo "no index/ here: the backend uses R2's index (if any) or the one baked into the image. Build it with: make index" >&2
    return 0
  fi
  local tmp; tmp="$(mktemp -d)"
  wr r2 object get "$BUCKET/index/manifest.json" --remote --pipe >"$tmp/remote.json" 2>/dev/null || : >"$tmp/remote.json"
  local plan
  plan="$("$PY" - "$idx/manifest.json" "$tmp/remote.json" <<'PY'
import json, sys
local = json.load(open(sys.argv[1]))
try:
    remote = json.load(open(sys.argv[2]))
except ValueError:
    remote = {}
if remote and float(remote.get("created") or 0) >= float(local.get("created") or 0):
    print("current"); sys.exit()
new = [n for n in (local.get("files") or {}).values() if n]
old = [n for n in (remote.get("files") or {}).values() if n and n not in new]
print("upload " + " ".join(new)); print("delete " + " ".join(old))
PY
)"
  if [ "$plan" = "current" ]; then echo "R2 already has this index" >&2; rm -rf "$tmp"; return 0; fi
  local f
  for f in $(sed -n 's/^upload //p' <<<"$plan"); do
    echo "put index/$f ($(du -h "$idx/$f" | cut -f1))" >&2
    wr r2 object put "$BUCKET/index/$f" --file "$idx/$f" --remote >/dev/null || die "upload of index/$f failed"
  done
  wr r2 object put "$BUCKET/index/manifest.json" --file "$idx/manifest.json" --remote >/dev/null || die "upload of index/manifest.json failed"
  for f in $(sed -n 's/^delete //p' <<<"$plan"); do
    wr r2 object delete "$BUCKET/index/$f" --remote >/dev/null 2>&1 || true
  done
  "$PY" -c 'import json,sys; m=json.load(open(sys.argv[1])); print("uploaded: %s papers, %s chunks" % (m.get("papers"), m.get("chunks")))' "$idx/manifest.json" >&2
  rm -rf "$tmp"
}

wait_healthy() {
  local url="$1" waited=0 body=""
  step "Waiting for the backend container ($url/api/health; the first start can take several minutes)"
  while [ "$waited" -lt "$HEALTH_WAIT" ]; do
    body="$(curl -fsS --max-time 100 "$url/api/health" 2>/dev/null || true)"
    if grep -q '"ok": *true' <<<"$body"; then echo "$body" >&2; return 0; fi
    sleep "$HEALTH_POLL"; waited=$((waited + HEALTH_POLL))
    printf '  … %ss %s\n' "$waited" "$(head -c 160 <<<"$body")" >&2
  done
  die "the Worker is deployed but the backend didn't answer within ${HEALTH_WAIT}s. Check: npx wrangler containers list; npx wrangler tail (in cloudflare/); the dashboard's Containers page. Then: cloudflare/deploy.sh status"
}

cmd_check() {
  need_node; need_python; need_login
  SECRETS_FILE="$(mktemp)"
  check_secrets "$SECRETS_FILE"
  command -v docker >/dev/null && docker info >/dev/null 2>&1 && echo "docker: running" >&2 || echo "docker: NOT running (needed for the deploy)" >&2
  echo "check: ok" >&2
}

cmd_deploy() {
  need_node; need_python; need_docker; need_login
  SECRETS_FILE="$(mktemp)"
  check_secrets "$SECRETS_FILE"

  step "npm install (@cloudflare/containers)"
  if [ -f package-lock.json ]; then npm ci --no-audit --no-fund >&2; else npm install --no-audit --no-fund >&2; fi

  step "Tests (worker + page)"
  # explicit files: Node 22 reads --test arguments as globs and no longer accepts a bare directory
  node --test test/*.test.js ../web/tests/*.test.js >/dev/null \
    || die "tests failed, not deploying (run: node --test cloudflare/test/*.test.js web/tests/*.test.js)"

  ensure_dockerignore
  ensure_bucket
  upload_index

  step "wrangler deploy (Worker, then image build + push, then container rollout)"
  local args=() log; log="$(mktemp)"
  if [ "$(cat "$SECRETS_FILE")" != "{}" ]; then args+=(--secrets-file "$SECRETS_FILE"); fi
  wr deploy ${args[@]+"${args[@]}"} "$@" 2>&1 | tee "$log" >&2 || { rm -f "$log"; die "wrangler deploy failed (output above)"; }
  local url; url="$(grep -Eo 'https://[A-Za-z0-9.-]+\.workers\.dev' "$log" | head -1 || true)"
  rm -f "$log"
  mkdir -p "$STATE_DIR"
  if [ -n "$url" ]; then
    printf 'WORKER_URL=%s\n' "$url" >"$STATE"
  fi
  url="$(worker_url)"
  wait_healthy "$url"

  cat >&2 <<EOF

Deployed: $url
  page            $url/
  backend health  $url/api/health
  voice tools     $url/api/tools/{search_papers,start_task,run_status}  (ElevenLabs sends X-Macrae-Secret)

Next:
  - voice (first time, or after editing tasks/tasks.json):  make voice-setup
    then put the printed agent id on the Worker:            cd cloudflare && npx wrangler secret put ELEVENLABS_AGENT_ID
  - changed a secret only?  cloudflare/deploy.sh restart     (the container reads secrets when it starts)
EOF
}

cmd_secrets() {
  need_node; need_python; need_login
  SECRETS_FILE="$(mktemp)"
  check_secrets "$SECRETS_FILE"
  if [ "$(cat "$SECRETS_FILE")" = "{}" ]; then echo "deploy/.env has no secret values to upload" >&2; return 0; fi
  wr secret bulk "$SECRETS_FILE" >&2 || die "wrangler secret bulk failed (deploy the Worker first: cloudflare/deploy.sh)"
  echo "The running container keeps its old values until it restarts: cloudflare/deploy.sh restart" >&2
}

cmd_index() {
  need_node; need_python; need_login
  ensure_bucket
  upload_index
  echo "The running container keeps its index until it restarts: cloudflare/deploy.sh restart" >&2
}

cmd_restart() {
  need_python
  local url hdr; url="$(worker_url)"; hdr="$(op_header)"
  curl -fsS -X POST -H "$hdr" "$url/admin/restart" >&2 || die "restart failed (is MACRAE_ADMIN_SECRET / MACRAE_TOOL_SECRET the Worker's value?)"
  echo >&2
  echo "It stops after running flows finish (up to ~14 min); the next request starts it again with the current secrets." >&2
}

cmd_status() {
  need_python
  local url hdr; url="$(worker_url)"; hdr="$(op_header)"
  echo "GET $url/api/health" >&2; curl -sS --max-time 100 "$url/api/health" >&2 || true; echo >&2
  echo "GET $url/admin/status" >&2; curl -sS -H "$hdr" "$url/admin/status" >&2 || true; echo >&2
}

case "${1:-deploy}" in
  check) cmd_check ;;
  secrets) cmd_secrets ;;
  index) cmd_index ;;
  restart) cmd_restart ;;
  status) cmd_status ;;
  deploy) shift || true; cmd_deploy "$@" ;;
  -h|--help|help) sed -n '2,14p' "$HERE/deploy.sh" | sed 's/^# \{0,1\}//' ;;
  -*) cmd_deploy "$@" ;;
  *) die "unknown command $1 (deploy, check, secrets, index, restart, status)" ;;
esac
