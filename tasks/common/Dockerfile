# macrae agent image for Modal: agent_runner/image (Claude Code preinstalled) plus the live-trace wrapper.
# `claude` on every PATH Harbor uses is tasks/common/claude, which runs the real CLI (/usr/local/lib/macrae/claude-real)
# and forwards its stream-json to the backend (claude_live.py). Harbor's install check (`command -v claude`) passes,
# so Claude Code is not installed again at trial start.
#
# Build and push (Modal pulls it from the registry; it must be public or have a Modal registry secret):
#   tasks/common/build.sh ghcr.io/<you>/macrae-agent:latest --push
# then set AGENT_RUNNER_MODAL_IMAGE=ghcr.io/<you>/macrae-agent:latest on the backend (Worker var/secret).
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash ca-certificates curl git procps ripgrep jq unzip python3 python3-pip python3-venv nodejs npm \
        build-essential \
    && rm -rf /var/lib/apt/lists/*
# the native Claude Code binary, moved out of root's home so any user can run it, under a name the wrapper knows
RUN curl -fsSL https://downloads.claude.ai/claude-code-releases/bootstrap.sh | bash \
    && mkdir -p /usr/local/lib/macrae \
    && cp -L /root/.local/bin/claude /usr/local/lib/macrae/claude-real \
    && chmod 0755 /usr/local/lib/macrae/claude-real \
    && rm -rf /root/.local/bin/claude /root/.local/share/claude \
    && /usr/local/lib/macrae/claude-real --version
COPY claude_live.py /usr/local/lib/macrae/claude_live.py
COPY claude /usr/local/bin/claude
# Harbor runs `export PATH="$HOME/.local/bin:$PATH"; claude …`: make that name the wrapper too
RUN chmod 0755 /usr/local/bin/claude /usr/local/lib/macrae/claude_live.py \
    && mkdir -p /root/.local/bin && ln -sf /usr/local/bin/claude /root/.local/bin/claude \
    && python3 -I -c "import ast,sys; ast.parse(open('/usr/local/lib/macrae/claude_live.py').read())" \
    && claude --version
# the native binary updates itself into ~/.local otherwise, which would bypass the wrapper
ENV DISABLE_AUTOUPDATER=1
WORKDIR /app
