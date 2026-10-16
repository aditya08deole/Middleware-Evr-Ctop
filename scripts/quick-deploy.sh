#!/bin/bash
# Run this ONE script after extracting the deployment zip on the server.
# Nothing manual — the zip already bundles .env, service-account-key.json,
# and encryption.key (that's the whole point: they travel with the code,
# so there's no separate "go copy this secret file" step for whoever is
# deploying). This script installs Docker if it's missing, locks down the
# secret files, builds the image, starts everything, and checks it's alive.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== 1/4: Checking Docker ==="
if ! command -v docker >/dev/null 2>&1; then
    echo "Docker not found — installing..."
    curl -fsSL https://get.docker.com | sudo sh
else
    echo "Docker already installed."
fi
sudo systemctl enable docker >/dev/null 2>&1 || true
sudo systemctl start docker >/dev/null 2>&1 || true

echo "=== 2/4: Checking required secret files are present ==="
for f in .env service-account-key.json encryption.key; do
    if [ ! -f "$f" ]; then
        echo "ERROR: $f is missing from this folder." >&2
        echo "The deployment zip is supposed to include it — re-extract from a complete zip." >&2
        exit 1
    fi
done
chmod 600 .env service-account-key.json encryption.key

echo "=== 3/4: Building and starting the app (+ bundled nginx on port 80) ==="
docker compose up -d --build

echo "=== 4/4: Verifying it's alive ==="
sleep 5
if curl -sf http://localhost/health >/dev/null; then
    echo "Deploy successful — app is up at http://$(hostname -I | awk '{print $1}')/"
else
    echo "WARNING: health check didn't respond yet. Check status with: docker compose ps" >&2
fi
