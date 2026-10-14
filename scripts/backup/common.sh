# shellcheck shell=bash
# Shared helpers for offsite_backup.sh and restore_check.sh (sourced, not run).
# Secrets are only ever referenced by FILE PATH; nothing here prints them.

BACKUP_SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
DB_CONTAINER=${PU_BACKUP_DB_CONTAINER:-puw-primary-next-primary-db}
BACKEND_CONTAINER=${PU_BACKUP_BACKEND_CONTAINER:-puw-primary-next-primary-backend}
DB_USER=${PU_BACKUP_DB_USER:-pu_user}
DB_NAME=${PU_BACKUP_DB_NAME:-pu_workspace}
PASSPHRASE_FILE=${PU_BACKUP_PASSPHRASE_FILE:-/root/.config/pu-workspace/backup/passphrase}
GOOGLE_TOKEN_ID=${PU_BACKUP_GOOGLE_TOKEN_ID:-11}
DRIVE_FOLDER=${PU_BACKUP_DRIVE_FOLDER:-PU Workspace — резервные копии БД}
KEY_TABLES=${PU_BACKUP_KEY_TABLES:-projects tasks messages contracts response_drafts audit_logs}
REMOTE_DIR=

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

die() {
  log "ERROR: $*"
  exit 1
}

require_passphrase_file() {
  [ -f "$PASSPHRASE_FILE" ] || die "passphrase file missing: $PASSPHRASE_FILE"
  [ -s "$PASSPHRASE_FILE" ] || die "passphrase file is empty: $PASSPHRASE_FILE"
  mode=$(stat -c '%a' "$PASSPHRASE_FILE")
  case "$mode" in
    600|400) ;;
    *) die "passphrase file must be chmod 600 (is $mode): $PASSPHRASE_FILE" ;;
  esac
  command -v gpg >/dev/null 2>&1 || die "gpg is not installed"
}

# Content-free failure alert through the backend's own Telegram notifier.
notify_failure() {
  docker exec "$BACKEND_CONTAINER" python -c \
    "import sys; from app.integrations.telegram import notify_telegram; notify_telegram(sys.argv[1])" \
    "$1" >/dev/null 2>&1 || log "telegram notification failed"
}

# Copy drive_ops.py + rotation.py into a private temp dir in the backend container.
remote_setup() {
  REMOTE_DIR=$(docker exec "$BACKEND_CONTAINER" mktemp -d /tmp/puw-backup.XXXXXX)
  docker cp -q "$BACKUP_SCRIPT_DIR/drive_ops.py" "$BACKEND_CONTAINER:$REMOTE_DIR/drive_ops.py"
  docker cp -q "$BACKUP_SCRIPT_DIR/rotation.py" "$BACKEND_CONTAINER:$REMOTE_DIR/rotation.py"
}

remote_cleanup() {
  if [ -n "$REMOTE_DIR" ]; then
    docker exec "$BACKEND_CONTAINER" rm -rf "$REMOTE_DIR" >/dev/null 2>&1 || true
    REMOTE_DIR=
  fi
}

drive_ops() {
  docker exec \
    -e PU_BACKUP_GOOGLE_TOKEN_ID="$GOOGLE_TOKEN_ID" \
    -e PU_BACKUP_DRIVE_FOLDER="$DRIVE_FOLDER" \
    -e PU_BACKUP_KEEP_DAILY="${PU_BACKUP_KEEP_DAILY:-7}" \
    -e PU_BACKUP_KEEP_WEEKLY="${PU_BACKUP_KEEP_WEEKLY:-4}" \
    -e PU_BACKUP_KEEP_MONTHLY="${PU_BACKUP_KEEP_MONTHLY:-6}" \
    "$BACKEND_CONTAINER" python "$REMOTE_DIR/drive_ops.py" "$@"
}

# JSON object {"table": rows, ...} of the key tables; read-only query on prod.
prod_row_counts() {
  sql="select json_build_object("
  sep=
  for table in $KEY_TABLES; do
    sql="$sql$sep'$table', (select count(*) from $table)"
    sep=", "
  done
  sql="$sql)"
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -XAtq \
    -c "set default_transaction_read_only = on" -c "$sql"
}
