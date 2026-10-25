# syntax=docker/dockerfile:1.7
#
# Two-stage build. Stage 1 compiles any dependency that has no prebuilt
# wheel for this platform; stage 2 installs the resulting wheels and ships
# WITHOUT a compiler. The previous single-stage image installed
# build-essential and never removed it, so a C toolchain shipped to
# production inside every image.
#
# Build-time caching: the pip cache is a BuildKit cache mount, so an
# unchanged requirements.txt means dependencies are never re-downloaded or
# recompiled — a one-line application change rebuilds only the final COPY
# layer. BuildKit is the default in Docker 23+; nothing extra to enable.

ARG PYTHON_VERSION=3.11-slim-bookworm


# ---------------------------------------------------------------------------
# Stage 1 — build wheels
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .

RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel --wheel-dir /wheels -r requirements.txt


# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS runtime

# ENV key=value (not the legacy space-separated form, which Docker now warns
# about on every build).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is needed by the container healthcheck and nothing else.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# The container runs as an unprivileged user instead of root. APP_UID/APP_GID
# are build args because /app/instance is a BIND MOUNT from the host: the
# in-container user must share the host directory's numeric owner or it
# cannot write the SQLite mirror or the scheduler lock file. 1000:1000 is the
# first user created on a standard Ubuntu install, which is the common case;
# override at build time if your server differs:
#   docker compose build --build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --gid ${APP_GID} app \
    && useradd --uid ${APP_UID} --gid ${APP_GID} --create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Install from the locally built wheels only (--no-index), so the runtime
# stage never reaches the network and never needs a compiler. gunicorn is
# already pinned in requirements.txt — the old Dockerfile installed it a
# second time separately, which could silently float to a different version
# than the one pinned.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    pip install --no-index --find-links=/wheels -r requirements.txt

# Application code, owned by the unprivileged user.
COPY --chown=app:app . .

# instance/ holds the SQLite mirror and the scheduler lock. Owned by the app
# user rather than the previous `chmod 777`, which made it world-writable on
# the host through the bind mount.
RUN mkdir -p /app/instance \
    && chown ${APP_UID}:${APP_GID} /app/instance \
    && chmod 750 /app/instance \
    && chmod +x /app/entrypoint.sh \
    && find /app/scripts -name '*.sh' -exec chmod +x {} +

USER app

EXPOSE 8090

# Declared here as well as in compose, so `docker run` without compose still
# reports health. start-period covers the Firebase startup sync and the EMQX
# subscriptions, which take tens of seconds on a cold start.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8090/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
