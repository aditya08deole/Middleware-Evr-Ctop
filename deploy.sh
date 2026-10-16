#!/bin/bash
# One-click deploy: pull latest main, verify it's clean, then (re)start the
# stack. Safe to run by hand or from the self-hosted CI runner — it refuses
# to touch the running containers if lint or tests fail, so a bad commit
# never takes down a working deployment.
set -euo pipefail
cd "$(dirname "$0")"

echo "=== 1/5: Fetching latest main ==="
git fetch origin main
git reset --hard origin/main

echo "=== 2/5: Installing dependencies ==="
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt

echo "=== 3/5: Lint check (blocking — deploy stops here on failure) ==="
ruff check .

echo "=== 4/5: Test suite (blocking) ==="
python -m pytest tests/ -v

echo "=== 5/5: Rebuilding and restarting containers ==="
docker compose build
docker compose up -d --remove-orphans

echo "=== Deploy complete ==="
docker compose ps
