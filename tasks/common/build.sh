#!/usr/bin/env bash
# Build (and optionally push) the macrae agent image with the live-trace claude wrapper.
#   tasks/common/build.sh IMAGE[:TAG] [--push]
# Then set AGENT_RUNNER_MODAL_IMAGE=IMAGE[:TAG] for the backend (cloudflare: npx wrangler secret put
# AGENT_RUNNER_MODAL_IMAGE, then cloudflare/deploy.sh restart). Modal must be able to pull it (public, or a
# Modal registry secret).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
image="${1:-}"
if [ -z "$image" ] || [ "${image#-}" != "$image" ]; then
  echo "usage: $0 IMAGE[:TAG] [--push]   e.g. $0 ghcr.io/you/macrae-agent:latest --push" >&2
  exit 2
fi
docker build --platform linux/amd64 -t "$image" -f "$here/Dockerfile" "$here"
docker run --rm --platform linux/amd64 "$image" sh -c 'command -v claude && claude --version' >/dev/null
echo "built $image (claude wrapper OK)"
if [ "${2:-}" = "--push" ]; then
  docker push "$image"
  echo "pushed. Set AGENT_RUNNER_MODAL_IMAGE=$image on the backend."
fi
