#!/bin/sh
set -eu
: "${DATABASE_URL:?DATABASE_URL is required}"
backup="${1:?Usage: restore-job-queue.sh BACKUP.dump}"
test -s "$backup"
if [ "${PU_CONFIRM_QUEUE_RESTORE:-}" != "RESTORE_QUEUE_TABLES" ]; then
  echo "Refusing restore. Set PU_CONFIRM_QUEUE_RESTORE=RESTORE_QUEUE_TABLES." >&2
  exit 2
fi
pg_restore --exit-on-error --single-transaction --clean --if-exists \
  --no-owner --no-privileges --dbname="$DATABASE_URL" "$backup"
echo "Queue tables restored. Start workers after schema revision verification."
