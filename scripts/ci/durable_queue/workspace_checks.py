"""Synthetic PostgreSQL workspace/queue checks, BEFORE workers/scheduler start.

No storage calls; failed assertions abort the disposable harness. Not production code.
"""
import json
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

from app.api import workspace
from app.database import SessionLocal
from app.jobs import queue
from app.models.drive_connection import DriveConnection
from app.models.job import BackgroundJob
from app.models.organization_contract import Organization
from app.models.organizer import OrganizerSession
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.workspace import SourceFolder, WorkspaceSnapshot


def main():
    with SessionLocal() as db:
        url = db.get_bind().url
        assert url.get_backend_name() == "postgresql"
        assert url.host == "db" and url.database == "puw_queue_test"
        org = Organization(name="CI workspace recovery")
        user = User(name="CI owner", email="workspace-recovery@example.test")
        db.add_all([org, user]); db.flush()
        project = Project(name="CI synthetic project", organization_id=org.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        connection = DriveConnection(project_id=project.id, provider="google_drive",
                                     connection_id="ci-no-credentials", account_email="ci@example.test", root_folder_id="synthetic-folder")
        source = SourceFolder(project_id=project.id, external_id="synthetic-folder", name="CI", provider="google_drive")
        db.add_all([connection, source]); db.flush()
        binding = workspace._binding(connection, source.external_id)
        snapshot = WorkspaceSnapshot(project_id=project.id, source_folder_id=source.id,
                                     status="failed", analysis_status="pending",
                                     analysis_result={"storage_binding": binding})
        db.add(snapshot); db.commit()
        pid, sid, uid = project.id, snapshot.id, user.id
        active = queue.enqueue(db, "workspace.snapshot", {"snapshot_id": sid, "project_id": pid,
                               "external_id": "synthetic-folder"}, idempotency_key=f"workspace.snapshot:{sid}")
        active_id = active.id

    # The real handlers must not resolve any external adapter during these checks.
    with patch.object(workspace, "storage_for_project", side_effect=AssertionError("External I/O forbidden")):
        for status in ("queued", "running", "retrying"):
            with SessionLocal() as db:
                db.get(BackgroundJob, active_id).status = status
                db.commit()
                try:
                    workspace.retry_snapshot_build(pid, sid, db=db, user=db.get(User, uid))
                except HTTPException as exc:
                    assert exc.status_code == 409
                else:
                    raise AssertionError("manual retry competed with active job")
            assert workspace._enqueue_snapshot(sid, pid, "synthetic-folder") == active_id

        with SessionLocal() as db:
            db.get(BackgroundJob, active_id).status = "completed"
            snap = db.get(WorkspaceSnapshot, sid)
            snap.status, snap.analysis_status = "ready", "pending"
            db.commit()
            result = workspace.analyze_workspace_snapshot(pid, sid, db=db, user=db.get(User, uid))
            assert result["status"] == "analyzing" and not result["already_queued"]
            manual = db.scalar(select(BackgroundJob).where(BackgroundJob.kind == "workspace.analysis"))
            manual_id = manual.id
            assert ":manual:" in manual.idempotency_key
        assert workspace._enqueue_analysis(sid, pid) == manual_id
        with SessionLocal() as db:
            assert len(list(db.scalars(select(BackgroundJob).where(BackgroundJob.kind == "workspace.analysis")))) == 1
            db.get(BackgroundJob, manual_id).status = "completed"
            db.commit()

        with patch.object(queue, "enqueue", side_effect=RuntimeError("synthetic enqueue failure")):
            try:
                workspace._start_safe_copy_pipeline(sid, pid, "synthetic-folder", "CI")
            except RuntimeError:
                pass
            else:
                raise AssertionError("fault not injected")
        with SessionLocal() as db:
            assert not list(db.scalars(select(OrganizerSession).where(OrganizerSession.project_id == pid)))
        session_id = workspace._start_safe_copy_pipeline(sid, pid, "synthetic-folder", "CI")
        with SessionLocal() as db:
            db.get(WorkspaceSnapshot, sid).analysis_result = {"storage_binding": binding}
            db.commit()
        assert workspace._start_safe_copy_pipeline(sid, pid, "synthetic-folder", "CI") == session_id
        with SessionLocal() as db:
            sessions = list(db.scalars(select(OrganizerSession).where(OrganizerSession.project_id == pid)))
            jobs = list(db.scalars(select(BackgroundJob).where(BackgroundJob.kind == "workspace.safe_copy")))
            assert len(sessions) == len(jobs) == 1
            safe_id = jobs[0].id
            # Leave no runnable fake storage work for the subsequent runtime stage.
            jobs[0].status = "completed"
            sessions[0].status = "proposed"
            db.get(WorkspaceSnapshot, sid).analysis_status = "ready"
            db.commit()
    print(json.dumps({"snapshot_id": sid, "manual_job_id": manual_id, "safe_copy_job_id": safe_id,
                      "session_id": session_id, "active_retry_rejected": True,
                      "canonical_reused_manual": True, "enqueue_recovery_reused_session": True}))


if __name__ == "__main__":
    main()
