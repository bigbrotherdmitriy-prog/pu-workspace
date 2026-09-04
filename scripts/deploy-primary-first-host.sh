#!/bin/sh
set -eu

# Build-free, first-host deployment for a future primary stack. The candidate
# stays on a loopback-only port; DNS and public TLS are deliberately out of scope.
#
# Usage:
#   deploy-primary-first-host.sh ROOT RELEASE_DIR IMAGE PROJECT PORT [INITIAL_DUMP]
#
# INITIAL_DUMP is optional and may only be imported into a newly created volume.

umask 077

ROOT=${1:-}
RELEASE_DIR=${2:-}
CANDIDATE_IMAGE=${3:-}
PROJECT=${4:-}
PORT=${5:-}
INITIAL_DUMP=${6:-none}
CURRENT_LINK=$ROOT/current
SOURCE_ENV=$ROOT/shared/.env.primary
RUNTIME_DIR=$ROOT/runtime
BACKUP_DIR=$ROOT/backups
COMPOSE_FILE=$RELEASE_DIR/infra/primary/docker-compose.yml
REVISION=$(basename "$RELEASE_DIR" 2>/dev/null || true)
VOLUME_NAME=${PROJECT}_primary_data
CONTAINER_PREFIX=${PROJECT}-primary
RUNTIME_ENV=$RUNTIME_DIR/$REVISION/.env.primary
HOST_MARKER=$ROOT/shared/.pu-primary-host
SWITCHED=false
PREVIOUS_RELEASE=
BACKUP_FILE=
VOLUME_WAS_PRESENT=false
ACTIVE_RELEASE=$RELEASE_DIR
ACTIVE_ENV=$RUNTIME_ENV

fail() {
  echo "first-host primary deploy failed: $*" >&2
  exit 1
}

case "$ROOT" in
  /|/opt|/srv|/var|/home|'') fail "primary root is empty or too broad" ;;
  /*) ;;
  *) fail "primary root must be absolute" ;;
esac
case "$ROOT" in *[!A-Za-z0-9._/-]*) fail "primary root contains unsafe characters" ;; esac
case "$RELEASE_DIR" in "$ROOT"/releases/*) ;; *) fail "release must be inside ROOT/releases" ;; esac
case "$REVISION" in *[!0-9a-f]*|'') fail "release basename must be a lowercase commit SHA" ;; esac
[ "${#REVISION}" -eq 40 ] || fail "release basename must contain 40 characters"
case "$PROJECT" in
  app|pu-workspace|puw-staging|production|*[!a-z0-9_-]*|'') fail "unsafe or reused primary Compose project" ;;
esac
case "$PORT" in *[!0-9]*|'') fail "primary port must be numeric" ;; esac
[ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || fail "primary port is out of range"
case "$PORT" in 3000|3010|443|5678|8080) fail "primary port conflicts with a reserved service" ;; esac
case "$CANDIDATE_IMAGE" in *[!A-Za-z0-9._/@:+-]*|'') fail "unsafe candidate image reference" ;; esac
[ "$INITIAL_DUMP" = none ] || [ -s "$INITIAL_DUMP" ] || fail "initial database dump is missing or empty"
[ -d "$ROOT" ] && [ ! -L "$ROOT" ] || fail "primary root must be an existing real directory"
[ "$(readlink -f "$ROOT" 2>/dev/null || true)" = "$ROOT" ] || fail "primary root must be canonical"
[ "$(stat -c %u "$ROOT" 2>/dev/null || true)" = "$(id -u)" ] || fail "primary root must belong to the deploy user"
[ -d "$RELEASE_DIR" ] && [ ! -L "$RELEASE_DIR" ] || fail "candidate release must be a real directory"
[ "$(cat "$RELEASE_DIR/.pu-primary-release" 2>/dev/null || true)" = "$REVISION" ] \
  || fail "candidate release marker is missing or does not match its directory"
[ ! -e "$RELEASE_DIR/.env" ] || fail "candidate release must not contain an environment file"
[ -s "$COMPOSE_FILE" ] || fail "first-host primary Compose file is missing"
[ -s "$RELEASE_DIR/backend/migrations/env.py" ] || fail "candidate migrations are missing"
[ -s "$SOURCE_ENV" ] && [ ! -L "$SOURCE_ENV" ] || fail "dedicated primary secret file is missing or is a symlink"
SOURCE_MODE=$(stat -c %a "$SOURCE_ENV" 2>/dev/null || true)
[ "$SOURCE_MODE" = 600 ] || [ "$SOURCE_MODE" = 400 ] || fail "primary secret file mode must be 600 or 400"
[ "$(stat -c %u "$SOURCE_ENV" 2>/dev/null || true)" = "$(id -u)" ] || fail "primary secret file must belong to the deploy user"
[ -f "$HOST_MARKER" ] && [ ! -L "$HOST_MARKER" ] || fail "new-primary host marker is missing"
EXPECTED_MARKER=$(printf '%s\n' \
  "PU_WORKSPACE_NEW_PRIMARY=1" \
  "PRIMARY_PROJECT=$PROJECT" \
  "PRIMARY_PORT=$PORT" \
  "PRIMARY_VOLUME=$VOLUME_NAME")
[ "$(tr -d '\r' < "$HOST_MARKER")" = "$EXPECTED_MARKER" ] || fail "new-primary host marker is invalid"
MARKER_MODE=$(stat -c %a "$HOST_MARKER" 2>/dev/null || true)
[ "$MARKER_MODE" = 600 ] || [ "$MARKER_MODE" = 400 ] || fail "new-primary host marker mode must be 600 or 400"
[ "$(stat -c %u "$HOST_MARKER" 2>/dev/null || true)" = "$(id -u)" ] || fail "new-primary host marker must belong to the deploy user"

for command in docker flock python3 readlink stat; do
  command -v "$command" >/dev/null 2>&1 || fail "$command is required"
done
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
docker image inspect "$CANDIDATE_IMAGE" >/dev/null 2>&1 || fail "candidate image does not exist"
IMAGE_REVISION=$(docker image inspect "$CANDIDATE_IMAGE" \
  --format '{{ index .Config.Labels "com.pu-workspace.primary.revision" }}' 2>/dev/null || true)
[ "$IMAGE_REVISION" = "$REVISION" ] || fail "candidate image is not labelled with the release revision"

install -d -m 700 "$ROOT/releases" "$RUNTIME_DIR" "$BACKUP_DIR"
for directory in "$ROOT/releases" "$RUNTIME_DIR" "$BACKUP_DIR"; do
  [ "$(readlink -f "$directory")" = "$directory" ] || fail "managed directory escapes primary root"
done
[ ! -e "$CURRENT_LINK" ] || [ -L "$CURRENT_LINK" ] || fail "current must be absent or a symlink"
if [ -L "$CURRENT_LINK" ]; then
  PREVIOUS_RELEASE=$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)
  case "$PREVIOUS_RELEASE" in "$ROOT"/releases/*) ;; *) fail "current release escapes primary root" ;; esac
fi
[ ! -L "$ROOT/deploy.lock" ] || fail "deploy lock must not be a symlink"
exec 9>"$ROOT/deploy.lock"
flock -n 9 || fail "another primary deployment is already in progress"

compose() {
  docker compose --env-file "$ACTIVE_ENV" -f "$ACTIVE_RELEASE/infra/primary/docker-compose.yml" -p "$PROJECT" "$@"
}

local_smoke() {
  release=$1
  python3 "$release/scripts/check_primary_local_smoke.py" \
    --port "$PORT" --expected-release "$(basename "$release")"
}

if [ "$PREVIOUS_RELEASE" = "$RELEASE_DIR" ]; then
  [ -s "$RUNTIME_ENV" ] || fail "active release runtime environment is missing"
  compose --profile cutover config --format json | python3 "$RELEASE_DIR/scripts/validate_primary_compose.py" \
    --project "$PROJECT" --image "$CANDIDATE_IMAGE" --port "$PORT" \
    --volume "$VOLUME_NAME" --container-prefix "$CONTAINER_PREFIX"
  echo "primary candidate $REVISION is already active; running loopback smoke only"
  local_smoke "$RELEASE_DIR"
  exit 0
fi

PREVIOUS_OPTION=
if [ -n "$PREVIOUS_RELEASE" ]; then
  PREVIOUS_REVISION=$(basename "$PREVIOUS_RELEASE")
  PREVIOUS_ENV=$RUNTIME_DIR/$PREVIOUS_REVISION/.env.primary
  [ -s "$PREVIOUS_ENV" ] || fail "previous private runtime environment is missing"
  PREVIOUS_OPTION=$PREVIOUS_ENV
fi
if [ -n "$PREVIOUS_OPTION" ]; then
  python3 "$RELEASE_DIR/scripts/render_primary_environment.py" \
    --source "$SOURCE_ENV" \
    --output "$RUNTIME_ENV" \
    --revision "$REVISION" \
    --project "$PROJECT" \
    --port "$PORT" \
    --image "$CANDIDATE_IMAGE" \
    --volume "$VOLUME_NAME" \
    --container-prefix "$CONTAINER_PREFIX" \
    --previous "$PREVIOUS_OPTION"
else
  python3 "$RELEASE_DIR/scripts/render_primary_environment.py" \
  --source "$SOURCE_ENV" \
  --output "$RUNTIME_ENV" \
  --revision "$REVISION" \
  --project "$PROJECT" \
  --port "$PORT" \
  --image "$CANDIDATE_IMAGE" \
  --volume "$VOLUME_NAME" \
  --container-prefix "$CONTAINER_PREFIX"
fi
chmod 600 "$RUNTIME_ENV"

atomic_current() {
  target=$1
  temporary=$ROOT/.current.$$.tmp
  [ ! -e "$temporary" ] && [ ! -L "$temporary" ] || fail "temporary current link already exists"
  ln -s "$target" "$temporary"
  mv -Tf "$temporary" "$CURRENT_LINK"
}

compose --profile cutover config --format json | python3 "$RELEASE_DIR/scripts/validate_primary_compose.py" \
  --project "$PROJECT" --image "$CANDIDATE_IMAGE" --port "$PORT" \
  --volume "$VOLUME_NAME" --container-prefix "$CONTAINER_PREFIX"

echo "[1/6] running candidate backend tests without secrets"
docker run --rm --network none \
  -e PYTHONPATH=/app \
  -e DATABASE_URL=sqlite+pysqlite:///:memory: \
  -v "$RELEASE_DIR:/workspace:ro" \
  -w /workspace/backend \
  "$CANDIDATE_IMAGE" \
  python -m pytest tests -q -p no:cacheprovider

if docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
  VOLUME_WAS_PRESENT=true
  [ "$INITIAL_DUMP" = none ] || fail "initial dump import is forbidden for an existing database volume"
  EXISTING_VOLUME_PROJECT=$(docker volume inspect "$VOLUME_NAME" \
    --format '{{ index .Labels "com.docker.compose.project" }}')
  EXISTING_VOLUME_KEY=$(docker volume inspect "$VOLUME_NAME" \
    --format '{{ index .Labels "com.docker.compose.volume" }}')
  [ "$EXISTING_VOLUME_PROJECT" = "$PROJECT" ] && [ "$EXISTING_VOLUME_KEY" = data ] \
    || fail "existing database volume does not belong to this primary Compose project"
fi

echo "[2/6] starting only the isolated database"
compose up -d db --wait --wait-timeout 120
VOLUME_PROJECT=$(docker volume inspect "$VOLUME_NAME" --format '{{ index .Labels "com.docker.compose.project" }}')
VOLUME_KEY=$(docker volume inspect "$VOLUME_NAME" --format '{{ index .Labels "com.docker.compose.volume" }}')
[ "$VOLUME_PROJECT" = "$PROJECT" ] && [ "$VOLUME_KEY" = data ] \
  || fail "database volume does not belong to this primary Compose project"

DB_IMAGE=$(docker inspect "$(compose ps -q db)" --format '{{.Image}}')
validate_dump() {
  dump=$1
  docker run --rm --network none -i --user postgres --entrypoint sh "$DB_IMAGE" -ec '
    initdb -D /tmp/restore-db >/dev/null
    pg_ctl -D /tmp/restore-db -o "-c listen_addresses=" -w start >/dev/null
    createdb restore_check
    pg_restore --exit-on-error --no-owner --no-acl -d restore_check
    table_count=$(psql -d restore_check -tAc "select count(*) from pg_tables where schemaname=current_schema()")
    [ "$table_count" -gt 0 ]
  ' < "$dump"
}

if [ "$VOLUME_WAS_PRESENT" = true ]; then
  echo "[3/6] backing up and validating the existing primary database"
  STAMP=$(date -u +%Y%m%dT%H%M%SZ)
  BACKUP_FILE=$BACKUP_DIR/pu_workspace_primary_before_${REVISION}_${STAMP}.dump
  compose exec -T db pg_dump -U pu_user -d pu_workspace -Fc > "$BACKUP_FILE"
  [ -s "$BACKUP_FILE" ] || fail "primary backup is empty"
  validate_dump "$BACKUP_FILE"
elif [ "$INITIAL_DUMP" != none ]; then
  echo "[3/6] validating and importing the initial database dump"
  validate_dump "$INITIAL_DUMP"
  compose exec -T db pg_restore --exit-on-error --no-owner --no-acl \
    -U pu_user -d pu_workspace < "$INITIAL_DUMP"
  TABLE_COUNT=$(compose exec -T db psql -U pu_user -d pu_workspace -tAc \
    "select count(*) from pg_tables where schemaname=current_schema()" | tr -d '[:space:]')
  [ "$TABLE_COUNT" -gt 0 ] || fail "initial database restore produced no tables"
else
  echo "[3/6] fresh primary database requested; no dump imported"
fi

rollback() {
  code=$?
  trap - INT TERM HUP EXIT
  if [ "$SWITCHED" = true ] && [ -z "$PREVIOUS_RELEASE" ]; then
    echo "first primary activation failed; stopping candidate and preserving its database volume" >&2
    compose down --timeout 20 || echo "candidate cleanup failed; inspect containers manually" >&2
    [ ! -L "$CURRENT_LINK" ] || unlink "$CURRENT_LINK"
    exit "$code"
  fi
  if [ "$SWITCHED" = true ] && [ -n "$PREVIOUS_RELEASE" ] && [ -d "$PREVIOUS_RELEASE" ]; then
    DB_REVISION=$(compose exec -T db psql -U pu_user -d pu_workspace -tAc \
      'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]' || true)
    if [ -z "$DB_REVISION" ] || ! grep -REqs \
      "^revision(:[^=]+)?[[:space:]]*=[[:space:]]*['\"]${DB_REVISION}['\"]" \
      "$PREVIOUS_RELEASE/backend/migrations/versions" 2>/dev/null; then
      echo "schema is not proven compatible with the previous release; refusing application rollback" >&2
      echo "candidate remains active; database backup: ${BACKUP_FILE:-none}" >&2
      exit "$code"
    fi
    OLD_REVISION=$(basename "$PREVIOUS_RELEASE")
    OLD_ENV=$RUNTIME_DIR/$OLD_REVISION/.env.primary
    [ -s "$OLD_ENV" ] || { echo "previous private runtime environment is missing; rollback refused" >&2; exit "$code"; }
    echo "candidate smoke failed; restoring previous application release" >&2
    ACTIVE_RELEASE=$PREVIOUS_RELEASE
    ACTIVE_ENV=$OLD_ENV
    atomic_current "$PREVIOUS_RELEASE"
    if ! compose up -d --no-build --force-recreate --wait --wait-timeout 180 db backend; then
      echo "ROLLBACK FAILED: previous release could not be started" >&2
      exit "$code"
    fi
    if ! local_smoke "$PREVIOUS_RELEASE"; then
      echo "ROLLBACK FAILED: previous release failed loopback smoke" >&2
      exit "$code"
    fi
    echo "primary application rollback verified: release=$OLD_REVISION" >&2
  fi
  exit "$code"
}
trap rollback INT TERM HUP EXIT

echo "[4/6] atomically selecting the candidate release"
atomic_current "$RELEASE_DIR"
SWITCHED=true

echo "[5/6] starting only database and backend; background services stay stopped until cutover"
compose up -d --no-build --force-recreate --wait --wait-timeout 180 db backend

echo "[6/6] running read-only loopback smoke; no public host is contacted"
local_smoke "$RELEASE_DIR"

SWITCHED=false
trap - INT TERM HUP EXIT
echo "first-host primary deploy complete: release=$REVISION backup=${BACKUP_FILE:-none} port=$PORT"
