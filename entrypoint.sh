#!/usr/bin/env bash

set -Eeuo pipefail

# ==============================================================================
# BRIGER ENTRYPOINT
# ==============================================================================

APP_DIR="${APP_DIR:-/app}"
WORKSPACE_DIR="${WORKSPACE_DIR:-/app/workspace}"

OPENCODE_CONFIG_DIR="${OPENCODE_CONFIG_DIR:-/app/.opencode}"

OPENCODE_SERVER_HOSTNAME="${OPENCODE_SERVER_HOSTNAME:-0.0.0.0}"
OPENCODE_SERVER_PORT="${OPENCODE_SERVER_PORT:-4096}"

WEBUI_PORT="${PORT:-7860}"

LOG_DIR="${LOG_DIR:-/app/logs}"

# ==============================================================================
# Helpers
# ==============================================================================

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

# ==============================================================================
# Directories
# ==============================================================================

mkdir -p \
    "$WORKSPACE_DIR" \
    "$OPENCODE_CONFIG_DIR" \
    "$OPENCODE_CONFIG_DIR/skills" \
    "$LOG_DIR" \
    "$APP_DIR/data"

# ==============================================================================
# Environment
# ==============================================================================

export WORKSPACE_DIR
export OPENCODE_CONFIG_DIR
export OPENCODE_SERVER_HOSTNAME
export OPENCODE_SERVER_PORT
export PORT="$WEBUI_PORT"
export OPENCODE_DIR="$WORKSPACE_DIR"

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
#
# These skills are already
# installed by the Dockerfile in:
# /app/.opencode/skills
#
# Do NOT copy them again.
#
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

    error "OpenCode binary was not found."

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

SUPERVISOR_CONF="/etc/supervisor/conf.d/supervisord.conf"

if [[ ! -f "$SUPERVISOR_CONF" ]]; then
    fail "Missing $SUPERVISOR_CONF"
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
