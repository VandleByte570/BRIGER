#!/bin/bash
# =============================================================================
# Unified AI Suite — Local Build Script
# =============================================================================
# Usage:
#   ./scripts/build.sh              # Build with default tag
#   ./scripts/build.sh --push       # Build and push to registry
#   ./scripts/build.sh --no-cache   # Build without Docker cache
# =============================================================================

set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-unified-ai-suite}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
REGISTRY="${REGISTRY:-ghcr.io/yourusername}"
PUSH=false
NO_CACHE=""

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${GREEN}[BUILD]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
info() { echo -e "${BLUE}[INFO]${NC} $1"; }

while [[ $# -gt 0 ]]; do
    case $1 in
        --push)
            PUSH=true
            shift
            ;;
        --no-cache)
            NO_CACHE="--no-cache"
            shift
            ;;
        --tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        --registry)
            REGISTRY="$2"
            shift 2
            ;;
        *)
            warn "Unknown option: $1"
            shift
            ;;
    esac
done

FULL_IMAGE_NAME="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

log "Building Unified AI Suite..."
info "Image: ${FULL_IMAGE_NAME}"
info "Target: final"
info "Cache: $([ -n "$NO_CACHE" ] && echo "disabled" || echo "enabled")"

docker build \
    $NO_CACHE \
    --target final \
    --tag "${IMAGE_NAME}:${IMAGE_TAG}" \
    --tag "${FULL_IMAGE_NAME}" \
    --label "org.opencontainers.image.source=$(git remote get-url origin 2>/dev/null || echo 'unknown')" \
    --label "org.opencontainers.image.revision=$(git rev-parse HEAD 2>/dev/null || echo 'unknown')" \
    --label "org.opencontainers.image.created=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    .

log "Build complete: ${IMAGE_NAME}:${IMAGE_TAG}"

if [ "$PUSH" = true ]; then
    log "Pushing to registry: ${FULL_IMAGE_NAME}"
    docker push "${FULL_IMAGE_NAME}"
    log "Push complete."
fi

info "Image size:"
docker images "${IMAGE_NAME}:${IMAGE_TAG}" --format "{{.Size}}"

log "Done. Run with:"
info "  docker run -p 8080:8080 -p 4096:4096 ${IMAGE_NAME}:${IMAGE_TAG}"
