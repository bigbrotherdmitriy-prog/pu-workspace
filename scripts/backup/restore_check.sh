#!/usr/bin/env bash
# Automated restore check of the newest off-site backup.
#
# Downloads the newest backup from Google Drive, verifies its md5, decrypts it,
# restores it into a throwaway postgres:16-alpine container (--network none,
# --memory=600m) and compares key-table row counts with the prod counts recorded
# at backup time (stored on the Drive file). Restored >= recorded is a PASS;
# a missing or empty table is a FAIL. Prints one PASS/FAIL line, always removes
# the container. Never writes to the prod DB.
set -eu
set -o pipefail
umask 077

. "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)/common.sh"

WORK_PARENT=${PU_BACKUP_WORK_DIR:-/var/tmp}
RESTORE_IMAGE=${PU_RESTORE_IMAGE:-postgres:16-alpine}
RESTORE_CONTAINER=puw-restore-check-$$
WORK=
STARTED=

finish() {
  status=$?
  remote_cleanup
  [ -n "$STARTED" ] && docker rm -f "$RESTORE_CONTAINER" >/dev/null 2>&1
  [ -n "$WORK" ] && rm -rf "$WORK"
  if [ "$status" -ne 0 ]; then
    echo "FAIL restore check${FAIL_REASON:+: $FAIL_REASON}"
    notify_failure "PU Workspace: ежемесячная проверка восстановления резервной копии БД не пройдена. Проверьте journalctl -u pu-restore-check на сервере."
  fi
  exit "$status"
}
FAIL_REASON=
trap finish EXIT

fail() {
  FAIL_REASON=$1
  log "ERROR: $1"
  exit 1
}

require_passphrase_file
WORK=$(mktemp -d "$WORK_PARENT/puw-restore.XXXXXX")
mkdir -m 700 "$WORK/gnupg"

remote_setup
META=$(drive_ops latest) || fail "no backup found on Drive"
field() {
  printf '%s' "$META" | python3 -c 'import json,sys; v=json.load(sys.stdin)[sys.argv[1]]; print(v if not isinstance(v, dict) else json.dumps(v))' "$1"
}
FILE_ID=$(field id)
NAME=$(field name)
EXPECTED_MD5=$(field md5)
RECORDED=$(field counts)
log "newest backup: $NAME"

drive_ops download "$FILE_ID" > "$WORK/$NAME" || fail "download failed"
remote_cleanup
ACTUAL_MD5=$(md5sum "$WORK/$NAME" | cut -d' ' -f1)
[ "$ACTUAL_MD5" = "$EXPECTED_MD5" ] || fail "md5 mismatch after download"

STARTED=1
docker run -d --name "$RESTORE_CONTAINER" --network none --memory=600m \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=restore_check \
  "$RESTORE_IMAGE" >/dev/null
for _ in $(seq 1 60); do
  # TCP probe: during the init phase postgres listens on the socket only.
  if docker exec "$RESTORE_CONTAINER" pg_isready -U postgres -d restore_check -h 127.0.0.1 >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$RESTORE_CONTAINER" pg_isready -U postgres -d restore_check -h 127.0.0.1 >/dev/null \
  || fail "throwaway postgres did not start"

gpg --homedir "$WORK/gnupg" --batch --quiet --no-tty --pinentry-mode loopback \
  --passphrase-file "$PASSPHRASE_FILE" --decrypt "$WORK/$NAME" \
  | docker exec -i "$RESTORE_CONTAINER" pg_restore -U postgres -d restore_check \
      --no-owner --no-privileges --exit-on-error \
  || fail "decrypt or pg_restore failed"
rm -f "$WORK/$NAME"

SUMMARY=
for table in $KEY_TABLES; do
  restored=$(docker exec "$RESTORE_CONTAINER" psql -U postgres -d restore_check -XAtq \
    -c "select count(*) from $table" 2>/dev/null) || fail "table $table missing in restore"
  recorded=$(printf '%s' "$RECORDED" | python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$table")
  SUMMARY="$SUMMARY $table=$restored"
  [ "$restored" -gt 0 ] || fail "table $table is empty in restore"
  if [ -n "$recorded" ] && [ "$restored" -lt "$recorded" ]; then
    fail "table $table restored $restored < $recorded rows recorded at backup"
  fi
done

echo "PASS restore check $NAME:$SUMMARY"
