# Deployment

Single source of truth for deploying and operating the CTOP middleware on
Ubuntu. Supersedes the deployment sections of `README.md` and
`HOW_TO_RUN.md`, and the retired `quick-deploy.sh` / `server-setup.sh`
scripts.

Target: **Ubuntu 22.04 or 24.04**.

---

## 1. First-time server setup

Three steps. The bootstrap script is idempotent — safe to re-run after a
change or a partial failure.

### 1.1 Clone the repository

```bash
sudo mkdir -p /opt/ctop-middleware
sudo chown "$USER:$USER" /opt/ctop-middleware
git clone <repo-url> /opt/ctop-middleware
cd /opt/ctop-middleware
```

### 1.2 Put the three secret files in place

These are **never** committed, never baked into an image, and never shipped
in an archive. Copy them from a machine that already has them:

```bash
scp .env encryption.key service-account-key.json <user>@<server>:/opt/ctop-middleware/
```

| File | Can it be regenerated? |
|---|---|
| `.env` | Yes — see `.env.example` |
| `service-account-key.json` | Yes — Firebase console, new service-account key |
| `encryption.key` | **No.** See the warning below |

> **`encryption.key` cannot be regenerated.** Every device credential stored
> in Firestore is encrypted with it. If it is lost, or if the application is
> allowed to generate a replacement, none of those credentials can ever be
> decrypted again, and the only recovery is asking the CTOP team to reissue
> every node token. Back it up somewhere off this server before you do
> anything else.

### 1.3 Run the bootstrap

```bash
sudo ./scripts/bootstrap-ubuntu.sh
```

To also register the CI runner so pushes to `main` deploy themselves
(token from repo *Settings → Actions → Runners → New self-hosted runner*;
it expires in about an hour):

```bash
RUNNER_TOKEN=<token> REPO_URL=<repo-url> \
  sudo ./scripts/bootstrap-ubuntu.sh --with-runner
```

What it does:

| | |
|---|---|
| Docker | Installs `docker-ce` + compose/buildx plugins from Docker's **official apt repository** with its signing key — not `curl \| sudo sh` |
| Firewall | `ufw` allowing SSH, 80, 443. Allows SSH *before* enabling, so it cannot lock you out |
| Updates | `unattended-upgrades` for security patches. Automatic reboots deliberately **off** — this host runs a data pipeline |
| systemd | `ctop-middleware.service` for boot ordering, plus timers for backups and the watchdog |
| Log rotation | Host logs via logrotate; container logs capped in compose (10 MB × 5) |
| Permissions | Sets `instance/` ownership to match the container user; `chmod 600` on secrets |

Options: `--deploy-dir PATH`, `--with-runner`, `--skip-firewall`,
`--skip-start`.

---

## 2. Deploying

```bash
make deploy          # build, start, verify health, auto-rollback on failure
```

Or just `git push origin main`, which runs the same script via CI.

The sequence:

1. **Preflight** — secrets present, correct type, Docker usable
2. Resolve the commit and tag the image with its short SHA
3. Run the tests
4. **Record the currently running tag** as the rollback target
5. Build `ctop-middleware:<sha>`
6. Start it
7. **Wait for `/health`** (up to 150 s)
8. If it never becomes healthy → **put the previous image back automatically**, verify *that* is healthy, and report failure

The old `deploy.sh` ended at step 6 and printed `docker compose ps`. A new
image that started and then crash-looped was reported as a successful
deploy.

```bash
./scripts/deploy.sh --ref <commit>     # deploy a specific commit
./scripts/deploy.sh --no-rollback      # keep a broken version up to debug it
./scripts/deploy.sh --skip-tests       # tests already ran in CI
```

---

## 3. Rolling back

Automatic rollback only covers a version that fails its health check. For a
version that is *healthy but wrong*, decide yourself:

```bash
make rollback             # back to the previous image
make rollback-list        # what is available
./scripts/rollback.sh --to <tag>
```

Running `make rollback` again returns you to where you were — the tag you
leave becomes the new rollback target.

If the target image has been pruned, rebuild that commit instead:

```bash
./scripts/deploy.sh --ref <tag>
```

---

## 4. Day-to-day

| Command | |
|---|---|
| `make status` | Container state plus a health probe |
| `make logs` | Follow the middleware log |
| `make health` | Block until healthy, fail after a timeout |
| `make restart` | Restart the app container only |
| `make shell` | Shell inside the running container |
| `make backup` | Back up the key, Firebase key and SQLite mirror |
| `make watchdog` | Check now whether deliveries are failing |
| `sudo systemctl status ctop-middleware` | Service state |
| `sudo journalctl -u ctop-middleware -n 50` | Boot and start/stop history |

Local development, with live code mounting and debug logging:

```bash
make dev
```

This loads `docker-compose.dev.yml` **explicitly**. The file is deliberately
not called `docker-compose.override.yml`, because Compose loads an override
file automatically and silently — a developer overlay left on a server would
quietly reconfigure production.

> Running locally while the deployed instance is live? `docker-compose.dev.yml`
> sets `EMQX_CLIENT_ID_PREFIX=local-dev`. Without it, both instances connect
> to the MQTT broker with the same client IDs and the broker evicts each in
> turn every few seconds, breaking telemetry on **both** sides.

---

## 5. Monitoring

A container health check cannot tell you that deliveries have stopped: if
the far end is broken, the service is still perfectly healthy. In October
2026 CTOP's API returned 503 for about twelve hours while everything on this
side looked fine, and the outage was found only because someone read the
logs.

`scripts/watchdog.sh` runs every 15 minutes via `ctop-watchdog.timer` and
compares the delivery counters from `/api/diagnostics` between runs:

| Condition | Result |
|---|---|
| `/health` not answering | **CRITICAL** — service down |
| Failures climbing, successes flat | **CRITICAL** — deliveries failing (usually the far end) |
| Deliveries resume after a failure | **RECOVERED** notice |
| No activity at all for ~2 hours | **WARNING** — sensors quiet or the scheduler stalled |
| Counters went backwards | Treated as a restart, re-baselined — not an alert |

To receive alerts, set a webhook in `.env` (Slack-compatible
`{"text": "..."}`):

```bash
WATCHDOG_WEBHOOK_URL=https://hooks.slack.com/services/...
WATCHDOG_FAIL_DELTA=5      # failures per interval that count as broken
WATCHDOG_IDLE_CYCLES=8     # idle checks before it says anything
```

Without a webhook it still logs to journald: `journalctl -u ctop-watchdog`.

---

## 6. Backups

`ctop-backup.timer` runs nightly at 02:30 and keeps 30 days in `backups/`.
SQLite files are snapshotted with `sqlite3 .backup` rather than copied,
because a plain copy of a live database can capture a torn write.

```bash
make backup                                  # run one now
tar -tzf backups/ctop-backup-<stamp>.tar.gz  # inspect
```

Restore: stop the stack, extract over the deployment directory, start again.

```bash
sudo systemctl stop ctop-middleware
tar -xzf backups/ctop-backup-<stamp>.tar.gz -C /opt/ctop-middleware
sudo systemctl start ctop-middleware
```

> These backups live on the same server as the thing they protect. A disk
> failure or a destroyed VM takes both. Copy them off the box periodically.

---

## 7. Configuration reference

Full list in `.env.example`. The ones that matter most:

| Variable | Notes |
|---|---|
| `SECRET_KEY` | Required. Startup **fails** without it in production |
| `USE_FIREBASE` | `true` in real deployments; `false` selects the SQLite fallback |
| `FIREBASE_PROJECT_ID` | Must match the project in `service-account-key.json` |
| `SCHEDULER_INTERVAL_SECONDS` | Default 15 |
| `EMQX_CLIENT_ID_PREFIX` | Leave unset in production. Set it for any second instance |
| `APP_UID` / `APP_GID` | Must match the owner of `instance/`. Default 1000 |
| `GUNICORN_WORKERS` | Defaults to `CPUs × 2 + 1`, capped at 8 |

---

## 8. Troubleshooting

**Preflight says a secret file is a directory.**
Docker creates an empty directory when a bind-mounted file is missing. Remove
it and restore the real file — and if it was `encryption.key`, do not start
the app until the original is back.

```bash
sudo rmdir /opt/ctop-middleware/encryption.key
```

**Container cannot write `instance/`.** The bind mount keeps the host's
numeric ownership and the container is not root:

```bash
sudo chown -R 1000:1000 instance
# or build to match the host user:
APP_UID=$(id -u) APP_GID=$(id -g) make build
```

**`bad interpreter` running a script.** CRLF line endings. `.gitattributes`
forces LF; re-checkout the file:

```bash
git checkout -- scripts/
```

**Deploy fails, service still up.** Automatic rollback worked. Read the
failing logs, fix, redeploy:

```bash
docker compose logs --tail=120 middleware
```

**Rollback also failed.** The problem is probably not the image — check
Firestore reachability, `bash scripts/preflight.sh`, and `df -h`.

**Deliveries failing but the app is healthy.** Almost always the far end:

```bash
docker compose logs --tail=50 middleware | grep 'CTOP SEND ERROR'
curl -i -X POST https://dev-ctop.iiit.ac.in/api/nodes/create-cin/<node> \
     -H 'Content-Type: application/json' -d '{}'
```

An unauthenticated POST returning `401 "token is missing"` means their API is
alive and the problem is credentials; `502`/`503` means their service or
database is down and nothing on this side will help.
