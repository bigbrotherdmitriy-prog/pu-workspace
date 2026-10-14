#!/usr/bin/env bash
# Off-site, encrypted, rotated backup of the production PostgreSQL DB.
#
#   pg_dump -Fc (in the DB container) -> gpg --symmetric AES256 (passphrase FILE)
#   -> upload to the owner's Google Drive through the backend's own Google client
#   -> verify size + md5Checksum reported by Drive -> retention (7 daily,
#   4 weekly/Sunday, 6 monthly/1st), only after a verified upload.
#
# Configuration (env, all optional): PU_BACKUP_PASSPHRASE_FILE,
# PU_BACKUP_GOOGLE_TOKEN_ID, PU_BACKUP_DRIVE_FOLDER, PU_BACKUP_DB_CONTAINER,
# PU_BACKUP_BACKEND_CONTAINER, PU_BACKUP_WORK_DIR, PU_BACKUP_KEEP_DAILY/WEEKLY/MONTHLY.
set -eu
set -o pipefail
umask 077

. "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)/common.sh"

WORK_PARENT=${PU_BACKUP_WORK_DIR:-/var/tmp}
WORK=

finish() {
  status=$?
  remote_cleanup
  [ -n "$WORK" ] && rm -rf "$WORK"
  if [ "$status" -ne 0 ]; then
    log "FAIL offsite backup (exit $status)"
    notify_failure "PU Workspace: резервное копирование БД в Google Drive не выполнено. Проверьте journalctl -u pu-offsite-backup на сервере."
  fi
  exit "$status"
}
trap finish EXIT

require_passphrase_file
WORK=$(mktemp -d "$WORK_PARENT/puw-offsite.XXXXXX")
mkdir -m 700 "$WORK/gnupg"
gpg_base() {
  gpg --homedir "$WORK/gnupg" --batch --yes --quiet --no-tty \
    --pinentry-mode loopback --passphrase-file "$PASSPHRASE_FILE" "$@"
}

NAME="pu_workspace-$(date -u +%Y%m%dT%H%M%SZ).dump.gpg"
FILE="$WORK/$NAME"

# Counts are taken just before the dump, so the dump holds at least these rows.
COUNTS=$(prod_row_counts)
[ -n "$COUNTS" ] || die "could not read prod row counts"

log "dumping $DB_NAME and encrypting to $NAME"
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc \
  | gpg_base --symmetric --cipher-algo AES256 --compress-algo none --output "$FILE"

# Integrity: the encrypted file must decrypt to a readable pg_dump archive.
# pg_restore --list stops after the TOC; drain the rest so gpg decrypts (and
# authenticates) the whole file instead of dying on SIGPIPE.
gpg_base --decrypt "$FILE" \
  | { docker exec -i "$DB_CONTAINER" pg_restore --list >/dev/null && cat >/dev/null; } \
  || die "encrypted dump failed the local decrypt/pg_restore --list check"

SIZE=$(stat -c '%s' "$FILE")
MD5=$(md5sum "$FILE" | cut -d' ' -f1)
[ "$SIZE" -gt 1024 ] || die "dump is suspiciously small ($SIZE bytes)"

remote_setup
docker cp -q "$FILE" "$BACKEND_CONTAINER:$REMOTE_DIR/$NAME"
RESULT=$(drive_ops upload "$REMOTE_DIR/$NAME" "$NAME" "$SIZE" "$MD5" "$COUNTS") \
  || die "upload or Drive verification failed"
docker exec "$BACKEND_CONTAINER" rm -f "$REMOTE_DIR/$NAME"
log "uploaded and verified: $RESULT"

ROTATION=$(drive_ops rotate) || die "retention step failed (new backup is uploaded)"
log "retention: $ROTATION"

echo "OK offsite backup $NAME size=$SIZE md5=$MD5"
