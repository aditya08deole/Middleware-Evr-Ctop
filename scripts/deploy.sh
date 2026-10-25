#!/usr/bin/env bash
#
# Deploy with a health gate and automatic rollback.
#
# Replaces the old root-level deploy.sh, which had three problems:
#   * It pip-installed every dependency onto the HOST and then built the
#     same dependencies again inside Docker — doing the slow part twice.
#   * It ran `git reset --hard origin/main`, deploying whatever main pointed
#     at by the time it ran rather than a specific reviewed commit.
#   * It finished with `docker compose ps` and called that success. If the
#     new image started and then crash-looped, or came up unable to reach
#     Firestore, the deploy was reported as complete and the service was
#     down until someone noticed.
#
# This script instead: tags every image, remembers what was running, waits
# for /health to actually answer, and puts the previous image back by itself
# if the new one does not come up.
#
# Usage:
#   ./scripts/deploy.sh                 # deploy the current checkout
#   ./scripts/deploy.sh --ref <commit>  # check out and deploy a specific ref
#   ./scripts/deploy.sh --no-rollback   # leave the broken version running
#                                       # (for debugging a failing start)
#   ./scripts/deploy.sh --skip-tests    # skip the local test run
#
# Environment:
#   HEALTH_TIMEOUT   seconds to wait for health (default 150)
#   KEEP_IMAGES      tagged images to retain (default 5)

set -euo pipefail
cd "$(dirname "$0")/.."

HEALTH_URL="${HEALTH_URL:-http://localhost:8090/health}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-150}"
KEEP_IMAGES="${KEEP_IMAGES:-5}"
STATE_DIR=".deploy-state"
IMAGE_NAME="ctop-middleware"

DO_ROLLBACK=1
SKIP_TESTS=0
TARGET_REF=""

while [ $# -gt 0 ]; do
    case "$1" in
        --ref) TARGET_REF="$2"; shift 2 ;;
        --no-rollback) DO_ROLLBACK=0; shift ;;
        --skip-tests) SKIP_TESTS=1; shift ;;
        -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32mok\033[0m   %s\n' "$*"; }
info() { printf '    \033[34m--\033[0m   %s\n' "$*"; }
warn() { printf '    \033[33mWARN\033[0m %s\n' "$*"; }
err()  { printf '\n\033[31m%s\033[0m\n' "$*" >&2; }

mkdir -p "$STATE_DIR"

wait_for_health() {
    local timeout="$1" deadline
    deadline=$(( $(date +%s) + timeout ))
    until curl -fsS "$HEALTH_URL" >/dev/null 2>&1; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            return 1
        fi
        sleep 3
    done
    return 0
}

# ---------------------------------------------------------------------------
step "1/8  Preflight"
bash scripts/preflight.sh

# ---------------------------------------------------------------------------
step "2/8  Resolving the commit to deploy"
if [ -n "$TARGET_REF" ]; then
    git fetch --prune origin
    git checkout --detach "$TARGET_REF"
    ok "checked out $TARGET_REF"
fi
if git rev-parse --git-dir >/dev/null 2>&1; then
    NEW_TAG="$(git rev-parse --short=12 HEAD)"
    if ! git diff --quiet || ! git diff --cached --quiet; then
        NEW_TAG="${NEW_TAG}-dirty-$(date +%H%M%S)"
        warn "working tree has uncommitted changes; tagging as ${NEW_TAG}"
    fi
else
    NEW_TAG="notgit-$(date +%Y%m%d-%H%M%S)"
    warn "not a git repository; tagging as ${NEW_TAG}"
fi
ok "new image tag: ${IMAGE_NAME}:${NEW_TAG}"

# ---------------------------------------------------------------------------
step "3/8  Tests"
if [ "$SKIP_TESTS" -eq 1 ]; then
    info "skipped (--skip-tests)"
elif python -c 'import pytest' >/dev/null 2>&1; then
    # `python -m pytest`, not bare `pytest`: the console script does not
    # add the repo root to sys.path, so conftest.py's `from models import
    # db` fails with ModuleNotFoundError.
    python -m pytest
    ok "test suite passed"
else
    info "pytest not installed on this host; CI is the gate (pip install -r requirements-dev.txt to run here)"
fi

# ---------------------------------------------------------------------------
step "4/8  Recording what is currently running"
# The tag that is live right now becomes the rollback target. Read it from
# the running container rather than from a file we wrote last time, so a
# manual `docker compose up` outside this script is still recorded correctly.
PREVIOUS_TAG=""
RUNNING_IMAGE="$(docker compose ps --format '{{.Image}}' middleware 2>/dev/null | head -1 || true)"
if [ -n "$RUNNING_IMAGE" ]; then
    PREVIOUS_TAG="${RUNNING_IMAGE##*:}"
fi
if [ -z "$PREVIOUS_TAG" ] && [ -f "$STATE_DIR/current-tag" ]; then
    PREVIOUS_TAG="$(cat "$STATE_DIR/current-tag")"
fi

if [ -n "$PREVIOUS_TAG" ] && [ "$PREVIOUS_TAG" != "$NEW_TAG" ]; then
    echo "$PREVIOUS_TAG" > "$STATE_DIR/previous-tag"
    ok "rollback target: ${IMAGE_NAME}:${PREVIOUS_TAG}"
elif [ "$PREVIOUS_TAG" = "$NEW_TAG" ]; then
    info "the same tag is already running; redeploying it"
else
    warn "nothing running — first deploy, so there is nothing to roll back to"
fi

# ---------------------------------------------------------------------------
step "5/8  Building ${IMAGE_NAME}:${NEW_TAG}"
IMAGE_TAG="$NEW_TAG" docker compose build
ok "build complete"

# ---------------------------------------------------------------------------
step "6/8  Starting the new version"
IMAGE_TAG="$NEW_TAG" docker compose up -d --remove-orphans
ok "containers started"

# ---------------------------------------------------------------------------
step "7/8  Waiting up to ${HEALTH_TIMEOUT}s for ${HEALTH_URL}"
if wait_for_health "$HEALTH_TIMEOUT"; then
    ok "healthy"
else
    err "DEPLOY FAILED: ${IMAGE_NAME}:${NEW_TAG} did not become healthy within ${HEALTH_TIMEOUT}s."
    echo "--- last 60 log lines ---" >&2
    IMAGE_TAG="$NEW_TAG" docker compose logs --tail=60 middleware >&2 || true
    echo "-------------------------" >&2

    if [ "$DO_ROLLBACK" -eq 0 ]; then
        warn "--no-rollback given; leaving the failed version running for debugging."
        exit 1
    fi
    if [ ! -s "$STATE_DIR/previous-tag" ]; then
        err "No previous image recorded — cannot roll back automatically."
        err "The service is DOWN. Investigate with: docker compose logs middleware"
        exit 1
    fi

    ROLLBACK_TAG="$(cat "$STATE_DIR/previous-tag")"
    step "ROLLING BACK to ${IMAGE_NAME}:${ROLLBACK_TAG}"
    if ! docker image inspect "${IMAGE_NAME}:${ROLLBACK_TAG}" >/dev/null 2>&1; then
        err "Rollback image ${IMAGE_NAME}:${ROLLBACK_TAG} is not present locally."
        err "It may have been pruned. Rebuild it: ./scripts/deploy.sh --ref ${ROLLBACK_TAG}"
        exit 1
    fi
    IMAGE_TAG="$ROLLBACK_TAG" docker compose up -d --remove-orphans
    if wait_for_health "$HEALTH_TIMEOUT"; then
        echo "$ROLLBACK_TAG" > "$STATE_DIR/current-tag"
        ok "rolled back successfully; ${IMAGE_NAME}:${ROLLBACK_TAG} is healthy"
        err "The deploy of ${NEW_TAG} FAILED and was reverted. Service is up on ${ROLLBACK_TAG}."
        exit 1
    fi
    err "ROLLBACK ALSO FAILED TO BECOME HEALTHY. The service is DOWN."
    err "This usually means the problem is not the image — check the host:"
    err "  * is Firestore reachable?   curl -sS https://firestore.googleapis.com"
    err "  * are the secrets intact?   bash scripts/preflight.sh"
    err "  * disk full?                df -h"
    exit 1
fi

# ---------------------------------------------------------------------------
step "8/8  Recording success and pruning old images"
echo "$NEW_TAG" > "$STATE_DIR/current-tag"
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$STATE_DIR/last-deploy-at"

# Keep the most recent KEEP_IMAGES tags so rollback has somewhere to go,
# and drop the rest so months of deploys do not fill the disk. The tag in
# previous-tag is protected regardless of age.
PROTECT="$NEW_TAG"
[ -s "$STATE_DIR/previous-tag" ] && PROTECT="$PROTECT $(cat "$STATE_DIR/previous-tag")"
OLD_TAGS="$(docker images "$IMAGE_NAME" --format '{{.Tag}}\t{{.CreatedAt}}' \
    | sort -k2 -r | tail -n +$((KEEP_IMAGES + 1)) | cut -f1 || true)"
for t in $OLD_TAGS; do
    case " $PROTECT " in
        *" $t "*) continue ;;
    esac
    [ "$t" = "local" ] && continue
    docker image rm "${IMAGE_NAME}:${t}" >/dev/null 2>&1 \
        && info "pruned ${IMAGE_NAME}:${t}" || true
done

step "Deployed ${IMAGE_NAME}:${NEW_TAG}"
docker compose ps
echo
echo "  Roll back with: make rollback"
