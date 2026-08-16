#!/usr/bin/env bash

set -Eeuo pipefail

# ==============================================================================
# BRIGER ENTRYPOINT
# ==============================================================================

# Default application directory. Many environments (including Hugging Face Spaces)
# mount the repository at /workspace — prefer that when available unless the user
# explicitly sets APP_DIR.
APP_DIR="${APP_DIR:-/app}"

# DATA_DIR: persistent storage for workspace, config, logs. Default to /app/data
# but prefer /workspace/data when running in HF Spaces.
DATA_DIR="${DATA_DIR:-/app/data}"
if [[ -d "/workspace" && "${DATA_DIR}" == "/app/data" ]]; then
    DATA_DIR="/workspace/data"
fi

# Prefer repository mount at /workspace when available.
if [[ -d "/workspace" && "${APP_DIR}" == "/app" ]]; then
    APP_DIR="/workspace"
fi

# Directories (prefer DATA_DIR to keep runtime files on persistent volume)
WORKSPACE_DIR="${WORKSPACE_DIR:-$DATA_DIR/workspace}"
OPENCODE_CONFIG_DIR="${OPENCODE_CONFIG_DIR:-$DATA_DIR/.opencode}"
LOG_DIR="${LOG_DIR:-$DATA_DIR/logs}"

# Secure default: bind OpenCode to localhost unless the user overrides explicitly
OPENCODE_SERVER_HOSTNAME="${OPENCODE_SERVER_HOSTNAME:-127.0.0.1}"
OPENCODE_SERVER_PORT="${OPENCODE_SERVER_PORT:-4096}"

WEBUI_PORT="${PORT:-7860}"

# Export early so supervisord receives these environment variables
export DATA_DIR WORKSPACE_DIR OPENCODE_CONFIG_DIR LOG_DIR OPENCODE_SERVER_HOSTNAME OPENCODE_SERVER_PORT PORT="$WEBUI_PORT"

# Helpers
log() {
    echo "[BRIGER] $*"
}

error() {
    echo "[BRIGER][ERROR] $*" >&2
}

fail() {
    error "$*"
    exit 1
}

# Ensure directories exist before proceeding
mkdir -p \
    "$WORKSPACE_DIR" \
    "$OPENCODE_CONFIG_DIR" \
    "$OPENCODE_CONFIG_DIR/skills" \
    "$LOG_DIR" \
    "$APP_DIR/data"

# Safe one-time migration: copy repo-provided defaults into DATA_DIR when target empty
if [[ -d "/app/.opencode" && -z "$(ls -A "$OPENCODE_CONFIG_DIR" 2>/dev/null)" ]]; then
    cp -a /app/.opencode/* "$OPENCODE_CONFIG_DIR/" 2>/dev/null || true
fi
if [[ -d "/app/workspace" && -z "$(ls -A "$WORKSPACE_DIR" 2>/dev/null)" ]]; then
    cp -a /app/workspace/* "$WORKSPACE_DIR/" 2>/dev/null || true
fi

# ==============================================================================
# Environment (export redundant values for scripts that expect them)
# ==============================================================================

export WORKSPACE_DIR
export OPENCODE_CONFIG_DIR
export OPENCODE_SERVER_HOSTNAME
export OPENCODE_SERVER_PORT
export PORT="$WEBUI_PORT"
export OPENCODE_DIR="$WORKSPACE_DIR"
export LOG_DIR

# ==============================================================================
# OpenCode configuration
# ==============================================================================

if [[ -f "$APP_DIR/config/opencode.json" ]]; then

    cp -f \
        "$APP_DIR/config/opencode.json" \
        "$OPENCODE_CONFIG_DIR/opencode.json"

    log "OpenCode configuration installed."

else

    error "OpenCode configuration not found:"
    error "$APP_DIR/config/opencode.json"

fi

# ==============================================================================
# External OpenCode skills
# ==============================================================================

SKILL_SOURCE_DIR="$APP_DIR/opencode/skills"
SKILL_TARGET_DIR="$OPENCODE_CONFIG_DIR/skills"

if [[ -d "$SKILL_SOURCE_DIR" ]]; then

    while IFS= read -r -d '' skill_file; do

        skill_name="$(basename "$skill_file")"
        target_file="$SKILL_TARGET_DIR/$skill_name"

        # Never copy a file onto itself safely under 'set -e'
        if [[ -f "$target_file" ]] && [[ "$(realpath "$skill_file")" == "$(realpath "$target_file")" ]]; then
            log "Skill already installed: $skill_name"
            continue
        fi

        cp -f \
            "$skill_file" \
            "$target_file"

        log "Installed skill: $skill_name"

    done < <(
        find "$SKILL_SOURCE_DIR" \
            -maxdepth 1 \
            -type f \
            -name "*.md" \
            -print0
    )

fi

# ===================================================
# Repository-level skills
# ===================================================

REPOSITORY_SKILL_DIR="$APP_DIR/.opencode/skills"

if [[ -d "$REPOSITORY_SKILL_DIR" ]]; then

    while IFS= read -r -d '' skill_file; do
        log "Skill available: $(basename "$skill_file")"
    done < <(
        find "$REPOSITORY_SKILL_DIR" \
            -maxdepth 1 \
            -type f \
            -name "*.md" \
            -print0
    )

fi

# ==============================================================================
# BRIGER system skill
# ==============================================================================

BRIGER_SKILL="$SKILL_TARGET_DIR/briger.md"

if [[ ! -f "$BRIGER_SKILL" ]]; then

    cat > "$BRIGER_SKILL" <<'EOF'
# BRIGER Engineering Rules

You are operating inside the BRIGER workspace.

## Workspace

Work inside the configured workspace.

## Workflow

1. Inspect the existing project.
2. Understand the architecture.
3. Plan the smallest safe change.
4. Implement the change.
5. Test the change.
6. Review the result.
7. Report what changed.

## Safety

Never expose API keys, passwords, tokens, cookies,
private keys, or other credentials.

Do not delete the entire repository.

Do not modify unrelated files.

Do not push to a remote repository unless explicitly requested.

## Code Quality

Use existing project conventions.

Avoid unnecessary dependencies.

Do not rewrite unrelated code.

Fix root causes rather than hiding symptoms.

Verify changes whenever practical.
EOF

    log "Created BRIGER system skill."

fi

# ==============================================================================
# Verify OpenCode
# ==============================================================================

if command -v opencode >/dev/null 2>&1; then

    log "OpenCode:"
    opencode --version || true

else

    error "OpenCode binary was not found. The server will continue but /tui will return 503 until opencode is installed."

fi

# ==============================================================================
# Verify BRIGER server
# ==============================================================================

if [[ ! -f "$APP_DIR/opencode_server/main.py" ]]; then
    fail "Missing $APP_DIR/opencode_server/main.py"
fi

# ==============================================================================
# Verify Supervisor
# ==============================================================================

SYSTEM_SUPERVISOR_CONF="/etc/supervisor/conf.d/supervisord.conf"
REPO_SUPERVISOR_CONF="$APP_DIR/config/supervisord.conf"

if [[ -f "$SYSTEM_SUPERVISOR_CONF" ]]; then
    SUPERVISOR_CONF="$SYSTEM_SUPERVISOR_CONF"
    log "Supervisor configuration found: $SUPERVISOR_CONF"

elif [[ -f "$REPO_SUPERVISOR_CONF" ]]; then
    SUPERVISOR_CONF="$REPO_SUPERVISOR_CONF"
    log "Supervisor configuration found in repository: $SUPERVISOR_CONF"

else
    fail "Supervisor configuration not found."
    fail "Checked: $SYSTEM_SUPERVISOR_CONF"
    fail "Checked: $REPO_SUPERVISOR_CONF"
fi

# ==============================================================================
# Remove stale environment export
# ==============================================================================

rm -f "$APP_DIR/.env.export" 2>/dev/null || true

# ==============================================================================
# Supervisor
# ==============================================================================

cat <<'BANNER'
██████╗ ██████╗ ██╗██████╗ ███████╗██████╗
██╔══██╗██╔══██╗██║██╔════╝ ██╔════╝██╔══██╗
██████╔╝██████╔╝██║██║  ███╗█████╗  ██████╔╝
██╔══██╗██╔══██╗██║██║   ██║██╔══╝  ██╔══██╗
██████╔╝██║  ██║██║╚██████╔╝███████╗██║  ██║
╚═════╝ ╚═╝  ╚═╝╚═╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
BANNER

log "Workspace       : $WORKSPACE_DIR"
log "OpenCode server : $OPENCODE_SERVER_HOSTNAME:$OPENCODE_SERVER_PORT"
log "Open WebUI      : 0.0.0.0:$WEBUI_PORT"
log "Config          : $OPENCODE_CONFIG_DIR"
log "=================================================="

# ==============================================================================
# Start Supervisor
# ==============================================================================

exec /usr/bin/supervisord \
    -n \
    -c "$SUPERVISOR_CONF"
