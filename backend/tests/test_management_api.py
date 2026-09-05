from datetime import date, time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.api.management import (
    DigestEnqueueRequest,
    DigestPreferenceUpdate,
    EvidenceProposalConfirm,
    EvidenceProposalCreate,
    MeetingCreate,
    MeetingUpdate,
    ObligationUpdate,
    finish_meeting,
    router,
)
from app.database import Base
from app.models.audit_log import AuditLog
from app.models.governance import Decision, Risk
from app.models.management import Meeting
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.user import User


def test_mvp3_routes_are_registered():
    paths = {route.path for route in router.routes}
    methods = {(route.path, method) for route in router.routes for method in route.methods}
    assert "/management/obligations" in paths
    assert "/management/meetings" in paths
    assert "/management/notifications/refresh" in paths
    assert "/management/notifications/{notification_id}/read" in paths
    assert "/management/v2/meetings/{meeting_id}/proposals" in paths
    assert "/management/v2/messages/{message_id}/proposals" in paths
    assert "/management/v2/proposals/{entity_type}/{entity_id}/confirm" in paths
    assert "/management/v2/digests" in paths
    assert ("/management/v2/meetings/{meeting_id}/proposals", "GET") in methods
    assert ("/management/v2/messages/{message_id}/proposals", "GET") in methods
    assert ("/management/v2/projects/{project_id}/digest-preference", "GET") in methods
    assert ("/management/v2/projects/{project_id}/digest-preference", "PUT") in methods


def test_meeting_and_obligation_contracts():
    meeting = MeetingCreate(project_id=1, title="Планёрка", agenda="Проверить сроки")
    assert meeting.contract_id is None
    assert MeetingUpdate(minutes="Подрядчик должен направить акт до 28 августа.").status == "completed"
    assert ObligationUpdate(status="confirmed").result_note is None


def test_evidence_proposal_and_digest_contracts_are_fail_closed():
    proposal = EvidenceProposalCreate(project_id=4, candidates=[{
        "kind": "task", "title": "Передать акт", "owner_user_id": 2,
        "evidence_pins": [{"ref": {"kind": "evidence", "id": {"value": "00000000-0000-0000-0000-000000000001"},
                                      "tenant": {"value": "1"}}}],
        "due_date": date(2026, 9, 12),
    }])
    assert proposal.candidates[0].kind == "task"
    assert EvidenceProposalConfirm(project_id=4, expected_version=1).create_internal_task is False
    digest = DigestEnqueueRequest(
        project_id=4, timezone="Europe/Moscow", quiet_start=time(20), quiet_end=time(8),
        channel="in_app", local_date=date(2026, 9, 5),
    )
    assert digest.channel == "in_app"
    stored = DigestPreferenceUpdate(
        expected_version=0, timezone="UTC", quiet_start=time(22), quiet_end=time(7),
    )
    assert stored.cadence == "daily" and stored.channel == "in_app"
    with pytest.raises(ValueError):
        DigestEnqueueRequest(
            project_id=4, timezone="Europe/Moscow", quiet_start=time(20), quiet_end=time(8),
            channel="email", local_date=date(2026, 9, 5),
        )


def test_finishing_meeting_does_not_create_unverified_business_entities():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(name="Manager", email="manager@example.test", is_admin=False)
        organization = Organization(name="Synthetic")
        db.add_all([user, organization]); db.flush()
        project = Project(name="Synthetic project", organization_id=organization.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        meeting = Meeting(project_id=project.id, created_by_user_id=user.id,
                          title="Synthetic meeting", status="planned")
        db.add(meeting); db.commit()

        result = finish_meeting(
            meeting.id, MeetingUpdate(minutes="Sensitive synthetic minutes", status="completed"), db, user,
        )

        assert result["proposal_state"] == "awaiting_evidence"
        assert result["tasks"] == result["risks"] == result["decisions"] == 0
        assert db.scalars(select(Task)).all() == []
        assert db.scalars(select(Risk)).all() == []
        assert db.scalars(select(Decision)).all() == []
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "meeting_minutes_recorded"))
        assert "Sensitive" not in (audit.details or "")
    engine.dispose()
