from datetime import date, datetime, time, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.database import Base
from app.models.audit_log import AuditLog
from app.models.ai_secretary import Message
from app.models.job import BackgroundJob
from app.models.management import Meeting, Obligation
from app.models.management_digest import ManagementDigestPreference, ManagementProposalOrigin
from app.models.project_member import ProjectMember
from app.models.v54_pilot import EvidenceAssessment, SourceReference
from app.mvp3.meeting_digest import (
    DigestPreference,
    DigestPreferenceService,
    MeetingActionCandidate,
    MeetingProposalService,
    install_digest_runtime,
    run_digest_job,
    schedule_digest_jobs,
)
from app.mvp3.lifecycle import ManagementConflict, ManagementDenied
from app.jobs import scheduler
from v54_pilot_fixture import pin, seed, uid


@pytest.fixture
def world():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed(db)
        db.get(Message, 6).context_confirmed = True
        source = db.get(SourceReference, uid(13))
        source.availability = "available"
        source.freshness = "fresh"
        source.sync_state = "current"
        db.add_all([
            ProjectMember(project_id=4, user_id=2, role="manager"),
            ProjectMember(project_id=4, user_id=3, role="editor"),
            Meeting(project_id=4, created_by_user_id=2, title="Synthetic meeting", status="completed"),
        ])
        db.commit()
        yield db
        db.rollback()
    engine.dispose()


def evidence_pin():
    return pin("evidence", uid(16), tenant=1)


def preference(**changes):
    values = dict(
        timezone="Europe/Moscow", quiet_start=time(20), quiet_end=time(8),
        channel="in_app", cadence="daily",
    )
    values.update(changes)
    return DigestPreference(**values)


def candidate(kind="task"):
    return MeetingActionCandidate(
        kind=kind, title="Synthetic action", owner_user_id=2,
        evidence_pins=[evidence_pin()], due_date=date(2026, 9, 12),
    )


def test_unsaved_preference_is_safe_visible_default_but_does_not_schedule(world):
    result = DigestPreferenceService().get(world, project_id=4, user_id=2)
    assert result == {
        "project_id": 4, "user_id": 2, "timezone": "Europe/Moscow",
        "quiet_start": "20:00:00", "quiet_end": "08:00:00",
        "channel": "in_app", "cadence": "daily", "record_version": 0,
        "persisted": False, "external_actions_enabled": False,
    }
    assert schedule_digest_jobs(world, now=datetime(2026, 9, 7, 10, tzinfo=timezone.utc)) == 0
    assert world.scalars(select(BackgroundJob)).all() == []


def test_preference_create_update_and_cas_are_scoped_and_audited_without_configuration(world):
    service = DigestPreferenceService()
    row = service.put(
        world, project_id=4, user_id=2, expected_version=0,
        preference=preference(cadence="weekdays"),
    )
    world.commit()
    assert row.record_version == 1
    assert service.get(world, project_id=4, user_id=2)["cadence"] == "weekdays"

    updated = service.put(
        world, project_id=4, user_id=2, expected_version=1,
        preference=preference(channel="disabled"),
    )
    world.commit()
    assert updated.record_version == 2 and updated.channel == "disabled"
    with pytest.raises(ManagementConflict, match="version_conflict"):
        service.put(
            world, project_id=4, user_id=2, expected_version=1,
            preference=preference(),
        )
    audits = world.scalars(select(AuditLog).where(
        AuditLog.entity_type == "management_digest_preference",
    )).all()
    assert len(audits) == 2
    assert all("Europe" not in (item.details or "") and "20:00" not in (item.details or "") for item in audits)
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        service.get(world, project_id=4, user_id=1)


def test_scheduler_enqueues_one_ids_only_job_per_preference_version_and_local_day(world):
    row = DigestPreferenceService().put(
        world, project_id=4, user_id=2, expected_version=0, preference=preference(),
    )
    world.commit()
    now = datetime(2026, 9, 7, 10, tzinfo=timezone.utc)
    assert schedule_digest_jobs(world, now=now) == 1
    assert schedule_digest_jobs(world, now=now) == 0
    jobs = world.scalars(select(BackgroundJob)).all()
    assert len(jobs) == 1
    assert jobs[0].kind == "mvp3.management_digest"
    assert jobs[0].payload == {
        "project_id": 4, "user_id": 2, "local_date": "2026-09-07",
        "preference_id": row.id, "preference_version": 1,
    }
    assert jobs[0].idempotency_key == f"mvp3.digest.preference:{row.id}:v1:2026-09-07"
    forbidden = {"content", "minutes", "message", "document", "email", "evidence_pins", "timezone"}
    assert not forbidden.intersection(jobs[0].payload)


def test_existing_scheduler_invokes_digest_pass_without_a_second_queue(world, monkeypatch):
    DigestPreferenceService().put(
        world, project_id=4, user_id=2, expected_version=0, preference=preference(),
    )
    world.commit()

    class ExistingSession:
        def __enter__(self):
            return world

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(scheduler, "SessionLocal", ExistingSession)
    monkeypatch.setattr(scheduler, "gmail_enabled", lambda: False)
    monkeypatch.setattr(scheduler, "ai_enabled", lambda: False)
    monkeypatch.setattr("app.pilot_dispatch.recover_installed", lambda: 0)
    monkeypatch.setattr("app.local_upload_staging.recover_local_upload_retention", lambda: 0)
    monkeypatch.setattr("app.staging.gmail.recover_gmail_attachment_jobs", lambda: 0)

    created = scheduler.schedule_once(
        now=datetime(2026, 9, 7, 10, tzinfo=timezone.utc), service_id="synthetic-scheduler",
    )
    assert created == 1
    assert len(world.scalars(select(BackgroundJob).where(
        BackgroundJob.kind == "mvp3.management_digest",
    )).all()) == 1


@pytest.mark.parametrize(
    ("value", "now"),
    [
        (preference(channel="disabled"), datetime(2026, 9, 7, 10, tzinfo=timezone.utc)),
        (preference(cadence="weekdays"), datetime(2026, 9, 6, 10, tzinfo=timezone.utc)),
        (preference(), datetime(2026, 9, 7, 19, tzinfo=timezone.utc)),
    ],
)
def test_scheduler_skips_disabled_weekend_and_quiet_preferences(world, value, now):
    DigestPreferenceService().put(
        world, project_id=4, user_id=2, expected_version=0, preference=value,
    )
    world.commit()
    assert schedule_digest_jobs(world, now=now) == 0
    assert world.scalars(select(BackgroundJob)).all() == []


def test_worker_rereads_exact_preference_and_rejects_stale_version(world):
    row = DigestPreferenceService().put(
        world, project_id=4, user_id=2, expected_version=0, preference=preference(),
    )
    world.commit()
    payload = {
        "project_id": 4, "user_id": 2, "local_date": "2026-09-07",
        "preference_id": row.id, "preference_version": 1,
    }
    try:
        install_digest_runtime(
            lambda: world, clock=lambda: datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
        )
        assert run_digest_job(payload)["status"] == "empty"
        DigestPreferenceService().put(
            world, project_id=4, user_id=2, expected_version=1,
            preference=preference(channel="disabled"),
        )
        world.commit()
        stale = run_digest_job(payload)
        assert stale["status"] == "stale_preference"
        assert stale["external_actions_created"] is False
    finally:
        install_digest_runtime()


def test_proposals_are_reloadable_with_exact_evidence_and_manual_review(world):
    service = MeetingProposalService()
    created = service.propose_message(
        world, project_id=4, message_id=6, actor_user_id=3, candidates=[candidate()],
    )
    world.commit()
    listed = service.list_for_origin(
        world, project_id=4, actor_user_id=2, origin_type="message", origin_id=6,
    )
    assert listed == [{
        **created[0], "evidence_pins": [evidence_pin()], "manual_review_required": True,
    }]
    link = world.scalar(select(ManagementProposalOrigin))
    assert link.proposal_kind == "task" and link.evidence_pins == [evidence_pin()]


def test_proposal_read_and_confirmation_fail_closed_after_evidence_becomes_stale(world):
    service = MeetingProposalService()
    created = service.propose_message(
        world, project_id=4, message_id=6, actor_user_id=3, candidates=[candidate()],
    )[0]
    assessment = world.get(EvidenceAssessment, uid(16))
    assessment.freshness = "stale"
    world.flush()
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        service.list_for_origin(
            world, project_id=4, actor_user_id=2, origin_type="message", origin_id=6,
        )
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        service.confirm(
            world, project_id=4, actor_user_id=2, entity_type="obligation",
            entity_id=created["entity_id"], expected_version=1, create_internal_task=True,
        )
    assert world.get(Obligation, created["entity_id"]).status == "needs_confirmation"


def test_message_proposal_reload_rechecks_confirmed_origin_and_source_binding(world):
    service = MeetingProposalService()
    message = world.get(Message, 6)
    message.context_confirmed = True
    created = service.propose_message(
        world, project_id=4, message_id=message.id, actor_user_id=3,
        candidates=[candidate("decision")],
    )
    world.commit()
    listed = service.list_for_origin(
        world, project_id=4, actor_user_id=2, origin_type="message", origin_id=message.id,
    )
    assert listed[0]["entity_id"] == created[0]["entity_id"]
    assert listed[0]["kind"] == "decision"
    message.context_confirmed = False
    world.flush()
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        service.list_for_origin(
            world, project_id=4, actor_user_id=2, origin_type="message", origin_id=message.id,
        )


def test_proposal_origin_is_append_only(world):
    MeetingProposalService().propose_message(
        world, project_id=4, message_id=6, actor_user_id=3, candidates=[candidate()],
    )
    world.commit()
    link = world.scalar(select(ManagementProposalOrigin))
    link.proposal_kind = "obligation"
    with pytest.raises(ValueError, match="management_proposal_origin_is_append_only"):
        world.flush()
