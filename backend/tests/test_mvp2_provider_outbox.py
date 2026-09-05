from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.gmail import send_gmail
from app.api.tasks import ExternalActionApproval, TaskUpdate, approve_external, update_task
from app.jobs.queue import execution_owner
from app.models.audit_log import AuditLog
from app.models.google_token import GoogleOAuthToken
from app.models.job import BackgroundJob
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.v54_provider_action import (
    ProviderAction, ProviderActionApproval, ProviderDispatchOutbox,
    ProviderOutcomeObservation,
)
from app.provider_actions.contracts import ProviderActionError
from app.provider_actions.product import (
    RECONCILE_KIND, build_product_runtime, queue_confirmed_action, queue_reconciliation,
    run_product_reconcile_job,
)
from app.provider_actions.runtime import PRODUCT_KIND


SCOPES = " ".join((
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar.events",
))


@pytest.fixture
def world(db_session, user_factory):
    db = db_session
    user = user_factory()
    org = Organization(name="Synthetic provider organization")
    db.add(org); db.flush()
    project = Project(name="Synthetic provider project", organization_id=org.id)
    db.add(project); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
    db.add(GoogleOAuthToken(project_id=project.id, access_token=None, refresh_token=None,
                            scopes=SCOPES))
    draft = ResponseDraft(
        project_id=project.id, reviewer_user_id=user.id, subject="Synthetic subject",
        body="Synthetic body marker", recipient_to="recipient@example.test", status="approved",
        source_file_id="synthetic", source_file_name="synthetic", source_excerpt="synthetic",
        source_excerpt_hash="a" * 64, confidence=1,
    )
    task = Task(
        project_id=project.id, assignee_user_id=user.id, created_by_user_id=user.id,
        title="Synthetic task", description="Synthetic description", status="assigned",
        due_date=date(2035, 1, 2), source_type="synthetic", source_file_id="synthetic",
        source_file_name="synthetic", source_excerpt="synthetic evidence",
        source_excerpt_hash="b" * 64, confidence=1, needs_review=False,
    )
    db.add_all([draft, task]); db.commit()
    return SimpleNamespace(db=db, user=user, org=org, project=project, draft=draft, task=task)


class Request:
    def __init__(self, result=None, error=None):
        self.result, self.error = result or {}, error

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class FakeGoogle:
    def __init__(self, kind: str, *, fail_after=False):
        self.kind = kind
        self.fail_after = fail_after
        self.effects = 0
        self.sent = []
        self.task_items = []
        self.events_by_id = {}

    def users(self): return self
    def messages(self): return self
    def tasks(self): return self
    def events(self): return self

    def send(self, **kwargs):
        self.effects += 1
        item = {"id": "gmail-external-1", "body": kwargs["body"]}
        self.sent.append(item)
        return Request(item, RuntimeError("synthetic timeout after effect") if self.fail_after else None)

    def list(self, **kwargs):
        if self.kind == "gmail.message.send":
            return Request({"messages": [{"id": row["id"]} for row in self.sent]})
        return Request({"items": list(self.task_items)})

    def insert(self, **kwargs):
        self.effects += 1
        if self.kind == "google.tasks.upsert":
            item = {"id": "task-external-1", **kwargs["body"]}; self.task_items.append(item)
        else:
            item = {"id": kwargs["body"].get("id", "calendar-external-1"), **kwargs["body"]}
            self.events_by_id[item["id"]] = item
        return Request(item)

    def patch(self, **kwargs):
        self.effects += 1
        external_id = kwargs.get("task") or kwargs.get("eventId")
        return Request({"id": external_id, **kwargs["body"]})

    def get(self, **kwargs):
        if "eventId" in kwargs:
            return Request(self.events_by_id[kwargs["eventId"]])
        task_id = kwargs["task"]
        return Request(next(item for item in self.task_items if item["id"] == task_id))


def sessions(world):
    return sessionmaker(bind=world.db.bind, expire_on_commit=False)


def owner(world, job_id: int, worker: str, attempt=1):
    with sessions(world).begin() as db:
        job = db.get(BackgroundJob, job_id)
        now = datetime.now(timezone.utc)
        job.status = "running"; job.worker_id = worker; job.attempts = attempt
        job.locked_at = now; job.lease_expires_at = now + timedelta(minutes=5)
    return job_id, worker, attempt, now


def test_gmail_confirmation_only_queues_content_free_durable_action(world, monkeypatch):
    monkeypatch.setattr("app.api.gmail.require_project_role", lambda *args: "manager")
    result = send_gmail(world.draft.id, db=world.db, user=world.user)

    job = world.db.get(BackgroundJob, result["job_id"])
    action = world.db.get(ProviderAction, (result["action_id"], result["revision"]))
    approval = world.db.scalar(select(ProviderActionApproval).where(
        ProviderActionApproval.action_id == action.action_id,
    ))
    serialized = repr(job.payload) + repr(action) + repr(list(world.db.scalars(select(AuditLog))))
    assert result["status"] == "queued" and world.draft.status == "sending"
    assert job.kind == PRODUCT_KIND
    assert set(job.payload) == {"organization_id", "action_id", "revision"}
    assert action.payload_hash == approval.payload_hash and action.envelope_hash == approval.envelope_hash
    assert action.synthetic_only is False and action.mode == "CONFIRM"
    assert "recipient@example.test" not in serialized and "Synthetic body marker" not in serialized


def test_task_and_calendar_confirmation_queue_two_scoped_actions_without_effect(world, monkeypatch):
    monkeypatch.setattr("app.api.tasks.require_project_role", lambda *args: "manager")
    result = approve_external(
        world.task.id, ExternalActionApproval(publish_task=True, publish_calendar=True),
        db=world.db, user=world.user,
    )
    jobs = list(world.db.scalars(select(BackgroundJob).where(BackgroundJob.kind == PRODUCT_KIND)))
    assert result["external_action_status"] == "queued"
    assert len(result["actions"]) == 2 and len(jobs) == 2
    assert {row.action_kind for row in world.db.scalars(select(ProviderAction))} == {
        "google.tasks.upsert", "google.calendar.upsert",
    }
    assert all(set(job.payload) == {"organization_id", "action_id", "revision"} for job in jobs)


def test_worker_dispatches_task_once_and_persists_receipt_and_external_id(world):
    queued = queue_confirmed_action(
        world.db, action_kind="google.tasks.upsert", target_id=world.task.id, actor=world.user,
    )
    fake = FakeGoogle("google.tasks.upsert")
    runtime = build_product_runtime(sessions=sessions(world), service_factory=lambda *args: fake)
    result = runtime.execute_job(
        {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1},
        owner(world, queued["job_id"], "worker-a"),
    )
    repeated = runtime.execute_job(
        {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1},
        owner(world, queued["job_id"], "worker-b", 2),
    )
    assert result["outcome"] == repeated["outcome"] == "APPLIED"
    assert fake.effects == 1
    with sessions(world)() as db:
        assert db.get(Task, world.task.id).google_task_id == "task-external-1"
        observation = db.scalar(select(ProviderOutcomeObservation))
        assert observation.outcome == "APPLIED" and observation.external_ref == "task-external-1"


def test_timeout_after_gmail_effect_becomes_unknown_then_lookup_not_resend(world):
    queued = queue_confirmed_action(
        world.db, action_kind="gmail.message.send", target_id=world.draft.id, actor=world.user,
    )
    fake = FakeGoogle("gmail.message.send", fail_after=True)
    runtime = build_product_runtime(sessions=sessions(world), service_factory=lambda *args: fake)
    payload = {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1}
    first = runtime.execute_job(payload, owner(world, queued["job_id"], "worker-a"))
    second = runtime.execute_job(payload, owner(world, queued["job_id"], "worker-b", 2))
    assert first["outcome"] == "UNKNOWN" and second["outcome"] == "APPLIED"
    assert fake.effects == 1
    with sessions(world)() as db:
        assert db.get(ResponseDraft, world.draft.id).sent_external_id == "gmail-external-1"


def test_unknown_reconciliation_is_itself_a_content_free_durable_job(world):
    queued = queue_confirmed_action(
        world.db, action_kind="gmail.message.send", target_id=world.draft.id, actor=world.user,
    )
    fake = FakeGoogle("gmail.message.send", fail_after=True)
    runtime = build_product_runtime(sessions=sessions(world), service_factory=lambda *args: fake)
    payload = {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1}
    assert runtime.execute_job(payload, owner(world, queued["job_id"], "worker-a"))["outcome"] == "UNKNOWN"

    reconciliation = queue_reconciliation(
        world.db, action_id=queued["action_id"], revision=1, actor=world.user,
    )
    job = world.db.get(BackgroundJob, reconciliation["job_id"])
    assert job.kind == RECONCILE_KIND and job.payload == payload
    assert "recipient@example.test" not in repr(job.payload)
    assert "Synthetic body marker" not in repr(job.payload)


def test_reconciliation_worker_looks_up_without_resending(world, monkeypatch):
    queued = queue_confirmed_action(
        world.db, action_kind="gmail.message.send", target_id=world.draft.id, actor=world.user,
    )
    fake = FakeGoogle("gmail.message.send", fail_after=True)
    runtime = build_product_runtime(sessions=sessions(world), service_factory=lambda *args: fake)
    payload = {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1}
    assert runtime.execute_job(payload, owner(world, queued["job_id"], "worker-a"))["outcome"] == "UNKNOWN"
    reconciliation = queue_reconciliation(
        world.db, action_id=queued["action_id"], revision=1, actor=world.user,
    )
    reconcile_owner = owner(world, reconciliation["job_id"], "worker-reconcile")
    monkeypatch.setattr("app.provider_actions.product.build_product_runtime", lambda: runtime)

    with execution_owner(
        reconcile_owner[0], reconcile_owner[1],
        attempt=reconcile_owner[2], locked_at=reconcile_owner[3],
    ):
        result = run_product_reconcile_job(payload)

    assert result["outcome"] == "APPLIED"
    assert fake.effects == 1


def test_authority_and_payload_are_rechecked_immediately_before_effect(world):
    queued = queue_confirmed_action(
        world.db, action_kind="google.tasks.upsert", target_id=world.task.id, actor=world.user,
    )
    membership = world.db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == world.project.id, ProjectMember.user_id == world.user.id,
    ))
    membership.role = "editor"; world.db.commit()
    fake = FakeGoogle("google.tasks.upsert")
    runtime = build_product_runtime(sessions=sessions(world), service_factory=lambda *args: fake)
    with pytest.raises(ProviderActionError, match="authority_stale"):
        runtime.execute_job(
            {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1},
            owner(world, queued["job_id"], "worker-a"),
        )
    assert fake.effects == 0


def test_payload_change_after_confirmation_is_not_applied(world):
    queued = queue_confirmed_action(
        world.db, action_kind="google.tasks.upsert", target_id=world.task.id, actor=world.user,
    )
    world.task.title = "Changed after confirmation"
    world.task.record_version = int(world.task.record_version or 1) + 1
    world.db.commit()
    fake = FakeGoogle("google.tasks.upsert")
    runtime = build_product_runtime(sessions=sessions(world), service_factory=lambda *args: fake)
    with pytest.raises(ProviderActionError, match="authority_stale"):
        runtime.execute_job(
            {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1},
            owner(world, queued["job_id"], "worker-a"),
        )
    assert fake.effects == 0


def test_worker_dispatches_calendar_once_with_deterministic_external_id(world):
    queued = queue_confirmed_action(
        world.db, action_kind="google.calendar.upsert", target_id=world.task.id, actor=world.user,
    )
    fake = FakeGoogle("google.calendar.upsert")
    runtime = build_product_runtime(sessions=sessions(world), service_factory=lambda *args: fake)
    payload = {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1}
    first = runtime.execute_job(payload, owner(world, queued["job_id"], "worker-a"))
    repeated = runtime.execute_job(payload, owner(world, queued["job_id"], "worker-b", 2))
    assert first["outcome"] == repeated["outcome"] == "APPLIED"
    assert fake.effects == 1
    with sessions(world)() as db:
        task = db.get(Task, world.task.id)
        observation = db.scalar(select(ProviderOutcomeObservation).where(
            ProviderOutcomeObservation.action_id == queued["action_id"],
        ))
        assert task.google_calendar_event_id == observation.external_ref


def test_editing_published_task_requires_new_human_confirmation(world, monkeypatch):
    monkeypatch.setattr("app.api.tasks.require_project_role", lambda *args: "manager")
    world.task.external_action_status = "executed"; world.db.commit()
    before = int(world.task.record_version or 1)
    result = update_task(
        world.task.id, TaskUpdate(status="in_progress"), db=world.db, user=world.user,
    )
    assert result["status"] == "in_progress"
    assert world.task.external_action_status == "proposed"
    assert world.task.record_version == before + 1
    assert world.db.scalar(select(BackgroundJob)) is None


def test_replayed_confirmation_reuses_same_action_job(world):
    first = queue_confirmed_action(
        world.db, action_kind="google.tasks.upsert", target_id=world.task.id, actor=world.user,
    )
    second = queue_confirmed_action(
        world.db, action_kind="google.tasks.upsert", target_id=world.task.id, actor=world.user,
    )
    assert second == {**first, "already_queued": True}
    assert len(list(world.db.scalars(select(ProviderAction)))) == 1
    assert len(list(world.db.scalars(select(BackgroundJob)))) == 1


def test_replay_repairs_outbox_job_binding_after_enqueue_checkpoint(world):
    first = queue_confirmed_action(
        world.db, action_kind="google.tasks.upsert", target_id=world.task.id, actor=world.user,
    )
    outbox = world.db.get(ProviderDispatchOutbox, (first["action_id"], first["revision"]))
    outbox.job_id = None
    world.db.commit()

    repeated = queue_confirmed_action(
        world.db, action_kind="google.tasks.upsert", target_id=world.task.id, actor=world.user,
    )

    world.db.expire_all()
    repaired = world.db.get(ProviderDispatchOutbox, (first["action_id"], first["revision"]))
    assert repeated == {**first, "already_queued": True}
    assert repaired.job_id == first["job_id"]
    assert len(list(world.db.scalars(select(ProviderAction)))) == 1
    assert len(list(world.db.scalars(select(BackgroundJob)))) == 1
