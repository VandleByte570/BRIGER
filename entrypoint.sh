#!/bin/bash

# =============================================================================
# BRIGER - Container Entrypoint
# =============================================================================

set -euo pipefail


# =============================================================================
# Colors
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'


# =============================================================================
# Logging
# =============================================================================

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}


# =============================================================================
# Environment
# =============================================================================

log_step "Initializing BRIGER..."

export PORT="${PORT:-8080}"
export HOST="${HOST:-0.0.0.0}"

export DATA_DIR="${DATA_DIR:-/app/data}"

export OPENCODE_CONFIG_DIR="${OPENCODE_CONFIG_DIR:-/app/.opencode}"
export OPENCODE_SERVER_PORT="${OPENCODE_SERVER_PORT:-4096}"
export OPENCODE_SERVER_HOSTNAME="${OPENCODE_SERVER_HOSTNAME:-0.0.0.0}"

export OPENCODE_SERVER_USERNAME="${OPENCODE_SERVER_USERNAME:-opencode}"
export OPENCODE_SERVER_PASSWORD="${OPENCODE_SERVER_PASSWORD:-}"

export WEBUI_AUTH="${WEBUI_AUTH:-true}"
export ENABLE_SIGNUP="${ENABLE_SIGNUP:-true}"
export WEBUI_NAME="${WEBUI_NAME:-Unified AI Suite}"

export OPENAI_API_BASE_URL="${OPENAI_API_BASE_URL:-}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
export GOOGLE_API_KEY="${GOOGLE_API_KEY:-}"

export DATABASE_URL="${DATABASE_URL:-sqlite:////app/data/webui.db}"

export ENV="${ENV:-prod}"
export DOCKER="${DOCKER:-true}"

export SCARF_NO_ANALYTICS="${SCARF_NO_ANALYTICS:-true}"
export DO_NOT_TRACK="${DO_NOT_TRACK:-true}"
export ANONYMIZED_TELEMETRY="${ANONYMIZED_TELEMETRY:-false}"

export RAG_EMBEDDING_MODEL="${RAG_EMBEDDING_MODEL:-sentence-transformers/all-MiniLM-L6-v2}"
export RAG_EMBEDDING_ENGINE="${RAG_EMBEDDING_ENGINE:-}"

export DEVICE_TYPE="${DEVICE_TYPE:-cpu}"

export ENABLE_RAG_WEB_SEARCH="${ENABLE_RAG_WEB_SEARCH:-false}"
export RAG_WEB_SEARCH_ENGINE="${RAG_WEB_SEARCH_ENGINE:-duckduckgo}"
export SEARXNG_QUERY_URL="${SEARXNG_QUERY_URL:-}"

export GLOBAL_LOG_LEVEL="${GLOBAL_LOG_LEVEL:-INFO}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

export WORKSPACE_DIR="${WORKSPACE_DIR:-/app/workspace}"


# =============================================================================
# Secret
# =============================================================================

if [ -z "${WEBUI_SECRET_KEY:-}" ]; then
    export WEBUI_SECRET_KEY="$(openssl rand -hex 32)"

    log_warn "WEBUI_SECRET_KEY was not provided."
    log_warn "A temporary key has been generated."
    log_warn "Set WEBUI_SECRET_KEY in production."
fi


# =============================================================================
# Directories
# =============================================================================

log_step "Preparing directories..."

mkdir -p \
    "${DATA_DIR}" \
    "${OPENCODE_CONFIG_DIR}" \
    "${OPENCODE_CONFIG_DIR}/skills" \
    "${WORKSPACE_DIR}" \
    /app/logs \
    /var/log/supervisor \
    /var/run


# =============================================================================
# Permissions
# =============================================================================

chown -R appuser:appuser \
    "${DATA_DIR}" \
    "${WORKSPACE_DIR}" \
    /app/logs \
    /var/log/supervisor \
    /var/run \
    2>/dev/null || true


# =============================================================================
# OpenCode Configuration
# =============================================================================

log_step "Configuring OpenCode..."

OPENCODE_CONFIG_FILE="${OPENCODE_CONFIG_DIR}/opencode.json"

cat > "${OPENCODE_CONFIG_FILE}" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",

  "server": {
    "port": ${OPENCODE_SERVER_PORT},
    "hostname": "${OPENCODE_SERVER_HOSTNAME}",
    "mdns": false,
    "cors": [
      "http://localhost:${PORT}",
      "http://127.0.0.1:${PORT}",
      "http://localhost:8080",
      "http://127.0.0.1:8080",
      "http://localhost:7860",
      "http://127.0.0.1:7860"
    ]
  },

  "skills": [
    "/app/.opencode/skills"
  ],

  "permission": {
    "read": "allow",
    "edit": "allow",
    "write": "allow",
    "bash": "allow",
    "glob": "allow",
    "grep": "allow",
    "list": "allow",
    "lsp": "allow",
    "skill": "allow",
    "task": "allow",
    "webfetch": "allow",
    "websearch": "allow",
    "external_directory": "deny",
    "git": "allow"
  },

  "workspace": "/app/workspace",
  "log_level": "${LOG_LEVEL}"
}
EOF

chown appuser:appuser "${OPENCODE_CONFIG_FILE}"
chmod 644 "${OPENCODE_CONFIG_FILE}"

log_info "OpenCode configuration created."


# =============================================================================
# Open WebUI Functions
# =============================================================================

log_step "Installing Open WebUI integrations..."

OPEN_WEBUI_FUNC_DIR="${DATA_DIR}/functions"
OPEN_WEBUI_TOOLS_DIR="${DATA_DIR}/tools"

mkdir -p \
    "${OPEN_WEBUI_FUNC_DIR}" \
    "${OPEN_WEBUI_TOOLS_DIR}"

if [ -d "/app/open_webui/functions" ]; then
    cp -f /app/open_webui/functions/*.py \
        "${OPEN_WEBUI_FUNC_DIR}/" \
        2>/dev/null || true
fi

if [ -d "/app/open_webui/tools" ]; then
    cp -f /app/open_webui/tools/*.py \
        "${OPEN_WEBUI_TOOLS_DIR}/" \
        2>/dev/null || true
fi


# =============================================================================
# Pre-flight
# =============================================================================

log_step "Running pre-flight checks..."


if ! command -v open-webui >/dev/null 2>&1; then
    log_error "open-webui executable not found."
    exit 1
fi

log_info "Open WebUI: $(command -v open-webui)"


if ! command -v opencode >/dev/null 2>&1; then
    log_error "opencode executable not found."
    exit 1
fi

log_info "OpenCode: $(command -v opencode)"


if ! command -v supervisord >/dev/null 2>&1; then
    log_error "supervisord executable not found."
    exit 1
fi


if ! command -v python3 >/dev/null 2>&1; then
    log_error "python3 executable not found."
    exit 1
fi


OPENCODE_VERSION="$(opencode --version 2>/dev/null || echo unknown)"

log_info "OpenCode version: ${OPENCODE_VERSION}"
log_info "Open WebUI port: ${PORT}"
log_info "OpenCode port: ${OPENCODE_SERVER_PORT}"


# =============================================================================
# Validate Supervisor Configuration
# =============================================================================

log_step "Validating Supervisor configuration..."

supervisord \
    -n \
    -t \
    -c /etc/supervisor/conf.d/supervisord.conf \
    2>&1 || true


# =============================================================================
# Shutdown
# =============================================================================

cleanup() {
    log_warn "Shutdown signal received."

    if [ -n "${SUPERVISORD_PID:-}" ]; then
        if kill -0 "${SUPERVISORD_PID}" 2>/dev/null; then
            kill -TERM "${SUPERVISORD_PID}" 2>/dev/null || true
            wait "${SUPERVISORD_PID}" 2>/dev/null || true
        fi
    fi

    log_info "Shutdown complete."
    exit 0
}

trap cleanup SIGTERM SIGINT


# =============================================================================
# Start Supervisor
# =============================================================================

log_step "Starting Supervisor..."

supervisord \
    -c /etc/supervisor/conf.d/supervisord.conf \
    &

SUPERVISORD_PID=$!

log_info "Supervisor PID: ${SUPERVISORD_PID}"


# =============================================================================
# Health Monitoring
# =============================================================================

OPEN_WEBUI_READY=false
OPENCODE_READY=false

MAX_WAIT=180
WAITED=0


while [ "${WAITED}" -lt "${MAX_WAIT}" ]; do

    if [ "${OPEN_WEBUI_READY}" = false ]; then

        if curl \
            -fsS \
            "http://127.0.0.1:${PORT}/health" \
            >/dev/null 2>&1
        then
            log_info "Open WebUI is healthy."
            OPEN_WEBUI_READY=true
        fi

    fi


    if [ "${OPENCODE_READY}" = false ]; then

        if curl \
            -fsS \
            "http://127.0.0.1:${OPENCODE_SERVER_PORT}/health" \
            >/dev/null 2>&1
        then
            log_info "OpenCode server is healthy."
            OPENCODE_READY=true
        fi

    fi


    if [ "${OPEN_WEBUI_READY}" = true ] &&
       [ "${OPENCODE_READY}" = true ]
    then

        log_info "=============================================="
        log_info "BRIGER is ready."
        log_info "Open WebUI: http://localhost:${PORT}"
        log_info "OpenCode:   http://localhost:${OPENCODE_SERVER_PORT}"
        log_info "=============================================="

        break
    fi


    sleep 2

    WAITED=$((WAITED + 2))

done


if [ "${OPEN_WEBUI_READY}" = false ]; then
    log_error "Open WebUI did not become healthy."
    log_error "See /var/log/supervisor/openwebui-stderr.log"
fi


if [ "${OPENCODE_READY}" = false ]; then
    log_error "OpenCode server did not become healthy."
    log_error "See /var/log/supervisor/opencode-stderr.log"
fi


# =============================================================================
# Keep Container Alive
# =============================================================================

wait "${SUPERVISORD_PID}"

EXIT_CODE=$?

log_warn "Supervisor exited with code ${EXIT_CODE}"

exit "${EXIT_CODE}"
