# =============================================================================
# BRIGER
# Open WebUI + OpenCode + FastAPI Bridge
# =============================================================================

FROM python:3.11-slim-bookworm AS base

LABEL maintainer="BRIGER"
LABEL description="BRIGER - Open WebUI + OpenCode"
LABEL version="2.0.0"

ENV DEBIAN_FRONTEND=noninteractive \
 PYTHONDONTWRITEBYTECODE=1 \
 PYTHONUNBUFFERED=1 \
 PIP_NO_CACHE_DIR=1 \
 PIP_DISABLE_PIP_VERSION_CHECK=1

# =============================================================================
# System dependencies
# =============================================================================

RUN apt-get update && apt-get install -y --no-install-recommends \
 git \
 build-essential \
 gcc \
 g++ \
 python3-dev \
 curl \
 jq \
 ca-certificates \
 supervisor \
 tini \
 nodejs \
 npm \
 ffmpeg \
 pandoc \
 libsm6 \
 libxext6 \
 libgl1 \
 && rm -rf /var/lib/apt/lists/* \
 /tmp/* \
 /var/tmp/*

# =============================================================================
# Open WebUI
# =============================================================================

FROM base AS openwebui-builder

RUN pip install --no-cache-dir \
 "open-webui>=0.6.0,<1.0.0"

# =============================================================================
# OpenCode
# =============================================================================

FROM base AS opencode-builder

RUN npm install -g opencode-ai \
 && opencode --version

# =============================================================================
# Final image
# =============================================================================

FROM base AS final

# -----------------------------------------------------------------------------
# Python / Open WebUI
# -----------------------------------------------------------------------------

COPY --from=openwebui-builder \
 /usr/local/lib/python3.11/site-packages \
 /usr/local/lib/python3.11/site-packages

COPY --from=openwebui-builder \
 /usr/local/bin/open-webui \
 /usr/local/bin/open-webui

# -----------------------------------------------------------------------------
# Node / OpenCode
# -----------------------------------------------------------------------------

COPY --from=opencode-builder \
 /usr/local/lib/node_modules \
 /usr/local/lib/node_modules

COPY --from=opencode-builder \
 /usr/local/bin/opencode \
 /usr/local/bin/opencode

# -----------------------------------------------------------------------------
# Application directories
# -----------------------------------------------------------------------------

RUN mkdir -p \
 /app/data \
 /app/config \
 /app/.opencode/skills \
 /app/.opencode/plugins \
 /app/.cache/opencode \
 /app/.config/opencode \
 /app/.local/share/opencode \
 /app/scripts \
 /app/open_webui/functions \
 /app/open_webui/tools \
 /app/opencode_server \
 /app/logs \
 /app/workspace \
 /var/log/supervisor \
 /var/run

# -----------------------------------------------------------------------------
# OpenCode environment
# -----------------------------------------------------------------------------

ENV OPENCODE_CONFIG_DIR=/app/.opencode \
 OPENCODE_CACHE_DIR=/app/.cache/opencode \
 XDG_CONFIG_HOME=/app/.config \
 XDG_CACHE_HOME=/app/.cache \
 XDG_DATA_HOME=/app/.local/share

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

COPY config/opencode.json \
 /app/.opencode/opencode.json

COPY config/supervisord.conf \
 /etc/supervisor/conf.d/supervisord.conf

COPY config/openwebui.env.example \
 /app/config/openwebui.env.example

COPY config/webui_config.json \
 /app/config/webui_config.json

# -----------------------------------------------------------------------------
# Skills
# -----------------------------------------------------------------------------

COPY .opencode/skills/ \
 /app/.opencode/skills/

# -----------------------------------------------------------------------------
# Open WebUI integrations
# -----------------------------------------------------------------------------

COPY open_webui/functions/ \
 /app/open_webui/functions/

COPY open_webui/tools/ \
 /app/open_webui/tools/

# -----------------------------------------------------------------------------
# BRIGER OpenCode API
# -----------------------------------------------------------------------------

COPY opencode_server/ \
 /app/opencode_server/

RUN pip install --no-cache-dir \
 -r /app/opencode_server/requirements.txt

# -----------------------------------------------------------------------------
# Scripts
# -----------------------------------------------------------------------------

COPY scripts/build.sh \
 /app/scripts/build.sh

COPY entrypoint.sh \
 /app/entrypoint.sh

# -----------------------------------------------------------------------------
# Permissions
# -----------------------------------------------------------------------------

RUN chmod +x \
 /app/entrypoint.sh \
 /app/scripts/build.sh \
 && chmod 644 \
 /app/.opencode/opencode.json \
 /etc/supervisor/conf.d/supervisord.conf

# =============================================================================
# Non-root user
# =============================================================================

RUN groupadd \
 --system \
 --gid 1000 \
 appuser \
 && useradd \
 --system \
 --uid 1000 \
 --gid 1000 \
 --home-dir /app \
 --shell /bin/bash \
 appuser \
 && chown -R \
 appuser:appuser \
 /app \
 /var/log/supervisor \
 /var/run

# =============================================================================
# Runtime environment
# =============================================================================

ENV DATA_DIR=/app/data \
 PORT=8080 \
 HOST=0.0.0.0 \
 OPENCODE_SERVER_PORT=4096 \
 OPENCODE_SERVER_HOSTNAME=0.0.0.0 \
 OPENCODE_SERVER_USERNAME=opencode \
 OPENCODE_SERVER_PASSWORD="" \
 WORKSPACE_DIR=/app/workspace \
 OPENCODE_CONFIG_DIR=/app/.opencode \
 OPENCODE_TIMEOUT=1800 \
 COMMAND_TIMEOUT=300 \
 DOCKER=true \
 ENV=prod \
 WEBUI_NAME="BRIGER" \
 ENABLE_SIGNUP=true \
 DEFAULT_MODELS="" \
 SCARF_NO_ANALYTICS=true \
 DO_NOT_TRACK=true \
 ANONYMIZED_TELEMETRY=false \
 WEBUI_SECRET_KEY="" \
 PYTHONPATH=/usr/local/lib/python3.11/site-packages:/app \
 PATH="/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"

# =============================================================================
# Ports
# =============================================================================

EXPOSE 8080
EXPOSE 7860
EXPOSE 4096

# =============================================================================
# Persistent data
# =============================================================================

VOLUME [
 "/app/data",
 "/app/workspace",
 "/app/logs"
]

# =============================================================================
# Health check
# =============================================================================

HEALTHCHECK \
 --interval=30s \
 --timeout=10s \
 --start-period=90s \
 --retries=5 \
 CMD-SHELL \
 curl -fsS \
 "http://127.0.0.1:$${PORT:-8080}/health" \
 >/dev/null \
 || exit 1

# =============================================================================
# Run as non-root
# =============================================================================

USER appuser

# =============================================================================
# Init
# =============================================================================

ENTRYPOINT [
 "/usr/bin/tini",
 "--"
]

CMD [
 "/app/entrypoint.sh"
]
