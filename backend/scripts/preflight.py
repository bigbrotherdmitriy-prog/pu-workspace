import argparse
import json

from app.core.readiness import readiness_report


parser = argparse.ArgumentParser()
parser.add_argument(
    "--startup",
    action="store_true",
    help="Check configuration and migrated schema before dependent services start.",
)
args = parser.parse_args()
report = readiness_report(include_live_services=not args.startup)
print(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(0 if report["ready"] else 1)
