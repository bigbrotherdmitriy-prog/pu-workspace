"""Cross-stream regressions, reusing both provider fixtures and real queue."""
import pytest
from sqlalchemy import select

from test_storage_binding_validation import bound, choose
from app.api import workspace
from app.jobs import queue
from app.models.job import BackgroundJob
from app.models.workspace import WorkspaceSnapshot
from app.models.organizer import OrganizerSession
from app.models.workspace import SourceFolder


@pytest.mark.parametrize("status", ["queued", "running", "retrying"])
def test_manual_retry_does_not_compete_with_active_job(bound, status):
    first = choose(bound).json()
    with bound.db() as db:
        db.get(WorkspaceSnapshot, first['id']).status = 'failed'
        db.get(BackgroundJob, first['job_id']).status = status
        db.commit()
    r = bound.client.post(f'/projects/{bound.new}/snapshots/{first["id"]}/retry-build')
    assert r.status_code == 409
    with bound.db() as db:
        assert len(list(db.scalars(select(BackgroundJob)))) == 1
        assert db.get(WorkspaceSnapshot, first['id']).retry_count == 0


@pytest.mark.parametrize("kind", ['workspace.analysis', 'workspace.safe_copy'])
def test_virtual_analysis_does_not_compete_with_automatic_retry(bound, kind):
    first = choose(bound).json()
    with bound.db() as db:
        db.get(BackgroundJob, first['job_id']).status = 'completed'
        snap = db.get(WorkspaceSnapshot, first['id'])
        snap.status, snap.analysis_status = 'ready', 'failed'
        db.add(BackgroundJob(kind=kind, payload={'snapshot_id': snap.id, 'project_id': bound.new}, status='retrying'))
        db.commit()
    r = bound.client.post(f'/projects/{bound.new}/snapshots/{first["id"]}/analyze')
    assert r.status_code == 409
    with bound.db() as db:
        assert len(list(db.scalars(select(BackgroundJob)))) == 2


def test_retry_enqueue_failure_is_atomic(bound, monkeypatch):
    first = choose(bound).json()
    with bound.db() as db:
        db.get(WorkspaceSnapshot, first['id']).status = 'failed'
        db.get(BackgroundJob, first['job_id']).status = 'failed'
        db.commit()
    def crash(*args, **kwargs):
        raise RuntimeError('synthetic queue failure')
    monkeypatch.setattr(queue, 'enqueue', crash)
    with pytest.raises(RuntimeError):
        bound.client.post(f'/projects/{bound.new}/snapshots/{first["id"]}/retry-build')
    with bound.db() as db:
        snap = db.get(WorkspaceSnapshot, first['id'])
        assert snap.status == 'failed' and snap.retry_count == 0


def test_safe_copy_snapshot_error_does_not_expose_provider_text(bound, monkeypatch):
    import app.organizer as organizer
    first = choose(bound).json()
    session_id = workspace._start_safe_copy_pipeline(first['id'], bound.new, bound.adapter.ids[-1], 'Synthetic')
    def failure(*args, **kwargs):
        with bound.db() as db:
            session = db.get(OrganizerSession, session_id)
            session.status = 'failed'
            session.error_message = 'DOCUMENT_BODY secret=unpublished'
            db.commit()
    monkeypatch.setattr(organizer, '_scan_worker', failure)
    workspace._run_safe_copy_pipeline(first['id'], session_id, bound.new, bound.adapter.ids[-1])
    with bound.db() as db:
        snap = db.get(WorkspaceSnapshot, first['id'])
        assert 'DOCUMENT_BODY' not in snap.analysis_error
        assert 'unpublished' not in snap.analysis_error
        assert snap.analysis_result['storage_binding']['project_id'] == bound.new


def test_completed_virtual_analysis_is_not_executed_again(bound, monkeypatch):
    first = choose(bound).json()
    with bound.db() as db:
        snap = db.get(WorkspaceSnapshot, first['id'])
        snap.status, snap.analysis_status = 'ready', 'ready'
        snap.analysis_result = {**snap.analysis_result, 'status': 'ready', 'documents': 1}
        db.commit()
    def forbidden(*args, **kwargs):
        pytest.fail('completed analysis must not repeat business writes')
    monkeypatch.setattr(workspace, '_populate_content', forbidden)
    workspace._analyze_snapshot_worker(first['id'], bound.new, raise_errors=True)


def test_changed_source_provider_is_rejected_before_storage(bound):
    from fastapi import HTTPException
    first = choose(bound).json()
    bound.adapter.calls.clear()
    with bound.db() as db:
        snap = db.get(WorkspaceSnapshot, first['id'])
        db.get(SourceFolder, snap.source_folder_id).provider = 'other-provider'
        db.commit()
    with pytest.raises(HTTPException):
        workspace._build_snapshot(first['id'], bound.new, bound.adapter.ids[-1], raise_errors=True)
    assert bound.adapter.calls == []


def test_safe_copy_replay_reuses_durable_session_after_result_replacement(bound):
    first = choose(bound).json()
    sid = workspace._start_safe_copy_pipeline(first['id'], bound.new, bound.adapter.ids[-1], 'Synthetic')
    with bound.db() as db:
        snap = db.get(WorkspaceSnapshot, first['id'])
        snap.analysis_result = {'storage_binding': snap.analysis_result['storage_binding'], 'status': 'ready'}
        db.commit()
    assert workspace._start_safe_copy_pipeline(first['id'], bound.new, bound.adapter.ids[-1], 'Synthetic') == sid


def test_recovery_reuses_manual_analysis_job(bound):
    first = choose(bound).json()
    with bound.db() as db:
        db.get(BackgroundJob, first['job_id']).status = 'completed'
        snap = db.get(WorkspaceSnapshot, first['id'])
        snap.status, snap.analysis_status = 'ready', 'pending'
        db.commit()
    assert bound.client.post(f'/projects/{bound.new}/snapshots/{first["id"]}/analyze').status_code == 200
    workspace.recover_incomplete_analyses()
    with bound.db() as db:
        jobs = list(db.scalars(select(BackgroundJob).where(BackgroundJob.kind == 'workspace.analysis')))
        assert len(jobs) == 1
