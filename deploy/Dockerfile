# syntax=docker/dockerfile:1.7
# macrae backend: FastAPI (server/) + rag/ + tasks/ + agent_runner + Harbor CLI, uvicorn on :8080.
# No Docker inside: Harbor runs agent steps on Modal (`--env modal`); `docker` is a stub that fails cleanly.
#
# Cloudflare (the deployment): `wrangler deploy` in cloudflare/ builds this file (context = repo root) and runs it
# as the MacraeBackend container. Its Container class talks to port 8080 (defaultPort) and passes the secrets as env
# vars; start.py then syncs runs/traces/index with R2 and drains on SIGTERM (see deploy/cloudflare.md).
# If index/ exists at build time it is baked in as /app/index and used when R2 has no newer index.
#
#   docker build -f deploy/Dockerfile -t macrae-backend .          (from the repo root; any x86-64 host works)
#   docker run --env-file deploy/.env -p 8080:8080 -v $PWD/deploy/data:/data macrae-backend
ARG PYTHON_IMAGE=python:3.11-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:latest

FROM ${UV_IMAGE} AS uv

# Collect the per-module requirements that exist (server, rag, tasks, deploy) so the install layer below is only
# rebuilt when one of them changes, not on every code edit.
FROM ${PYTHON_IMAGE} AS reqs
COPY . /src
RUN mkdir /req && for m in server rag tasks deploy; do \
      if [ -f "/src/$m/requirements.txt" ]; then cp "/src/$m/requirements.txt" "/req/$m.txt"; fi; \
    done && ls /req

FROM ${PYTHON_IMAGE}
ARG HARBOR_SPEC="harbor[modal]==0.24.0"
ARG HARBOR_PYTHON=3.12
ARG EMBED_MODEL=BAAI/bge-small-en-v1.5
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_TOOL_DIR=/opt/uv/tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    FASTEMBED_CACHE_PATH=/opt/fastembed \
    HF_HOME=/opt/fastembed/hf \
    MACRAE_CONTAINER=1 \
    PYTHONPATH=/app \
    PORT=8080 \
    MACRAE_PAPERS_DIR=/data/papers \
    MACRAE_INDEX_DIR=/data/index \
    AGENT_RUNNER_HOME=/data/agent-runner

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git tini procps jq \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /usr/local/bin/

# Harbor CLI in its own environment (needs Python >= 3.12; uv fetches it), with the Modal backend.
RUN uv tool install --python "${HARBOR_PYTHON}" "${HARBOR_SPEC}" && harbor --help >/dev/null

# Python deps for server, rag, tasks and the container itself.
COPY --from=reqs /req /tmp/req
RUN set -e; args=""; for f in /tmp/req/*.txt; do args="$args -r $f"; done; \
    uv pip install --system $args && rm -rf /root/.cache

# Embedding model used by rag (fastembed), fetched at build time so the first search isn't a 130 MB download.
RUN python - <<PY || echo "warning: embedding model not prefetched (rag will download it on first use)"
import importlib.util, sys
if importlib.util.find_spec("fastembed") is None:
    print("fastembed not installed, skipping model prefetch"); sys.exit(0)
from fastembed import TextEmbedding
TextEmbedding("${EMBED_MODEL}")
print("prefetched ${EMBED_MODEL}")
PY

RUN useradd --create-home --uid 1000 --shell /bin/bash macrae \
    && mkdir -p /data /opt/fastembed && chown -R macrae:macrae /data /opt/fastembed
COPY deploy/docker-shim.sh /usr/local/bin/docker

WORKDIR /app
# The code, plus index/ if it was there at build time (baked paper index, copied to /data/index on first start)
COPY --chown=macrae:macrae . /app
RUN uv pip install --system --no-deps -e . && python -c "import agent_runner, yaml"

USER macrae
VOLUME ["/data"]
# = defaultPort of MacraeBackend (cloudflare/index.js) and the compose/Caddy port
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "deploy/start.py"]
