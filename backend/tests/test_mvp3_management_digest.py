from datetime import date, datetime, time, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.api import management as api
from app.jobs import handlers, scheduler
from app.management_digest import (
    install_digest_runtime, run_digest_job, schedule_digest_jobs,
)
from app.models.job import BackgroundJob
from app.models.management import (
    ManagementDigest, ManagementHistory, MeetingProposal, MeetingSourceBinding,
    Notification, NotificationPolicy, Obligation,
)
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


@pytest.fixture
def digest_world(db_session, user_factory):
    organization = Organization(name="Digest tenant")
    db_session.add(organization); db_session.flush()
    user = user_factory(name="Digest user")
    project = Project(name="Digest project", organization_id=organization.id)
    db_session.add(project); db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
    policy = NotificationPolicy(
        organization_id=organization.id, project_id=project.id, user_id=user.id,
        timezone="Europe/Moscow", quiet_start=time(22), quiet_end=time(7),
        channels=["in_app", "email"], enabled=True, digest_enabled=True,
        digest_cadence="daily", digest_local_time=time(9),
    )
    db_session.add(policy); db_session.flush()
    return organization, project, user, policy


def _obligation(project_id, user_id, *, status="confirmed", title="Private raw title",
                source_id="source-1", source_hash="a" * 64):
    return Obligation(
        project_id=project_id, owner_user_id=user_id, title=title, status=status,
        due_date=date(2026, 9, 30), source_type="document", source_id=source_id,
        source_name="Private source name", source_excerpt="PRIVATE BODY MUST NOT LEAK",
        source_hash=source_hash, confidence=1.0,
    )


def _payload(organization, project, user, policy, local_date="2026-09-21"):
    return {
        "organization_id": organization.id, "project_id": project.id,
        "user_id": user.id, "policy_id": policy.id,
        "policy_record_version": policy.record_version,
        "local_date": local_date,
    }


def test_policy_cas_reuses_notification_policy_and_generic_history(digest_world, db_session):
    organization, project, user, policy = digest_world
    payload = api.NotificationPolicyUpdate(
        expected_record_version=policy.record_version,
        timezone="Europe/Moscow", deadline_local_time=time(9),
        quiet_start=time(21), quiet_end=time(8), escalation_delays=[0, 60],
        channels=["in_app"], enabled=True, digest_enabled=True,
        digest_cadence="weekdays", digest_local_time=time(10, 30),
    )
    updated = api.update_notification_policy(project.id, payload, db_session, user)
    assert updated["record_version"] == 2
    assert updated["digest_cadence"] == "weekdays"
    assert updated["digest_local_time"] == time(10, 30)
    history = db_session.scalar(select(ManagementHistory).where(
        ManagementHistory.entity_type == "notification_policy",
        ManagementHistory.entity_id == policy.id,
    ))
    assert history and history.organization_id == organization.id and history.action == "updated"
    with pytest.raises(Exception) as conflict:
        api.update_notification_policy(project.id, payload, db_session, user)
    assert getattr(conflict.value, "status_code", None) == 409


@pytest.mark.parametrize(
    ("changes", "now"),
    [
        ({"digest_enabled": False}, datetime(2026, 9, 21, 8, tzinfo=timezone.utc)),
        ({"digest_cadence": "weekdays"}, datetime(2026, 9, 20, 8, tzinfo=timezone.utc)),
        ({}, datetime(2026, 9, 21, 3, tzinfo=timezone.utc)),
        ({}, datetime(2026, 9, 21, 20, tzinfo=timezone.utc)),
    ],
)
def test_scheduler_skips_disabled_weekend_before_time_and_quiet(
    digest_world, db_session, changes, now,
):
    _, _, _, policy = digest_world
    for key, value in changes.items():
        setattr(policy, key, value)
    db_session.commit()
    assert schedule_digest_jobs(db_session, now=now) == 0
    assert db_session.scalar(select(BackgroundJob)) is None


def test_scheduler_is_idempotent_and_transports_identifiers_only(digest_world, db_session):
    organization, project, user, policy = digest_world
    now = datetime(2026, 9, 21, 8, tzinfo=timezone.utc)
    assert schedule_digest_jobs(db_session, now=now) == 1
    assert schedule_digest_jobs(db_session, now=now) == 0
    job = db_session.scalar(select(BackgroundJob))
    assert job.kind == "mvp3.management_digest"
    assert job.payload == _payload(organization, project, user, policy)
    assert not {"title", "body", "excerpt", "evidence", "timezone"}.intersection(job.payload)


def test_digest_contains_only_active_confirmed_refs_and_no_raw_content(digest_world, db_session):
    organization, project, user, policy = digest_world
    db_session.add_all([
        _obligation(project.id, user.id, status="confirmed"),
        _obligation(project.id, user.id, status="needs_confirmation", title="Excluded proposal",
                    source_id="source-2", source_hash="b" * 64),
        _obligation(project.id, user.id, status="fulfilled", title="Excluded closed",
                    source_id="source-3", source_hash="c" * 64),
    ])
    db_session.commit()
    try:
        install_digest_runtime(lambda: db_session, clock=lambda: datetime(2026, 9, 21, 8, tzinfo=timezone.utc))
        result = run_digest_job(_payload(organization, project, user, policy))
    finally:
        install_digest_runtime()
    assert result["status"] == "created" and result["item_count"] == 1
    digest = db_session.get(ManagementDigest, result["digest_id"])
    serialized = str(digest.item_refs)
    assert "PRIVATE BODY" not in serialized and "Private raw title" not in serialized
    assert digest.item_refs[0]["source"] == {
        "source_type": "document", "source_id": "source-1", "source_hash": "a" * 64,
    }
    assert digest.requested_channels == ["in_app", "email"]
    assert db_session.get(Notification, digest.notification_id).kind == "management_digest"
    history = db_session.scalar(select(ManagementHistory).where(
        ManagementHistory.entity_type == "management_digest",
        ManagementHistory.entity_id == digest.id,
    ))
    assert history and "PRIVATE" not in str(history.evidence)


def test_confirmed_meeting_proposal_keeps_exact_origin_evidence(digest_world, db_session):
    organization, project, user, policy = digest_world
    binding_id = str(uuid4())
    binding = MeetingSourceBinding(
        id=binding_id, organization_id=organization.id, project_id=project.id,
        meeting_id=99, meeting_record_version=2, source_id=str(uuid4()),
        source_version_id=str(uuid4()), evidence_id=str(uuid4()),
        materialization_id=str(uuid4()), command_id=str(uuid4()),
        command_hash="b" * 64, bound_by_user_id=user.id,
    )
    # A meeting task target is represented by Task in production.  Here a risk
    # target keeps the fixture small while exercising the same origin contract.
    from app.models.governance import Risk
    risk = Risk(
        project_id=project.id, owner_user_id=user.id, kind="risk", title="Raw risk",
        description="Raw description", criticality="high", status="confirmed",
        source_type="meeting", source_id="meeting:99:proposal:1",
        source_name="Raw source", source_excerpt="Raw excerpt", source_hash="c" * 64,
        confidence=1.0,
    )
    db_session.add_all([binding, risk]); db_session.flush()
    proposal = MeetingProposal(
        organization_id=organization.id, project_id=project.id, meeting_id=99,
        binding_id=binding.id, proposal_type="risk", payload={"title": "must not leak"},
        fingerprint="d" * 64, status="confirmed", target_entity_type="risk",
        target_entity_id=risk.id, created_by_user_id=user.id, confirmed_by_user_id=user.id,
    )
    db_session.add(proposal); db_session.commit()
    try:
        install_digest_runtime(lambda: db_session, clock=lambda: datetime(2026, 9, 21, 8, tzinfo=timezone.utc))
        result = run_digest_job(_payload(organization, project, user, policy))
    finally:
        install_digest_runtime()
    ref = db_session.get(ManagementDigest, result["digest_id"]).item_refs[0]
    assert ref["proposal_origin"] == {
        "proposal_id": proposal.id, "meeting_id": 99, "binding_id": binding.id,
        "source_id": binding.source_id, "source_version_id": binding.source_version_id,
        "evidence_id": binding.evidence_id, "materialization_id": binding.materialization_id,
    }
    assert "must not leak" not in str(ref)


def test_worker_replay_stale_policy_and_revoked_scope_fail_closed(digest_world, db_session):
    organization, project, user, policy = digest_world
    db_session.add(_obligation(project.id, user.id)); db_session.commit()
    payload = _payload(organization, project, user, policy)
    try:
        install_digest_runtime(lambda: db_session, clock=lambda: datetime(2026, 9, 21, 8, tzinfo=timezone.utc))
        first = run_digest_job(payload)
        second = run_digest_job(payload)
        assert first["status"] == "created" and second["status"] == "already_created"
        policy.record_version += 1; db_session.commit()
        stale = run_digest_job(payload)
        assert stale["status"] == "stale_policy"
        db_session.query(ProjectMember).filter_by(project_id=project.id, user_id=user.id).delete()
        db_session.commit()
        assert run_digest_job({**payload, "policy_record_version": policy.record_version})["status"] == "scope_revoked"
    finally:
        install_digest_runtime()
    assert db_session.query(ManagementDigest).count() == 1
    assert db_session.query(Notification).filter_by(kind="management_digest").count() == 1


def test_handler_and_scheduler_wiring(digest_world, db_session, monkeypatch):
    organization, project, user, policy = digest_world
    db_session.add(_obligation(project.id, user.id)); db_session.commit()
    try:
        install_digest_runtime(lambda: db_session, clock=lambda: datetime(2026, 9, 21, 8, tzinfo=timezone.utc))
        assert handlers.run("mvp3.management_digest", _payload(organization, project, user, policy))["status"] == "created"
    finally:
        install_digest_runtime()

    class ExistingSession:
        def __enter__(self): return db_session
        def __exit__(self, *_args): return False

    monkeypatch.setattr(scheduler, "SessionLocal", ExistingSession)
    monkeypatch.setattr(scheduler, "gmail_enabled", lambda: False)
    monkeypatch.setattr(scheduler, "ai_enabled", lambda: False)
    monkeypatch.setattr(scheduler, "notifications_enabled", lambda: False)
    monkeypatch.setattr("app.pilot_dispatch.recover_installed", lambda: 0)
    monkeypatch.setattr("app.local_upload_staging.recover_local_upload_retention", lambda: 0)
    monkeypatch.setattr("app.staging.gmail.recover_gmail_attachment_jobs", lambda: 0)
    assert scheduler.schedule_once(now=datetime(2026, 9, 22, 8, tzinfo=timezone.utc)) == 1
