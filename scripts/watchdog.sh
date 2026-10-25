#!/usr/bin/env bash
#
# Alert when the pipeline stops delivering readings to CTOP.
#
# WHY THIS EXISTS
# On 24-25 Oct 2026, CTOP's dev API returned HTTP 503 "Database service
# temporarily unavailable" for roughly twelve hours. Throughout, the
# middleware was healthy, the containers were up, /health returned 200, the
# host was fine — and not a single reading was delivered. Nothing raised a
# flag; the outage was found by a person happening to look at the logs.
#
# A container health check cannot catch that, because the service genuinely
# IS healthy. The thing that is broken is at the far end. The only signal is
# that failed deliveries keep climbing while successful ones stay flat, which
# is exactly what this watchdog watches.
#
# Run every 15 minutes by ctop-watchdog.timer, or by hand: make watchdog
#
# Configuration (all optional; put them in .env):
#   WATCHDOG_WEBHOOK_URL   HTTP endpoint to POST alerts to (Slack-compatible
#                          JSON: {"text": "..."}). Unset = log only.
#   WATCHDOG_FAIL_DELTA    failed sends in one interval that counts as broken
#                          (default 5)
#   WATCHDOG_IDLE_CYCLES   consecutive fully-idle checks before mentioning it
#                          (default 8, i.e. ~2 hours at a 15 min interval)
#
# Exit codes: 0 = healthy or merely idle, 1 = alerting. The systemd timer
# ignores the code; it is there for running this by hand or from other tools.

set -euo pipefail
cd "$(dirname "$0")/.."

BASE_URL="${WATCHDOG_BASE_URL:-http://localhost:8090}"
FAIL_DELTA="${WATCHDOG_FAIL_DELTA:-5}"
IDLE_CYCLES="${WATCHDOG_IDLE_CYCLES:-8}"
STATE_DIR=".deploy-state"
STATE_FILE="${STATE_DIR}/watchdog-state"
WEBHOOK="${WATCHDOG_WEBHOOK_URL:-}"

mkdir -p "$STATE_DIR"

NOW="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
HOST="$(hostname 2>/dev/null || echo unknown)"

log() { printf '[watchdog %s] %s\n' "$NOW" "$*"; }

# Find a JSON parser by actually RUNNING each candidate, never by checking
# whether the name resolves. `command -v python3` succeeds on Windows/Git
# Bash against a Microsoft Store stub that then exits 49 — which silently
# broke counter parsing here and made the watchdog report "could not parse"
# on every run instead of watching anything. Same trap as in preflight.sh.
PY=""
for candidate in python3 python; do
    if "$candidate" -c 'import json,sys' >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done
HAVE_JQ=0
if command -v jq >/dev/null 2>&1 && printf '{}' | jq -e . >/dev/null 2>&1; then
    HAVE_JQ=1
fi

# Send an alert. Always logged (journald picks it up); POSTed if a webhook
# is configured. A webhook failure must never make the watchdog itself fail.
notify() {
    local severity="$1" message="$2"
    log "${severity}: ${message}"
    [ -n "$WEBHOOK" ] || return 0
    local text payload
    text="[${severity}] CTOP middleware on ${HOST}: ${message}"
    if [ -n "$PY" ]; then
        payload="$("$PY" -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' "$text")"
    else
        # Escape the few characters that would break the JSON string.
        payload="{\"text\":\"$(printf '%s' "$text" | sed 's/\\/\\\\/g; s/"/\\"/g')\"}"
    fi
    if curl -fsS -m 15 -X POST -H 'Content-Type: application/json' \
            -d "$payload" "$WEBHOOK" >/dev/null 2>&1; then
        log "alert delivered to webhook"
    else
        log "WARNING: could not deliver alert to the configured webhook"
    fi
}

# --- read previous state ---
PREV_OK=""; PREV_FAIL=""; PREV_IDLE=0; PREV_STATE="unknown"
if [ -f "$STATE_FILE" ]; then
    # shellcheck disable=SC1090
    . "$STATE_FILE" 2>/dev/null || true
    PREV_OK="${last_ok:-}"
    PREV_FAIL="${last_fail:-}"
    PREV_IDLE="${idle_count:-0}"
    PREV_STATE="${last_state:-unknown}"
fi

write_state() {
    cat > "$STATE_FILE" <<EOF
# Written by scripts/watchdog.sh — do not edit by hand.
last_checked='${NOW}'
last_ok='${1}'
last_fail='${2}'
idle_count='${3}'
last_state='${4}'
EOF
}

# --- 1. is the service answering at all? ---
if ! curl -fsS -m 10 "${BASE_URL}/health" >/dev/null 2>&1; then
    notify CRITICAL "/health is not responding at ${BASE_URL}. The service may be down — check: systemctl status ctop-middleware && docker compose logs --tail=50 middleware"
    write_state "$PREV_OK" "$PREV_FAIL" "$PREV_IDLE" "down"
    exit 1
fi

# --- 2. read the delivery counters ---
DIAG="$(curl -fsS -m 15 "${BASE_URL}/api/diagnostics" 2>/dev/null || true)"
if [ -z "$DIAG" ]; then
    notify WARNING "/health is up but /api/diagnostics returned nothing; cannot assess delivery health this cycle."
    write_state "$PREV_OK" "$PREV_FAIL" "$PREV_IDLE" "$PREV_STATE"
    exit 0
fi

extract() {
    local key="$1"
    if [ "$HAVE_JQ" -eq 1 ]; then
        printf '%s' "$DIAG" | jq -r ".telemetry.${key} // empty" 2>/dev/null
        return 0
    fi
    if [ -n "$PY" ]; then
        printf '%s' "$DIAG" | "$PY" -c "
import json, sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

value = (data.get('telemetry') or {}).get('$key')
if value is not None:
    print(value)
" 2>/dev/null
        return 0
    fi
    return 0
}

CUR_OK="$(extract successful_sends | head -1)"
CUR_FAIL="$(extract failed_sends | head -1)"

if ! printf '%s' "${CUR_OK:-}" | grep -qE '^[0-9]+$' || \
   ! printf '%s' "${CUR_FAIL:-}" | grep -qE '^[0-9]+$'; then
    notify WARNING "could not parse delivery counters from /api/diagnostics (install jq for reliable parsing)."
    write_state "$PREV_OK" "$PREV_FAIL" "$PREV_IDLE" "$PREV_STATE"
    exit 0
fi

log "counters: successful_sends=${CUR_OK} failed_sends=${CUR_FAIL}"

# First ever run: record a baseline. Judging anything from a single sample
# would just alert on whatever happened to have accumulated historically.
if ! printf '%s' "${PREV_OK:-}" | grep -qE '^[0-9]+$'; then
    log "no previous sample — recording a baseline, no judgement this cycle"
    write_state "$CUR_OK" "$CUR_FAIL" 0 "baseline"
    exit 0
fi

# Counters reset when the process restarts. A lower value than last time
# means a restart, not a problem.
if [ "$CUR_OK" -lt "$PREV_OK" ] || [ "$CUR_FAIL" -lt "$PREV_FAIL" ]; then
    log "counters went backwards — the app restarted since the last check; re-baselining"
    write_state "$CUR_OK" "$CUR_FAIL" 0 "baseline"
    exit 0
fi

OK_DELTA=$(( CUR_OK - PREV_OK ))
FAIL_DELTA_ACTUAL=$(( CUR_FAIL - PREV_FAIL ))
log "since last check: +${OK_DELTA} delivered, +${FAIL_DELTA_ACTUAL} failed"

# --- 3. the condition that matters: failing, not merely quiet ---
#
# Two levels, because "nothing is getting through" and "most of it is not
# getting through" are both real and need different urgency. An earlier
# version of this check required successes to be EXACTLY zero, which would
# have caught the twelve-hour total outage but silently ignored a partial
# one — e.g. +4 delivered against +56 failed, which is plainly broken. Note
# that a handful of failures is normal: CTOPService retries each payload up
# to 3 times, so one bad payload logs several failures. Hence comparing the
# two deltas rather than alerting on any failure at all.
if [ "$FAIL_DELTA_ACTUAL" -ge "$FAIL_DELTA" ] && [ "$OK_DELTA" -eq 0 ]; then
    notify CRITICAL "NOTHING is reaching CTOP: +${FAIL_DELTA_ACTUAL} failures and 0 successes since the last check. The app itself is healthy, so this is most likely the CTOP endpoint being down. Check: docker compose logs --tail=40 middleware | grep 'CTOP SEND ERROR'"
    write_state "$CUR_OK" "$CUR_FAIL" 0 "failing"
    exit 1
fi

if [ "$FAIL_DELTA_ACTUAL" -ge "$FAIL_DELTA" ] && [ "$FAIL_DELTA_ACTUAL" -gt "$OK_DELTA" ]; then
    notify WARNING "deliveries to CTOP are mostly failing: +${FAIL_DELTA_ACTUAL} failures against only +${OK_DELTA} successes since the last check. Some nodes are getting through and others are not — check for per-node 401/404 responses: docker compose logs --tail=60 middleware | grep 'CTOP SEND ERROR'"
    write_state "$CUR_OK" "$CUR_FAIL" 0 "failing"
    exit 1
fi

# --- 4. recovery notice ---
if [ "$OK_DELTA" -gt 0 ] && { [ "$PREV_STATE" = "failing" ] || [ "$PREV_STATE" = "down" ]; }; then
    notify RECOVERED "deliveries have resumed: +${OK_DELTA} successful since the last check."
    write_state "$CUR_OK" "$CUR_FAIL" 0 "healthy"
    exit 0
fi

# --- 5. completely idle: possible, and often legitimate ---
# Stale or dead field sensors produce no readings to send, which is not a
# delivery fault. Mentioned only after a long run of idle checks, and as a
# warning rather than a critical, so it never cries wolf.
if [ "$OK_DELTA" -eq 0 ] && [ "$FAIL_DELTA_ACTUAL" -eq 0 ]; then
    IDLE=$(( PREV_IDLE + 1 ))
    if [ "$IDLE" -ge "$IDLE_CYCLES" ]; then
        notify WARNING "no delivery activity at all for ${IDLE} consecutive checks. Either no sensor is reporting, or the scheduler has stalled. Check device status on the dashboard."
        write_state "$CUR_OK" "$CUR_FAIL" 0 "idle"
        exit 0
    fi
    log "idle (${IDLE}/${IDLE_CYCLES} before this is worth mentioning)"
    write_state "$CUR_OK" "$CUR_FAIL" "$IDLE" "idle"
    exit 0
fi

log "healthy"
write_state "$CUR_OK" "$CUR_FAIL" 0 "healthy"
