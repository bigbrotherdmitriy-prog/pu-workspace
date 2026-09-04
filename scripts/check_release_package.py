#!/usr/bin/env python3
"""Read-only commercial release package preflight."""

from __future__ import annotations

import json
import sys
from pathlib import Path


REQUIRED_PATHS = (
    "Dockerfile", "docker-compose.yml", ".env.example", "README.md",
    "backend/migrations/versions", "scripts/deploy-production.sh",
    "scripts/check_public_smoke.py", "scripts/check_pilot_readiness.py",
    "docs/operations.md", "docs/retention-policy.md", "docs/USER_GUIDE_RU.md",
)
REQUIRED_ENV_KEYS = (
    "POSTGRES_PASSWORD", "APP_SECRET_KEY", "TOKEN_ENCRYPTION_KEY", "BOOTSTRAP_TOKEN",
)


def evaluate(root: Path) -> list[dict[str, object]]:
    checks = [{"name": path, "ok": (root / path).exists()} for path in REQUIRED_PATHS]
    example = (root / ".env.example").read_text(encoding="utf-8") if (root / ".env.example").is_file() else ""
    checks.append({"name": "environment placeholders", "ok": all(f"{key}=" in example for key in REQUIRED_ENV_KEYS)})
    ignore = (root / ".gitignore").read_text(encoding="utf-8") if (root / ".gitignore").is_file() else ""
    checks.append({"name": "secrets excluded", "ok": ".env" in {line.strip() for line in ignore.splitlines()}})
    migrations = root / "backend" / "migrations" / "versions"
    checks.append({"name": "database migrations", "ok": migrations.is_dir() and any(migrations.glob("*.py"))})
    return checks


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    checks = evaluate(root)
    print(json.dumps({"ready": all(row["ok"] for row in checks), "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if all(row["ok"] for row in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
