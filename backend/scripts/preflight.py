import json

from app.core.readiness import readiness_report


report = readiness_report()
print(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(0 if report["ready"] else 1)
