"""Safe CI orchestrator for the isolated v5.4 PostgreSQL acceptance.

Subprocess output is retained only in memory and never emitted or attached.
The artifact contains allowlisted counters/statuses and no DSN or source text.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from alembic.config import Config
from alembic.script import ScriptDirectory
import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "v54-runtime-artifacts" / "protocol.json"
HEAD = "a54f001c0a18"
DATABASES = (
    "puw_v54_test_migrations", "puw_v54_test_foundation", "puw_v54_test_runtime",
    "puw_mvp3_test_runtime",
    "puw_mvp2_test_gmail_history",
)
PHASES: list[dict] = []
CREATED: list[str] = []
PYTEST_FAILURE = re.compile(
    r"(?m)^(?:FAILED|ERROR) "
    r"(?P<nodeid>(?:backend|scripts)/[A-Za-z0-9_./-]+\.py"
    r"(?:::[A-Za-z_][A-Za-z0-9_]*)+)"
)
PYTEST_LOCATION = re.compile(
    r"(?m)^(?P<location>(?:backend|scripts)/[A-Za-z0-9_./-]+\.py:[1-9][0-9]*):"
)
PROBE_CHECKPOINTS = {
    "require_database", "fixture_setup", "prepare", "enqueue", "first_claim",
    "rival_no_claim", "first_terminated", "lease_recovery", "stale_owner_rejected",
    "second_claim", "second_completion", "invariants", "cleanup",
    "s07_fixture_setup", "s07_t1_committed", "s07_producer_terminated",
    "s07_recover_enqueue", "s07_second_worker", "t2_rollback_fixture_setup",
    "t2_flushed_before_commit", "t2_uncommitted_worker_terminated",
    "s08_fixture_setup", "s08_t2_committed", "s08_worker_terminated",
}
UUID_VALUE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def exact(expected):
    return lambda value: type(value) is type(expected) and value == expected


def nonnegative_int(value) -> bool:
    return type(value) is int and 0 <= value <= 2 ** 63 - 1


def positive_int(value) -> bool:
    return type(value) is int and 0 < value <= 2 ** 63 - 1


def uuid_value(value) -> bool:
    return isinstance(value, str) and UUID_VALUE.fullmatch(value) is not None


COUNTER_SCHEMA = {
    "tasks": nonnegative_int,
    "history": nonnegative_int,
    "receipts": nonnegative_int,
    "projections": nonnegative_int,
    "success_audits": nonnegative_int,
    "job_status": exact("completed"),
}
RUNTIME_RECORD_SCHEMAS = (
    {
        "probe": exact("process_reclaim"),
        "job_id": positive_int,
        "receipt_id": uuid_value,
        "status": exact("PASS"),
        "expiry": exact("accelerated"),
        "external_effects": exact("not_tested"),
        **COUNTER_SCHEMA,
    },
    {
        "probe": exact("s07_intent_recovery"),
        "case": exact("S07"),
        "status": exact("PASS"),
        "process_kill": exact(True),
        "pending_before_recovery": nonnegative_int,
        "jobs_before_recovery": nonnegative_int,
        "jobs_after_recovery": nonnegative_int,
        "receipt_id": uuid_value,
        **COUNTER_SCHEMA,
    },
    {
        "probe": exact("t2_precommit_rollback"),
        "case": exact("S08-precommit"),
        "status": exact("PASS"),
        "process_kill": exact(True),
        "uncommitted_tasks": nonnegative_int,
        "uncommitted_receipts": nonnegative_int,
        "receipt_id": uuid_value,
        **COUNTER_SCHEMA,
    },
    {
        "probe": exact("s08_receipt_replay"),
        "case": exact("S08"),
        "status": exact("PASS"),
        "process_kill": exact(True),
        "job_before_reclaim": exact("running"),
        "receipt_id": uuid_value,
        **COUNTER_SCHEMA,
    },
    {"cleanup": exact("test_schema_dropped")},
)


def validate_runtime_records(runtime: list[dict]) -> list[dict]:
    """Copy only the exact, typed child protocol; reject everything else."""
    if type(runtime) is not list or len(runtime) != len(RUNTIME_RECORD_SCHEMAS):
        raise RuntimeError("runtime_protocol_schema_invalid")
    validated = []
    for record, schema in zip(runtime, RUNTIME_RECORD_SCHEMAS):
        if type(record) is not dict or set(record) != set(schema):
            raise RuntimeError("runtime_protocol_schema_invalid")
        if any(not validator(record[field]) for field, validator in schema.items()):
            raise RuntimeError("runtime_protocol_schema_invalid")
        validated.append({field: record[field] for field in schema})
    return validated


def parse_runtime_output(output: str) -> list[dict]:
    records = [json.loads(line) for line in output.splitlines() if line.strip()]
    return validate_runtime_records(records)


def base_url(database: str) -> str:
    host = os.environ.get("POSTGRES_HOST", "postgres")
    user = os.environ.get("POSTGRES_USER", "puw_ci")
    password = os.environ["POSTGRES_PASSWORD"]
    if not re.fullmatch(r"[a-z0-9_-]+", host) or not re.fullmatch(r"[a-z0-9_-]+", user):
        raise RuntimeError("unsafe_database_identity")
    return f"postgresql+psycopg://{user}:{password}@{host}:5432/{database}"


def admin_connect():
    return psycopg.connect(base_url("postgres").replace("postgresql+psycopg://", "postgresql://"),
                           autocommit=True, connect_timeout=5)


def run_phase(name: str, args: list[str], *, env: dict | None = None, timeout: int = 600,
              cwd: Path = ROOT) -> str:
    started = time.monotonic()
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True,
                            timeout=timeout, errors="replace")
    record = {"name": name, "exit": result.returncode,
              "seconds": round(time.monotonic() - started, 2),
              "stdout_bytes": len(result.stdout.encode("utf8")),
              "stderr_bytes": len(result.stderr.encode("utf8")), "raw_published": False}
    summary = re.search(r"(?P<passed>\d+) passed(?:, (?P<skipped>\d+) skipped)?", result.stdout)
    if summary:
        record["passed"] = int(summary.group("passed"))
        record["skipped"] = int(summary.group("skipped") or 0)
    if result.returncode:
        failed_nodeids = list(dict.fromkeys(
            match.group("nodeid") for match in PYTEST_FAILURE.finditer(result.stdout)
        ))[:20]
        if failed_nodeids:
            record["failed_nodeids"] = failed_nodeids
        failure_locations = list(dict.fromkeys(
            match.group("location") for match in PYTEST_LOCATION.finditer(result.stdout)
        ))[:20]
        if failure_locations:
            record["failure_locations"] = failure_locations
        if name == "postgres_process_fault":
            for line in result.stdout.splitlines():
                try:
                    probe = json.loads(line)
                except (TypeError, ValueError):
                    continue
                checkpoint = probe.get("checkpoint") if isinstance(probe, dict) else None
                error_type = probe.get("error_type") if isinstance(probe, dict) else None
                if (probe.get("status") == "FAIL" and checkpoint in PROBE_CHECKPOINTS
                        and isinstance(error_type, str)
                        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", error_type)):
                    record["failure_checkpoint"] = checkpoint
                    record["child_error_type"] = error_type
                    break
    PHASES.append(record)
    if result.returncode:
        raise RuntimeError(name + "_failed")
    return result.stdout


def create_databases() -> None:
    with admin_connect() as connection:
        existing = {row[0] for row in connection.execute(
            "SELECT datname FROM pg_database WHERE datname = ANY(%s)", (list(DATABASES),))}
        if existing:
            raise RuntimeError("test_database_preexists")
        for name in DATABASES:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            CREATED.append(name)


def cleanup_databases() -> None:
    if not CREATED:
        return
    with admin_connect() as connection:
        for name in reversed(CREATED):
            connection.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (name,))
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_env() -> dict:
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(ROOT / "backend"),
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "PUW_V54_TEST_DATABASE_URL": base_url("puw_v54_test_foundation"),
        "PUW_V54_SOURCE_TEST_DATABASE_URL": base_url("puw_v54_test_runtime"),
        "PUW_V54_CONTEXT_TEST_DATABASE_URL": base_url("puw_v54_test_runtime"),
        "PUW_V54_MAILBOX_TEST_DATABASE_URL": base_url("puw_v54_test_runtime"),
        "PUW_V54_INTEGRATION_DATABASE_URL": base_url("puw_v54_test_runtime"),
        "PUW_V54_PROVIDER_MIGRATION_DATABASE_URL": base_url("puw_v54_test_migrations"),
        "PUW_MVP3_TEST_DATABASE_URL": base_url("puw_mvp3_test_runtime"),
        "PUW_MVP2_GMAIL_HISTORY_DATABASE_URL": base_url("puw_mvp2_test_gmail_history"),
        "GMAIL_AUTO_SYNC_ENABLED": "false",
        "AI_SECRETARY_AUTOMATION_ENABLED": "false",
    })
    return env


def write_protocol(result: str, failure: BaseException | None, runtime: list[dict]) -> None:
    if result == "PASS":
        runtime = validate_runtime_records(runtime)
    elif runtime:
        raise RuntimeError("runtime_protocol_schema_invalid")
    protocol = {
        "schema": "puw.v54.runtime.protocol.v1", "result": result, "head": HEAD,
        "commit": os.environ.get("GITHUB_SHA", "local"), "phases": PHASES,
        "runtime": runtime,
        "corpus": {
            "structural": "PASS" if any(p["name"] == "corpus" and p["exit"] == 0 for p in PHASES) else "FAIL",
            "executed_cases": ["C01", "C07", "P02", "P06", "S02", "S06", "S07", "S08", "S09"],
            "expected_gaps": {
                "S10": "external UNKNOWN remains fake-contract only",
                "P04": "finance outside pilot",
            },
        },
        "cleanup": "PASS" if not CREATED else "FAIL",
        "failure_type": type(failure).__name__ if failure else None,
        "raw_output_published": False,
    }
    forbidden = [os.environ.get(name, "") for name in (
        "POSTGRES_PASSWORD", "APP_SECRET_KEY", "BOOTSTRAP_TOKEN", "TOKEN_ENCRYPTION_KEY")]
    encoded = json.dumps(protocol, ensure_ascii=True, sort_keys=True, indent=2)
    if any(value and value in encoded for value in forbidden):
        raise RuntimeError("secret_in_protocol")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(encoded + "\n", encoding="utf8")


def main() -> None:
    failure = None
    runtime: list[dict] = []
    try:
        config = Config(str(ROOT / "backend/alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "backend/migrations"))
        if ScriptDirectory.from_config(config).get_heads() != [HEAD]:
            raise RuntimeError("unexpected_alembic_head")
        create_databases()
        env = test_env()
        migration_env = dict(env, DATABASE_URL=base_url("puw_v54_test_migrations"))
        run_phase("migration", [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", HEAD],
                  env=migration_env, timeout=180, cwd=ROOT / "backend")
        with psycopg.connect(base_url("puw_v54_test_migrations").replace("postgresql+psycopg://", "postgresql://"), connect_timeout=5) as db:
            if db.execute("SELECT version_num FROM alembic_version").fetchone()[0] != HEAD:
                raise RuntimeError("migration_head_mismatch")
        run_phase("postgres_mvp3_runtime", [
            sys.executable, "-m", "pytest",
            "backend/tests/test_mvp3_management_acceptance_postgres.py",
            "backend/tests/test_mvp3_management_runtime_postgres.py",
            "-q", "--tb=short", "-rfsE",
        ], env=env, timeout=300)
        run_phase("gmail_history_migration", [
            sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", HEAD,
        ], env=dict(env, DATABASE_URL=base_url("puw_mvp2_test_gmail_history")),
            timeout=180, cwd=ROOT / "backend")
        run_phase("postgres_gmail_history", [
            sys.executable, "-m", "pytest",
            "backend/tests/test_mvp2_gmail_history_cursor_postgres.py",
            "backend/tests/test_mvp2_gmail_history_migration.py",
            "-q", "--tb=short", "-rfsE",
        ], env=env, timeout=180)
        run_phase("backend_full", [sys.executable, "-m", "pytest", "backend/tests", "-q", "--tb=short", "-rs"],
                  env=dict(env, PUW_V54_TEST_DATABASE_URL="", PUW_V54_SOURCE_TEST_DATABASE_URL="",
                           PUW_V54_CONTEXT_TEST_DATABASE_URL="", PUW_V54_MAILBOX_TEST_DATABASE_URL="",
                           PUW_V54_INTEGRATION_DATABASE_URL="",
                           PUW_V54_PROVIDER_MIGRATION_DATABASE_URL="",
                           PUW_MVP3_TEST_DATABASE_URL="",
                           PUW_MVP2_GMAIL_HISTORY_DATABASE_URL=""), timeout=900)
        targets = [
            "backend/tests/test_v54_pilot_foundation.py",
            "backend/tests/test_v54_source_evidence_pilot.py", "backend/tests/test_v54_source_evidence_postgres.py",
            "backend/tests/test_v54_context_communication.py", "backend/tests/test_v54_context_communication_postgres.py",
            "backend/tests/test_v54_mailbox_identity_postgres.py",
            "backend/tests/test_v54_task_claims.py", "backend/tests/test_v54_action_trust.py",
            "backend/tests/test_v54_action_trust_external_contract.py",
            "backend/tests/test_v54_provider_action_migration.py",
            "backend/tests/test_v54_autonomy_authorization.py",
            "backend/tests/test_v54_staging_safety_hardening.py",
            "backend/tests/test_v54_pilot_integration.py",
            "backend/tests/test_v54_product_acceptance.py",
            "backend/tests/test_v54_c01_content_pipeline.py",
            "backend/tests/test_v54_corpus_confirm_subset.py",
        ]
        run_phase("postgres_abc_integration", [sys.executable, "-m", "pytest", *targets, "-q", "--tb=short", "-rfsE"], env=env, timeout=900)
        corpus = run_phase("corpus", [sys.executable, "docs/acceptance/v54-corpus/validate.py", "--self-test"], timeout=120)
        corpus_result = json.loads(corpus)
        if corpus_result.get("structural") != "PASS" or corpus_result.get("cases") != 28:
            raise RuntimeError("corpus_contract_failed")
        run_phase("durable_gzip_regression", [sys.executable, "-m", "pytest",
            "scripts/ci/durable_queue/test_contract.py", "scripts/ci/durable_queue/test_run.py", "-q", "--tb=short"], env=env, timeout=180)
        output = run_phase("postgres_process_fault", [sys.executable, "scripts/ci/v54_pilot_runtime.py"], env=env, timeout=180)
        runtime = parse_runtime_output(output)
    except BaseException as exc:
        failure = exc
    finally:
        try:
            cleanup_databases()
            CREATED.clear()
        except BaseException as cleanup_error:
            failure = failure or cleanup_error
        write_protocol("PASS" if failure is None else "FAIL", failure, runtime)
    if failure:
        print("v5.4 runtime failed; raw diagnostics withheld")
        raise SystemExit(1)
    print("v5.4 runtime PASS; safe protocol created")


if __name__ == "__main__":
    main()
