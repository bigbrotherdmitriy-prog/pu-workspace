#!/bin/sh
set -eu
umask 077

# Restore a retained complete application artifact. Never downgrade the DB.
# Run the script from the candidate release directory, with the deploy account.
# ROOT PREVIOUS_RELEASE PREVIOUS_IMAGE_ID PROJECT PORT PUBLIC_HTTPS_ORIGIN
ROOT=${1:-}
PREVIOUS_RELEASE=${2:-}
PREVIOUS_IMAGE=${3:-}
PROJECT=${4:-}
PORT=${5:-}
PUBLIC_ORIGIN=${6:-}
VERIFIER_RELEASE=$(pwd -P)
fail() { echo "primary rollback failed: $*" >&2; exit 1; }
case "$ROOT" in /opt/*|/srv/*) ;; *) fail "explicit dedicated primary root required" ;; esac
[ "$(readlink -f "$ROOT")" = "$ROOT" ] && [ ! -L "$ROOT" ] || fail "noncanonical primary root"
case "$PREVIOUS_RELEASE" in "$ROOT"/releases/*) ;; *) fail "previous release escapes primary root" ;; esac
case "$VERIFIER_RELEASE" in "$ROOT"/releases/*) ;; *) fail "start in the candidate release, not /root" ;; esac
[ "$(readlink -f "$PREVIOUS_RELEASE")" = "$PREVIOUS_RELEASE" ] && [ ! -L "$PREVIOUS_RELEASE" ] || fail "previous release is not canonical"
REVISION=$(basename "$PREVIOUS_RELEASE")
case "$REVISION" in *[!0-9a-f]*|'') fail "invalid previous release SHA" ;; esac
[ "${#REVISION}" -eq 40 ] || fail "full previous SHA required"
case "$PREVIOUS_IMAGE" in sha256:*) ;; *) fail "exact retained image ID required" ;; esac
case "$PROJECT" in app|pu-workspace|puw-staging|*[!a-z0-9_-]*|'') fail "unsafe primary project" ;; esac
case "$PORT" in *[!0-9]*|'') fail "invalid primary port" ;; esac
[ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || fail "invalid primary port"
case "$PORT" in 3000|3010|443|5678|8080) fail "reserved primary port" ;; esac
[ "$(stat -c %u "$ROOT")" = "$(id -u)" ] || fail "use the primary root owner"
[ -L "$ROOT/current" ] || fail "current is not a symlink"
[ ! -L "$ROOT/deploy.lock" ] || fail "unsafe deploy lock"
exec 9>"$ROOT/deploy.lock"
flock -n 9 || fail "another deployment is active"
CURRENT_RELEASE=$(readlink -f "$ROOT/current")
case "$CURRENT_RELEASE" in "$ROOT"/releases/*) ;; *) fail "current release escapes primary root" ;; esac
OLD_ENV=$ROOT/runtime/$REVISION/.env.primary
CURRENT_ENV=$ROOT/runtime/$(basename "$CURRENT_RELEASE")/.env.primary
[ -s "$OLD_ENV" ] && [ ! -L "$OLD_ENV" ] && [ -s "$CURRENT_ENV" ] || fail "retained runtime environment missing"
OLD_REVISION=$(python3 - "$VERIFIER_RELEASE" "$OLD_ENV" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + '/scripts')
from render_primary_environment import read_env
values = read_env(Path(sys.argv[2]))
assert values.get('GMAIL_AUTO_SYNC_ENABLED', '').lower() == 'true', 'Gmail flag changed'
print(values['PU_RELEASE_REVISION'])
PY
)
[ "$OLD_REVISION" = "$REVISION" ] || fail "retained runtime release differs"
DB_REVISION=$(docker compose --env-file "$CURRENT_ENV" \
  -f "$CURRENT_RELEASE/infra/primary/docker-compose.yml" -p "$PROJECT" \
  exec -T -e PGOPTIONS='-c default_transaction_read_only=on' db \
  psql -U pu_user -d pu_workspace -tAc 'select version_num from alembic_version' | tr -d '[:space:]')
[ -n "$DB_REVISION" ] && grep -REqs \
  "^revision(:[^=]+)?[[:space:]]*=[[:space:]]*['\"]${DB_REVISION}['\"]" \
  "$PREVIOUS_RELEASE/backend/migrations/versions" || fail "schema compatibility not proven; no automatic DB downgrade"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)-$$
[ ! -L "$ROOT/static-receipts" ] || fail "unsafe static receipts directory"
install -d -m 700 "$ROOT/static-receipts"
python3 "$VERIFIER_RELEASE/scripts/verify_image_static.py" \
  --image "$PREVIOUS_IMAGE" --expected-image-id "$PREVIOUS_IMAGE" \
  --revision "$REVISION" --legacy-image \
  --receipt "$ROOT/static-receipts/${REVISION}-${STAMP}-rollback-preflight.json"
python3 "$VERIFIER_RELEASE/scripts/pin_primary_image.py" \
  --env "$OLD_ENV" --image "$PREVIOUS_IMAGE" --revision "$REVISION"

# Use immutable IDs for all four apps, even if an old env still contains a tag.
OVERRIDE=$(mktemp "$ROOT/runtime/.rollback-compose.XXXXXXXX")
LINK=$ROOT/.rollback-current.$$.tmp
OWNED_LINK=false
cleanup() { rm -f "$OVERRIDE"; if [ "$OWNED_LINK" = true ] && [ -L "$LINK" ]; then unlink "$LINK"; fi; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
printf 'services:\n  backend:\n    image: %s\n  worker:\n    image: %s\n  scheduler:\n    image: %s\n' \
  "$PREVIOUS_IMAGE" "$PREVIOUS_IMAGE" "$PREVIOUS_IMAGE" > "$OVERRIDE"
docker compose --profile cutover --env-file "$OLD_ENV" \
  -f "$PREVIOUS_RELEASE/infra/primary/docker-compose.yml" -f "$OVERRIDE" -p "$PROJECT" \
  config --format json | python3 "$VERIFIER_RELEASE/scripts/validate_primary_compose.py" \
  --project "$PROJECT" --image "$PREVIOUS_IMAGE" --port "$PORT" \
  --volume "${PROJECT}_primary_data" --container-prefix "${PROJECT}-primary"
[ ! -e "$LINK" ] && [ ! -L "$LINK" ] || fail "temporary current link exists"
ln -s "$PREVIOUS_RELEASE" "$LINK"
OWNED_LINK=true
mv -Tf "$LINK" "$ROOT/current"
docker compose --profile cutover --env-file "$OLD_ENV" \
  -f "$PREVIOUS_RELEASE/infra/primary/docker-compose.yml" -f "$OVERRIDE" \
  -p "$PROJECT" up -d --no-build --force-recreate --wait --wait-timeout 180 backend worker scheduler
python3 "$VERIFIER_RELEASE/scripts/verify_image_static.py" \
  --image "$PREVIOUS_IMAGE" --expected-image-id "$PREVIOUS_IMAGE" \
  --revision "$REVISION" --legacy-image --compose-project "$PROJECT" \
  --base-url "$PUBLIC_ORIGIN" \
  --receipt "$ROOT/static-receipts/${REVISION}-${STAMP}-rollback-https.json"
echo "complete primary application rollback verified: release=$REVISION image=$PREVIOUS_IMAGE"
