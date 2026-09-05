from datetime import date, datetime, time, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.database import Base
from app.jobs.handlers import run as run_job
from app.models.ai_secretary import Message
from app.models.governance import Decision
from app.models.job import BackgroundJob
from app.models.management import Meeting, Notification, Obligation
from app.models.audit_log import AuditLog
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.v54_pilot import SourceReference
from app.mvp3.meeting_digest import (
    DigestPreference,
    MeetingActionCandidate,
    MeetingDigestService,
    MeetingProposalService,
    enqueue_digest,
    install_digest_runtime,
)
from app.mvp3.lifecycle import ManagementDenied, ManagementLifecycle
from v54_pilot_fixture import pin, seed, uid


@pytest.fixture
def world():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed(db)
        source = db.get(SourceReference, uid(13))
        source.availability = "available"
        source.freshness = "fresh"
        source.sync_state = "current"
        db.add_all([
            ProjectMember(project_id=4, user_id=2, role="manager"),
            ProjectMember(project_id=4, user_id=3, role="editor"),
            Meeting(project_id=4, created_by_user_id=2, title="Планёрка", status="completed"),
        ])
        db.flush()
        yield db
        db.rollback()
    engine.dispose()


def evidence_pin():
    return pin("evidence", uid(16), tenant=1)


def candidate(kind="task", **changes):
    values = dict(kind=kind, title="Передать исполнительную документацию", owner_user_id=2,
                  evidence_pins=[evidence_pin()], due_date=date(2026, 9, 12))
    values.update(changes)
    return MeetingActionCandidate(**values)


def _propose_bound_message(world, service, *, actor_user_id, candidates):
    world.get(Message, 6).context_confirmed = True
    return service.propose_message(world, project_id=4, message_id=6,
                                   actor_user_id=actor_user_id, candidates=candidates)


def test_bound_message_extraction_creates_only_reviewable_evidence_backed_proposals(world):
    service = MeetingProposalService(ManagementLifecycle())
    result = _propose_bound_message(world, service, actor_user_id=3,
                             candidates=[candidate()])

    obligation = world.get(Obligation, result[0]["entity_id"])
    assert result == [{"kind": "task", "entity_type": "obligation", "entity_id": obligation.id,
                       "record_version": 1, "status": "needs_confirmation",
                       "review_state": "needs_review", "task_id": None}]
    assert obligation.evidence_pins == [evidence_pin()]
    assert world.scalars(select(Task)).all() == []
    assert obligation.source_excerpt == "Exact evidence pin"


def test_manager_confirmation_is_required_before_internal_task(world):
    service = MeetingProposalService(ManagementLifecycle())
    result = _propose_bound_message(world, service, actor_user_id=3,
                             candidates=[candidate()])[0]
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        service.confirm(world, project_id=4, actor_user_id=3, entity_type="obligation",
                        entity_id=result["entity_id"], expected_version=1, create_internal_task=True)
    assert world.scalars(select(Task)).all() == []

    confirmed = service.confirm(world, project_id=4, actor_user_id=2, entity_type="obligation",
                                entity_id=result["entity_id"], expected_version=1,
                                create_internal_task=True)
    assert confirmed["status"] == "confirmed"
    assert confirmed["task_id"] is not None
    task = world.get(Task, confirmed["task_id"])
    assert task.external_action_status == "proposed"
    assert task.google_task_id is None and task.google_calendar_event_id is None


def test_decision_remains_proposed_until_manager_confirmation(world):
    service = MeetingProposalService(ManagementLifecycle())
    result = _propose_bound_message(world, service, actor_user_id=3,
                             candidates=[candidate("decision", title="Утвердить новый срок?")])[0]
    row = world.get(Decision, result["entity_id"])
    assert row.status == "needs_confirmation" and row.evidence_pins == [evidence_pin()]
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        service.confirm(world, project_id=4, actor_user_id=3, entity_type="decision",
                        entity_id=row.id, expected_version=1)
    service.confirm(world, project_id=4, actor_user_id=2, entity_type="decision",
                    entity_id=row.id, expected_version=1)
    assert world.get(Decision, row.id).status == "confirmed"


def test_proposal_requires_completed_same_project_meeting_and_exact_current_evidence(world):
    service = MeetingProposalService(ManagementLifecycle())
    world.get(Meeting, 1).status = "planned"
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        service.propose(world, project_id=4, meeting_id=1, actor_user_id=2, candidates=[candidate()])
    world.get(Meeting, 1).status = "completed"
    with pytest.raises(ValueError, match="at least 1 item"):
        candidate(evidence_pins=[])


def test_extraction_is_idempotent_and_conflicting_reuse_fails_closed(world):
    service = MeetingProposalService(ManagementLifecycle())
    first = _propose_bound_message(world, service, actor_user_id=2,
                            candidates=[candidate()])
    second = _propose_bound_message(world, service, actor_user_id=2,
                             candidates=[candidate()])
    assert first == second
    assert len(world.scalars(select(Obligation)).all()) == 1
    audits = world.scalars(select(AuditLog).where(AuditLog.action == "mvp3_proposal_created")).all()
    assert len(audits) == 1 and "Передать" not in (audits[0].details or "")
    with pytest.raises(ManagementDenied, match="evidence_already_bound"):
        _propose_bound_message(world, service, actor_user_id=2,
                        candidates=[candidate(title="Другое действие")])


def test_extractor_does_not_accept_or_store_raw_protocol(world):
    fields = MeetingActionCandidate.model_fields
    assert "minutes" not in fields and "content" not in fields and "message" not in fields
    service = MeetingProposalService(ManagementLifecycle())
    _propose_bound_message(world, service, actor_user_id=2, candidates=[candidate()])
    assert all("Передать" not in (job.payload if isinstance(job.payload, str) else repr(job.payload))
               for job in world.scalars(select(BackgroundJob)).all())


def test_confirmed_message_can_propose_but_unconfirmed_or_unrelated_source_cannot(world):
    service = MeetingProposalService(ManagementLifecycle())
    row = world.get(Message, 6)
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        service.propose_message(world, project_id=4, message_id=6, actor_user_id=2,
                                candidates=[candidate()])
    row.context_confirmed = True
    result = service.propose_message(world, project_id=4, message_id=6, actor_user_id=2,
                                     candidates=[candidate()])
    assert result[0]["status"] == "needs_confirmation"
    assert world.scalar(select(AuditLog).where(AuditLog.action == "mvp3_proposal_created")).entity_type == "message"
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        service.propose_message(world, project_id=4, message_id=6, actor_user_id=2,
                                candidates=[candidate(evidence_pins=[pin("evidence", uid(999), tenant=1)])])


def _confirmed_obligation(world):
    service = MeetingProposalService(ManagementLifecycle())
    proposal = _propose_bound_message(world, service, actor_user_id=2,
                               candidates=[candidate()])[0]
    service.confirm(world, project_id=4, actor_user_id=2, entity_type="obligation",
                    entity_id=proposal["entity_id"], expected_version=1)


def test_digest_creates_safe_internal_notification_once(world):
    _confirmed_obligation(world)
    service = MeetingDigestService()
    pref = DigestPreference(timezone="Europe/Moscow", quiet_start=time(20), quiet_end=time(8), channel="in_app")
    now = datetime(2026, 9, 5, 10, tzinfo=timezone.utc)
    first = service.generate(world, project_id=4, user_id=2, preference=pref, now=now)
    second = service.generate(world, project_id=4, user_id=2, preference=pref, now=now)
    assert first["status"] == "created" and second["status"] == "already_created"
    assert len(world.scalars(select(Notification)).all()) == 1
    notification = world.scalar(select(Notification))
    assert notification.kind == "management_digest"
    assert "Передать" not in notification.title + notification.body
    assert "Evidence" not in notification.title + notification.body
    assert first["external_actions_created"] is False


def test_digest_respects_quiet_hours_timezone_and_disabled_channel(world):
    _confirmed_obligation(world)
    service = MeetingDigestService()
    quiet = service.generate(
        world, project_id=4, user_id=2,
        preference=DigestPreference(timezone="Europe/Moscow", quiet_start=time(20), quiet_end=time(8), channel="in_app"),
        now=datetime(2026, 9, 5, 19, tzinfo=timezone.utc),
    )
    assert quiet["status"] == "deferred_quiet_hours"
    assert quiet["deferred_until"].endswith("+03:00")
    disabled = service.generate(
        world, project_id=4, user_id=2,
        preference=DigestPreference(timezone="UTC", quiet_start=time(22), quiet_end=time(7), channel="disabled"),
        now=datetime(2026, 9, 5, 12, tzinfo=timezone.utc),
    )
    assert disabled["status"] == "disabled"
    assert world.scalars(select(Notification)).all() == []


@pytest.mark.parametrize("zone", ["not/a-zone", "UTC+99:00"])
def test_digest_rejects_invalid_timezone(world, zone):
    with pytest.raises(ValueError, match="invalid_timezone"):
        DigestPreference(timezone=zone, quiet_start=time(20), quiet_end=time(8), channel="in_app")


def test_digest_job_payload_is_id_only_and_enqueue_is_idempotent(world, monkeypatch):
    monkeypatch.setattr("app.mvp3.meeting_digest.queue.enqueue", lambda db, kind, payload, **kwargs:
                        BackgroundJob(kind=kind, payload=payload, idempotency_key=kwargs["idempotency_key"]))
    pref = DigestPreference(timezone="Europe/Moscow", quiet_start=time(20), quiet_end=time(8), channel="in_app")
    job = enqueue_digest(world, project_id=4, user_id=2, actor_user_id=2,
                         preference=pref, local_date=date(2026, 9, 5))
    assert job.kind == "mvp3.management_digest"
    assert set(job.payload) == {"project_id", "user_id", "timezone", "quiet_start", "quiet_end", "channel", "local_date"}
    assert not ({"content", "minutes", "message", "document", "email", "evidence_pins"} & set(job.payload))
    assert job.idempotency_key == "mvp3.digest:4:2:2026-09-05:in_app"


def test_job_handler_uses_installed_runtime_and_never_calls_provider(world):
    _confirmed_obligation(world)
    try:
        install_digest_runtime(lambda: world, clock=lambda: datetime(2026, 9, 5, 10, tzinfo=timezone.utc))
        result = run_job("mvp3.management_digest", {
            "project_id": 4, "user_id": 2, "timezone": "Europe/Moscow",
            "quiet_start": "20:00:00", "quiet_end": "08:00:00", "channel": "in_app",
            "local_date": "2026-09-05",
        })
        assert result["status"] == "created"
        assert result["external_actions_created"] is False
    finally:
        install_digest_runtime()


def test_job_handler_rejects_unknown_or_extra_payload(world):
    try:
        install_digest_runtime(lambda: world)
        with pytest.raises(ValueError, match="invalid_job_payload"):
            run_job("mvp3.management_digest", {"project_id": 4, "user_id": 2, "content": "secret"})
    finally:
        install_digest_runtime()
