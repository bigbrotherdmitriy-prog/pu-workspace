#!/bin/sh
set -eu

# Safe, repeatable production switch for an already extracted release and built image.
# Usage: scripts/deploy-production.sh /opt/pu-workspace/releases/<revision> app-backend:<revision>

RELEASE_DIR=${1:-}
CANDIDATE_IMAGE=${2:-}
DEPLOY_RELAY=${DEPLOY_RELAY:-true}
APP_ROOT=/opt/pu-workspace
CURRENT_LINK=$APP_ROOT/current
PROXY_OVERRIDE=/opt/pu-workspace/docker-compose.proxy.yml
BACKUP_DIR=$APP_ROOT/backups
COMPOSE="docker compose -p app -f docker-compose.yml -f $PROXY_OVERRIDE"
SWITCHED=false

fail() {
  echo "deploy failed: $*" >&2
  exit 1
}

# Serialize production switches. Parallel deploys can otherwise race during
# `docker compose --force-recreate` and temporarily contend for fixed names.
command -v flock >/dev/null 2>&1 || fail "flock is required for safe deployment"
exec 9>"$APP_ROOT/deploy.lock"
flock -n 9 || fail "another deployment is already in progress"

case "$RELEASE_DIR" in
  "$APP_ROOT"/releases/*) ;;
  *) fail "release must be inside $APP_ROOT/releases" ;;
esac
[ -n "$CANDIDATE_IMAGE" ] || fail "candidate image is required"
[ -d "$RELEASE_DIR" ] || fail "release directory does not exist"
[ -s "$RELEASE_DIR/.env" ] || fail "release .env is missing or empty"
[ -s "$RELEASE_DIR/docker-compose.yml" ] || fail "docker-compose.yml is missing"
[ -s "$PROXY_OVERRIDE" ] || fail "persistent proxy override is missing"
docker image inspect "$CANDIDATE_IMAGE" >/dev/null 2>&1 || fail "candidate image does not exist"

REVISION=$(basename "$RELEASE_DIR")
PU_RELEASE_REVISION=$REVISION
export PU_RELEASE_REVISION
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_FILE=$BACKUP_DIR/pu_workspace_before_${REVISION}_${STAMP}.dump
PREVIOUS_RELEASE=$(readlink "$CURRENT_LINK" 2>/dev/null || true)
ROLLBACK_IMAGE=app-backend:rollback-${STAMP}

rollback() {
  code=$?
  if [ "$SWITCHED" = true ]; then
    DB_REVISION=$(docker exec pu-workspace-db psql -U pu_user -d pu_workspace -tAc 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]' || true)
    if [ -n "$DB_REVISION" ] && [ -n "$PREVIOUS_RELEASE" ] && \
       ! grep -Rqs "revision = \"$DB_REVISION\"" "$PREVIOUS_RELEASE/backend/migrations/versions" 2>/dev/null; then
      echo "readiness failed after schema advanced to $DB_REVISION; refusing incompatible application rollback" >&2
      echo "candidate remains active for diagnosis; database backup: $BACKUP_FILE" >&2
      SWITCHED=false
      exit "$code"
    fi
    echo "readiness failed; restoring previous release" >&2
    docker tag "$ROLLBACK_IMAGE" app-backend:latest || true
    docker tag "$ROLLBACK_IMAGE" app-backend || true
    if [ -n "$PREVIOUS_RELEASE" ] && [ -d "$PREVIOUS_RELEASE" ]; then
      PU_RELEASE_REVISION=$(basename "$PREVIOUS_RELEASE")
      export PU_RELEASE_REVISION
      ln -sfn "$PREVIOUS_RELEASE" "$CURRENT_LINK"
      cd "$PREVIOUS_RELEASE"
      if [ "$DEPLOY_RELAY" = true ]; then
        $COMPOSE up -d --no-build --force-recreate backend telegram-relay || true
      else
        $COMPOSE up -d --no-build --force-recreate backend || true
      fi
    fi
  fi
  exit "$code"
}
trap rollback INT TERM HUP EXIT

echo "[1/6] validating compose and candidate tests"
cd "$RELEASE_DIR"
$COMPOSE config --quiet
docker run --rm -e PYTHONPATH=/app \
  -v "$RELEASE_DIR:/workspace:ro" \
  -v /dev/null:/workspace/.env:ro \
  -w /workspace/backend "$CANDIDATE_IMAGE" python -m pytest tests -q

echo "[2/6] creating and validating PostgreSQL backup"
install -d -m 700 "$BACKUP_DIR"
docker exec pu-workspace-db pg_dump -U pu_user -d pu_workspace -Fc -f /tmp/deploy-backup.dump
docker cp pu-workspace-db:/tmp/deploy-backup.dump "$BACKUP_FILE"
[ -s "$BACKUP_FILE" ] || fail "backup is empty"
docker run --rm -v "$BACKUP_FILE:/backup.dump:ro" postgres:16-alpine pg_restore -l /backup.dump >/dev/null

echo "[3/6] preserving rollback image"
docker tag app-backend:latest "$ROLLBACK_IMAGE"

echo "[4/6] switching release"
docker tag "$CANDIDATE_IMAGE" app-backend:latest
docker tag "$CANDIDATE_IMAGE" app-backend
ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"
SWITCHED=true
if [ "$DEPLOY_RELAY" = true ]; then
  $COMPOSE up -d --no-build --force-recreate backend telegram-relay
else
  echo "relay restart skipped by DEPLOY_RELAY=false"
  $COMPOSE up -d --no-build --force-recreate backend
fi

echo "[5/6] waiting for backend readiness"
ready=false
attempt=1
while [ "$attempt" -le 20 ]; do
  if docker exec pu-workspace-backend python -c 'import json,urllib.request; data=json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/readiness", timeout=3)); raise SystemExit(0 if data.get("ready") else 1)' 2>/dev/null; then
    ready=true
    break
  fi
  sleep 3
  attempt=$((attempt + 1))
done
[ "$ready" = true ] || fail "backend readiness did not become green"

echo "[6/6] checking relay and public endpoint"
if [ "$DEPLOY_RELAY" = true ]; then
  relay_ready=false
  attempt=1
  while [ "$attempt" -le 15 ]; do
    if docker exec pu-workspace-backend python -c 'import json,urllib.request; data=json.load(urllib.request.urlopen("http://172.19.0.1:18080/health", timeout=5)); raise SystemExit(0 if data.get("status") in ("ok", "healthy") else 1)' 2>/dev/null; then
      relay_ready=true
      break
    fi
    sleep 3
    attempt=$((attempt + 1))
  done
  [ "$relay_ready" = true ] || fail "telegram relay did not become healthy"
fi
docker run --rm \
  -e PU_EXPECTED_RELEASE="$REVISION" \
  -v "$RELEASE_DIR/scripts:/workspace/scripts:ro" \
  -w /workspace \
  "$CANDIDATE_IMAGE" \
  python scripts/check_public_smoke.py https://pu-workspace.duckdns.org/

SWITCHED=false
trap - INT TERM HUP EXIT
echo "deploy complete: release=$REVISION backup=$BACKUP_FILE rollback_image=$ROLLBACK_IMAGE"
