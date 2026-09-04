#!/bin/sh
set -eu

# Deploy an isolated public staging stack. This script deliberately refuses the
# production root, Compose project, port and public host.
# Usage: deploy-staging.sh ROOT REVISION PROJECT PORT PUBLIC_URL ARCHIVE

ROOT=${1:-}
REVISION=${2:-}
PROJECT=${3:-}
PORT=${4:-}
PUBLIC_URL=${5:-}
ARCHIVE=${6:-}
SOURCE_ENV=$ROOT/shared/.env.staging
RUNTIME_ENV=$ROOT/runtime/.env.staging
RELEASE_DIR=$ROOT/releases/$REVISION
CURRENT_LINK=$ROOT/current
BACKUP_DIR=$ROOT/backups
SWITCHED=false
PREVIOUS_RELEASE=
BACKUP_FILE=

fail() {
  echo "staging deploy failed: $*" >&2
  exit 1
}

case "$ROOT" in
  /opt/pu-workspace|/opt/pu-workspace/*|/|/opt) fail "production or broad root is forbidden" ;;
  /*) ;;
  *) fail "staging root must be absolute" ;;
esac
case "$ROOT" in *[!A-Za-z0-9._/-]*) fail "staging root contains unsafe characters" ;; esac
case "$REVISION" in
  *[!0-9a-f]*|'') fail "revision must be a lowercase commit SHA" ;;
esac
[ "${#REVISION}" -eq 40 ] || fail "revision must contain 40 characters"
case "$PROJECT" in
  app|pu-workspace|production|*[!a-z0-9_-]*|'') fail "unsafe staging Compose project" ;;
esac
case "$PORT" in *[!0-9]*|'') fail "staging port must be numeric" ;; esac
[ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] && [ "$PORT" -ne 3000 ] && [ "$PORT" -ne 443 ] \
  || fail "unsafe staging port"
case "$PUBLIC_URL" in
  https://pu-workspace.duckdns.org*|https://puworkspace.ru*|https://www.puworkspace.ru*)
    fail "production public URL is forbidden" ;;
  https://*) ;;
  *) fail "staging public URL must use HTTPS" ;;
esac
[ -s "$ARCHIVE" ] || fail "release archive is missing"
[ -s "$SOURCE_ENV" ] || fail "dedicated staging secret file is missing"
RESOLVED_ROOT=$(readlink -f "$ROOT" 2>/dev/null || true)
[ "$RESOLVED_ROOT" = "$ROOT" ] || fail "staging root must be an existing canonical non-symlink path"
RESOLVED_SOURCE=$(readlink -f "$SOURCE_ENV" 2>/dev/null || true)
[ "$RESOLVED_SOURCE" = "$SOURCE_ENV" ] || fail "staging secret file must not be a symlink"
command -v flock >/dev/null 2>&1 || fail "flock is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v docker >/dev/null 2>&1 || fail "docker is required"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"

install -d -m 700 "$ROOT" "$ROOT/releases" "$ROOT/runtime" "$BACKUP_DIR"
exec 9>"$ROOT/deploy.lock"
flock -n 9 || fail "another staging deployment is already in progress"

if [ -e "$RELEASE_DIR" ]; then
  if [ "$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)" = "$RELEASE_DIR" ]; then
    echo "staging release $REVISION is already active; running smoke only"
    docker run --rm --network host -e PU_EXPECTED_RELEASE="$REVISION" \
      -v "$RELEASE_DIR/scripts:/workspace/scripts:ro" -w /workspace \
      "pu-workspace-staging:$REVISION" python scripts/check_public_smoke.py "$PUBLIC_URL"
    exit 0
  fi
  fail "release directory already exists and is not active"
fi

# The archive is produced by git archive. Reject absolute and parent paths
# before extraction and create the release atomically inside the staging root.
if tar -tzf "$ARCHIVE" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  fail "release archive contains an unsafe path"
fi
if tar -tvzf "$ARCHIVE" | grep -Eq '^[lh]'; then
  fail "release archive must not contain links"
fi
TEMP_RELEASE=$(mktemp -d "$ROOT/releases/.${REVISION}.XXXXXX")
cleanup_temp() { [ ! -d "$TEMP_RELEASE" ] || rm -rf -- "$TEMP_RELEASE"; }
trap cleanup_temp INT TERM HUP EXIT
tar -xzf "$ARCHIVE" -C "$TEMP_RELEASE"
[ -s "$TEMP_RELEASE/docker-compose.ci.yml" ] || fail "staging compose file is missing"
[ -s "$TEMP_RELEASE/scripts/check_public_smoke.py" ] || fail "public smoke script is missing"
mv "$TEMP_RELEASE" "$RELEASE_DIR"
TEMP_RELEASE=

python3 "$RELEASE_DIR/scripts/render_staging_environment.py" \
  --source "$SOURCE_ENV" --output "$RUNTIME_ENV" --revision "$REVISION" --port "$PORT"
chmod 600 "$RUNTIME_ENV"

compose() {
  docker compose --env-file "$RUNTIME_ENV" -f "$RELEASE_DIR/docker-compose.ci.yml" -p "$PROJECT" "$@"
}

compose config --quiet
echo "[1/5] building isolated staging image"
docker build -f "$RELEASE_DIR/Dockerfile.ci" -t "pu-workspace-staging:$REVISION" "$RELEASE_DIR"

PREVIOUS_RELEASE=$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)
if docker volume inspect "${PROJECT}_data" >/dev/null 2>&1; then
  echo "[2/5] backing up and validating staging PostgreSQL"
  compose up -d db --wait --wait-timeout 120
  STAMP=$(date -u +%Y%m%dT%H%M%SZ)
  BACKUP_FILE=$BACKUP_DIR/pu_workspace_staging_before_${REVISION}_${STAMP}.dump
  compose exec -T db pg_dump -U pu_test -d pu_test -Fc > "$BACKUP_FILE"
  [ -s "$BACKUP_FILE" ] || fail "staging backup is empty"
  docker run --rm --network none -i --user postgres --entrypoint sh postgres:16-alpine -ec '
    initdb -D /tmp/restore-db >/dev/null
    pg_ctl -D /tmp/restore-db -o "-c listen_addresses=" -w start >/dev/null
    createdb restore_check
    pg_restore --exit-on-error --no-owner --no-acl -d restore_check
    table_count=$(psql -d restore_check -tAc "select count(*) from pg_tables where schemaname=current_schema()")
    [ "$table_count" -gt 0 ]
  ' < "$BACKUP_FILE"
else
  echo "[2/5] first staging deployment; no existing database to back up"
fi

rollback() {
  code=$?
  trap - INT TERM HUP EXIT
  if [ "$SWITCHED" = true ] && [ -z "$PREVIOUS_RELEASE" ]; then
    echo "first staging deployment failed verification; stopping candidate without deleting its database" >&2
    compose down --timeout 20 || true
    [ ! -L "$CURRENT_LINK" ] || unlink "$CURRENT_LINK"
    exit "$code"
  fi
  if [ "$SWITCHED" = true ] && [ -n "$PREVIOUS_RELEASE" ] && [ -d "$PREVIOUS_RELEASE" ]; then
    DB_REVISION=$(compose exec -T db psql -U pu_test -d pu_test -tAc 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]' || true)
    if [ -n "$DB_REVISION" ] && ! grep -Rqs "$DB_REVISION" "$PREVIOUS_RELEASE/backend/migrations/versions" 2>/dev/null; then
      echo "staging schema advanced to $DB_REVISION; refusing incompatible application rollback" >&2
      echo "candidate remains active; staging backup: ${BACKUP_FILE:-none}" >&2
      exit "$code"
    fi
    echo "staging verification failed; restoring previous application release" >&2
    RELEASE_DIR=$PREVIOUS_RELEASE
    OLD_REVISION=$(basename "$PREVIOUS_RELEASE")
    python3 "$RELEASE_DIR/scripts/render_staging_environment.py" \
      --source "$SOURCE_ENV" --output "$RUNTIME_ENV" --revision "$OLD_REVISION" --port "$PORT"
    ln -sfn "$PREVIOUS_RELEASE" "$CURRENT_LINK"
    compose up -d --no-build --force-recreate --wait --wait-timeout 180 || true
  fi
  exit "$code"
}
trap rollback INT TERM HUP EXIT

echo "[3/5] switching only the isolated staging Compose project"
ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"
SWITCHED=true
compose up -d --no-build --force-recreate --wait --wait-timeout 180

echo "[4/5] checking loopback readiness and persistent test account"
if [ -z "$PREVIOUS_RELEASE" ]; then
  python3 "$RELEASE_DIR/scripts/check_ci_smoke.py" --env-file "$RUNTIME_ENV" --seed
else
  python3 "$RELEASE_DIR/scripts/check_ci_smoke.py" --env-file "$RUNTIME_ENV"
fi

echo "[5/5] checking the dedicated public HTTPS endpoint"
docker run --rm --network host -e PU_EXPECTED_RELEASE="$REVISION" \
  -v "$RELEASE_DIR/scripts:/workspace/scripts:ro" -w /workspace \
  "pu-workspace-staging:$REVISION" python scripts/check_public_smoke.py "$PUBLIC_URL"

SWITCHED=false
trap - INT TERM HUP EXIT
echo "staging deploy complete: release=$REVISION backup=${BACKUP_FILE:-none}"
