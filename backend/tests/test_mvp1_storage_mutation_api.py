import pytest
from fastapi import HTTPException

from app.api.storage_mutations import MutationRequest, confirm, explicit_rollback, prepare, status
from app.models.job import BackgroundJob
from app.models.project_member import ProjectMember
from app.models.user import User


def principal(db, project_id, role):
    user = User(name=f"Synthetic {role}", email=f"storage-{role}@example.test", is_admin=False)
    db.add(user); db.flush()
    db.add(ProjectMember(project_id=project_id, user_id=user.id, role=role)); db.flush()
    return user


def test_prepare_is_project_scoped_and_never_exposes_provider_locator(db_session, monkeypatch):
    from test_mvp1_storage_mutation_repository import world
    project, _connection, _snapshot, action = world(db_session)
    viewer = principal(db_session, project.id, "viewer")
    monkeypatch.delenv("PU_STORAGE_MUTATION_SYNTHETIC_API_ENABLED", raising=False)
    result = prepare(project.id, action.proposal_id, action.id, db_session, viewer)
    assert result["execution_allowed"] is False
    assert result["synthetic_only"] is True
    assert not ({"connection_id", "folder_id", "object_id", "path", "locator"} & set(result))
    with pytest.raises(HTTPException) as denied:
        prepare(project.id + 1, action.proposal_id, action.id, db_session, viewer)
    assert denied.value.status_code == 403


def test_live_binding_is_hard_denied_even_when_flag_is_enabled(db_session, monkeypatch):
    from test_mvp1_storage_mutation_repository import world
    project, _connection, _snapshot, action = world(db_session)
    manager = principal(db_session, project.id, "manager")
    monkeypatch.setenv("PU_STORAGE_MUTATION_SYNTHETIC_API_ENABLED", "true")
    body = MutationRequest(proposal_id=action.proposal_id, action_id=action.id, record_version=1)
    with pytest.raises(HTTPException) as denied:
        confirm(project.id, body, "storage-live-key-01", db_session, manager)
    assert denied.value.status_code == 403
    assert db_session.query(BackgroundJob).count() == 0


def test_viewer_cannot_enqueue_and_rollback_requires_applied_receipt(db_session, monkeypatch):
    from test_mvp1_storage_mutation_repository import world
    project, connection, snapshot, action = world(db_session)
    connection.connection_id = "synthetic:connection-1"
    binding = dict(snapshot.analysis_result["storage_binding"]); binding["connection_id"] = connection.connection_id
    snapshot.analysis_result = {"storage_binding": binding}
    viewer = principal(db_session, project.id, "viewer")
    manager = principal(db_session, project.id, "manager")
    monkeypatch.setenv("PU_STORAGE_MUTATION_SYNTHETIC_API_ENABLED", "true")
    body = MutationRequest(proposal_id=action.proposal_id, action_id=action.id, record_version=1)
    with pytest.raises(HTTPException) as role_denied:
        confirm(project.id, body, "storage-viewer-key-01", db_session, viewer)
    assert role_denied.value.status_code == 403
    with pytest.raises(HTTPException) as rollback_denied:
        explicit_rollback(project.id, body, "storage-rollback-key-01", db_session, manager)
    assert rollback_denied.value.status_code == 409


def test_synthetic_cohort_enqueues_ids_only_once_and_enforces_cas(db_session, monkeypatch):
    from test_mvp1_storage_mutation_repository import world
    project, connection, _snapshot, action = world(db_session)
    connection.connection_id = "synthetic:connection-1"
    # Keep the exact snapshot pin consistent with the explicit synthetic cohort.
    snapshot = _snapshot
    binding = dict(snapshot.analysis_result["storage_binding"])
    binding["connection_id"] = connection.connection_id
    snapshot.analysis_result = {"storage_binding": binding}
    manager = principal(db_session, project.id, "manager")
    monkeypatch.setenv("PU_STORAGE_MUTATION_SYNTHETIC_API_ENABLED", "true")
    body = MutationRequest(proposal_id=action.proposal_id, action_id=action.id, record_version=1)
    first = confirm(project.id, body, "storage-synthetic-key-01", db_session, manager)
    second = confirm(project.id, body, "storage-synthetic-key-01", db_session, manager)
    assert first["job_id"] == second["job_id"]
    assert second["already_queued"] is True
    job = db_session.get(BackgroundJob, first["job_id"])
    assert set(job.payload) == {"project_id", "proposal_id", "action_id", "command_key",
                                "expected_record_version", "operation"}
    assert not ({"path", "locator", "connection_id", "folder_id", "content"} & set(job.payload))
    with pytest.raises(HTTPException) as stale:
        confirm(project.id, MutationRequest(proposal_id=action.proposal_id, action_id=action.id,
                                             record_version=2),
                "storage-synthetic-key-02", db_session, manager)
    assert stale.value.status_code == 409


def test_status_does_not_cross_project_boundary(db_session):
    from test_mvp1_storage_mutation_repository import world
    project, _connection, _snapshot, action = world(db_session)
    viewer = principal(db_session, project.id, "viewer")
    job = BackgroundJob(kind="workspace.storage_mutation", status="queued", progress=0,
                        payload={"project_id": project.id, "proposal_id": action.proposal_id})
    db_session.add(job); db_session.flush()
    assert status(project.id, job.id, db_session, viewer)["job_id"] == job.id
    with pytest.raises(HTTPException):
        status(project.id + 1, job.id, db_session, viewer)
