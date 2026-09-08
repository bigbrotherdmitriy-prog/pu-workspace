"""Opt-in actual PostgreSQL/process snapshot fault phase, NOT wired into CI yet.

Prerequisite: freshly migrated EMPTY dedicated test database. Never reads real
provider credentials or documents. Only the provider is synthetic; handler, queue,
worker.main and HTTP route are production implementations. Output is allowlisted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import quote

from sqlalchemy.engine import make_url

PHASES = ("guard", "seed_http", "first_walk", "second_worker", "kill", "lease_expiry", "recovered", "replay")
_phase = "guard"


def validate_target(url: str, enabled: str, environment: str) -> None:
    if enabled != "1" or environment not in {"test", "ci"}:
        raise ValueError("test_environment_required")
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or parsed.query:
        raise ValueError("isolated_postgresql_required")
    if not (parsed.host in {"localhost", "127.0.0.1", "::1", "db"}
            and (parsed.database == "puw_queue_test" or
                 (parsed.database or "").startswith("puw_v7_test_"))):
        raise ValueError("isolated_postgresql_required")


def guard():
    validate_target(os.environ.get("DATABASE_URL", ""),
                    os.environ.get("PUW_SNAPSHOT_RECOVERY_TEST", ""), os.environ.get("APP_ENV", ""))


class MetadataProvider:
    provider = "google_drive"

    def __init__(self, factory, *, hold=False):
        from app.core.integration_types import StorageObject
        self.factory, self.hold = factory, hold
        self.root = "synthetic-customer-project-nested"
        self.items = [
            StorageObject(self.root, "Synthetic selected project", "inode/directory", "synthetic-customer", provider=self.provider),
            StorageObject("synthetic-nested-child", "Synthetic child", "inode/directory", self.root, provider=self.provider),
            StorageObject("synthetic-leaf", "Synthetic.txt", "text/plain", "synthetic-nested-child", provider=self.provider),
        ]

    def get_object(self, identifier):
        assert identifier == self.root
        return self.items[0]

    def walk_tree(self, identifier):
        from app.jobs import queue
        from app.models.audit_log import AuditLog
        assert identifier == self.root
        claim = queue.current_execution_claim()
        assert claim is not None
        # Test-only DB checkpoint proves the actual handler reached provider walk.
        # It is not a provider write or a successful snapshot receipt.
        with self.factory() as db:
            db.add(AuditLog(action="ci_snapshot_walk_started", entity_type="ci_snapshot_probe",
                            entity_id=claim[0], details=str(claim[2])))
            db.commit()
        if self.hold and claim[2] == 1:
            while True: time.sleep(.2)  # coordinator must kill this actual process
        return self.items[1:]

    def read_bytes(self, *_):
        raise AssertionError("content_read_forbidden")


def install_provider(factory, *, hold=False):
    from app.api import workspace
    provider = MetadataProvider(factory, hold=hold)
    workspace.storage_for_project = lambda _project_id, _db: provider
    return provider


def worker_mode():
    guard()
    from app.database import SessionLocal
    install_provider(SessionLocal, hold=True)
    from app.jobs.worker import main
    main()


def wait_for(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result: return result
        time.sleep(.2)
    raise AssertionError("checkpoint_timeout")


def coordinator():
    global _phase
    guard()
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select, text
    from app.api import workspace
    from app.core.auth import require_user
    from app.database import SessionLocal, get_db
    from app.models.audit_log import AuditLog
    from app.models.drive_connection import DriveConnection
    from app.models.job import BackgroundJob, ServiceHeartbeat
    from app.models.organization_contract import Organization
    from app.models.project import Project
    from app.models.project_member import ProjectMember
    from app.models.user import User
    from app.models.workspace import VirtualNode, WorkspaceSnapshot
    from app.schema import CURRENT_SCHEMA_REVISION

    with SessionLocal() as db:
        assert list(db.scalars(text("SELECT version_num FROM alembic_version"))) == [CURRENT_SCHEMA_REVISION]
        # Never let generic workers claim pre-existing user work.
        assert db.scalar(select(func.count()).select_from(Project)) == 0
        assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 0
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(Organization)) == 0
        org = Organization(name="Synthetic snapshot recovery")
        user = User(name="Synthetic owner", email="snapshot-owner@example.invalid")
        db.add_all([org, user]); db.flush()
        project = Project(name="Synthetic nested project", organization_id=org.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        db.add(DriveConnection(project_id=project.id, provider="google_drive", connection_id="synthetic-no-credentials",
                               root_folder_id="synthetic-customer-project-nested"))
        db.commit(); pid, uid = project.id, user.id
    provider = install_provider(SessionLocal)
    app = FastAPI(); app.include_router(workspace.router)
    def session():
        with SessionLocal() as db: yield db
    def user():
        with SessionLocal() as db: return db.get(User, uid)
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[require_user] = user
    path = f"/projects/{pid}/source-folders/{quote(provider.root, safe='')}/snapshot-queue"
    _phase = "seed_http"
    with TestClient(app) as client:
        first = client.post(path, headers={"Idempotency-Key": "synthetic-snapshot"})
        assert first.status_code == 200
        first = first.json()
    with TestClient(app) as client:
        repeated = client.post(path, headers={"Idempotency-Key": "synthetic-snapshot"}).json()
        assert repeated["id"] == first["id"] and repeated["job_id"] == first["job_id"]
    sid, jid = first["id"], first["job_id"]
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    env.update({"DATABASE_URL": os.environ["DATABASE_URL"], "APP_ENV": "test", "PUW_SNAPSHOT_RECOVERY_TEST": "1",
                "PYTHONPATH": str(Path(__file__).resolve().parents[3] / "backend"),
                "PU_JOB_LEASE_SECONDS": "60", "PU_JOB_POLL_SECONDS": "0.2"})
    children = []
    def spawn(label):
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker"],
                                 env={**env, "PU_WORKER_ID": label}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        children.append(child); return child
    def read_job():
        with SessionLocal() as db: return db.get(BackgroundJob, jid)
    def walked():
        with SessionLocal() as db:
            return db.scalar(select(AuditLog.id).where(AuditLog.action == "ci_snapshot_walk_started", AuditLog.entity_id == jid))
    started = time.monotonic()
    try:
        _phase = "first_walk"; first_worker = spawn("snapshot-a"); wait_for(walked)
        initial = read_job(); assert initial.status == "running" and initial.attempts == 1 and 1 < initial.progress < 100
        _phase = "second_worker"; second_worker = spawn("snapshot-b")
        def second_alive():
            with SessionLocal() as db:
                return db.scalar(select(ServiceHeartbeat.service_id).where(ServiceHeartbeat.service_id.like("snapshot-b-%")))
        wait_for(second_alive)
        assert read_job().worker_id == initial.worker_id
        _phase = "kill"; first_worker.kill(); first_worker.wait(timeout=10)
        _phase = "lease_expiry"
        # Real wall-clock expiry. No SQL lease rewinding in this runtime phase.
        wait_for(lambda: read_job().status == "completed", seconds=100)
        _phase = "recovered"; final = read_job()
        assert final.attempts == 2 and final.worker_id != initial.worker_id and final.progress == 100
        with SessionLocal() as db:
            snapshot = db.get(WorkspaceSnapshot, sid)
            assert snapshot.status == "ready" and snapshot.item_count == 3
            assert snapshot.analysis_result["storage_binding"]["project_id"] == pid
            assert snapshot.analysis_result["storage_binding"]["folder_id"] == provider.root
            assert db.scalar(select(func.count()).select_from(VirtualNode).where(VirtualNode.snapshot_id == sid)) == 3
            nodes = {node.external_id: node.parent_external_id for node in db.scalars(
                select(VirtualNode).where(VirtualNode.snapshot_id == sid))}
            assert nodes["synthetic-leaf"] == "synthetic-nested-child"
            assert nodes["synthetic-nested-child"] == provider.root
            assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "ci_snapshot_walk_started", AuditLog.entity_id == jid)) == 2
        _phase = "replay"
        with TestClient(app) as client:
            replay = client.post(path, headers={"Idempotency-Key": "synthetic-snapshot"}).json()
            assert replay["id"] == sid and replay["job_id"] == jid
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 1
        return {"status": "PASS", "phase": _phase, "job_id": jid, "snapshot_id": sid,
                "attempts": final.attempts, "progress": final.progress, "nodes": 3,
                "forced_kill": True, "real_lease_expiry": True, "api_process_restart": "NOT_RUN",
                "provider": "synthetic_metadata_only", "seconds": round(time.monotonic() - started, 2)}
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=10)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=10)


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        worker_mode()
    elif not sys.argv[1:]:
        try:
            result = coordinator()
        except Exception:
            print(json.dumps({"status": "FAIL", "phase": _phase, "raw_diagnostics_published": False}))
            raise SystemExit(1) from None
        print(json.dumps(result))
    else:
        raise SystemExit(2)
