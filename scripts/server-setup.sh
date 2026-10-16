#!/bin/bash
# One-time production server setup — run this ONCE on a fresh server (Linux)
# before the first automated deploy. It automates the two steps that CAN be
# automated:
#   1. Docker's own service set to start on boot (so `restart: always` in
#      docker-compose.yml actually has something to restart into after a
#      power cycle).
#   2. A GitHub Actions self-hosted runner, installed as a system service,
#      labeled "evara-middleware" to match .github/workflows/deploy.yml's
#      `runs-on: [self-hosted, evara-middleware]`.
#
# What this script does NOT do (can't be automated — see HOW_TO_RUN.md's
# "CI/CD Auto-Deploy Setup" section): placing encryption.key on this server.
# That file has to come from wherever the original key already lives; a
# server-side script has no way to produce or fetch a secret it doesn't
# already have.
#
# Usage:
#   RUNNER_TOKEN=<token from GitHub repo Settings > Actions > Runners > New> \
#   REPO_URL=https://github.com/<org>/<repo> \
#   ./scripts/server-setup.sh
set -euo pipefail

: "${RUNNER_TOKEN:?Set RUNNER_TOKEN to a fresh token from GitHub: repo Settings > Actions > Runners > New self-hosted runner (tokens expire after ~1 hour)}"
: "${REPO_URL:?Set REPO_URL, e.g. https://github.com/your-org/Middleware-Evr-CtoP}"

RUNNER_LABEL="evara-middleware"
RUNNER_VERSION="2.319.1"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"

echo "=== 1/2: Enabling Docker to start on boot ==="
sudo systemctl enable docker
sudo systemctl start docker

echo "=== 2/2: Installing GitHub Actions self-hosted runner ==="
mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

if [ ! -f ./config.sh ]; then
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64) PKG_ARCH="x64" ;;
        aarch64) PKG_ARCH="arm64" ;;
        *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
    esac
    curl -o actions-runner.tar.gz -L \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${PKG_ARCH}-${RUNNER_VERSION}.tar.gz"
    tar xzf actions-runner.tar.gz
    rm actions-runner.tar.gz
fi

./config.sh --url "$REPO_URL" --token "$RUNNER_TOKEN" --labels "$RUNNER_LABEL" --unattended --replace

sudo ./svc.sh install
sudo ./svc.sh start

echo "=== Done ==="
echo "Runner installed as a service in $RUNNER_DIR — it will auto-start on every boot."
echo "Next: copy encryption.key onto this server (one-time, see HOW_TO_RUN.md), then push to main."
