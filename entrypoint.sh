#!/bin/bash
# =============================================================================
# Unified AI Suite — Container Entrypoint
# =============================================================================
# Orchestrates Open WebUI and OpenCode server startup with graceful shutdown
# handling, health checks, and auto-configuration.
#
# Hugging Face Spaces Compatibility:
#   - HF Spaces sets PORT=7860 automatically
#   - The entrypoint adapts all services to use the provided PORT
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

# =============================================================================
# Configuration & Environment Setup
# =============================================================================

log_step "Initializing Unified AI Suite container..."

# Hugging Face Spaces compatibility: use their PORT if provided
export PORT=${PORT:-8080}
export HOST=${HOST:-0.0.0.0}
export OPENCODE_SERVER_PORT=${OPENCODE_SERVER_PORT:-4096}
export OPENCODE_SERVER_HOSTNAME=${OPENCODE_SERVER_HOSTNAME:-0.0.0.0}
export DATA_DIR=${DATA_DIR:-/app/data}
export OPENCODE_CONFIG_DIR=${OPENCODE_CONFIG_DIR:-/app/.opencode}

# Generate WEBUI_SECRET_KEY if not set
if [ -z "${WEBUI_SECRET_KEY:-}" ]; then
    export WEBUI_SECRET_KEY=$(openssl rand -hex 32)
    log_warn "WEBUI_SECRET_KEY was not set. Generated a random key."
    log_warn "For production, set a persistent WEBUI_SECRET_KEY to avoid session loss."
fi

# Ensure data directories exist
mkdir -p "${DATA_DIR}"
mkdir -p "${OPENCODE_CONFIG_DIR}"
mkdir -p /app/logs
mkdir -p /app/workspace
mkdir -p /var/log/supervisor
mkdir -p /var/run

# Fix permissions
chown -R appuser:appuser /app/data /app/logs /app/workspace /var/log/supervisor /var/run 2>/dev/null || true

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
    "cors": ["http://localhost:${PORT}", "http://127.0.0.1:${PORT}"]
  },
  "permission": {
    "edit": "ask",
    "bash": "ask",
    "write": "ask",
    "delete": "ask"
  },
  "skills": [
    "/app/.opencode/skills/godmode_define.md",
    "/app/.opencode/skills/godmode_plan.md",
    "/app/.opencode/skills/godmode_execute.md",
    "/app/.opencode/skills/godmode_review.md",
    "/app/.opencode/skills/godmode_ship.md"
  ],
  "workspace": "/app/workspace",
  "log_level": "${LOG_LEVEL:-info}"
}
EOF

log_info "OpenCode config written to ${OPENCODE_CONFIG_FILE}"

# =============================================================================
# Open WebUI Auto-Configuration
# =============================================================================

log_step "Configuring Open WebUI..."

OPEN_WEBUI_FUNC_DIR="${DATA_DIR}/functions"
if [ -d "/app/open_webui/functions" ]; then
    mkdir -p "${OPEN_WEBUI_FUNC_DIR}"
    cp -n /app/open_webui/functions/*.py "${OPEN_WEBUI_FUNC_DIR}/" 2>/dev/null || true
    log_info "Open WebUI custom functions copied to ${OPEN_WEBUI_FUNC_DIR}"
fi

OPEN_WEBUI_TOOLS_DIR="${DATA_DIR}/tools"
if [ -d "/app/open_webui/tools" ]; then
    mkdir -p "${OPEN_WEBUI_TOOLS_DIR}"
    cp -n /app/open_webui/tools/*.py "${OPEN_WEBUI_TOOLS_DIR}/" 2>/dev/null || true
    log_info "Open WebUI custom tools copied to ${OPEN_WEBUI_TOOLS_DIR}"
fi

# =============================================================================
# Pre-Flight Checks
# =============================================================================

log_step "Running pre-flight checks..."

if ! command -v open-webui &> /dev/null; then
    log_error "open-webui binary not found."
    exit 1
fi
log_info "Open WebUI binary: $(which open-webui)"

if ! command -v opencode &> /dev/null; then
    log_error "opencode binary not found."
    exit 1
fi
log_info "OpenCode binary: $(which opencode)"

if ! command -v supervisord &> /dev/null; then
    log_error "supervisord binary not found."
    exit 1
fi
log_info "Supervisord binary: $(which supervisord)"

if ! command -v python3 &> /dev/null; then
    log_error "python3 binary not found."
    exit 1
fi
log_info "Python3 binary: $(which python3)"

OPENCODE_VERSION=$(opencode --version 2>/dev/null || echo "unknown")
log_info "OpenCode version: ${OPENCODE_VERSION}"

# =============================================================================
# Signal Handling Setup
# =============================================================================

cleanup() {
    log_warn "Received shutdown signal. Stopping services gracefully..."
    if [ -n "${SUPERVISORD_PID:-}" ] && kill -0 "${SUPERVISORD_PID}" 2>/dev/null; then
        kill -TERM "${SUPERVISORD_PID}"
        wait "${SUPERVISORD_PID}"
    fi
    log_info "Shutdown complete."
    exit 0
}

trap cleanup SIGTERM SIGINT

# =============================================================================
# Start Services via Supervisord
# =============================================================================

log_step "Starting services via supervisord..."
log_info "Open WebUI will be available at: http://${HOST}:${PORT}"
log_info "OpenCode Server will be available at: http://${OPENCODE_SERVER_HOSTNAME}:${OPENCODE_SERVER_PORT}"

export > /app/.env.export

supervisord -c /etc/supervisor/conf.d/supervisord.conf &
SUPERVISORD_PID=$!

log_info "Supervisord started with PID ${SUPERVISORD_PID}"

# =============================================================================
# Wait for Services & Health Monitoring
# =============================================================================

log_step "Waiting for services to become healthy..."

OPEN_WEBUI_READY=false
OPENCODE_READY=false
MAX_WAIT=120
WAITED=0

while [ $WAITED -lt $MAX_WAIT ]; do
    if [ "$OPEN_WEBUI_READY" = false ]; then
        if curl -fsS "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
            log_info "Open WebUI is healthy on port ${PORT}"
            OPEN_WEBUI_READY=true
        fi
    fi

    if [ "$OPENCODE_READY" = false ]; then
        if curl -fsS "http://127.0.0.1:${OPENCODE_SERVER_PORT}/health" > /dev/null 2>&1 || \
           curl -fsS "http://127.0.0.1:${OPENCODE_SERVER_PORT}/docs" > /dev/null 2>&1 || \
           nc -z 127.0.0.1 ${OPENCODE_SERVER_PORT} 2>/dev/null; then
            log_info "OpenCode Server is listening on port ${OPENCODE_SERVER_PORT}"
            OPENCODE_READY=true
        fi
    fi

    if [ "$OPEN_WEBUI_READY" = true ] && [ "$OPENCODE_READY" = true ]; then
        log_info "================================================"
        log_info "Unified AI Suite is ready!"
        log_info "================================================"
        log_info "Open WebUI:     http://localhost:${PORT}"
        log_info "OpenCode API:   http://localhost:${OPENCODE_SERVER_PORT}"
        log_info "OpenCode Docs:  http://localhost:${OPENCODE_SERVER_PORT}/docs"
        log_info "================================================"
        break
    fi

    sleep 2
    WAITED=$((WAITED + 2))
    if [ $((WAITED % 10)) -eq 0 ]; then
        log_warn "Still waiting for services... (${WAITED}s / ${MAX_WAIT}s)"
    fi
done

if [ "$OPEN_WEBUI_READY" = false ]; then
    log_error "Open WebUI failed to start within ${MAX_WAIT} seconds."
    log_error "Check logs: /var/log/supervisor/openwebui-stderr.log"
fi

if [ "$OPENCODE_READY" = false ]; then
    log_error "OpenCode Server failed to start within ${MAX_WAIT} seconds."
    log_error "Check logs: /var/log/supervisor/opencode-stderr.log"
fi

# =============================================================================
# Keep Container Alive & Monitor
# =============================================================================

log_info "Container is running. Press Ctrl+C or send SIGTERM to stop."

wait "${SUPERVISORD_PID}"
EXIT_CODE=$?

log_warn "Supervisord exited with code ${EXIT_CODE}"
exit ${EXIT_CODE}
