"""Actual workspace.snapshot handler; synthetic metadata-only provider, no live I/O."""
from datetime import timedelta
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.api import workspace
from app.core.integration_types import StorageObject
from app.jobs import handlers, queue
from app.models.drive_connection import DriveConnection
from app.models.job import BackgroundJob
from app.models.workspace import VirtualNode, WorkspaceSnapshot
from test_storage_binding_validation import bound, choose  # shared isolated fixture; two providers


def claim(bound, owner="worker-a"):
    with bound.db() as db:
        job = queue.claim(db, owner, 60)
        assert job is not None
        return job


def execute(job):
    with queue.execution_owner(job.id, job.worker_id, attempt=job.attempts, locked_at=job.locked_at):
        return handlers.run(job.kind, dict(job.payload))


def expire_and_reclaim(bound, job):
    with bound.db() as db:
        db.get(BackgroundJob, job.id).lease_expires_at = queue.utcnow() - timedelta(seconds=1)
        db.commit()
        queue.recover_expired(db)
        db.get(BackgroundJob, job.id).available_at = queue.utcnow() - timedelta(seconds=1)
        db.commit()
    return claim(bound, "worker-b")


def nested(bound):
    root = bound.adapter.ids[-1]
    folder = StorageObject(root + "/nested", "Synthetic child", "inode/directory", root, provider=bound.adapter.provider)
    file = StorageObject(root + "/nested/leaf", "Synthetic.txt", "text/plain", folder.id, provider=bound.adapter.provider)
    bound.adapter.items.update({folder.id: folder, file.id: file})
    bound.adapter.walk_tree = lambda _: [folder, file]
    def deny_content(*_): raise AssertionError("snapshot metadata must not read file content")
    bound.adapter.read_bytes = deny_content
    return root, folder, file


def test_actual_snapshot_nested_binding_http_repetition_and_persisted_result(bound):
    root, folder, file = nested(bound)
    first = choose(bound, headers={"Idempotency-Key": "synthetic-snapshot"}).json()
    again = choose(bound, headers={"Idempotency-Key": "synthetic-snapshot"}).json()
    assert first["id"] == again["id"] and first["job_id"] == again["job_id"]
    job = claim(bound)
    assert execute(job) == {"snapshot_id": first["id"]}
    # Fresh sessions model API restart persistence, not an OS process restart.
    with bound.db() as db:
        snapshot = db.get(WorkspaceSnapshot, first["id"])
        assert snapshot.status == "ready" and snapshot.item_count == 3
        assert snapshot.analysis_result["storage_binding"]["folder_id"] == root
        assert snapshot.analysis_result["storage_binding"]["project_id"] == bound.new
        rows = list(db.scalars(select(VirtualNode).where(VirtualNode.snapshot_id == snapshot.id)))
        assert {row.external_id: row.parent_external_id for row in rows}[file.id] == folder.id
        assert db.get(DriveConnection, bound.connection).root_folder_id == root
        assert queue.succeed(db, job.id, job.worker_id, {"snapshot_id": snapshot.id})
    repeated = choose(bound).json()
    assert repeated["id"] == first["id"] and repeated["already_queued"]
    with bound.db() as db:
        assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 1
        assert db.get(BackgroundJob, job.id).progress == 100


def test_expired_owner_cannot_start_provider_read_or_mark_snapshot_failed(bound):
    nested(bound); response = choose(bound).json(); old = claim(bound)
    new = expire_and_reclaim(bound, old); bound.adapter.calls.clear()
    with pytest.raises(Exception): execute(old)
    assert bound.adapter.calls == []
    with bound.db() as db:
        assert db.get(WorkspaceSnapshot, response["id"]).status == "building"
        assert db.get(BackgroundJob, old.id).worker_id == new.worker_id
        assert db.scalar(select(func.count()).select_from(VirtualNode)) == 0


def test_expired_owner_during_walk_cannot_publish_after_new_claim(bound):
    nested(bound); response = choose(bound).json(); old = claim(bound)
    original = bound.adapter.walk_tree
    def walk(folder):
        rows = original(folder); expire_and_reclaim(bound, old); return rows
    bound.adapter.walk_tree = walk
    with pytest.raises(Exception): execute(old)
    with bound.db() as db:
        assert db.get(WorkspaceSnapshot, response["id"]).status == "building"
        assert db.scalar(select(func.count()).select_from(VirtualNode)) == 0


def test_binding_rotated_during_walk_is_revalidated_before_publish(bound):
    nested(bound); response = choose(bound).json(); job = claim(bound)
    original = bound.adapter.walk_tree
    def walk(folder):
        rows = original(folder)
        with bound.db() as db:
            db.get(DriveConnection, bound.connection).connection_id = "synthetic-replacement"
            db.commit()
        return rows
    bound.adapter.walk_tree = walk
    with pytest.raises(Exception): execute(job)
    with bound.db() as db:
        assert db.get(WorkspaceSnapshot, response["id"]).status != "ready"
        assert db.scalar(select(func.count()).select_from(VirtualNode)) == 0


def test_crash_before_publish_then_fresh_claim_builds_once(bound):
    nested(bound); response = choose(bound).json(); old = claim(bound)
    original = bound.adapter.walk_tree
    class SimulatedProcessExit(BaseException): pass
    def crash(_): raise SimulatedProcessExit()
    bound.adapter.walk_tree = crash
    with pytest.raises(SimulatedProcessExit): execute(old)
    new = expire_and_reclaim(bound, old); bound.adapter.walk_tree = original
    execute(new)
    with bound.db() as db:
        assert db.get(WorkspaceSnapshot, response["id"]).status == "ready"
        assert db.scalar(select(func.count()).select_from(VirtualNode)) == 3
        assert not queue.succeed(db, old.id, old.worker_id)
        assert queue.succeed(db, new.id, new.worker_id)


def test_handler_records_owned_progress_before_provider_walk(bound):
    nested(bound); choose(bound); job = claim(bound); original = bound.adapter.walk_tree
    def walk(folder):
        with bound.db() as db:
            assert 1 < db.get(BackgroundJob, job.id).progress < 100
        return original(folder)
    bound.adapter.walk_tree = walk
    execute(job)


def test_crash_after_metadata_commit_reuses_ready_snapshot_without_provider_read(bound):
    nested(bound); response = choose(bound).json(); old = claim(bound); execute(old)
    new = expire_and_reclaim(bound, old); bound.adapter.calls.clear(); execute(new)
    assert bound.adapter.calls == []
    with bound.db() as db:
        assert db.get(WorkspaceSnapshot, response["id"]).status == "ready"
        assert db.scalar(select(func.count()).select_from(VirtualNode)) == 3
        assert not queue.succeed(db, old.id, old.worker_id)
        assert queue.succeed(db, new.id, new.worker_id)


def test_same_worker_label_cannot_reuse_an_old_attempt(bound):
    nested(bound); choose(bound); old = claim(bound)
    with bound.db() as db:
        current = db.get(BackgroundJob, old.id)
        current.attempts += 1
        current.locked_at = queue.utcnow()
        db.commit()
    bound.adapter.calls.clear()
    with pytest.raises(Exception): execute(old)
    assert bound.adapter.calls == []


def test_existing_durable_job_cannot_be_bypassed_without_delivery_context(bound):
    nested(bound); response = choose(bound).json(); bound.adapter.calls.clear()
    with pytest.raises(Exception):
        workspace._build_snapshot(response["id"], bound.new, bound.adapter.ids[-1], raise_errors=True)
    assert bound.adapter.calls == []
    with bound.db() as db:
        assert db.get(WorkspaceSnapshot, response["id"]).status == "building"
        assert db.get(BackgroundJob, response["job_id"]).status == "queued"


def test_recovered_winner_is_not_clobbered_by_old_delivery(bound):
    nested(bound); response = choose(bound).json(); old = claim(bound); original = bound.adapter.walk_tree
    def walk(folder):
        bound.adapter.walk_tree = original
        new = expire_and_reclaim(bound, old)
        execute(new)
        with bound.db() as db: assert queue.succeed(db, new.id, new.worker_id)
        return original(folder)
    bound.adapter.walk_tree = walk
    with pytest.raises(Exception): execute(old)
    with bound.db() as db:
        assert db.get(WorkspaceSnapshot, response["id"]).status == "ready"
        assert db.get(BackgroundJob, old.id).status == "completed"
        assert db.scalar(select(func.count()).select_from(VirtualNode)) == 3


def harness_module():
    path = Path(__file__).resolve().parents[2] / "scripts/ci/durable_queue/workspace_snapshot_checks.py"
    spec = importlib.util.spec_from_file_location("snapshot_runtime_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("url,flag,environment", [
    ("postgresql://test@localhost/puw_v7_test_snapshot", "", "test"),
    ("postgresql://test@localhost/puw_v7_test_snapshot", "1", "production"),
    ("postgresql://test@external.example/puw_v7_test_snapshot", "1", "test"),
    ("postgresql://test@localhost/pu_workspace", "1", "test"),
    ("postgresql://test@localhost/puw_v7_test_snapshot?options=-csearch_path=public", "1", "test"),
    ("sqlite:///:memory:", "1", "test"),
])
def test_runtime_contract_refuses_unowned_database(url, flag, environment):
    with pytest.raises(ValueError): harness_module().validate_target(url, flag, environment)


def test_runtime_contract_only_allows_explicit_owned_pg():
    harness_module().validate_target("postgresql+psycopg://test@127.0.0.1/puw_v7_test_snapshot", "1", "test")
    harness_module().validate_target("postgresql://test@db/puw_queue_test", "1", "ci")
