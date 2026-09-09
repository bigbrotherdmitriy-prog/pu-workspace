"""Fail-closed synthetic canary gate for the minimum daily-work chain.

The gate deliberately calls only existing regression tests.  It never imports
provider credentials, calls an external API, or prints captured pytest output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit


PROTOCOL_VERSION = 1
EXPECTED_SCHEMA = "a54f001c0a22"
SCOPE_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{5,63}")
FORBIDDEN_ENV_MARKERS = (
    "AI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_ACCESS_TOKEN",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN",
    "GMAIL_ACCESS_TOKEN",
    "GMAIL_REFRESH_TOKEN",
    "OPENAI_API_KEY",
    "YANDEX_ACCESS_TOKEN",
    "YANDEX_CLIENT_SECRET",
    "TELEGRAM_BOT_TOKEN",
)

PROBES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("storage_binding", (
        "backend/tests/test_storage_binding_validation.py::test_confirm_persists_exact_binding_and_returns_job",
    )),
    ("content_analysis", (
        "backend/tests/test_storage_binding_validation.py::test_snapshot_analysis_reports_measured_progress_and_bound_result",
    )),
    ("contract", (
        "backend/tests/test_contract_document_control_flow.py::test_contract_analysis_keeps_local_document_provenance_in_dashboard",
    )),
    ("wbs", (
        "backend/tests/test_v7_schedule_wbs.py::test_wbs_hierarchy_order_and_rollup_are_persisted",
        "backend/tests/test_v7_schedule_wbs.py::test_summary_cannot_receive_fact_or_financial_link",
    )),
    ("dds", (
        "backend/tests/test_mvp4_budget_dds.py::test_low_confidence_cash_flow_remains_manual_review_proposal",
        "backend/tests/test_mvp4_budget_dds.py::test_payment_cannot_confirm_unapproved_proposal",
    )),
    ("finance_forecast", (
        "backend/tests/test_mvp4_explainable_forecast.py::test_cash_forecast_uses_actual_before_plan_and_exposes_gap",
        "backend/tests/test_mvp4_explainable_forecast.py::test_forecast_is_deterministic_draft_and_cannot_trigger_actions",
    )),
    ("task", (
        "backend/tests/test_v54_task_claims.py::test_actual_high_confidence_evidence_still_needs_human_claim_review",
    )),
    ("email", (
        "backend/tests/test_gmail_project_validation.py::test_confirmed_message_discovers_one_reviewable_company_contact",
        "backend/tests/test_gmail_project_validation.py::test_outgoing_does_not_complete_task_without_human_review",
    )),
)


class CanaryRefusal(RuntimeError):
    """Stable error safe for CI output."""


def _validate_environment(env: Mapping[str, str]) -> tuple[str, str]:
    if env.get("CI_CANARY_SYNTHETIC_ONLY") != "true":
        raise CanaryRefusal("synthetic_mode_required")
    scope = env.get("CI_CANARY_SCOPE", "")
    if not SCOPE_PATTERN.fullmatch(scope) or "prod" in scope:
        raise CanaryRefusal("invalid_canary_scope")
    if any(env.get(key) for key in FORBIDDEN_ENV_MARKERS):
        raise CanaryRefusal("provider_credentials_forbidden")
    database_url = env.get("DATABASE_URL", "")
    parsed = urlsplit(database_url)
    database = parsed.path.rsplit("/", 1)[-1].lower()
    if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
        raise CanaryRefusal("postgresql_required")
    if parsed.hostname not in {"127.0.0.1", "localhost", "postgres", "db"}:
        raise CanaryRefusal("isolated_database_host_required")
    if not any(marker in database for marker in ("canary", "test")):
        raise CanaryRefusal("isolated_database_name_required")
    return scope, database


def _scope_digest(scope: str) -> str:
    return hashlib.sha256(("puw-canary-scope-v1:" + scope).encode()).hexdigest()


def _receipt(previous: str, stage: str) -> str:
    return hashlib.sha256((previous + ":" + stage).encode()).hexdigest()


def _default_runner(nodeids: Sequence[str]) -> int:
    root = Path(__file__).resolve().parents[2]
    child_env = dict(os.environ)
    child_env["PYTHONPATH"] = str(root / "backend")
    child_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    pytest_args = [sys.executable, "-m", "pytest", "-q", "--tb=no"]
    if child_env.get("CI_CANARY_PYTEST_TMP"):
        pytest_args.extend(("--basetemp", child_env["CI_CANARY_PYTEST_TMP"]))
    result = subprocess.run(
        [*pytest_args, *nodeids],
        cwd=root,
        env=child_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
        check=False,
    )
    return result.returncode


def run_gate(
    env: Mapping[str, str],
    runner: Callable[[Sequence[str]], int] = _default_runner,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    scope, _database = _validate_environment(env)
    scope_digest = _scope_digest(scope)
    previous = scope_digest
    stages: list[dict[str, object]] = []
    overall = "PASS"
    for name, nodeids in PROBES:
        started = clock()
        try:
            return_code = runner(nodeids)
        except (OSError, subprocess.TimeoutExpired):
            return_code = 124
        elapsed = max(0, round((clock() - started) * 1000))
        status = "PASS" if return_code == 0 else "FAIL"
        overall = "FAIL" if status == "FAIL" else overall
        previous = _receipt(previous, name)
        stages.append({
            "stage": name,
            "status": status,
            "exit_code": return_code,
            "requested_checks": len(nodeids),
            "duration_ms": elapsed,
            "scope_receipt": previous,
        })
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_revision": EXPECTED_SCHEMA,
        "mode": "synthetic_only",
        "external_calls": False,
        "scope_digest": scope_digest,
        "status": overall,
        "stages": stages,
        "raw_output_included": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run_gate(os.environ)
    except CanaryRefusal as exc:
        report = {
            "protocol_version": PROTOCOL_VERSION,
            "schema_revision": EXPECTED_SCHEMA,
            "mode": "refused",
            "external_calls": False,
            "status": "FAIL",
            "reason": str(exc),
            "raw_output_included": False,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("CANARY_READINESS_PASS" if report["status"] == "PASS" else "CANARY_READINESS_FAIL")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
