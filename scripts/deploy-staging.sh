#!/bin/sh
set -eu

# Deploy an isolated public staging stack. This script deliberately refuses the
# production root, Compose project, port and public host.
# Usage: deploy-staging.sh ROOT REVISION PROJECT PORT PUBLIC_URL ARCHIVE

umask 077

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
ALREADY_ACTIVE=false
ARCHIVE_SHA=

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
  https://37.252.23.204*|https://pu-workspace.duckdns.org*|https://puworkspace.ru*|https://www.puworkspace.ru*)
    fail "production public URL is forbidden" ;;
  https://*) ;;
  *) fail "staging public URL must use HTTPS" ;;
esac
[ -s "$ARCHIVE" ] || fail "release archive is missing"
[ -s "$SOURCE_ENV" ] || fail "dedicated staging secret file is missing"
[ "$(id -u)" -ne 0 ] || fail "staging deployment must not run as root"
[ ! -e /opt/pu-workspace/current ] && [ ! -L /opt/pu-workspace/current ] \
  && [ ! -e /opt/pu-workspace/docker-compose.proxy.yml ] \
  || fail "production footprint detected; use a dedicated staging host"
RESOLVED_ROOT=$(readlink -f "$ROOT" 2>/dev/null || true)
[ "$RESOLVED_ROOT" = "$ROOT" ] || fail "staging root must be an existing canonical non-symlink path"
[ "$(stat -c %u "$ROOT" 2>/dev/null || true)" = "$(id -u)" ] \
  || fail "staging root must belong to the deploy user"
HOST_MARKER=$ROOT/shared/.pu-staging-host
[ -f "$HOST_MARKER" ] && [ ! -L "$HOST_MARKER" ] \
  || fail "dedicated staging host marker is missing"
EXPECTED_HOST_MARKER=$(printf '%s\n' \
  "PU_WORKSPACE_DEDICATED_STAGING=1" \
  "STAGING_PROJECT=$PROJECT" \
  "STAGING_PORT=$PORT" \
  "STAGING_PUBLIC_URL=$PUBLIC_URL")
[ "$(tr -d '\r' < "$HOST_MARKER")" = "$EXPECTED_HOST_MARKER" ] \
  || fail "dedicated staging host marker is invalid"
[ "$(stat -c %u "$HOST_MARKER" 2>/dev/null || true)" = "$(id -u)" ] \
  || fail "dedicated staging host marker must belong to the deploy user"
MARKER_MODE=$(stat -c %a "$HOST_MARKER" 2>/dev/null || true)
[ "$MARKER_MODE" = 600 ] || [ "$MARKER_MODE" = 400 ] \
  || fail "dedicated staging host marker mode must be 600 or 400"
RESOLVED_SOURCE=$(readlink -f "$SOURCE_ENV" 2>/dev/null || true)
[ "$RESOLVED_SOURCE" = "$SOURCE_ENV" ] || fail "staging secret file must not be a symlink"
SOURCE_MODE=$(stat -c %a "$SOURCE_ENV" 2>/dev/null || true)
[ "$SOURCE_MODE" = 600 ] || [ "$SOURCE_MODE" = 400 ] || fail "staging secret file mode must be 600 or 400"
SOURCE_OWNER=$(stat -c %u "$SOURCE_ENV" 2>/dev/null || true)
[ "$SOURCE_OWNER" = "$(id -u)" ] || fail "staging secret file must belong to the deploy user"
command -v flock >/dev/null 2>&1 || fail "flock is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v docker >/dev/null 2>&1 || fail "docker is required"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required"
command -v diff >/dev/null 2>&1 || fail "diff is required"
command -v awk >/dev/null 2>&1 || fail "awk is required"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"

install -d -m 700 "$ROOT" "$ROOT/releases" "$ROOT/runtime" "$BACKUP_DIR"
[ "$(readlink -f "$ROOT/releases")" = "$ROOT/releases" ] || fail "releases directory must not escape staging root"
[ "$(readlink -f "$ROOT/runtime")" = "$ROOT/runtime" ] || fail "runtime directory must not escape staging root"
[ "$(readlink -f "$BACKUP_DIR")" = "$BACKUP_DIR" ] || fail "backup directory must not escape staging root"
[ ! -e "$CURRENT_LINK" ] || [ -L "$CURRENT_LINK" ] || fail "current must be absent or a symlink"
CURRENT_TARGET=$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)
case "$CURRENT_TARGET" in ''|"$ROOT"/releases/*) ;; *) fail "current release escapes staging root" ;; esac
[ ! -L "$ROOT/deploy.lock" ] || fail "deploy lock must not be a symlink"
exec 9>"$ROOT/deploy.lock"
flock -n 9 || fail "another staging deployment is already in progress"
ARCHIVE_SHA=$(sha256sum "$ARCHIVE" | awk '{print $1}')
case "$ARCHIVE_SHA" in *[!0-9a-f]*|'') fail "release archive digest is invalid" ;; esac
[ "${#ARCHIVE_SHA}" -eq 64 ] || fail "release archive digest must contain 64 characters"

if [ -e "$RELEASE_DIR" ]; then
  [ -d "$RELEASE_DIR" ] && [ ! -L "$RELEASE_DIR" ] || fail "existing release is not a real directory"
  [ "$(cat "$RELEASE_DIR/.pu-staging-release" 2>/dev/null || true)" = "$REVISION" ] \
    || fail "existing release does not have the expected immutable marker"
  [ "$(cat "$RELEASE_DIR/.pu-staging-archive.sha256" 2>/dev/null || true)" = "$ARCHIVE_SHA" ] \
    || fail "existing release archive digest does not match"
  if [ "$CURRENT_TARGET" = "$RELEASE_DIR" ]; then
    if ! (
      set -eu
      VERIFY_RELEASE=$(mktemp -d "$ROOT/releases/.verify-${REVISION}.XXXXXX")
      trap 'rm -rf -- "$VERIFY_RELEASE"' EXIT
      tar -xzf "$ARCHIVE" -C "$VERIFY_RELEASE"
      diff -qr \
        --exclude=.pu-staging-release \
        --exclude=.pu-staging-archive.sha256 \
        "$VERIFY_RELEASE" "$RELEASE_DIR" >/dev/null
    ); then
      fail "active staging release files differ from the tested archive"
    fi
    ALREADY_ACTIVE=true
  else
    install -d -m 700 "$ROOT/rejected"
    [ "$(readlink -f "$ROOT/rejected")" = "$ROOT/rejected" ] || fail "rejected directory must not escape staging root"
    REJECTED_RELEASE=$ROOT/rejected/${REVISION}.$(date -u +%Y%m%dT%H%M%SZ).$$
    mv "$RELEASE_DIR" "$REJECTED_RELEASE"
    echo "replaced inactive staging candidate; previous files kept at $REJECTED_RELEASE"
  fi
fi
if [ "$ALREADY_ACTIVE" != true ]; then
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
  printf '%s\n' "$REVISION" > "$TEMP_RELEASE/.pu-staging-release"
  printf '%s\n' "$ARCHIVE_SHA" > "$TEMP_RELEASE/.pu-staging-archive.sha256"
  chmod 400 "$TEMP_RELEASE/.pu-staging-release" "$TEMP_RELEASE/.pu-staging-archive.sha256"
  mv "$TEMP_RELEASE" "$RELEASE_DIR"
  TEMP_RELEASE=
fi

python3 "$RELEASE_DIR/scripts/render_staging_environment.py" \
  --source "$SOURCE_ENV" --output "$RUNTIME_ENV" --revision "$REVISION" --port "$PORT"
chmod 600 "$RUNTIME_ENV"

compose() {
  docker compose --env-file "$RUNTIME_ENV" -f "$RELEASE_DIR/docker-compose.ci.yml" -p "$PROJECT" "$@"
}

# Keep the smoke implementation pinned to the candidate archive even while
# rollback temporarily points RELEASE_DIR at the previous application release.
DEPLOY_TOOLS_DIR=$RELEASE_DIR/scripts
public_smoke() {
  image_revision=$1
  expected_revision=$2
  smoke_password=$(python3 -c 'import sys
from pathlib import Path
values = dict(line.split("=", 1) for line in Path(sys.argv[1]).read_text().splitlines() if line and not line.startswith("#"))
print(values["PU_SMOKE_PASSWORD"], end="")' "$RUNTIME_ENV")
  [ -n "$smoke_password" ] || fail "staging smoke password is missing"
  printf '%s' "$smoke_password" | docker run --rm -i --network host \
    -e PU_EXPECTED_RELEASE="$expected_revision" \
    -v "$DEPLOY_TOOLS_DIR:/workspace/scripts:ro" -w /workspace \
    "pu-workspace-staging:$image_revision" \
    python scripts/check_public_smoke.py "$PUBLIC_URL" --staging-authenticated
}

compose config --format json | python3 "$RELEASE_DIR/scripts/validate_staging_compose.py" \
  --project "$PROJECT" --revision "$REVISION" --port "$PORT" --release "$RELEASE_DIR"

if [ "$ALREADY_ACTIVE" = true ]; then
  IMAGE_REVISION=$(docker image inspect "pu-workspace-staging:$REVISION" \
    --format '{{ index .Config.Labels "com.pu-workspace.staging.revision" }}' 2>/dev/null || true)
  [ "$IMAGE_REVISION" = "$REVISION" ] || fail "active staging image is missing or has drifted"
  echo "staging release $REVISION is already active; running local and public smoke"
  python3 "$RELEASE_DIR/scripts/check_ci_smoke.py" --env-file "$RUNTIME_ENV"
  public_smoke "$REVISION" "$REVISION"
  exit 0
fi

echo "[1/5] building isolated staging image"
docker build \
  --label "com.pu-workspace.staging.revision=$REVISION" \
  -f "$RELEASE_DIR/Dockerfile.ci" \
  -t "pu-workspace-staging:$REVISION" \
  "$RELEASE_DIR"
[ "$(docker image inspect "pu-workspace-staging:$REVISION" \
  --format '{{ index .Config.Labels "com.pu-workspace.staging.revision" }}')" = "$REVISION" ] \
  || fail "built staging image does not carry the expected revision"

PREVIOUS_RELEASE=$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)
if docker volume inspect "${PROJECT}_data" >/dev/null 2>&1; then
  VOLUME_PROJECT=$(docker volume inspect "${PROJECT}_data" \
    --format '{{ index .Labels "com.docker.compose.project" }}')
  VOLUME_NAME=$(docker volume inspect "${PROJECT}_data" \
    --format '{{ index .Labels "com.docker.compose.volume" }}')
  [ "$VOLUME_PROJECT" = "$PROJECT" ] && [ "$VOLUME_NAME" = data ] \
    || fail "existing database volume does not belong to this staging project"
  echo "[2/5] backing up and validating staging PostgreSQL"
  compose up -d db --wait --wait-timeout 120
  STAMP=$(date -u +%Y%m%dT%H%M%SZ)
  BACKUP_FILE=$BACKUP_DIR/pu_workspace_staging_before_${REVISION}_${STAMP}.dump
  compose exec -T db pg_dump -U pu_test -d pu_test -Fc > "$BACKUP_FILE"
  [ -s "$BACKUP_FILE" ] || fail "staging backup is empty"
  DB_IMAGE=$(docker inspect "$(compose ps -q db)" --format '{{.Image}}')
  docker run --rm --network none -i --user postgres --entrypoint sh "$DB_IMAGE" -ec '
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
    if ! compose down --timeout 20; then
      echo "first staging cleanup failed; candidate containers may still be running" >&2
    fi
    [ ! -L "$CURRENT_LINK" ] || unlink "$CURRENT_LINK"
    exit "$code"
  fi
  if [ "$SWITCHED" = true ] && [ -n "$PREVIOUS_RELEASE" ] && [ -d "$PREVIOUS_RELEASE" ]; then
    DB_REVISION=$(compose exec -T db psql -U pu_test -d pu_test -tAc 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]' || true)
    if [ -z "$DB_REVISION" ]; then
      echo "database revision is unknown; refusing unproven application rollback" >&2
      echo "candidate remains active; staging backup: ${BACKUP_FILE:-none}" >&2
      exit "$code"
    fi
    if ! grep -REqs "^revision(:[^=]+)?[[:space:]]*=[[:space:]]*['\"]${DB_REVISION}['\"]" \
      "$PREVIOUS_RELEASE/backend/migrations/versions" 2>/dev/null; then
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
    if ! compose up -d --no-build --force-recreate --wait --wait-timeout 180; then
      echo "ROLLBACK FAILED: previous staging release could not be started" >&2
      exit "$code"
    fi
    if ! python3 "$RELEASE_DIR/scripts/check_ci_smoke.py" --env-file "$RUNTIME_ENV"; then
      echo "ROLLBACK FAILED: previous staging release failed loopback smoke" >&2
      exit "$code"
    fi
    if ! public_smoke "$OLD_REVISION" "$OLD_REVISION"; then
      echo "ROLLBACK FAILED: previous staging release failed public smoke" >&2
      exit "$code"
    fi
    echo "staging rollback verified: release=$OLD_REVISION" >&2
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
public_smoke "$REVISION" "$REVISION"

SWITCHED=false
trap - INT TERM HUP EXIT
echo "staging deploy complete: release=$REVISION backup=${BACKUP_FILE:-none}"
