# macrae web agent: common tasks. `make help` lists them.
# Settings and secrets come from deploy/.env (cp deploy/env.example deploy/.env); variables already set in the
# environment win over the file.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

PY       ?= python3
PORT     ?= 8080
ENV_FILE ?= deploy/.env
DEV_BACKEND_URL ?= http://127.0.0.1:$(PORT)
WRANGLER ?= npx --yes wrangler@4
# load deploy/.env into the recipe's shell (quoted safely by envtool)
WITH_ENV = eval "$$($(PY) deploy/envtool.py export $(ENV_FILE))";
# test folders that exist (each module ships its own). Default (prepend) import mode, same as a bare `pytest` at the
# root: server/tests import helpers with `from conftest import …`, which importlib mode can't resolve. Test file
# basenames must therefore stay unique across modules (rag/ and tasks/ tests are packages, so theirs are free).
TEST_DIRS = $(wildcard tests server/tests rag/tests tasks/tests voice/tests deploy/tests evolve/tests)
REQS      = $(wildcard server/requirements.txt rag/requirements.txt tasks/requirements.txt deploy/requirements.txt \
              evolve/requirements.txt)

# Public URL: the Cloudflare Worker (saved by cloudflare/deploy.sh), else the AWS backend (deploy/aws_deploy.sh).
PUBLIC_URL = $$(sed -n 's/^WORKER_URL=//p' deploy/.state/cloudflare.env 2>/dev/null | tail -1)

.PHONY: help install index search serve test web-dev deploy-web web-secrets deploy-backend deploy \
        cf-check cf-index cf-restart cf-status cf-logs aws-deploy aws-web-secrets \
        backend-code backend-data backend-ingest backend-status backend-url backend-logs backend-ssh \
        backend-stop backend-start backend-down cost voice-setup docker-build docker-run smoke

help: ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'

install: ## pip install agent_runner (-e) and the server/rag/tasks/deploy/evolve requirements
	$(PY) -m pip install -e ".[test]" $(addprefix -r ,$(REQS))

index: ## build the RAG index: papers/ → index/ (python -m rag ingest)
	$(WITH_ENV) $(PY) -m rag ingest

search: ## try the index: make search Q="ion specific effects"
	$(WITH_ENV) $(PY) -m rag search "$(Q)"

serve: ## run the backend locally with reload (http://127.0.0.1:8080, PORT=…)
	$(WITH_ENV) $(PY) deploy/start.py --reload --port $(PORT)

test: ## run every module's tests (pytest), plus the page's and the Worker's (node --test)
	$(PY) -m pytest -q $(TEST_DIRS)
	@if [ -f cloudflare/package.json ] && grep -q '"test"' cloudflare/package.json; then \
	  echo "cloudflare: npm test"; cd cloudflare && npm test --silent; fi
	@if [ -f web/package.json ] && grep -q '"test"' web/package.json; then \
	  echo "web: npm test"; cd web && npm test --silent; fi

web-dev: ## run the Worker + web/ locally (wrangler dev), proxying /api to the local backend
	$(WITH_ENV) cd cloudflare && $(WRANGLER) dev \
	  --var "BACKEND_URL:$(DEV_BACKEND_URL)" \
	  --var "MACRAE_TOOL_SECRET:$${MACRAE_TOOL_SECRET:-}" \
	  --var "ELEVENLABS_API_KEY:$${ELEVENLABS_API_KEY:-}" \
	  --var "ELEVENLABS_AGENT_ID:$${ELEVENLABS_AGENT_ID:-}"

deploy: ## everything on Cloudflare: page + Worker + backend container + R2 (cloudflare/deploy.sh)
	MACRAE_ENV_FILE=$(abspath $(ENV_FILE)) bash cloudflare/deploy.sh

deploy-web: deploy ## same as deploy: the page, the Worker and the backend container are one Worker

deploy-backend: deploy ## same as deploy: the backend is the container inside the "macrae" Worker

web-secrets: ## upload the secrets in deploy/.env to the deployed Worker (then: make cf-restart)
	MACRAE_ENV_FILE=$(abspath $(ENV_FILE)) bash cloudflare/deploy.sh secrets

cf-check: ## Cloudflare preflight: node, docker, login, every secret present (changes nothing)
	MACRAE_ENV_FILE=$(abspath $(ENV_FILE)) bash cloudflare/deploy.sh check
cf-index: ## upload index/ to R2 (the container pulls it on its next start)
	bash cloudflare/deploy.sh index
cf-restart: ## restart the backend container gracefully (new secrets, new index)
	MACRAE_ENV_FILE=$(abspath $(ENV_FILE)) bash cloudflare/deploy.sh restart
cf-status: ## /api/health and the container's state
	MACRAE_ENV_FILE=$(abspath $(ENV_FILE)) bash cloudflare/deploy.sh status
cf-logs: ## live logs of the Worker and the container
	cd cloudflare && $(WRANGLER) tail

aws-deploy: ## alternative backend: AWS EC2 with HTTPS (deploy/aws_deploy.sh up; see deploy/aws.md)
	deploy/aws_deploy.sh up
aws-web-secrets: ## AWS backend only: Worker secrets including BACKEND_URL = the EC2 URL
	$(PY) deploy/envtool.py worker-secrets $(ENV_FILE) --state deploy/.state/aws.env --wrangler-dir cloudflare

backend-code: ## (AWS) ship code changes to the server and restart (no infra changes)
	deploy/aws_deploy.sh code
backend-data: ## (AWS) sync local papers/ and index/ to the server
	deploy/aws_deploy.sh data
backend-ingest: ## (AWS) build the RAG index on the server
	deploy/aws_deploy.sh ingest
backend-status: ## (AWS) instance state, URL and /api/health
	deploy/aws_deploy.sh status
backend-url: ## (AWS) print the backend URL
	@deploy/aws_deploy.sh url
backend-logs: ## (AWS) follow the backend + Caddy logs
	deploy/aws_deploy.sh logs
backend-ssh: ## (AWS) shell on the instance
	deploy/aws_deploy.sh ssh
backend-stop: ## (AWS) stop the instance (disk + IP still billed, ~$5/month)
	deploy/aws_deploy.sh stop
backend-start: ## (AWS) start it again (same URL)
	deploy/aws_deploy.sh start
backend-down: ## (AWS) delete everything on AWS (asks first)
	deploy/aws_deploy.sh down
cost: ## (AWS) monthly cost estimate
	@deploy/aws_deploy.sh cost

voice-setup: ## create/update the ElevenLabs agent with tools pointing at the deployed Worker
	$(WITH_ENV) BACKEND_URL="$${MACRAE_PUBLIC_URL:-$(PUBLIC_URL)}"; \
	  BACKEND_URL="$${BACKEND_URL:-$$($(PY) deploy/envtool.py get $(ENV_FILE) BACKEND_URL)}"; \
	  if [ -z "$$BACKEND_URL" ]; then BACKEND_URL="$$(deploy/aws_deploy.sh url)"; fi; \
	  export BACKEND_URL; echo "BACKEND_URL=$$BACKEND_URL"; $(PY) voice/setup_agent.py

docker-build: ## build the backend image locally (needs Docker)
	docker build -f deploy/Dockerfile -t macrae-backend:latest .

docker-run: ## run the backend container locally on :8080 (needs Docker, deploy/.env)
	MACRAE_PORT=$(PORT) docker compose -f deploy/compose.yaml up --build backend

smoke: ## curl /api/health and /api/tasks: make smoke [URL=https://…] (default: the deployed Worker)
	$(WITH_ENV) url="$(URL)"; [ -n "$$url" ] || url="$(PUBLIC_URL)"; [ -n "$$url" ] || url="$$(deploy/aws_deploy.sh url)"; \
	  url="$${url%/}"; \
	  echo "GET $$url/api/health"; curl -fsS "$$url/api/health"; echo; \
	  echo "GET $$url/api/tasks"; curl -fsS -H "X-Macrae-Secret: $${MACRAE_TOOL_SECRET:-}" "$$url/api/tasks" | head -c 600; echo
