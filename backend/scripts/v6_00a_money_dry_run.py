"""Read-only V6-00a before/after money report.

Run against the pre-migration database after the deployment backup has been
verified. The command never commits or mutates database state.
"""

import argparse
import json
from pathlib import Path

from app.database import SessionLocal
from app.money_migration_audit import assert_money_dry_run_safe, build_money_dry_run_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--expected-currency", default="RUB")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with SessionLocal() as db:
        report = build_money_dry_run_report(
            db,
            project_id=args.project_id,
            expected_currency=args.expected_currency,
        )
        db.rollback()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"V6-00a dry-run {report['status']}: project={report['project_id']} "
        f"values={report['value_count']} changed={report['changed_value_count']} "
        f"currency_mismatches={report['currency_mismatch_count']}"
    )
    try:
        assert_money_dry_run_safe(report)
    except RuntimeError as exc:
        print(str(exc))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
