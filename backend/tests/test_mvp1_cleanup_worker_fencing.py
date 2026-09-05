"""Synthetic worker boundary acceptance; no provider credentials or network."""
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.jobs import queue
from app.models.audit_log import AuditLog
from app.models.drive_connection import DriveConnection
from app.models.job import BackgroundJob
from app.models.organization_contract import Organization
from app.models.organizer import OrganizerSession
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.workspace import SourceFolder, WorkspaceSnapshot
from app.organizer_engine import managed_copies as cleanup


@pytest.fixture(params=["google_drive", "yandex_disk"])
def fenced(tmp_path, monkeypatch, request):
    engine = create_engine(f"sqlite:///{tmp_path / 'fencing.sqlite'}")
    Base.metadata.create_all(engine, tables=[model.__table__ for model in (
        Organization, User, Project, ProjectMember, SourceFolder, DriveConnection,
        OrganizerSession, WorkspaceSnapshot, BackgroundJob, AuditLog)])
    factory = sessionmaker(bind=engine)
    with factory() as db:
        org = Organization(name="Synthetic")
        user = User(name="Owner", email="owner@example.test")
        db.add_all([org, user]); db.flush()
        project = Project(name="Synthetic", organization_id=org.id)
        db.add(project); db.flush()
        member = ProjectMember(project_id=project.id, user_id=user.id, role="owner")
        source = SourceFolder(project_id=project.id, provider=request.param,
                              external_id="original", name="Original")
        connection = DriveConnection(project_id=project.id, provider=request.param,
                                     connection_id="synthetic", root_folder_id="original",
                                     account_email="owner@example.test")
        db.add_all([member, source, connection]); db.flush()
        sessions = []
        for copy_id in ["copy-a", "copy-b"]:
            session = OrganizerSession(project_id=project.id, source_folder_id="original",
                                       source_folder_name="Original", copy_folder_id=copy_id)
            db.add(session); db.flush()
            sessions.append(session.id)
            db.add(WorkspaceSnapshot(project_id=project.id, source_folder_id=source.id,
                status="ready", analysis_result={"mode": "safe_copy",
                    "organizer_session_id": session.id, "copy_folder_id": copy_id,
                    "storage_binding": {"project_id": project.id, "provider": request.param,
                        "connection_row_id": connection.id, "connection_id": "synthetic",
                        "folder_id": "original"}}))
        db.commit()
        payload = {"project_id": project.id, "command_key": "synthetic:cleanup:001",
                   "cleanup_version": cleanup.cleanup_version(cleanup.managed_copies(db, project.id))}
        job = queue.enqueue(db, "workspace.safe_copy_cleanup", payload,
            idempotency_key=f"workspace.safe_copy_cleanup:{project.id}:{user.id}:{payload['command_key']}")
        claimed = queue.claim(db, "synthetic-worker")
        claim = (claimed.id, claimed.worker_id, claimed.attempts, claimed.locked_at)
        ids = dict(project=project.id, user=user.id, member=member.id, source=source.id,
                   connection=connection.id, job=job.id, sessions=sessions)
    state = SimpleNamespace(db=factory, payload=payload, claim=claim, ids=ids,
                            calls=[], effects=set(), callback=None, resolutions=[])
    def trash(copy_id):
        state.calls.append(copy_id)
        state.effects.add(copy_id)
        if state.callback:
            state.callback(copy_id)
    state.adapter = SimpleNamespace(supports_managed_copy_cleanup=True, trash_safe_copy=trash)
    def resolve(*args):
        state.resolutions.append(True)
        return state.adapter
    monkeypatch.setattr(cleanup, "SessionLocal", factory)
    state.capability_guard = cleanup._require_cleanup_capability
    # Static gate is opened only alongside our credential-free synthetic factory.
    monkeypatch.setattr(cleanup, "_require_cleanup_capability", lambda provider: None)
    monkeypatch.setattr(cleanup, "storage_for_project", resolve)
    def run(payload=None, claim=None):
        job_id, worker, attempt, locked = claim or state.claim
        with queue.execution_owner(job_id, worker, attempt=attempt, locked_at=locked):
            return cleanup.run_managed_copy_cleanup(payload or state.payload)
    state.run = run
    yield state
    engine.dispose()


def receipts(state, action):
    with state.db() as db:
        return [json.loads(row.details) for row in db.scalars(select(AuditLog).where(AuditLog.action == action))]


@pytest.mark.parametrize("invalid", ["missing", "inexact", "job", "worker", "attempt", "locked"])
def test_exact_claim_required_before_resolution(fenced, invalid):
    claim = list(fenced.claim)
    with pytest.raises(ValueError, match="managed_copy_cleanup_worker"):
        if invalid == "missing":
            cleanup.run_managed_copy_cleanup(fenced.payload)
        elif invalid == "inexact":
            with queue.execution_owner(claim[0], claim[1]):
                cleanup.run_managed_copy_cleanup(fenced.payload)
        else:
            index = {"job": 0, "worker": 1, "attempt": 2, "locked": 3}[invalid]
            claim[index] = (claim[index] + 1 if index in (0, 2) else
                            "old-worker" if index == 1 else claim[index] - timedelta(seconds=1))
            fenced.run(claim=claim)
    assert fenced.resolutions == fenced.calls == []
    assert receipts(fenced, cleanup.ITEM_RECEIPT) == []


@pytest.mark.parametrize("state", ["queued", "retrying", "cancelled", "failed", "dead_letter", "completed",
                                   "expired", "cancel_requested", "cancelled_at"])
def test_invalid_job_state_fails_closed(fenced, state):
    with fenced.db() as db:
        job = db.get(BackgroundJob, fenced.ids["job"])
        if state == "expired":
            job.lease_expires_at = queue.utcnow() - timedelta(seconds=1)
        elif state == "cancel_requested":
            job.result = {"cancel_requested": True}
        elif state == "cancelled_at":
            job.cancelled_at = queue.utcnow()
        else:
            job.status = state
        db.commit()
    with pytest.raises(ValueError, match="managed_copy_cleanup_worker"):
        fenced.run()
    assert fenced.calls == fenced.resolutions == []


@pytest.mark.parametrize("field,value", [("command_key", "different-command"), ("cleanup_version", "0" * 64),
                                         ("project_id", 999)])
def test_payload_must_equal_claimed_job(fenced, field, value):
    with pytest.raises(ValueError, match="managed_copy_cleanup_worker"):
        fenced.run(payload={**fenced.payload, field: value})
    assert fenced.calls == fenced.resolutions == []


@pytest.mark.parametrize("change", ["worker", "attempt", "expired", "cancel", "payload", "binding", "role",
                                    "original", "source_provider", "session_project", "project"])
def test_mutation_during_first_effect_prevents_receipt_and_next_effect(fenced, change):
    def mutate(copy_id):
        with fenced.db() as db:
            job = db.get(BackgroundJob, fenced.ids["job"])
            if change == "worker": job.worker_id = "replacement"
            elif change == "attempt": job.attempts += 1
            elif change == "expired": job.lease_expires_at = queue.utcnow() - timedelta(seconds=1)
            elif change == "cancel": job.result = {"cancel_requested": True}
            elif change == "payload": job.payload = {**job.payload, "command_key": "changed-command"}
            elif change == "binding": db.get(DriveConnection, fenced.ids["connection"]).connection_id = "replacement"
            elif change == "role": db.get(ProjectMember, fenced.ids["member"]).role = "viewer"
            elif change == "original":
                db.add(SourceFolder(project_id=fenced.ids["project"], external_id=copy_id, name="Protected"))
            elif change == "source_provider": db.get(SourceFolder, fenced.ids["source"]).provider = "other"
            elif change == "session_project": db.get(OrganizerSession, fenced.ids["sessions"][0]).project_id = 999
            elif change == "project": db.get(Project, fenced.ids["project"]).organization_id += 1
            db.commit()
    fenced.callback = mutate
    with pytest.raises(ValueError, match="managed_copy_"):
        fenced.run()
    assert len(fenced.calls) == 1
    assert receipts(fenced, cleanup.ITEM_RECEIPT) == []
    assert receipts(fenced, cleanup.FINAL_RECEIPT) == []


def test_receipt_replay_is_bound_to_job_and_version(fenced):
    first = fenced.run()
    replay = fenced.run()
    assert first["trashed"] == replay["trashed"] == 2
    assert replay["idempotent_replay"] is True
    assert len(fenced.calls) == 2
    with fenced.db() as db:
        job = db.get(BackgroundJob, fenced.ids["job"])
        job.payload = {**job.payload, "cleanup_version": "0" * 64}
        db.commit()
    with pytest.raises(ValueError, match="managed_copy_cleanup_version_conflict"):
        fenced.run(payload={**fenced.payload, "cleanup_version": "0" * 64})
    assert len(fenced.calls) == 2


def test_crash_before_receipt_requires_capable_adapter_reconciliation(fenced):
    def crash(_):
        raise RuntimeError("synthetic crash after effect")
    fenced.callback = crash
    with pytest.raises(RuntimeError): fenced.run()
    assert receipts(fenced, cleanup.ITEM_RECEIPT) == []
    fenced.callback = None
    assert fenced.run()["trashed"] == 2
    assert len(fenced.calls) == 3 and len(fenced.effects) == 2


def test_capability_deny_has_no_provider_effect(fenced):
    fenced.adapter.supports_managed_copy_cleanup = False
    with pytest.raises(ValueError, match="managed_copy_cleanup_capability_unavailable"):
        fenced.run()
    assert fenced.calls == []


def test_real_adapter_capability_denies_before_client_or_token_resolution(fenced, monkeypatch):
    monkeypatch.setattr(cleanup, "_require_cleanup_capability", fenced.capability_guard)
    with pytest.raises(ValueError, match="managed_copy_cleanup_capability_unavailable"):
        fenced.run()
    assert fenced.calls == fenced.resolutions == []


def test_lease_expiring_during_last_inventory_read_has_no_effect(fenced, monkeypatch):
    inventory = cleanup._inventory_guard
    reads = []
    def slow_inventory(*args):
        result = inventory(*args)
        reads.append(True)
        if len(reads) == 3:
            with fenced.db() as db:
                db.get(BackgroundJob, fenced.ids["job"]).lease_expires_at = queue.utcnow() - timedelta(seconds=1)
                db.commit()
        return result
    monkeypatch.setattr(cleanup, "_inventory_guard", slow_inventory)
    with pytest.raises(ValueError, match="managed_copy_cleanup_worker"):
        fenced.run()
    assert fenced.calls == []


@pytest.mark.parametrize("proof", [None, True, "false"])
def test_unconfirmed_final_receipt_cannot_invent_originals_proof(fenced, proof):
    fenced.run()
    with fenced.db() as db:
        final = db.scalar(select(AuditLog).where(AuditLog.action == cleanup.FINAL_RECEIPT))
        details = json.loads(final.details)
        details["originals_affected"] = proof
        final.details = json.dumps(details)
        db.commit()
    with pytest.raises(ValueError, match="managed_copy_cleanup_version_conflict"):
        fenced.run()
    assert len(fenced.calls) == 2


@pytest.mark.parametrize("change", ["cancel", "binding", "role", "original"])
def test_committed_first_receipt_does_not_authorize_next_item(fenced, monkeypatch, change):
    commit = cleanup._commit_receipt
    def commit_then_change(db, payload, claim, details, action):
        commit(db, payload, claim, details, action)
        if action != cleanup.ITEM_RECEIPT:
            return
        with fenced.db() as other:
            if change == "cancel":
                queue.request_cancel(other, fenced.ids["job"], allow_running=True)
            elif change == "binding":
                other.get(DriveConnection, fenced.ids["connection"]).status = "disconnected"
            elif change == "role":
                other.get(ProjectMember, fenced.ids["member"]).role = "viewer"
            elif change == "original":
                remaining = ({"copy-a", "copy-b"} - fenced.effects).pop()
                other.add(SourceFolder(project_id=fenced.ids["project"], external_id=remaining, name="Protected"))
            other.commit()
    monkeypatch.setattr(cleanup, "_commit_receipt", commit_then_change)
    with pytest.raises(ValueError, match="managed_copy_"):
        fenced.run()
    assert len(fenced.calls) == len(receipts(fenced, cleanup.ITEM_RECEIPT)) == 1
    assert receipts(fenced, cleanup.FINAL_RECEIPT) == []


def test_recovered_attempt_skips_receipt_and_stale_attempt_cannot_replay(fenced, monkeypatch):
    commit = cleanup._commit_receipt
    def commit_then_crash(db, payload, claim, details, action):
        commit(db, payload, claim, details, action)
        raise RuntimeError("crash after committed receipt")
    monkeypatch.setattr(cleanup, "_commit_receipt", commit_then_crash)
    with pytest.raises(RuntimeError): fenced.run()
    assert len(receipts(fenced, cleanup.ITEM_RECEIPT)) == 1
    with fenced.db() as db:
        db.get(BackgroundJob, fenced.ids["job"]).lease_expires_at = queue.utcnow() - timedelta(seconds=1)
        db.commit()
        assert queue.recover_expired(db) == 1
        # Reuse the worker label deliberately; attempt and locked_at must fence it.
        job = queue.claim(db, fenced.claim[1])
        recovered = (job.id, job.worker_id, job.attempts, job.locked_at)
    with pytest.raises(ValueError, match="managed_copy_cleanup_worker"):
        fenced.run()
    monkeypatch.setattr(cleanup, "_commit_receipt", commit)
    assert fenced.run(claim=recovered)["trashed"] == 2
    assert len(fenced.calls) == 2
    assert {row["attempt"] for row in receipts(fenced, cleanup.ITEM_RECEIPT)} == {1, 2}


def test_same_command_text_from_other_actor_cannot_borrow_final_receipt(fenced):
    assert fenced.run()["trashed"] == 2
    with fenced.db() as db:
        db.get(BackgroundJob, fenced.ids["job"]).status = "completed"
        actor = User(name="Second owner", email="second@example.test")
        db.add(actor); db.flush()
        db.add(ProjectMember(project_id=fenced.ids["project"], user_id=actor.id, role="owner"))
        job = queue.enqueue(db, "workspace.safe_copy_cleanup", fenced.payload,
            idempotency_key=f"workspace.safe_copy_cleanup:{fenced.ids['project']}:{actor.id}:{fenced.payload['command_key']}")
        second = queue.claim(db, "second-owner-worker")
        claim = (second.id, second.worker_id, second.attempts, second.locked_at)
    with pytest.raises(ValueError, match="managed_copy_cleanup_version_conflict"):
        fenced.run(claim=claim)
    assert len(fenced.calls) == 2


def test_two_active_commands_do_not_both_execute(fenced):
    with fenced.db() as db:
        payload = {**fenced.payload, "command_key": "second:command"}
        job = queue.enqueue(db, "workspace.safe_copy_cleanup", payload,
            idempotency_key=f"workspace.safe_copy_cleanup:{fenced.ids['project']}:{fenced.ids['user']}:second:command")
        second = queue.claim(db, "second-worker")
        claim = (second.id, second.worker_id, second.attempts, second.locked_at)
    with pytest.raises(ValueError, match="managed_copy_cleanup_command_conflict"):
        fenced.run(payload=payload, claim=claim)
    assert fenced.calls == []
    assert fenced.run()["trashed"] == 2
    with fenced.db() as db:
        db.get(BackgroundJob, fenced.ids["job"]).status = "completed"
        db.commit()
    with pytest.raises(ValueError, match="managed_copy_cleanup_version_conflict"):
        fenced.run(payload=payload, claim=claim)
    assert len(fenced.calls) == 2
