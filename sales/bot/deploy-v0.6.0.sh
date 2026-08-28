#!/usr/bin/env bash
set -euo pipefail

version=v0.6.0
previous_version=v0.5.2
root=/opt/pu-workspace-sales-bot
release="$root/releases/$version"
archive="/opt/pu-workspace-sales-bot-$version.tar.gz"
backup_dir="$root/backups"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
old_container=pu-workspace-sales-bot
rollback_container="pu-workspace-sales-bot-$previous_version-$stamp"

test -f "$root/.env"
test -f "$archive"
mkdir -p "$release" "$backup_dir"
tar -xzf "$archive" -C "$release"

docker build \
  --build-arg BASE_IMAGE=pu-workspace-sales-bot:$previous_version \
  -t pu-workspace-sales-bot:$version "$release"

http_proxy=$(docker inspect "$old_container" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^HTTP_PROXY=//p')
https_proxy=$(docker inspect "$old_container" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^HTTPS_PROXY=//p')
no_proxy=$(docker inspect "$old_container" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^NO_PROXY=//p')
test -n "$http_proxy"
test -n "$https_proxy"

docker run --rm \
  --env-file "$root/.env" \
  -e HTTP_PROXY="$http_proxy" -e HTTPS_PROXY="$https_proxy" -e NO_PROXY="$no_proxy" \
  pu-workspace-sales-bot:$version \
  python -c "from app.config import Settings; from app.telegram import TelegramClient; assert TelegramClient(Settings.from_env().token, 30).call('getMe')['username'] == 'puworkspace_bot'"

volume_path=$(docker volume inspect pu_workspace_sales_bot_data --format '{{.Mountpoint}}')
test -f "$volume_path/sales_bot.sqlite3"
cp "$volume_path/sales_bot.sqlite3" "$backup_dir/sales_bot-$stamp.sqlite3"

docker stop "$old_container"
docker rename "$old_container" "$rollback_container"

rollback() {
  docker rm -f "$old_container" >/dev/null 2>&1 || true
  docker rename "$rollback_container" "$old_container"
  docker start "$old_container" >/dev/null
}
trap rollback ERR

docker run -d \
  --name "$old_container" \
  --restart unless-stopped \
  --env-file "$root/.env" \
  -e HTTP_PROXY="$http_proxy" -e HTTPS_PROXY="$https_proxy" -e NO_PROXY="$no_proxy" \
  -v pu_workspace_sales_bot_data:/app/data \
  pu-workspace-sales-bot:$version >/dev/null

sleep 8
test "$(docker inspect "$old_container" --format '{{.State.Status}}')" = running
docker logs "$old_container" 2>&1 | grep -q 'sales bot started'
docker exec "$old_container" python -c "from app.config import Settings; from app.telegram import TelegramClient; assert TelegramClient(Settings.from_env().token, 30).call('getMe')['username'] == 'puworkspace_bot'"

trap - ERR
echo "DEPLOY_OK $version $stamp rollback=$rollback_container"
