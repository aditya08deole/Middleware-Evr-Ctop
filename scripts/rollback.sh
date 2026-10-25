#!/usr/bin/env bash
#
# Deliberate one-command revert to the previously deployed image.
#
# scripts/deploy.sh already rolls back automatically when a new version
# fails its health check. This script is for the other case: the deploy
# succeeded, the container is healthy, and the new version is nonetheless
# wrong — bad behaviour, a regression, a config change that turned out to be
# a mistake. Health checks cannot detect that; a person has to decide.
#
# Usage:
#   ./scripts/rollback.sh              # revert to the previous tag
#   ./scripts/rollback.sh --to <tag>   # revert to a specific tag
#   ./scripts/rollback.sh --list       # show available tags, newest first

set -euo pipefail
cd "$(dirname "$0")/.."

HEALTH_URL="${HEALTH_URL:-http://localhost:8090/health}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-150}"
STATE_DIR=".deploy-state"
IMAGE_NAME="ctop-middleware"

TARGET=""
DO_LIST=0
while [ $# -gt 0 ]; do
    case "$1" in
        --to) TARGET="$2"; shift 2 ;;
        --list) DO_LIST=1; shift ;;
        -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

ok()   { printf '    \033[32mok\033[0m   %s\n' "$*"; }
info() { printf '    \033[34m--\033[0m   %s\n' "$*"; }
err()  { printf '\033[31m%s\033[0m\n' "$*" >&2; }

if [ "$DO_LIST" -eq 1 ]; then
    echo "Available ${IMAGE_NAME} images (newest first):"
    docker images "$IMAGE_NAME" --format '  {{.Tag}}\t{{.CreatedSince}}\t{{.Size}}' \
        | sort -k2 || true
    echo
    [ -s "$STATE_DIR/current-tag" ]  && echo "  currently deployed : $(cat "$STATE_DIR/current-tag")"
    [ -s "$STATE_DIR/previous-tag" ] && echo "  rollback target    : $(cat "$STATE_DIR/previous-tag")"
    exit 0
fi

if [ -z "$TARGET" ]; then
    if [ ! -s "$STATE_DIR/previous-tag" ]; then
        err "No previous deployment recorded in ${STATE_DIR}/previous-tag."
        err "Pick a tag explicitly:  ./scripts/rollback.sh --list"
        exit 1
    fi
    TARGET="$(cat "$STATE_DIR/previous-tag")"
fi

if ! docker image inspect "${IMAGE_NAME}:${TARGET}" >/dev/null 2>&1; then
    err "Image ${IMAGE_NAME}:${TARGET} is not present locally (pruned?)."
    err "Available tags:"
    docker images "$IMAGE_NAME" --format '  {{.Tag}}' >&2 || true
    err "Rebuild that commit instead:  ./scripts/deploy.sh --ref ${TARGET}"
    exit 1
fi

CURRENT="$( [ -s "$STATE_DIR/current-tag" ] && cat "$STATE_DIR/current-tag" || echo 'unknown' )"
echo "Rolling back: ${CURRENT}  ->  ${TARGET}"

bash scripts/preflight.sh

IMAGE_TAG="$TARGET" docker compose up -d --remove-orphans

echo "Waiting up to ${HEALTH_TIMEOUT}s for ${HEALTH_URL} ..."
deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
until curl -fsS "$HEALTH_URL" >/dev/null 2>&1; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        err "Rolled-back version did not become healthy within ${HEALTH_TIMEOUT}s."
        docker compose logs --tail=60 middleware >&2 || true
        exit 1
    fi
    sleep 3
done

# The tag we just left becomes the new rollback target, so a rollback can be
# undone by running this again.
mkdir -p "$STATE_DIR"
[ "$CURRENT" != "unknown" ] && echo "$CURRENT" > "$STATE_DIR/previous-tag"
echo "$TARGET" > "$STATE_DIR/current-tag"

ok "healthy on ${IMAGE_NAME}:${TARGET}"
info "run rollback again to return to ${CURRENT}"
docker compose ps
