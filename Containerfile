FROM debian:stable

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv curl git ca-certificates nodejs npm \
    systemd systemd-sysv \
    && rm -rf /var/lib/apt/lists/*

# Install uv globally
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && cp /root/.local/bin/uv /usr/local/bin/

# Install Claude Code globally
RUN npm install -g @anthropic-ai/claude-code

# Create fera user
RUN useradd -m -s /bin/bash fera

# Application lives at /opt/fera, venv at /opt/fera-venv
# UV_PYTHON_INSTALL_DIR: uv's managed Python goes to a shared location
# (default is ~/.local/share/uv/python which is under /root and inaccessible to fera user)
WORKDIR /opt/fera
ENV UV_PROJECT_ENVIRONMENT=/opt/fera-venv
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python

# Install dependencies (cached layer)
COPY pyproject.toml .python-version uv.lock ./
RUN uv sync --no-dev --no-install-project

# Install application
COPY . .
RUN uv sync --no-dev

# Build web UI frontend
WORKDIR /opt/fera/webui
RUN npm ci && npm run build
WORKDIR /opt/fera

# Install and enable systemd units
COPY deploy/*.service /etc/systemd/system/
RUN systemctl enable fera-memory.service fera-gateway.service fera-webui.service

ENV FERA_HOME=/home/fera
EXPOSE 8389 8080
CMD ["/usr/sbin/init"]
