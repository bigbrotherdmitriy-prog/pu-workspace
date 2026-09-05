"""Disposable Docker-only fault protocol; never targets an existing project.

Run from checkout root. Raw diagnostics/backups/secrets remain in a temporary
directory and are not artifacts. Only protocol.json is published.
"""
import base64
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
import io
import tarfile
from contextlib import nullcontext

ROOT = Path(__file__).resolve().parents[3]
EVENTS = []


def safe_operation(args):
    """Return an allowlisted coarse operation without paths or arguments."""
    if not args:
        return "unknown"
    executable = Path(str(args[0])).name.lower()
    if executable in {"git", "git.exe"}:
        return "git_metadata"
    if executable not in {"docker", "docker.exe"}:
        return "subprocess"
    if len(args) > 1 and args[1] == "compose":
        allowed = {
            "config", "cp", "down", "exec", "kill", "logs", "restart",
            "run", "stop", "up",
        }
        action = next((str(item) for item in args[2:] if item in allowed), "other")
        return "compose_" + action
    action = str(args[1]) if len(args) > 1 and args[1] in {
        "build", "network", "ps", "volume",
    } else "other"
    return "docker_" + action


def safe_failure(stdout, stderr):
    """Emit only allowlisted classifications, never provider/document/log text.

    Classification is a diagnostic hint, not proof of the underlying cause.
    Unknown output stays unclassified rather than being published as a tail.
    """
    message = (stderr or b"").lower()
    signatures = (
        ("dockerfile_missing", (b"failed to read dockerfile", b"cannot locate specified dockerfile")),
        ("registry_rate_limit", (b"toomanyrequests", b"pull rate limit")),
        ("registry_access_denied", (b"pull access denied", b"unauthorized: authentication required")),
        ("daemon_unavailable", (b"cannot connect to the docker daemon",)),
        ("buildx_unavailable", (b"buildx component is missing", b"buildx is not a docker command")),
        ("network_failure", (b"no such host", b"tls handshake timeout", b"network is unreachable")),
        ("disk_full", (b"no space left on device",)),
        ("permission_denied", (b"permission denied",)),
    )
    category = next((name for name, patterns in signatures if any(p in message for p in patterns)), "unclassified")
    return {"category": category, "stdout_bytes": len(stdout or b""),
            "stderr_bytes": len(stderr or b""), "raw_published": False}


def main():
    project = f"puw-queue-{os.environ.get('GITHUB_RUN_ID', str(time.time_ns()))}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    assert re.fullmatch(r"puw-queue-\d+-\d+", project)
    # Do not inherit Compose/Docker endpoint overrides or credentials as test env.
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    env["COMPOSE_DISABLE_ENV_FILE"] = "1"
    image = project + ":test"
    base = project + ":base"
    forbidden = []

    def command(args, *, timeout=120, data=None, expected=0):
        start = time.monotonic()
        r = subprocess.run(args, input=data, capture_output=True, env=env, cwd=ROOT, timeout=timeout)
        EVENTS.append({"operation": safe_operation(args), "exit": r.returncode,
                       "seconds": round(time.monotonic()-start, 2)})
        if r.returncode != expected:
            EVENTS[-1]["failure"] = safe_failure(r.stdout, r.stderr)
            raise RuntimeError("command failed; raw output withheld")
        return r.stdout

    def inventory():
        return [command(["docker", *args, "--filter", f"label=com.docker.compose.project={project}"], timeout=20).strip()
                for args in (["ps", "-aq"], ["network", "ls", "-q"], ["volume", "ls", "-q"])]

    def build_context(prefix):
        # Only tracked source files; never send .git, local .env or other worktrees.
        paths = command(["git", "ls-files", "-z", "--", prefix]).decode().split("\0")
        content = io.BytesIO()
        # Buildx sniffs only 1024 stdin bytes. A leading PAX mtime record
        # consumes that window before the first file header. Compression magic
        # makes the stream unambiguously a context, not an inline Dockerfile.
        with tarfile.open(fileobj=content, mode="w:gz") as archive:
            for relative in filter(None, paths):
                path = Path(relative)
                assert path.name != ".env"
                name = path.relative_to("backend").as_posix() if prefix == "backend" else path.as_posix()
                archive.add(ROOT / path, arcname=name, recursive=False)
        return content.getvalue()

    assert not any(inventory()), "project already exists"
    revision = command(["git", "rev-parse", "HEAD"]).decode().strip()
    # Keep env on failed teardown so the unconditional workflow cleanup can retry.
    with nullcontext(tempfile.mkdtemp(prefix="puw-queue-")) as temp:
        env_file = Path(temp) / "ci.env"
        values = {"POSTGRES_PASSWORD": secrets.token_hex(24), "APP_SECRET_KEY": secrets.token_hex(32),
                  "BOOTSTRAP_TOKEN": secrets.token_hex(32),
                  "TOKEN_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
                  "CI_SMOKE_PASSWORD": secrets.token_hex(24), "PU_RELEASE_REVISION": revision, "QUEUE_CI_IMAGE": image}
        forbidden = [v.encode() for k, v in values.items() if k not in {"PU_RELEASE_REVISION", "QUEUE_CI_IMAGE"}]
        forbidden += [b"CI_DOCUMENT_SENTINEL", b"CI_SECRET_SENTINEL"]
        env.update(values)
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("".join(f"{k}={v}\n" for k, v in values.items()))
        (ROOT / "queue-runtime-state.json").write_text(json.dumps({"project": project, "env_file": str(env_file)}))
        dc = ["docker", "compose", "--project-name", project, "--file", "docker-compose.queue-ci.yml", "--env-file", str(env_file)]
        def compose(*args, **kw):
            return command(dc + list(args), **kw)
        def probe(op, **args):
            return json.loads(compose("exec", "-T", "-e", "CI_SMOKE_PASSWORD", "api1", "python", "/queue_ci/client.py", op, json.dumps(args)))
        def wait(test, seconds=150):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    value = test()
                    if value:
                        return value
                except (RuntimeError, KeyError):
                    pass
                time.sleep(1)
            raise TimeoutError("condition deadline")
        def state(job):
            row = probe("state", id=job)
            EVENTS.append({"job": row})
            return row
        def until(job, status):
            return wait(lambda: (r if (r := state(job))["status"] == status else None))
        def sql(query, database="puw_queue_test"):
            return compose("exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1", "-U", "puw_ci", "-d", database, "-Atc", query)

        try:
            compose("config", "--quiet")
            command(["docker", "build", "-t", base, "-"], data=build_context("backend"), timeout=900)
            command(["docker", "build", "--build-arg", f"BASE_IMAGE={base}", "-f", "scripts/ci/durable_queue/Dockerfile", "-t", image, "-"], data=build_context("scripts"), timeout=120)
            compose("up", "-d", "--wait", "--wait-timeout", "90", "db")
            compose("run", "--rm", "--no-deps", "api1", "alembic", "-c", "alembic.ini", "upgrade", "head")
            assert sql("SELECT version_num FROM alembic_version").strip() == b"a54f001c0a16"
            race = compose("run", "--rm", "--no-deps", "api1", "python", "/queue_ci/postgres_checks.py")
            EVENTS.append(json.loads(race))
            workspace_result = compose("run", "--rm", "--no-deps", "api1", "python", "/queue_ci/workspace_checks.py")
            EVENTS.append(json.loads(workspace_result))
            compose("up", "-d", "worker1", "worker2", "scheduler")
            # Readiness preflight needs worker/scheduler heartbeat before APIs start.
            wait(lambda: int(sql("SELECT count(*) FROM service_heartbeats").strip()) >= 3)
            compose("up", "-d", "api1", "api2")
            wait(lambda: probe("ready"))
            compose("exec", "-T", "-e", "CI_SMOKE_PASSWORD", "-e", "CI_SMOKE_BASE_URL=http://api1:8000", "api1", "python", "-",
                    data=(ROOT / "scripts/ci/smoke_api.py").read_bytes())
            job = probe("create", key="ci-kill", hold=90)["id"]
            assert probe("create", key="ci-kill", hold=90, api="api2")["id"] == job
            row = wait(lambda: (r if (r := state(job))["effects"] == 1 and r["progress"] >= 25 else None))
            old_owner = row["worker"]
            first_lease = row["lease"]
            wait(lambda: state(job)["lease"] != first_lease, seconds=40)
            EVENTS.append({"job_id": job, "heartbeat_extended": True})
            victim = "worker1" if old_owner.startswith("worker1-") else "worker2"
            compose("kill", "-s", "SIGKILL", victim)
            # Full real lease timeout (60s); no timestamp manipulation.
            recovered = until(job, "completed")
            assert recovered["attempts"] == 2 and recovered["effects"] == 1 and recovered["worker"] != old_owner
            code = "from app.database import SessionLocal; from app.jobs.queue import heartbeat,set_progress,succeed,fail; " \
                   f"db=SessionLocal(); j={job}; w={old_owner!r}; " \
                   "assert not heartbeat(db,j,w); assert not set_progress(db,j,w,99); assert not succeed(db,j,w); assert fail(db,j,w,RuntimeError()) == 'lost'"
            compose("exec", "-T", "api1", "python", "-c", code)
            compose("up", "-d", victim)
            wait(lambda: probe("metrics")["workers"] >= 2)
            probe("permissions", id=job)
            sql("UPDATE users SET is_admin=false WHERE email='smoke@example.test'")
            probe("permissions-member", id=job)
            sql("UPDATE users SET is_admin=true WHERE email='smoke@example.test'")
            queued = probe("create", key="ci-persist", delay=600)["id"]
            compose("restart", "api1")
            wait(lambda: probe("ready")); assert state(queued)["status"] == "queued"
            compose("restart", "api1", "api2")
            wait(lambda: probe("ready")); assert state(queued)["status"] == "queued"
            compose("down", "--remove-orphans")  # Deliberately preserve this project's DB volume.
            compose("up", "-d", "--wait", "--wait-timeout", "90", "db")
            compose("up", "-d", "worker1", "worker2", "scheduler")
            # Ensure both new worker heartbeats exist before API preflight.
            wait(lambda: int(sql("SELECT count(*) FROM service_heartbeats WHERE last_seen > now()-interval '10 seconds'").strip()) >= 3)
            compose("up", "-d", "api1", "api2")
            wait(lambda: probe("ready")); assert state(queued)["status"] == "queued"
            assert probe("cancel", id=queued)["status"] == "cancelled"
            retry_job = probe("create", key="ci-retry", failures=1)["id"]
            retry_state = until(retry_job, "retrying")
            assert retry_state["error"] == "TimeoutError"
            from datetime import datetime, timezone
            assert datetime.fromisoformat(retry_state["available_at"]) > datetime.now(timezone.utc)
            assert until(retry_job, "completed")["effects"] == 1
            failed = probe("create", key="ci-failed", failures=1, permanent=True)["id"]
            assert until(failed, "failed")["error"] == "ValueError"
            probe("retry", id=failed)
            assert until(failed, "completed")["attempts"] == 2
            dead = probe("create", key="ci-dead", failures=5, max_attempts=2)["id"]
            assert until(dead, "dead_letter")["attempts"] == 2
            probe("redrive", id=dead)
            redriven = until(dead, "dead_letter")
            assert redriven["effects"] == 1 and redriven["attempts"] == 2
            assert state(queued)["attempts"] == 0
            compose("stop", "worker1", "worker2", "scheduler")
            # Snapshot/restore uses the existing scripts with PostgreSQL client tools.
            compose("cp", "scripts/backup-job-queue.sh", "db:/tmp/backup-job-queue.sh")
            compose("cp", "scripts/restore-job-queue.sh", "db:/tmp/restore-job-queue.sh")
            compose("exec", "-T", "db", "sh", "-c", "DATABASE_URL=postgresql:///puw_queue_test?user=puw_ci sh /tmp/backup-job-queue.sh /tmp/queue.dump")
            dump_sql = compose("exec", "-T", "db", "pg_restore", "-f", "-", "/tmp/queue.dump")
            assert not any(secret in dump_sql for secret in forbidden)
            assert b"ci.probe" in dump_sql
            sql("CREATE DATABASE puw_queue_restore_test")
            compose("exec", "-T", "db", "sh", "-c", "DATABASE_URL=postgresql:///puw_queue_restore_test?user=puw_ci PU_CONFIRM_QUEUE_RESTORE=RESTORE_QUEUE_TABLES sh /tmp/restore-job-queue.sh /tmp/queue.dump")
            snapshot = "SELECT row_to_json(j) FROM background_jobs j ORDER BY id"
            assert sql(snapshot) == sql(snapshot, "puw_queue_restore_test")
            assert sql("SELECT row_to_json(h) FROM service_heartbeats h ORDER BY service_id") == sql("SELECT row_to_json(h) FROM service_heartbeats h ORDER BY service_id", "puw_queue_restore_test")
            # Check sequence restoration, not only row counts.
            result = sql("INSERT INTO background_jobs(kind,payload,status,priority,attempts,max_attempts,progress) VALUES ('ci.probe','{}','queued',100,0,3,0) RETURNING id", "puw_queue_restore_test")
            assert int(result.splitlines()[0]) > dead
            EVENTS.append({"backup_restore": "PASS", "rows_equal": True, "sequence": "PASS"})
            logs = compose("logs", "--no-color")
            assert not any(secret in logs for secret in forbidden)
            EVENTS.append({"log_secret_scan": "PASS", "runtime": "PASS"})
        finally:
            # No raw logs are artifacts; capture safe failure evidence before down.
            try:
                logs = compose("logs", "--no-color")
                EVENTS.append({"diagnostics": {"secret_free": not any(s in logs for s in forbidden), "raw_published": False}})
            finally:
                compose("down", "--volumes", "--remove-orphans", "--timeout", "10")
                assert not any(inventory()), "cleanup left resources"
                EVENTS.append({"cleanup": "PASS", "project": project})
                env_file.unlink()
                Path(temp).rmdir()
                (ROOT / "queue-runtime-state.json").unlink()


if __name__ == "__main__":
    result = "FAIL"
    try:
        main()
        result = "PASS"
    except Exception as exc:
        EVENTS.append({"failure_type": type(exc).__name__})
    finally:
        out = ROOT / "queue-artifacts"
        out.mkdir(exist_ok=True)
        (out / "protocol.json").write_text(json.dumps({"result": result, "events": EVENTS}, indent=2))
    raise SystemExit(0 if result == "PASS" else 1)
