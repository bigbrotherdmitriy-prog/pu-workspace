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
from urllib.parse import quote

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
    "puw_v54_test_storage", "puw_mvp4_test_runtime",
)
PHASES: list[dict] = []
CREATED: list[str] = []


def test_nodes(path: str, *names: str) -> tuple[str, ...]:
    return tuple(path + "::" + name for name in names)


# Pin individual proofs: a removed/renamed test must fail collection, not reduce coverage.
MVP_TESTS = {
    "postgres_mvp1_storage": test_nodes(
        "backend/tests/test_mvp1_storage_mutation_postgres_runtime.py",
        "test_postgres_resolver_serializes_overlapping_transactions",
        "test_postgres_worker_crash_reconciles_without_double_provider_effect",
    ),
    "postgres_gmail_history": (
        *test_nodes("backend/tests/test_mvp2_gmail_history_cursor_postgres.py",
                    "test_postgresql_concurrent_checkpoint_claim_has_one_winner"),
        *test_nodes("backend/tests/test_mvp2_gmail_history_migration.py",
                    "test_postgresql_history_schema_and_cas_constraints"),
    ),
    "postgres_mvp3_runtime": (
        *test_nodes("backend/tests/test_mvp3_management_acceptance_postgres.py",
                    "test_postgresql_obligation_cas_has_one_winner"),
        *test_nodes("backend/tests/test_mvp3_management_runtime_postgres.py",
                    "test_postgresql_digest_is_single_after_scheduler_race_restart_and_replay"),
    ),
    "postgres_mvp4_finance": test_nodes(
        "backend/tests/test_mvp4_finance_postgres_runtime.py",
        "test_postgres_concurrent_payment_confirmation_creates_one_fact",
        "test_postgres_competing_payment_corrections_are_cas_serialized",
    ),
}
MANDATORY_POSTGRES = (*MVP_TESTS, "postgres_abc_integration")
TEST_DATABASE_KEYS = (
    "TEST_POSTGRES_DSN", "PUW_MVP4_TEST_DATABASE_URL",
    "PUW_V54_TEST_DATABASE_URL", "PUW_V54_SOURCE_TEST_DATABASE_URL",
    "PUW_V54_CONTEXT_TEST_DATABASE_URL", "PUW_V54_MAILBOX_TEST_DATABASE_URL",
    "PUW_V54_INTEGRATION_DATABASE_URL", "PUW_V54_PROVIDER_MIGRATION_DATABASE_URL",
    "PUW_MVP3_TEST_DATABASE_URL", "PUW_MVP2_GMAIL_HISTORY_DATABASE_URL",
    "PUW_V54_AUTHORITY_DATABASE_URL", "PUW_V54_AUTHORITY_MIGRATION_DATABASE_URL",
    "PUW_V54_MATERIALIZATION_DATABASE_URL", "PUW_V54_LOCAL_UPLOAD_DATABASE_URL",
)
SUMMARY_LINE = re.compile(
    r"^(?:=+ )?(?P<counts>\d+ (?:passed|failed|skipped|deselected|xfailed|xpassed|errors?|warnings?)"
    r"(?:, \d+ (?:passed|failed|skipped|deselected|xfailed|xpassed|errors?|warnings?))*)"
    r" in \d+(?:\.\d+)?s(?: \([^\n]*\))?(?: =+)?$"
)
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
    if (host not in {"localhost", "127.0.0.1", "db"}
            and not (host == "postgres" and os.environ.get("GITHUB_ACTIONS") == "true")):
        raise RuntimeError("unsafe_database_host")
    if database not in (*DATABASES, "postgres") or not re.fullmatch(r"[a-z0-9_-]+", user):
        raise RuntimeError("unsafe_database_identity")
    return f"postgresql+psycopg://{user}:{quote(password, safe='')}@{host}:5432/{database}"


def admin_connect():
    return psycopg.connect(base_url("postgres").replace("postgresql+psycopg://", "postgresql://"),
                           autocommit=True, connect_timeout=5)


def run_phase(name: str, args: list[str], *, env: dict | None = None, timeout: int = 600,
              cwd: Path = ROOT) -> str:
    started = time.monotonic()
    try:
        result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True,
                                timeout=timeout, errors="replace")
    except (subprocess.TimeoutExpired, OSError):
        PHASES.append({"name": name, "exit": None, "status": "ERROR",
                       "seconds": round(time.monotonic() - started, 2), "raw_published": False})
        raise RuntimeError(name + "_execution_failed") from None
    record = {"name": name, "exit": result.returncode,
              "status": "FAIL" if result.returncode else "PASS",
              "seconds": round(time.monotonic() - started, 2),
              "stdout_bytes": len(result.stdout.encode("utf8")),
              "stderr_bytes": len(result.stderr.encode("utf8")), "raw_published": False}
    summaries = [match for line in result.stdout.splitlines()
                 if (match := SUMMARY_LINE.fullmatch(line.strip()))]
    counts = {}
    if summaries:
        counts = {label: int(count) for count, label in re.findall(
            r"(\d+) ([a-z]+)", summaries[-1].group("counts"))}
        record["passed"] = counts.get("passed", 0)
        record["skipped"] = counts.get("skipped", 0)
    if name in MANDATORY_POSTGRES:
        required = len(MVP_TESTS[name]) if name in MVP_TESTS else None
        if required is not None:
            record["required"] = required
        if not result.returncode:
            if counts.get("skipped", 0):
                record["status"] = "SKIPPED"
            elif (not counts.get("passed")
                  or (required is not None and counts.get("passed") != required)
                  or any(counts.get(key, 0) for key in (
                      "failed", "error", "errors", "xfailed", "xpassed", "deselected"))):
                record["status"] = "INCOMPLETE"
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
                if (isinstance(probe, dict) and probe.get("status") == "FAIL" and checkpoint in PROBE_CHECKPOINTS
                        and isinstance(error_type, str)
                        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", error_type)):
                    record["failure_checkpoint"] = checkpoint
                    record["child_error_type"] = error_type
                    break
    PHASES.append(record)
    if result.returncode:
        raise RuntimeError(name + "_failed")
    if record["status"] != "PASS":
        raise RuntimeError(name + "_mandatory_coverage_failed")
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
    failed = False
    with admin_connect() as connection:
        for name in reversed(tuple(CREATED)):
            try:
                if name not in DATABASES:
                    raise RuntimeError("cleanup_database_not_owned")
                connection.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (name,))
                connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                CREATED.remove(name)
            except Exception:
                failed = True
    if failed:
        raise RuntimeError("owned_database_cleanup_failed")


def test_env() -> dict:
    env = dict(os.environ)
    # Parent-shell flags must not activate unowned databases or suppress mandatory proofs.
    for key in (*TEST_DATABASE_KEYS, "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(key, None)
    env.update({
        "PYTHONPATH": str(ROOT / "backend"),
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "PU_TEST_POSTGRES": "0",
        "TEST_POSTGRES_DSN": base_url("puw_v54_test_storage"),
        "PUW_MVP4_TEST_DATABASE_URL": base_url("puw_mvp4_test_runtime"),
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
    if result == "PASS" or runtime:
        runtime = validate_runtime_records(runtime)
    coverage = {name: next((phase["status"] for phase in PHASES if phase["name"] == name),
                           "NOT_RUN") for name in MANDATORY_POSTGRES}
    if result == "PASS" and any(status != "PASS" for status in coverage.values()):
        raise RuntimeError("mandatory_postgres_coverage_incomplete")
    protocol = {
        "schema": "puw.v54.runtime.protocol.v1", "result": result, "head": HEAD,
        "commit": os.environ.get("GITHUB_SHA", "local"), "phases": PHASES,
        "runtime": runtime,
        "mandatory_postgres": coverage,
        "coverage_limits": {
            "mvp1": "synthetic adapter and simulated crash; no live provider or process kill",
            "mvp2": "Gmail cursor CAS and migrated schema; no live mailbox or full worker recovery",
            "mvp3": "obligation CAS and digest restart/replay; no live channel",
            "mvp4": "manual payment and correction concurrency; no supply concurrency or backup restore",
        },
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


def migrate_database(phase: str, database: str, env: dict) -> None:
    if database not in CREATED:
        raise RuntimeError("migration_database_not_owned")
    run_phase(phase, [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", HEAD],
              env=dict(env, DATABASE_URL=base_url(database)), timeout=180, cwd=ROOT / "backend")
    with psycopg.connect(base_url(database).replace("postgresql+psycopg://", "postgresql://"),
                          connect_timeout=5) as db:
        if db.execute("SELECT version_num FROM alembic_version").fetchone()[0] != HEAD:
            for record in reversed(PHASES):
                if record["name"] == phase:
                    record["status"] = "FAIL"
                    record["schema_verified"] = False
                    break
            raise RuntimeError("migration_head_mismatch")


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
        migrate_database("migration", "puw_v54_test_migrations", env)
        migrate_database("storage_migration", "puw_v54_test_storage", env)
        run_phase("postgres_mvp1_storage", [
            sys.executable, "-m", "pytest",
            *MVP_TESTS["postgres_mvp1_storage"],
            "-q", "--tb=short", "-rfsE",
        ], env=env, timeout=300)
        migrate_database("mvp3_migration", "puw_mvp3_test_runtime", env)
        run_phase("postgres_mvp3_runtime", [
            sys.executable, "-m", "pytest", *MVP_TESTS["postgres_mvp3_runtime"],
            "-q", "--tb=short", "-rfsE",
        ], env=env, timeout=300)
        migrate_database("gmail_history_migration", "puw_mvp2_test_gmail_history", env)
        run_phase("postgres_gmail_history", [
            sys.executable, "-m", "pytest", *MVP_TESTS["postgres_gmail_history"],
            "-q", "--tb=short", "-rfsE",
        ], env=env, timeout=180)
        migrate_database("mvp4_migration", "puw_mvp4_test_runtime", env)
        run_phase("postgres_mvp4_finance", [
            sys.executable, "-m", "pytest", *MVP_TESTS["postgres_mvp4_finance"],
            "-q", "--tb=short", "-rfsE",
        ], env=env, timeout=300)
        run_phase("backend_full", [sys.executable, "-m", "pytest", "backend/tests", "-q", "--tb=short", "-rs"],
                  env=dict(env, **{key: "" for key in TEST_DATABASE_KEYS}), timeout=900)
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
