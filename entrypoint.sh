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

# Open WebUI port.
# PORT is commonly supplied by Hugging Face Spaces.
WEBUI_PORT="${PORT:-8080}"

LOG_DIR="${LOG_DIR:-/app/logs}"

# ==============================================================================
# Helpers
# ==============================================================================

log() {
    echo "[BRIGER] $*"
}

fail() {
    echo "[BRIGER][ERROR] $*" >&2
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
    "/app/data"

# ==============================================================================
# Permissions
# ==============================================================================

chmod +x \
    "$APP_DIR/entrypoint.sh" \
    2>/dev/null || true

# ==============================================================================
# Environment
# ==============================================================================

export WORKSPACE_DIR
export OPENCODE_CONFIG_DIR
export OPENCODE_SERVER_HOSTNAME
export OPENCODE_SERVER_PORT
export PORT="$WEBUI_PORT"

# OpenCode should operate against the BRIGER workspace.
export OPENCODE_DIR="$WORKSPACE_DIR"

# ==============================================================================
# OpenCode configuration
# ==============================================================================

if [[ -f "$APP_DIR/config/opencode.json" ]]; then

    cp \
        "$APP_DIR/config/opencode.json" \
        "$OPENCODE_CONFIG_DIR/opencode.json"

    log "OpenCode configuration installed."

fi

# ==============================================================================
# Skills
# ==============================================================================

SKILL_SOURCE_DIR="$APP_DIR/opencode/skills"
SKILL_TARGET_DIR="$OPENCODE_CONFIG_DIR/skills"

# ------------------------------------------------------------------------------
# Install skills from /app/opencode/skills when that directory exists.
# ------------------------------------------------------------------------------

if [[ -d "$SKILL_SOURCE_DIR" ]]; then

    while IFS= read -r -d '' skill_file; do

        skill_name="$(basename "$skill_file")"
        target_file="$SKILL_TARGET_DIR/$skill_name"

        # Do not copy a file onto itself.
        if [[ "$(realpath "$skill_file")" == "$(realpath "$target_file" 2>/dev/null || true)" ]]; then
            log "Skill already installed: $skill_name"
            continue
        fi

        cp \
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

# ------------------------------------------------------------------------------
# Install repository-level skills.
#
# The Dockerfile already copies:
#
#   .opencode/skills/
#
# into:
#
#   /app/.opencode/skills/
#
# which is also the target directory.
#
# Therefore, do NOT cp files onto themselves.
# ------------------------------------------------------------------------------

REPOSITORY_SKILL_DIR="$APP_DIR/.opencode/skills"

if [[ -d "$REPOSITORY_SKILL_DIR" ]]; then

    while IFS= read -r -d '' skill_file; do

        skill_name="$(basename "$skill_file")"
        target_file="$SKILL_TARGET_DIR/$skill_name"

        # Source and target are identical.
        if [[ "$skill_file" == "$target_file" ]]; then
            log "Skill already installed: $skill_name"
            continue
        fi

        # Extra protection for equivalent paths.
        if [[ "$(realpath "$skill_file")" == "$(realpath "$target_file" 2>/dev/null || true)" ]]; then
            log "Skill already installed: $skill_name"
            continue
        fi

        cp \
            "$skill_file" \
            "$target_file"

        log "Installed repository skill: $skill_name"

    done < <(
        find "$REPOSITORY_SKILL_DIR" \
            -maxdepth 1 \
            -type f \
            -name "*.md" \
            -print0
    )

fi

# ==============================================================================
# Create a safe BRIGER system skill
# ==============================================================================

BRIGER_SKILL="$SKILL_TARGET_DIR/briger.md"

if [[ ! -f "$BRIGER_SKILL" ]]; then

    cat > "$BRIGER_SKILL" <<'EOF'
# BRIGER Engineering Rules

You are operating inside the BRIGER workspace.

## Workspace

Only modify files inside the configured workspace.

## Workflow

1. Inspect the repository.
2. Understand the existing architecture.
3. Plan the smallest safe change.
4. Implement the change.
5. Run relevant tests.
6. Review the resulting changes.
7. Report what was changed.

## Safety

Never expose secrets.

Never print API keys, passwords, tokens, cookies, private keys,
or other credentials.

Do not delete the entire repository.

Do not modify files outside the workspace.

Do not push to remote Git repositories unless explicitly requested.

## Code Quality

Prefer existing project conventions.

Avoid unnecessary dependencies.

Do not rewrite unrelated code.

When fixing a bug, identify the root cause rather than masking symptoms.

Always verify changes where practical.
EOF

    log "Created BRIGER system skill."

fi

# ==============================================================================
# Verify OpenCode
# ==============================================================================

if command -v opencode >/dev/null 2>&1; then

    log "OpenCode binary:"
    opencode --version || true

else

    log "WARNING: 'opencode' binary was not found."

fi

# ==============================================================================
# Verify Python application
# ==============================================================================

if [[ ! -f "$APP_DIR/opencode_server/main.py" ]]; then

    fail "Missing opencode_server/main.py"

fi

# ==============================================================================
# Verify configuration
# ==============================================================================

SUPERVISOR_CONF="/etc/supervisor/conf.d/supervisord.conf"

if [[ ! -f "$SUPERVISOR_CONF" ]]; then

    fail "Missing $SUPERVISOR_CONF"

fi

# ==============================================================================
# Do NOT dump the complete environment.
#
# The previous implementation wrote `export` output to disk. That could expose
# API keys, passwords, tokens, and other secrets.
# ==============================================================================

rm -f \
    "/app/.env.export" \
    2>/dev/null || true

# ==============================================================================
# Supervisor
# ==============================================================================

cat <<'BANNER'
██████╗ ██████╗ ██╗██████╗ ███████╗██████╗ 
██╔══██╗██╔══██╗██║██╔════╝ ██╔════╝██╔══██╗
██████╔╝██████╔╝██║██║  ███╗█████╗  ██████╔╝
██╔══██╗██╔══██╗██║██║   ██║██╔══╝  ██╔══██╗
██████╔╝██║  ██║██║╚██████╔╝███████╗██║  ██║
╚═════╝ ╚═╝  ╚═╝╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝
BANNER

log "Workspace       : $WORKSPACE_DIR"
log "OpenCode server : $OPENCODE_SERVER_HOSTNAME:$OPENCODE_SERVER_PORT"
log "Open WebUI      : 0.0.0.0:$WEBUI_PORT"
log "Config          : $OPENCODE_CONFIG_DIR"
log "=================================================="

# ==============================================================================
# Start supervisor
# ==============================================================================

exec /usr/bin/supervisord \
    -n \
    -c "$SUPERVISOR_CONF"
