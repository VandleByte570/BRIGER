# =============================================================================
# Unified AI Suite: Open WebUI + OpenCode + GodMode Engine
# =============================================================================
# Production-ready multi-stage Dockerfile for CPU-only deployments.
# Compatible with: Docker, Docker Compose, Hugging Face Spaces (CPU Standard)
#
# Architecture:
#   - Open WebUI (Port 8080/7860): Central UI, model routing, chat interface
#   - OpenCode Server (Port 4096): Headless terminal/file agentic coding engine
#   - GodMode Engine: 5-stage gated workflow via OpenCode skills
# =============================================================================

# -----------------------------------------------------------------------------
# STAGE 1: Base System Dependencies
# -----------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS base

LABEL maintainer="Unified AI Suite"
LABEL description="Open WebUI + OpenCode + GodMode Engine"
LABEL version="1.0.0"

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies required by both Open WebUI and OpenCode
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    pandoc \
    gcc \
    g++ \
    netcat-openbsd \
    curl \
    jq \
    python3-dev \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    nodejs \
    npm \
    supervisor \
    ca-certificates \
    tini \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# -----------------------------------------------------------------------------
# STAGE 2: Install Open WebUI (Python stack)
# -----------------------------------------------------------------------------
FROM base AS openwebui-builder

# Install Open WebUI from PyPI (includes pre-built frontend static files)
RUN pip install --no-cache-dir "open-webui>=0.6.0,<1.0.0"

# -----------------------------------------------------------------------------
# STAGE 3: Install OpenCode CLI (Node.js stack)
# -----------------------------------------------------------------------------
FROM base AS opencode-builder

# Install OpenCode CLI globally via npm
RUN npm install -g opencode-ai && \
    opencode --version

# -----------------------------------------------------------------------------
# STAGE 4: Final Assembly
# -----------------------------------------------------------------------------
FROM base AS final

# Copy Python packages from openwebui-builder
COPY --from=openwebui-builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=openwebui-builder /usr/local/bin/open-webui /usr/local/bin/open-webui

# Copy Node modules and OpenCode binary from opencode-builder
COPY --from=opencode-builder /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=opencode-builder /usr/local/bin/opencode /usr/local/bin/opencode

# Create application directories
RUN mkdir -p /app/data \
    /app/config \
    /app/.opencode/skills \
    /app/.opencode/plugins \
    /app/scripts \
    /app/open_webui/functions \
    /app/open_webui/tools \
    /app/opencode_server \
    /app/logs \
    /app/workspace \
    /var/log/supervisor \
    /var/run

# Set up OpenCode config directory
ENV OPENCODE_CONFIG_DIR=/app/.opencode \
    OPENCODE_CACHE_DIR=/app/.cache/opencode \
    XDG_CONFIG_HOME=/app/.config \
    XDG_CACHE_HOME=/app/.cache \
    XDG_DATA_HOME=/app/.local/share

RUN mkdir -p /app/.config/opencode /app/.cache/opencode /app/.local/share/opencode

# Copy configuration files
COPY config/opencode.json /app/.opencode/opencode.json
COPY config/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY config/openwebui.env.example /app/config/openwebui.env.example
COPY config/webui_config.json /app/config/webui_config.json

# Copy GodMode skills
COPY .opencode/skills/ /app/.opencode/skills/

# Copy Open WebUI integration functions and tools
COPY open_webui/functions/ /app/open_webui/functions/
COPY open_webui/tools/ /app/open_webui/tools/

# Copy OpenCode server (Python FastAPI wrapper)
COPY opencode_server/ /app/opencode_server/
RUN pip install --no-cache-dir -r /app/opencode_server/requirements.txt

# Copy scripts
COPY scripts/build.sh /app/scripts/build.sh
COPY entrypoint.sh /app/entrypoint.sh

# Set permissions
RUN chmod +x /app/entrypoint.sh /app/scripts/build.sh && \
    chmod 644 /app/.opencode/opencode.json && \
    chmod 644 /etc/supervisor/conf.d/supervisord.conf

# Create non-root user for security
RUN groupadd -r appuser -g 1000 && \
    useradd -r -g appuser -u 1000 -d /app -s /bin/bash appuser && \
    chown -R appuser:appuser /app /var/log/supervisor /var/run

# Environment variables
ENV DATA_DIR=/app/data \
    PORT=8080 \
    HOST=0.0.0.0 \
    OPENCODE_SERVER_PORT=4096 \
    OPENCODE_SERVER_HOSTNAME=0.0.0.0 \
    OPENCODE_SERVER_USERNAME=opencode \
    DOCKER=true \
    ENV=prod \
    WEBUI_NAME="Unified AI Suite" \
    ENABLE_SIGNUP=true \
    DEFAULT_MODELS="" \
    SCARF_NO_ANALYTICS=true \
    DO_NOT_TRACK=true \
    ANONYMIZED_TELEMETRY=false \
    WEBUI_SECRET_KEY="" \
    PYTHONPATH=/usr/local/lib/python3.11/site-packages:/app/opencode_server \
    PATH="/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Expose service ports
# 8080: Open WebUI (configurable via PORT env var)
# 7860: Hugging Face Spaces default
# 4096: OpenCode headless server
EXPOSE 8080 7860 4096

# Volume for persistent data
VOLUME ["/app/data", "/app/workspace", "/app/logs"]

# Health check for Open WebUI
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=5 \
    CMD curl -fsS http://localhost:${PORT:-8080}/health > /dev/null || exit 1

# Use tini as init system for proper signal handling
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/entrypoint.sh"]
