"""Isolated snapshot acceptance; no raw child output is published."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "snapshot-recovery-artifacts/protocol.json"
DATABASE = "puw_v7_test_snapshot_recovery"
HEAD = "a54f001c0a20"
WORK_SECONDS = 360
TOTAL_SECONDS = 420


class ContainmentFailure(RuntimeError):
    pass


class ChildProofFailure(RuntimeError):
    def __init__(self, phase):
        super().__init__("child_proof_failed")
        self.phase = phase


def child_failure_phase(output):
    allowed = {"guard", "imports", "schema", "fixture_seed", "seed_http", "first_walk",
               "second_worker", "kill", "lease_expiry", "recovered", "replay"}
    for line in output.splitlines():
        if len(line) > 160:
            continue
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if (type(value) is dict
                and set(value) == {"status", "phase", "raw_diagnostics_published"}
                and value["status"] == "FAIL"
                and value["raw_diagnostics_published"] is False
                and type(value["phase"]) is str
                and value["phase"] in allowed):
            return value["phase"]
    return None


def environment():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("POSTGRES_HOST") != "db":
        raise ValueError("isolated_ci_required")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    if not password or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for c in password):
        raise ValueError("invalid_test_secret")
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL", "HOME", "TMP", "TEMP"}}
    env.update(DATABASE_URL=f"postgresql+psycopg://puw_ci:{password}@db:5432/{DATABASE}",
               APP_ENV="test", PUW_SNAPSHOT_RECOVERY_TEST="1", PYTHONPATH=str(ROOT / "backend"),
               APP_SECRET_KEY=secrets.token_hex(32), BOOTSTRAP_TOKEN=secrets.token_hex(32),
               TOKEN_ENCRYPTION_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
               PGCONNECT_TIMEOUT="5", PGOPTIONS="-cstatement_timeout=15000 -clock_timeout=5000",
               GMAIL_AUTO_SYNC_ENABLED="false", AI_SECRETARY_AUTOMATION_ENABLED="false")
    return env


def stop_group(child):
    # The child starts a new session; descendants cannot escape through ordinary spawn.
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=5)


def execute(args, env, deadline, cwd=ROOT):
    remaining = min(180, deadline - time.monotonic())
    if remaining <= 0:
        raise TimeoutError("work_deadline")
    child = subprocess.Popen(args, cwd=cwd, env=env, start_new_session=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    try:
        stdout, _ = child.communicate(timeout=remaining)
        if child.returncode:
            safe_phase = child_failure_phase(stdout)
            if safe_phase is not None:
                raise ChildProofFailure(safe_phase)
            raise RuntimeError("child_failed")
        return stdout
    finally:
        # Also cover descendants left behind after a coordinator exits early.
        try:
            stop_group(child)
        except BaseException:
            raise ContainmentFailure("owned_process_cleanup_failed") from None


def validate_result(value):
    exact = {"status": "PASS", "phase": "replay", "attempts": 2, "progress": 100,
             "nodes": 3, "forced_kill": True, "real_lease_expiry": True,
             "api_process_restart": "NOT_RUN", "provider": "synthetic_metadata_only"}
    if not isinstance(value, dict) or set(value) != set(exact) | {"job_id", "snapshot_id", "seconds"}:
        raise ValueError("invalid_protocol")
    for key, expected in exact.items():
        if type(value[key]) is not type(expected) or value[key] != expected:
            raise ValueError("invalid_protocol")
    if any(type(value[k]) is not int or not 0 < value[k] < 2**63 for k in ("job_id", "snapshot_id")):
        raise ValueError("invalid_protocol")
    if type(value["seconds"]) not in (int, float) or not 0 <= value["seconds"] <= 180:
        raise ValueError("invalid_protocol")
    return value


def connect(database="postgres"):
    import psycopg
    return psycopg.connect(host="db", user="puw_ci", password=os.environ["POSTGRES_PASSWORD"],
                           dbname=database, autocommit=True, connect_timeout=5,
                           options="-cstatement_timeout=5000 -clock_timeout=1000")


def main():
    started = time.monotonic()
    owned = False
    contained = True
    phase = "guard"
    result = {"status": "FAIL", "runtime": "NOT_RUN", "cleanup": "NOT_NEEDED", "raw_published": False}
    try:
        env = environment()
        if os.name != "posix":
            raise RuntimeError("posix_required")
        phase = "create_database"
        with connect() as db:
            if db.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DATABASE,)).fetchone():
                raise ValueError("preexisting_database")
            db.execute('CREATE DATABASE "puw_v7_test_snapshot_recovery"')
            owned = True
        phase = "migration"
        execute([sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
                env, started + WORK_SECONDS, ROOT / "backend")
        with connect(DATABASE) as db:
            if db.execute("SELECT version_num FROM alembic_version").fetchall() != [(HEAD,)]:
                raise ValueError("schema_mismatch")
        phase = "snapshot_fault"
        output = execute([sys.executable, "scripts/ci/durable_queue/workspace_snapshot_checks.py"],
                         env, started + WORK_SECONDS)
        result["proof"] = validate_result(json.loads(output))
        result.update(status="PASS", runtime="PASS")
    except ChildProofFailure as error:
        result["child_phase"] = error.phase
        result["status"] = "FAIL"
    except ContainmentFailure:
        contained = False
        result["status"] = "FAIL"
    except BaseException:
        result["status"] = "FAIL"
    finally:
        result["phase"] = phase
        if owned:
            result["cleanup"] = "FAIL"
            try:
                if not contained:
                    raise RuntimeError("process_cleanup_unconfirmed")
                if time.monotonic() >= started + TOTAL_SECONDS:
                    raise TimeoutError("cleanup_deadline")
                with connect() as db:
                    db.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (DATABASE,))
                    db.execute('DROP DATABASE "puw_v7_test_snapshot_recovery"')
                result["cleanup"] = "PASS"
            except BaseException:
                result["status"] = "FAIL"
        try:
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(result, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf8")
        except Exception:
            return 1
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
