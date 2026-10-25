#!/usr/bin/env bash
#
# One-time server setup for Ubuntu 22.04 / 24.04. Takes a bare server to a
# running, reboot-surviving, backed-up deployment.
#
# Replaces scripts/server-setup.sh and scripts/quick-deploy.sh, which between
# them had three problems this script fixes:
#   * quick-deploy.sh installed Docker with `curl https://get.docker.com |
#     sudo sh` — an unpinned remote script executed as root. This uses
#     Docker's official apt repository and signing key instead.
#   * quick-deploy.sh expected .env, encryption.key and the Firebase key to
#     be BUNDLED IN A ZIP alongside the code, so production credentials
#     travelled through chat and laptops with no way to revoke them. This
#     script never transports secrets; it verifies they are already present
#     and tells you exactly what is missing.
#   * server-setup.sh registered a CI runner for a workflow that did not
#     exist. It does now (.github/workflows/deploy.yml), and the runner step
#     here is opt-in via --with-runner.
#
# SAFE TO RE-RUN. Every step checks its own state first, so running this
# again after a change (or after a partial failure) converges rather than
# duplicating work.
#
# Usage:
#   sudo ./scripts/bootstrap-ubuntu.sh [options]
#
#   --deploy-dir PATH   Where the deployment lives (default /opt/ctop-middleware)
#   --with-runner       Also install a GitHub Actions self-hosted runner.
#                       Requires RUNNER_TOKEN and REPO_URL in the environment.
#   --skip-firewall     Do not configure ufw (use if you manage it elsewhere)
#   --skip-start        Set everything up but do not start the stack
#   -h | --help         Show this help

set -euo pipefail

DEPLOY_DIR="/opt/ctop-middleware"
WITH_RUNNER=0
SKIP_FIREWALL=0
SKIP_START=0
RUNNER_VERSION="2.319.1"
RUNNER_LABEL="evara-middleware"
SSH_PORT="${SSH_PORT:-22}"

while [ $# -gt 0 ]; do
    case "$1" in
        --deploy-dir) DEPLOY_DIR="$2"; shift 2 ;;
        --with-runner) WITH_RUNNER=1; shift ;;
        --skip-firewall) SKIP_FIREWALL=1; shift ;;
        --skip-start) SKIP_START=1; shift ;;
        -h|--help) sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

step()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()    { printf '    \033[32mok\033[0m   %s\n' "$*"; }
info()  { printf '    \033[34m--\033[0m   %s\n' "$*"; }
warn()  { printf '    \033[33mWARN\033[0m %s\n' "$*"; }
die()   { printf '\n\033[31mFATAL: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run with sudo: sudo $0 $*"

# The user who invoked sudo owns the deployment directory and the docker
# group membership — running everything as root would leave files only root
# can edit.
REAL_USER="${SUDO_USER:-root}"
REAL_UID="$(id -u "$REAL_USER")"
REAL_GID="$(id -g "$REAL_USER")"

step "Checking the operating system"
if [ -r /etc/os-release ]; then
    . /etc/os-release
    info "Detected: ${PRETTY_NAME:-unknown}"
    case "${VERSION_ID:-}" in
        22.04|24.04) ok "supported Ubuntu release" ;;
        *) warn "built and tested for Ubuntu 22.04/24.04 — continuing anyway" ;;
    esac
else
    warn "/etc/os-release not readable; cannot verify the distribution"
fi
command -v apt-get >/dev/null 2>&1 || die "apt-get not found — this script is for Debian/Ubuntu"

export DEBIAN_FRONTEND=noninteractive

step "Installing base packages"
apt-get update -qq
# jq      — the watchdog parses /api/diagnostics with it
# sqlite3 — scripts/backup.sh uses `.backup` for a consistent snapshot of the
#           live device mirror; without it the script falls back to `cp`,
#           which can capture a torn write on a database being written to
apt-get install -y -qq \
    ca-certificates curl gnupg git make jq sqlite3 logrotate \
    unattended-upgrades apt-listchanges >/dev/null
ok "base packages present (including jq and sqlite3)"

step "Installing Docker from Docker's official apt repository"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    ok "docker and the compose plugin are already installed"
else
    install -m 0755 -d /etc/apt/keyrings
    if [ ! -f /etc/apt/keyrings/docker.asc ]; then
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
            -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc
        ok "added Docker's signing key"
    fi
    codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin >/dev/null
    ok "installed docker-ce and the compose + buildx plugins"
fi

systemctl enable --now docker >/dev/null 2>&1 || true
systemctl is-active --quiet docker && ok "docker service is running and enabled on boot" \
    || warn "docker service is not active — check: systemctl status docker"

if [ "$REAL_USER" != "root" ]; then
    if id -nG "$REAL_USER" | tr ' ' '\n' | grep -qx docker; then
        ok "$REAL_USER is already in the docker group"
    else
        usermod -aG docker "$REAL_USER"
        ok "added $REAL_USER to the docker group"
        warn "$REAL_USER must log out and back in before 'docker' works without sudo"
    fi
fi

step "Preparing the deployment directory: $DEPLOY_DIR"
mkdir -p "$DEPLOY_DIR"
chown "$REAL_UID:$REAL_GID" "$DEPLOY_DIR"
if [ -d "$DEPLOY_DIR/.git" ]; then
    ok "already a git clone"
else
    warn "$DEPLOY_DIR is not a git clone yet."
    info "Clone the repository into it as $REAL_USER, for example:"
    info "  sudo -u $REAL_USER git clone <repo-url> $DEPLOY_DIR"
fi

# instance/ is bind-mounted into the container, which runs as APP_UID
# (default 1000). A bind mount keeps the host's numeric ownership, so this
# has to match or the container cannot write the SQLite mirror or take the
# scheduler lock.
mkdir -p "$DEPLOY_DIR/instance"
chown -R "$REAL_UID:$REAL_GID" "$DEPLOY_DIR/instance"
ok "instance/ exists and is owned by $REAL_USER (uid $REAL_UID)"
if [ "$REAL_UID" != "1000" ]; then
    warn "uid $REAL_UID != the image default 1000. Build with matching ids:"
    warn "  APP_UID=$REAL_UID APP_GID=$REAL_GID make build"
    if ! grep -q '^APP_UID=' "$DEPLOY_DIR/.env" 2>/dev/null; then
        info "or add APP_UID=$REAL_UID and APP_GID=$REAL_GID to .env"
    fi
fi

step "Checking for the secret files (this script never transports them)"
MISSING=0
for f in .env encryption.key service-account-key.json; do
    target="$DEPLOY_DIR/$f"
    if [ -d "$target" ]; then
        warn "$f is a DIRECTORY — Docker created it because the file was missing."
        warn "  Remove it and restore the real file: sudo rmdir '$target'"
        MISSING=1
    elif [ -f "$target" ] && [ -s "$target" ]; then
        chown "$REAL_UID:$REAL_GID" "$target"
        chmod 600 "$target"
        ok "$f present (mode set to 600)"
    else
        warn "$f is MISSING from $DEPLOY_DIR"
        MISSING=1
    fi
done
if [ "$MISSING" -eq 1 ]; then
    echo
    warn "Copy the missing files from a machine that already has them, e.g.:"
    warn "  scp .env encryption.key service-account-key.json ${REAL_USER}@<server>:${DEPLOY_DIR}/"
    warn "encryption.key in particular CANNOT be regenerated: every device"
    warn "credential in Firestore is encrypted with it. A fresh key means"
    warn "reissuing all node tokens from CTOP."
    SKIP_START=1
    info "Skipping the start step until the secrets are in place."
fi

step "Configuring log rotation for host-side logs"
cat > /etc/logrotate.d/ctop-middleware <<EOF
${DEPLOY_DIR}/*.log ${DEPLOY_DIR}/logs/*.log {
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su ${REAL_USER} ${REAL_USER}
}
EOF
ok "/etc/logrotate.d/ctop-middleware written"
info "Container logs are capped separately by docker-compose.yml (10m x 5)"

if [ "$SKIP_FIREWALL" -eq 0 ]; then
    step "Configuring the firewall (ufw)"
    apt-get install -y -qq ufw >/dev/null
    # Order matters: allow SSH BEFORE enabling, or enabling ufw over an SSH
    # session locks you out of your own server.
    ufw allow "${SSH_PORT}/tcp" >/dev/null 2>&1 || true
    ufw allow 80/tcp  >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
    ok "allowed ${SSH_PORT}/tcp (ssh), 80/tcp, 443/tcp"
    warn "Port 8090 is deliberately NOT opened: nginx proxies the app on :80."
    warn "  To reach the app directly from outside, run: sudo ufw allow 8090/tcp"
    if ufw status | head -1 | grep -q inactive; then
        ufw --force enable >/dev/null
        ok "ufw enabled"
    else
        ok "ufw already enabled"
    fi
else
    info "Skipping firewall configuration (--skip-firewall)"
fi

step "Enabling unattended security upgrades"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
# Deliberately NOT enabling automatic reboots: this host runs a data
# pipeline, and an unannounced 2am reboot is a worse outage than a pending
# kernel patch. Reboot on your own schedule.
ok "security updates will install automatically (no automatic reboots)"

step "Installing systemd units"
install -d /etc/systemd/system
for unit in ctop-middleware.service ctop-backup.service ctop-backup.timer \
            ctop-watchdog.service ctop-watchdog.timer; do
    src="$DEPLOY_DIR/systemd/$unit"
    if [ ! -f "$src" ]; then
        warn "$unit not found in $DEPLOY_DIR/systemd — skipped"
        continue
    fi
    # The units ship with /opt/ctop-middleware and a placeholder user baked
    # in; substitute the real values for this server.
    sed -e "s#__DEPLOY_DIR__#${DEPLOY_DIR}#g" \
        -e "s#__RUN_USER__#${REAL_USER}#g" \
        "$src" > "/etc/systemd/system/$unit"
    ok "installed $unit"
done
systemctl daemon-reload

systemctl enable ctop-middleware.service >/dev/null 2>&1 \
    && ok "ctop-middleware.service enabled (starts on boot)" \
    || warn "could not enable ctop-middleware.service"
systemctl enable --now ctop-backup.timer >/dev/null 2>&1 \
    && ok "nightly backup timer enabled" || warn "could not enable ctop-backup.timer"
systemctl enable --now ctop-watchdog.timer >/dev/null 2>&1 \
    && ok "delivery watchdog timer enabled" || warn "could not enable ctop-watchdog.timer"

mkdir -p "$DEPLOY_DIR/backups"
chown "$REAL_UID:$REAL_GID" "$DEPLOY_DIR/backups"
ok "backups/ directory ready"

if [ "$WITH_RUNNER" -eq 1 ]; then
    step "Installing the GitHub Actions self-hosted runner"
    : "${RUNNER_TOKEN:?Set RUNNER_TOKEN (repo Settings > Actions > Runners > New self-hosted runner; tokens expire in ~1 hour)}"
    : "${REPO_URL:?Set REPO_URL, e.g. https://github.com/your-org/your-repo}"
    RUNNER_DIR="${RUNNER_DIR:-/home/$REAL_USER/actions-runner}"
    mkdir -p "$RUNNER_DIR"
    chown "$REAL_UID:$REAL_GID" "$RUNNER_DIR"
    if [ ! -f "$RUNNER_DIR/config.sh" ]; then
        case "$(uname -m)" in
            x86_64)  PKG_ARCH="x64" ;;
            aarch64) PKG_ARCH="arm64" ;;
            *) die "Unsupported architecture: $(uname -m)" ;;
        esac
        sudo -u "$REAL_USER" curl -fsSL \
            "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${PKG_ARCH}-${RUNNER_VERSION}.tar.gz" \
            -o "$RUNNER_DIR/actions-runner.tar.gz"
        sudo -u "$REAL_USER" tar xzf "$RUNNER_DIR/actions-runner.tar.gz" -C "$RUNNER_DIR"
        rm -f "$RUNNER_DIR/actions-runner.tar.gz"
        ok "downloaded runner v${RUNNER_VERSION}"
    else
        ok "runner already downloaded"
    fi
    # config.sh refuses to run as root by design.
    sudo -u "$REAL_USER" bash -c "cd '$RUNNER_DIR' && ./config.sh \
        --url '$REPO_URL' --token '$RUNNER_TOKEN' --labels '$RUNNER_LABEL' \
        --unattended --replace"
    ( cd "$RUNNER_DIR" && ./svc.sh install "$REAL_USER" && ./svc.sh start )
    ok "runner installed as a service, labelled '$RUNNER_LABEL'"
    info "Set the DEPLOY_DIR repository variable to: $DEPLOY_DIR"
else
    info "Skipping the CI runner (pass --with-runner to install it)"
fi

if [ "$SKIP_START" -eq 0 ]; then
    step "Starting the stack"
    sudo -u "$REAL_USER" bash -c "cd '$DEPLOY_DIR' && bash scripts/preflight.sh"
    systemctl start ctop-middleware.service
    ok "ctop-middleware.service started"
    info "Waiting for the health endpoint..."
    deadline=$(( $(date +%s) + 180 ))
    until curl -fsS http://localhost:8090/health >/dev/null 2>&1; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            warn "not healthy after 180s. Inspect with:"
            warn "  sudo journalctl -u ctop-middleware -n 50"
            warn "  cd $DEPLOY_DIR && docker compose logs --tail=80 middleware"
            break
        fi
        sleep 5
    done
    curl -fsS http://localhost:8090/health >/dev/null 2>&1 && ok "application is healthy"
else
    info "Not starting the stack (--skip-start, or secrets were missing)"
fi

step "Done"
cat <<EOF

  Deployment directory : $DEPLOY_DIR
  Service              : systemctl status ctop-middleware
  Logs                 : cd $DEPLOY_DIR && make logs
  Health               : curl -fsS http://localhost:8090/health
  Deploy a change      : cd $DEPLOY_DIR && make deploy
  Roll back            : cd $DEPLOY_DIR && make rollback

  Backups  : nightly via ctop-backup.timer   -> $DEPLOY_DIR/backups
  Watchdog : every 15 min via ctop-watchdog.timer
             Set WATCHDOG_WEBHOOK_URL in .env to receive alerts.

EOF
if [ "$REAL_USER" != "root" ] && ! id -nG "$REAL_USER" | tr ' ' '\n' | grep -qx docker; then
    warn "Log out and back in so docker group membership takes effect."
fi
