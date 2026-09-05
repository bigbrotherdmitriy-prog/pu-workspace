#!/bin/sh
set -eu
: "${DATABASE_URL:?DATABASE_URL is required}"
output="${1:-background-jobs-$(date -u +%Y%m%dT%H%M%SZ).dump}"
umask 077
pg_dump --format=custom --no-owner --no-privileges \
  --table=background_jobs --table=service_heartbeats \
  --file="$output" "$DATABASE_URL"
test -s "$output"
echo "Queue backup created: $output"
