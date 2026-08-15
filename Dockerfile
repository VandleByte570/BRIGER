FROM python:3.11-slim-bookworm

LABEL maintainer="BRIGER"
LABEL description="BRIGER - Open WebUI + OpenCode"
LABEL version="2.0.1"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ============================================================
# System dependencies
# ============================================================

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
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

# ============================================================
# Open WebUI
# ============================================================

RUN pip install --no-cache-dir \
    "open-webui>=0.6.0,<1.0.0"

# ============================================================
# OpenCode
# ============================================================

RUN npm install -g opencode-ai \
    && opencode --version

# ============================================================
# Application directories
# ============================================================

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

WORKDIR /app

# ============================================================
# BRIGER files
# ============================================================

COPY config/opencode.json \
    /app/.opencode/opencode.json

COPY config/supervisord.conf \
    /etc/supervisor/conf.d/supervisord.conf
RUN test -f /etc/supervisor/conf.d/supervisord.conf && \
    echo "BRIGER: supervisord.conf installed successfully"

COPY config/openwebui.env.example \
    /app/config/openwebui.env.example

COPY config/webui_config.json \
    /app/config/webui_config.json

COPY .opencode/skills/ \
    /app/.opencode/skills/

COPY open_webui/functions/ \
    /app/open_webui/functions/

COPY open_webui/tools/ \
    /app/open_webui/tools/

COPY opencode_server/ \
    /app/opencode_server/

COPY scripts/build.sh \
    /app/scripts/build.sh

COPY entrypoint.sh \
    /app/entrypoint.sh

# ============================================================
# Python dependencies
# ============================================================

RUN pip install --no-cache-dir \
    -r /app/opencode_server/requirements.txt

# ============================================================
# Permissions
# ============================================================

RUN chmod +x /app/entrypoint.sh \
    /app/scripts/build.sh \
    && chmod 644 \
    /app/.opencode/opencode.json \
    /etc/supervisor/conf.d/supervisord.conf

# ============================================================
# User
# ============================================================

RUN groupadd --system --gid 1000 appuser \
    && useradd --system \
        --uid 1000 \
        --gid 1000 \
        --home-dir /app \
        --shell /bin/bash \
        appuser \
    && chown -R appuser:appuser /app \
    && chown -R appuser:appuser /var/log/supervisor \
    && chown appuser:appuser /var/run

# ============================================================
# Runtime configuration
# ============================================================

ENV DATA_DIR=/app/data \
    PORT=7860 \
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
    SCARF_NO_ANALYTICS=true \
    DO_NOT_TRACK=true \
    ANONYMIZED_TELEMETRY=false \
    PYTHONPATH=/usr/local/lib/python3.11/site-packages:/app

# ============================================================
# Hugging Face Space
# ============================================================

EXPOSE 7860
EXPOSE 4096

# ============================================================
# Persistent storage
# ============================================================

VOLUME ["/app/data", "/app/workspace", "/app/logs"]

# ============================================================
# Health check
# ============================================================

HEALTHCHECK \
    --interval=30s \
    --timeout=10s \
    --start-period=120s \
    --retries=5 \
    CMD-SHELL \
    curl -fsS "http://127.0.0.1:$${PORT:-7860}/health" \
    >/dev/null || exit 1

# ============================================================
# Non-root
# ============================================================

USER appuser

# ============================================================
# Startup
# ============================================================

ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["/app/entrypoint.sh"]
