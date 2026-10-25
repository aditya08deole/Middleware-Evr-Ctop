#!/usr/bin/env bash
#
# Refuse to start if the host is not in a state where the containers can run
# correctly. Run automatically by `make up` / `make deploy` and by the deploy
# workflow, or by hand: ./scripts/preflight.sh
#
# The check that matters most is encryption.key. docker-compose mounts it as
# a single file:
#
#     - ./encryption.key:/app/encryption.key:ro
#
# If that path does not exist on the host, Docker does not fail — it helpfully
# CREATES AN EMPTY DIRECTORY there. The container then sees a directory where
# it expects a key file, utils/encryption.py finds no readable key, and in a
# non-production config it generates a brand-new one. Every device credential
# in Firestore was encrypted with the OLD key, so none of them can ever be
# decrypted again: the only recovery is reissuing all 20 node tokens from
# CTOP. One missing file, silently unrecoverable. Hence this gate.

set -euo pipefail
cd "$(dirname "$0")/.."

FAIL=0
WARN=0

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
ylw()  { printf '\033[33m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }

fail() { red   "  FAIL  $*"; FAIL=$((FAIL + 1)); }
warn() { ylw   "  WARN  $*"; WARN=$((WARN + 1)); }
ok()   { grn   "  ok    $*"; }

echo "Preflight checks"
echo "----------------"

# --- 1. Secret files must exist AND be regular files, not directories ---
for f in .env encryption.key service-account-key.json; do
    if [ -d "$f" ]; then
        fail "$f is a DIRECTORY. Docker created it because the file was missing"
        red  "        when a container started. Remove it and restore the real file:"
        red  "          rmdir '$f' && cp /path/to/real/$f ./$f"
        if [ "$f" = "encryption.key" ]; then
            red  "        Do NOT let the app generate a new encryption.key — doing so"
            red  "        permanently orphans every stored device credential."
        fi
    elif [ ! -f "$f" ]; then
        fail "$f is missing. Copy it onto this server before deploying."
    elif [ ! -s "$f" ]; then
        fail "$f exists but is EMPTY — that is as bad as missing."
    else
        ok "$f present"
    fi
done

# --- 2. encryption.key must look like a real Fernet key ---
# A Fernet key is 32 bytes, urlsafe-base64 encoded => 44 characters.
if [ -f encryption.key ] && [ -s encryption.key ]; then
    key_len=$(tr -d '\n\r' < encryption.key | wc -c | tr -d ' ')
    if [ "$key_len" -ne 44 ]; then
        warn "encryption.key is ${key_len} chars; a Fernet key is normally 44."
        warn "       If credentials fail to decrypt, this is the first thing to check."
    else
        ok "encryption.key length looks correct (44 chars)"
    fi
fi

# --- 2b. encryption.key must be the SAME key as last time ---
#
# Length and presence are not enough. A freshly generated key is a perfectly
# valid 44-character Fernet key, so every check above passes while it is
# unable to decrypt a single stored credential — the loud, fixable "key is
# missing" error silently becomes "key is fine" plus permanent data loss.
# Several realistic paths lead there: the test suite generating one when the
# file is absent, a half-finished restore, or Docker turning a missing bind
# mount into an empty directory that somebody then "fixed" by making a new
# key.
#
# So the first successful run records a fingerprint, and every later run
# refuses to continue if it changes. Only a hash is stored, never the key.
#
# To accept a DELIBERATE key change (a real rotation, where you have already
# re-encrypted or reissued every credential):
#     rm .deploy-state/encryption-key.sha256
KEY_FP_FILE=".deploy-state/encryption-key.sha256"
if [ -f encryption.key ] && [ -s encryption.key ] && ! [ -d encryption.key ]; then
    hash_cmd=""
    if command -v sha256sum >/dev/null 2>&1; then
        hash_cmd="sha256sum"
    elif command -v shasum >/dev/null 2>&1; then
        hash_cmd="shasum -a 256"
    fi

    if [ -z "$hash_cmd" ]; then
        warn "no sha256sum/shasum available — cannot verify the key is unchanged"
    else
        current_fp=$(tr -d '\n\r' < encryption.key | $hash_cmd | awk '{print $1}')
        if [ -f "$KEY_FP_FILE" ]; then
            recorded_fp=$(tr -d '\n\r' < "$KEY_FP_FILE")
            if [ "$current_fp" = "$recorded_fp" ]; then
                ok "encryption.key is the same key as previous deploys"
            else
                fail "encryption.key HAS CHANGED since the last deploy."
                red  "        recorded: ${recorded_fp}"
                red  "        current : ${current_fp}"
                red  "        A different key cannot decrypt any credential already"
                red  "        stored in Firestore. Restore the original before"
                red  "        deploying — the newest backup has it:"
                red  "          ls -t backups/ctop-backup-*.tar.gz | head -1"
                red  "        If this change is intentional and every credential has"
                red  "        been reissued, accept it with:"
                red  "          rm ${KEY_FP_FILE}"
            fi
        else
            mkdir -p "$(dirname "$KEY_FP_FILE")"
            printf '%s' "$current_fp" > "$KEY_FP_FILE"
            ok "recorded this encryption.key's fingerprint for future checks"
        fi
    fi
fi

# --- 3. service-account-key.json must be valid JSON with the expected shape ---
#
# Finding an interpreter is done by actually RUNNING it, not by `command -v`.
# On Windows/Git Bash, `python3` resolves to a Microsoft Store stub that sits
# on PATH, passes `command -v`, and then exits 49 with "Python was not
# found" — which made this check report the key as invalid when it was
# perfectly good. A preflight gate that can block a deploy with a false
# failure is worse than no gate, so an unusable interpreter now downgrades
# to a skipped check rather than a hard failure.
PY=""
for candidate in python3 python; do
    if "$candidate" -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done

if [ -f service-account-key.json ] && [ -s service-account-key.json ]; then
    if [ -n "$PY" ]; then
        if "$PY" - <<'PY' 2>/dev/null
import json, sys

with open("service-account-key.json", encoding="utf-8") as fh:
    data = json.load(fh)

missing = [
    field
    for field in ("type", "project_id", "private_key", "client_email")
    if not data.get(field)
]
if missing:
    print("missing fields: " + ", ".join(missing))
    sys.exit(1)
if data["type"] != "service_account":
    print("type is %r, expected 'service_account'" % data["type"])
    sys.exit(1)
sys.exit(0)
PY
        then
            ok "service-account-key.json is valid service-account JSON"
        else
            fail "service-account-key.json is not valid service-account JSON (run: $PY -m json.tool service-account-key.json)"
        fi
    else
        warn "no working python interpreter found — skipped JSON validation"
    fi
fi

# --- 4. .env must define the variables the app refuses to start without ---
if [ -f .env ]; then
    for key in SECRET_KEY USE_FIREBASE FIREBASE_PROJECT_ID; do
        if grep -qE "^${key}=.+" .env; then
            ok ".env defines ${key}"
        else
            fail ".env is missing ${key} (config.py raises on startup without it)"
        fi
    done
fi

# --- 5. instance/ must be writable by the container's user ---
# The container runs as APP_UID (default 1000). A bind mount keeps the HOST's
# numeric ownership, so a root-owned instance/ makes the container unable to
# write the SQLite mirror or take the scheduler lock.
APP_UID="${APP_UID:-1000}"
mkdir -p instance
if [ -d instance ]; then
    owner_uid=$(stat -c '%u' instance 2>/dev/null || stat -f '%u' instance 2>/dev/null || echo "?")
    if [ "$owner_uid" = "?" ]; then
        warn "could not determine owner of instance/ on this platform"
    elif [ "$owner_uid" = "$APP_UID" ]; then
        ok "instance/ is owned by uid ${APP_UID} (matches container user)"
    else
        warn "instance/ is owned by uid ${owner_uid}, container runs as ${APP_UID}."
        warn "       Fix with:  sudo chown -R ${APP_UID}:${APP_UID} instance"
    fi
fi

# --- 6. Secret file permissions ---
for f in .env encryption.key service-account-key.json; do
    [ -f "$f" ] || continue
    mode=$(stat -c '%a' "$f" 2>/dev/null || stat -f '%Lp' "$f" 2>/dev/null || echo "")
    case "$mode" in
        600|400|"") ;;
        *) warn "$f has mode ${mode}; tighten with: chmod 600 $f" ;;
    esac
done

# --- 7. Docker must be present and usable ---
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        ok "docker is running and reachable"
    else
        fail "docker is installed but not reachable (daemon stopped, or add your user to the 'docker' group)"
    fi
    if docker compose version >/dev/null 2>&1; then
        ok "docker compose v2 plugin available"
    else
        fail "'docker compose' not available — install the docker-compose-plugin package"
    fi
else
    fail "docker is not installed. Run scripts/bootstrap-ubuntu.sh first."
fi

echo "----------------"
if [ "$FAIL" -gt 0 ]; then
    red "Preflight FAILED: ${FAIL} blocking problem(s), ${WARN} warning(s). Not starting."
    exit 1
fi
if [ "$WARN" -gt 0 ]; then
    ylw "Preflight passed with ${WARN} warning(s)."
else
    grn "Preflight passed."
fi
