#!/usr/bin/env bash
set -euo pipefail

echo "Starting Evara IoT Middleware..."

# instance/ is a bind mount, so it may arrive owned by the host user. The
# container no longer runs as root and cannot chmod 777 it (nor should it —
# that made the directory world-writable on the host). Instead, verify it is
# actually writable and say so clearly if it is not, rather than failing
# later with an opaque SQLite or lock-file error.
if [ ! -w /app/instance ]; then
    echo "FATAL: /app/instance is not writable by $(id -un) (uid $(id -u))." >&2
    echo "       instance/ is bind-mounted from the host and keeps the host's" >&2
    echo "       numeric owner. Fix on the host with:" >&2
    echo "         sudo chown -R $(id -u):$(id -g) instance" >&2
    echo "       or rebuild with matching ids:" >&2
    echo "         docker compose build --build-arg APP_UID=\$(id -u) --build-arg APP_GID=\$(id -g)" >&2
    exit 1
fi

# The SQLite schema only matters in the SQLite fallback mode. In the real
# deployment (USE_FIREBASE=true) devices live in Firestore and the local
# SQLite file is just a read cache written by utils/local_device_store, so
# running db.create_all() at every boot did nothing useful — it only created
# a set of empty tables that no longer reflect how the app stores anything,
# which is actively misleading when debugging.
USE_FIREBASE_LOWER=$(printf '%s' "${USE_FIREBASE:-false}" | tr '[:upper:]' '[:lower:]')
if [ "$USE_FIREBASE_LOWER" = "true" ]; then
    echo "Firestore mode — skipping SQLite schema init (devices live in Firestore)."
else
    echo "SQLite mode — initializing database schema..."
    python - <<'PY'
from app import create_app
from models import db

app = create_app("production")
with app.app_context():
    db.create_all()
    print("Database initialized successfully.")
PY
fi

# Worker count from the CPU allocation instead of a hardcoded 4, which was
# simultaneously too many for a 1-2 core VPS (context thrashing) and too few
# for a larger box. Capped at 8: this process is I/O-bound on HTTP and MQTT,
# so past a point extra workers add memory and contention, not throughput.
#
# Only ONE worker ever owns the APScheduler jobs and the MQTT clients —
# whichever wins the exclusive lock in utils/proc_lock.py. The rest only
# serve HTTP. That guard is what makes multiple workers safe here at all.
if [ -z "${GUNICORN_WORKERS:-}" ]; then
    CPUS=$(python -c 'import os; print(len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1))')
    GUNICORN_WORKERS=$(( CPUS * 2 + 1 ))
    [ "$GUNICORN_WORKERS" -gt 8 ] && GUNICORN_WORKERS=8
    echo "Detected ${CPUS} CPU(s) -> ${GUNICORN_WORKERS} gunicorn workers (override with GUNICORN_WORKERS)."
else
    echo "Using GUNICORN_WORKERS=${GUNICORN_WORKERS} from the environment."
fi

exec gunicorn \
    --bind "0.0.0.0:${PORT:-8090}" \
    --workers "${GUNICORN_WORKERS}" \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-25}" \
    --keep-alive 5 \
    --worker-tmp-dir /dev/shm \
    --access-logfile - \
    --error-logfile - \
    app:app
