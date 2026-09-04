#!/usr/bin/env bash
set -euo pipefail

root=/opt/pu-workspace-landing
log=/var/log/caddy/puworkspace-access.log
report_dir="$root/reports"
database_root=$(docker volume inspect pu_workspace_sales_bot_data --format '{{.Mountpoint}}')
database="$database_root/sales_bot.sqlite3"

test -f "$log"
test -f "$database"
mkdir -p "$report_dir"
umask 077
python3 "$root/campaign_report.py" "$log" --database "$database" > "$report_dir/latest.txt.tmp"
mv "$report_dir/latest.txt.tmp" "$report_dir/latest.txt"
