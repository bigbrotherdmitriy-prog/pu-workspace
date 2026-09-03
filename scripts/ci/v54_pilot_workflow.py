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
HEAD = "a54f001c0a02"
DATABASES = ("puw_v54_test_migrations", "puw_v54_test_foundation", "puw_v54_test_runtime")
PHASES: list[dict] = []
CREATED: list[str] = []


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
        "PUW_V54_INTEGRATION_DATABASE_URL": base_url("puw_v54_test_runtime"),
        "GMAIL_AUTO_SYNC_ENABLED": "false",
        "AI_SECRETARY_AUTOMATION_ENABLED": "false",
    })
    return env


def write_protocol(result: str, failure: BaseException | None, runtime: list[dict]) -> None:
    protocol = {
        "schema": "puw.v54.runtime.protocol.v1", "result": result, "head": HEAD,
        "commit": os.environ.get("GITHUB_SHA", "local"), "phases": PHASES,
        "runtime": runtime,
        "corpus": {
            "structural": "PASS" if any(p["name"] == "corpus" and p["exit"] == 0 for p in PHASES) else "FAIL",
            "executed_cases": ["P02", "P06", "S06", "S09"],
            "expected_gaps": {
                "C01": "content extraction and corpus due-date input are not wired to the synthetic fixture",
                "C07": "time-of-day claim unsupported",
                "S02": "legacy global message identity cutover unresolved",
                "S07": "process kill between T1 and enqueue not exercised",
                "S08": "process kill inside T2 before commit not exercised; transactional rollback is exercised",
                "S10": "external UNKNOWN remains fake-contract only",
                "P04": "finance outside pilot", "P06_AUTO": "AUTO remains denied",
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
        run_phase("backend_full", [sys.executable, "-m", "pytest", "backend/tests", "-q", "--tb=short", "-rs"],
                  env=dict(env, PUW_V54_TEST_DATABASE_URL="", PUW_V54_SOURCE_TEST_DATABASE_URL="",
                           PUW_V54_CONTEXT_TEST_DATABASE_URL="", PUW_V54_INTEGRATION_DATABASE_URL=""), timeout=900)
        targets = [
            "backend/tests/test_v54_pilot_foundation.py",
            "backend/tests/test_v54_source_evidence_pilot.py", "backend/tests/test_v54_source_evidence_postgres.py",
            "backend/tests/test_v54_context_communication.py", "backend/tests/test_v54_context_communication_postgres.py",
            "backend/tests/test_v54_task_claims.py", "backend/tests/test_v54_action_trust.py",
            "backend/tests/test_v54_action_trust_external_contract.py",
            "backend/tests/test_v54_pilot_integration.py", "backend/tests/test_v54_corpus_confirm_subset.py",
        ]
        run_phase("postgres_abc_integration", [sys.executable, "-m", "pytest", *targets, "-q", "--tb=short", "-rs"], env=env, timeout=900)
        corpus = run_phase("corpus", [sys.executable, "docs/acceptance/v54-corpus/validate.py", "--self-test"], timeout=120)
        corpus_result = json.loads(corpus)
        if corpus_result.get("structural") != "PASS" or corpus_result.get("cases") != 28:
            raise RuntimeError("corpus_contract_failed")
        run_phase("durable_gzip_regression", [sys.executable, "-m", "pytest",
            "scripts/ci/durable_queue/test_contract.py", "scripts/ci/durable_queue/test_run.py", "-q", "--tb=short"], env=env, timeout=180)
        output = run_phase("postgres_process_fault", [sys.executable, "scripts/ci/v54_pilot_runtime.py"], env=env, timeout=180)
        runtime = [json.loads(line) for line in output.splitlines() if line.strip()]
        if not runtime or runtime[-1].get("cleanup") != "test_schema_dropped" or runtime[0].get("status") != "PASS":
            raise RuntimeError("runtime_protocol_failed")
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
