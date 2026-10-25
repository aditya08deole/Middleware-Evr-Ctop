#!/usr/bin/env bash
#
# Back up the state that cannot be rebuilt from the repository:
#
#   encryption.key            Irreplaceable. Every device credential stored
#                             in Firestore is encrypted with it. There is no
#                             recovery from losing it other than asking the
#                             CTOP team to reissue all node tokens.
#   service-account-key.json  Reissuable from the Firebase console, but
#                             losing it means downtime until someone does.
#   instance/*.db             The local SQLite mirror of the device list and
#                             reading history. Rebuildable from Firestore,
#                             but restoring it avoids a cold resync.
#   .env                      Configuration, including SECRET_KEY.
#
# Run nightly by ctop-backup.timer, or by hand with: make backup
#
# Deliberately NOT included: application code (it is in git) and container
# logs (rotated, and of no value once rotated).

set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-30}"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="${BACKUP_DIR}/ctop-backup-${STAMP}.tar.gz"

mkdir -p "$BACKUP_DIR"

echo "Backing up to ${ARCHIVE}"

# Collect what exists; a missing optional file is not a failure, but a
# missing encryption.key is worth shouting about.
FILES=()
for f in encryption.key service-account-key.json .env; do
    if [ -f "$f" ]; then
        FILES+=("$f")
    else
        echo "  WARNING: $f not found — not included in this backup" >&2
    fi
done

if [ ! -f encryption.key ]; then
    echo "  CRITICAL: encryption.key is missing from this deployment." >&2
    echo "            If the app has already generated a replacement, the" >&2
    echo "            original credentials are already unrecoverable." >&2
fi

# The SQLite mirror is written by a live process. sqlite3's .backup takes a
# consistent snapshot of a database that is being written to; a plain file
# copy of a live SQLite file can capture a torn write. Fall back to cp only
# if sqlite3 is unavailable.
TMPDIR_BK="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_BK"' EXIT

if [ -d instance ]; then
    mkdir -p "$TMPDIR_BK/instance"
    shopt -s nullglob
    for db in instance/*.db; do
        base="$(basename "$db")"
        if command -v sqlite3 >/dev/null 2>&1; then
            if sqlite3 "$db" ".backup '$TMPDIR_BK/instance/$base'" 2>/dev/null; then
                echo "  snapshotted $base (consistent sqlite3 backup)"
            else
                cp -p "$db" "$TMPDIR_BK/instance/$base"
                echo "  copied $base (sqlite3 backup failed, used plain copy)"
            fi
        else
            cp -p "$db" "$TMPDIR_BK/instance/$base"
            echo "  copied $base (sqlite3 not installed, used plain copy)"
        fi
    done
    shopt -u nullglob
fi

if [ "${#FILES[@]}" -eq 0 ] && [ ! -d "$TMPDIR_BK/instance" ]; then
    echo "Nothing to back up." >&2
    exit 1
fi

# Build the archive from the secrets in place plus the snapshotted databases.
tar -czf "$ARCHIVE" \
    ${FILES[@]+"${FILES[@]}"} \
    $( [ -d "$TMPDIR_BK/instance" ] && echo "-C $TMPDIR_BK instance" )

chmod 600 "$ARCHIVE"
SIZE="$(du -h "$ARCHIVE" | cut -f1)"
echo "  wrote ${ARCHIVE} (${SIZE}, mode 600)"

# Verify the archive is readable rather than assuming tar succeeded — a
# corrupt backup discovered during a restore is not a backup.
if tar -tzf "$ARCHIVE" >/dev/null 2>&1; then
    echo "  verified: archive listing is readable"
else
    echo "  ERROR: archive failed verification — removing it" >&2
    rm -f "$ARCHIVE"
    exit 1
fi

# Retention.
DELETED="$(find "$BACKUP_DIR" -name 'ctop-backup-*.tar.gz' -type f -mtime "+${RETAIN_DAYS}" -print -delete | wc -l | tr -d ' ')"
[ "$DELETED" -gt 0 ] && echo "  pruned ${DELETED} backup(s) older than ${RETAIN_DAYS} days"

COUNT="$(find "$BACKUP_DIR" -name 'ctop-backup-*.tar.gz' -type f | wc -l | tr -d ' ')"
echo "Done. ${COUNT} backup(s) retained in ${BACKUP_DIR}."
echo
echo "NOTE: these backups sit on the same server as the thing they protect."
echo "      Copy them somewhere else periodically — a disk failure or a lost"
echo "      VM takes the backups with it:"
echo "        scp ${BACKUP_DIR}/ctop-backup-*.tar.gz <somewhere-else>:"
echo
echo "To restore: tar -xzf <archive> -C <deploy-dir>  (stop the stack first)"
