# Base image for Harbor agent steps: Claude Code preinstalled, so Harbor skips its ~2.5 min install.
# Build: agent-runner image build       (re-run now and then to pick up new Claude Code versions)
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash ca-certificates curl git procps ripgrep jq unzip python3 python3-pip python3-venv nodejs npm \
    && rm -rf /var/lib/apt/lists/*
# native Claude Code binary, copied out of root's home so any user in the container can run it
RUN curl -fsSL https://downloads.claude.ai/claude-code-releases/bootstrap.sh | bash \
    && cp -L /root/.local/bin/claude /usr/local/bin/claude \
    && chmod 0755 /usr/local/bin/claude \
    && claude --version
WORKDIR /app
