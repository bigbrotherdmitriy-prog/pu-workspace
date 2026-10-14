#!/bin/sh
# Host watchdog entry point (systemd: pu-watchdog.service / pu-watchdog.timer).
# All logic lives in host_watchdog.py next to this file; see
# docs/operations/MONITORING_RU.md.
#
#   host_watchdog.sh              run checks, alert to Telegram, update state
#   host_watchdog.sh --dry-run    run checks, print what would be sent, write nothing
#   host_watchdog.sh --silence 6h mute alerts (also: --unsilence)
#   host_watchdog.sh --test-alert send one test message
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/env python3 "$here/host_watchdog.py" "$@"
