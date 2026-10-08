# capability "calc-image" (kind: image): the agent image plus small-calc's calculation environment, pinned and
# preinstalled in /opt/calc, so agent steps skip the per-run PySCF install (~1-2 min) and the "ensurepip is not
# available" venv failure our first Modal runs hit. Build on top of the macrae agent image (tasks/common: Claude Code
# + the live-trace wrapper) so nothing else changes:
#   tasks/capabilities/calc-image/build.sh ghcr.io/<you>/macrae-calc:2026-10-08 --base ghcr.io/<you>/macrae-agent:latest --push
# Later runs use it through the small-calc flow var `calc_image` (the step's `image:`).
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive
# python3-venv is what the bare image lacked (ensurepip); a no-op on the macrae agent image, which has it
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock smoke.py /opt/calc-capability/
# install exactly the lock file, then prove it works: an image whose smoke test fails is never built
RUN python3 -m venv /opt/calc \
    && /opt/calc/bin/pip install --no-cache-dir --disable-pip-version-check -q -r /opt/calc-capability/requirements.lock \
    && /opt/calc/bin/python /opt/calc-capability/smoke.py
ENV MPLBACKEND=Agg
LABEL org.macrae.capability="calc-image" org.macrae.capability.kind="image" org.macrae.capability.version="1.0.0"
WORKDIR /app
