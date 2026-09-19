from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.api.gmail import send_gmail
from app.api.tasks import ExternalActionApproval, TaskUpdate, approve_external, update_task
from app.jobs.queue import execution_owner
from app.mailbox_identity.service import MailboxIdentityService
from app.models.audit_log import AuditLog
from app.models.google_token import GoogleOAuthToken
from app.models.job import BackgroundJob
from app.models.mailbox_identity import MailboxCredentialGeneration
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.telegram_chat import TelegramChatLink
from app.models.v54_provider_action import (
    ProviderAction, ProviderActionApproval, ProviderDispatchOutbox,
    ProviderOutcomeObservation,
)
from app.models.v54_pilot import ConnectionIdentity
from app.provider_actions.contracts import ProviderActionError, ProviderPreconditionFailed
from app.provider_actions.product import (
    GoogleWorkspaceProviderAdapter, RECONCILE_KIND, build_product_runtime, queue_confirmed_action, queue_reconciliation,
    resolve_reconciled_absence, run_product_reconcile_job, task_effect_states,
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
    token = GoogleOAuthToken(project_id=project.id, access_token=None, refresh_token=None,
                             scopes=SCOPES)
    db.add(token); db.flush()
    identity, mail, _generation = MailboxIdentityService().bind_verified_google_subject(
        db, organization_id=org.id, google_token_id=token.id,
        subject="synthetic-provider-subject",
    )
    draft = ResponseDraft(
        project_id=project.id, reviewer_user_id=user.id, subject="Synthetic subject",
        body="Synthetic body marker", recipient_to="recipient@example.test", status="approved",
        revision=1, approved_revision=1, approved_by_user_id=user.id,
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
    return SimpleNamespace(db=db, user=user, org=org, project=project, draft=draft, task=task,
                           token=token, identity=identity, mail=mail)


def rotate_oauth(world, *, subject=None, times=1):
    """Model one callback transaction per verified reconnect, including its identity binding."""
    for _ in range(times):
        world.token.credential_generation += 1
        MailboxIdentityService().bind_verified_google_subject(
            world.db, organization_id=world.org.id, google_token_id=world.token.id,
            subject=subject or world.identity.account_key,
        )
        world.db.commit()


class Request:
    def __init__(self, result=None, error=None):
        self.result, self.error = result or {}, error

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class FakeNotFound(Exception):
    resp = SimpleNamespace(status=404)


class FakeGoogle:
    def __init__(self, kind: str, *, fail_after=False, crash_after=False):
        self.kind = kind
        self.fail_after = fail_after
        self.crash_after = crash_after
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
        return Request(item, SystemExit("synthetic process termination") if self.crash_after else None)

    def patch(self, **kwargs):
        self.effects += 1
        external_id = kwargs.get("task") or kwargs.get("eventId")
        return Request({"id": external_id, **kwargs["body"]})

    def get(self, **kwargs):
        if "userId" in kwargs:
            return Request(next(item for item in self.sent if item["id"] == kwargs["id"]))
        if "eventId" in kwargs:
            item = self.events_by_id.get(kwargs["eventId"])
            return Request(item, None if item is not None else FakeNotFound())
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
    assert result["status"] == "queued" and world.draft.status == "queued"
    assert job.kind == PRODUCT_KIND
    assert set(job.payload) == {"organization_id", "action_id", "revision"}
    assert action.payload_hash == approval.payload_hash and action.envelope_hash == approval.envelope_hash
    assert action.synthetic_only is False and action.mode == "CONFIRM"
    assert "recipient@example.test" not in serialized and "Synthetic body marker" not in serialized


def test_both_public_gmail_routes_share_one_internal_command(world):
    from app.api.mail import MailDraftSend, send_mail_draft
    legacy = send_gmail(world.draft.id, db=world.db, user=world.user)
    modern = send_mail_draft(
        world.draft.id, MailDraftSend(revision=1, idempotency_key="route-shared-command-1"),
        world.db, world.user,
    )
    assert legacy["job_id"] == modern["job_id"]
    assert legacy["action_id"] == modern["action_id"]
    assert modern["already_queued"] is True
    assert len(list(world.db.scalars(select(ProviderAction)))) == 1
    assert len(list(world.db.scalars(select(BackgroundJob)))) == 1


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


def test_approved_task_and_calendar_dispatch_after_queue_status_update(world, monkeypatch):
    monkeypatch.setattr("app.api.tasks.require_project_role", lambda *args: "manager")
    approved = approve_external(
        world.task.id, ExternalActionApproval(publish_task=True, publish_calendar=True),
        db=world.db, user=world.user,
    )
    services = {kind: FakeGoogle(kind) for kind in ("google.tasks.upsert", "google.calendar.upsert")}
    runtime = build_product_runtime(
        sessions=sessions(world), service_factory=lambda kind, *_args: services[kind],
    )

    for action in approved["actions"]:
        job = world.db.get(BackgroundJob, action["job_id"])
        outcome = runtime.execute_job(job.payload, owner(world, job.id, f"worker-{job.id}"))
        assert outcome["outcome"] == "APPLIED"

    assert services["google.tasks.upsert"].effects == 1
    assert services["google.calendar.upsert"].effects == 1


def test_dead_letter_before_provider_attempt_is_visible_as_error(world):
    queued = queue_confirmed_action(
        world.db, action_kind="google.tasks.upsert", target_id=world.task.id, actor=world.user,
    )
    job = world.db.get(BackgroundJob, queued["job_id"])
    job.status = "dead_letter"
    world.db.commit()

    state = task_effect_states(world.db, world.task.id)["task"]
    assert state["status"] == "failed"
    assert state["external_id"] is None


def test_dead_letter_after_provider_attempt_is_visible_as_unknown(world):
    queued = queue_confirmed_action(
        world.db, action_kind="google.calendar.upsert", target_id=world.task.id, actor=world.user,
    )
    fake = FakeGoogle("google.calendar.upsert", crash_after=True)
    runtime = build_product_runtime(sessions=sessions(world), service_factory=lambda *_args: fake)
    with pytest.raises(SystemExit):
        runtime.execute_job(
            {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1},
            owner(world, queued["job_id"], "worker-crashed"),
        )
    job = world.db.get(BackgroundJob, queued["job_id"])
    job.status = "dead_letter"
    world.db.commit()

    state = task_effect_states(world.db, world.task.id)["calendar"]
    assert state["status"] == "unknown"
    assert state["external_id"] is None


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


def test_approval_revocation_blocks_provider_effect(world):
    queued = queue_confirmed_action(
        world.db, action_kind="google.tasks.upsert", target_id=world.task.id, actor=world.user,
    )
    approval = world.db.scalar(select(ProviderActionApproval).where(
        ProviderActionApproval.action_id == queued["action_id"],
    ))
    approval.state = "REVOKED"
    world.db.commit()
    fake = FakeGoogle("google.tasks.upsert")
    runtime = build_product_runtime(sessions=sessions(world), service_factory=lambda *args: fake)
    with pytest.raises(ProviderActionError, match="dispatch_binding_mismatch"):
        runtime.execute_job(
            {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1},
            owner(world, queued["job_id"], "worker-revoked"),
        )
    assert fake.effects == 0


def test_oauth_reconnect_revokes_queued_project_effect(world):
    queued = queue_confirmed_action(
        world.db, action_kind="google.calendar.upsert", target_id=world.task.id, actor=world.user,
    )
    rotate_oauth(world)
    fake = FakeGoogle("google.calendar.upsert")
    runtime = build_product_runtime(sessions=sessions(world), service_factory=lambda *args: fake)
    with pytest.raises(ProviderActionError, match="authority_stale"):
        runtime.execute_job(
            {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1},
            owner(world, queued["job_id"], "worker-reconnected"),
        )
    assert fake.effects == 0


@pytest.mark.parametrize("kind", ["google.tasks.upsert", "google.calendar.upsert"])
def test_rotated_oauth_reconciliation_is_lookup_only_and_requires_human_absence_resolution(
    world, monkeypatch, kind,
):
    queued = queue_confirmed_action(
        world.db, action_kind=kind, target_id=world.task.id, actor=world.user,
    )
    payload = {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1}

    def fail_before_provider(*_args):
        raise RuntimeError("synthetic adapter outage")

    initial_runtime = build_product_runtime(
        sessions=sessions(world), service_factory=fail_before_provider,
    )
    assert initial_runtime.execute_job(
        payload, owner(world, queued["job_id"], "worker-initial"),
    )["outcome"] == "UNKNOWN"

    first_check = queue_reconciliation(
        world.db, action_id=queued["action_id"], revision=1, actor=world.user,
    )
    first_job = world.db.get(BackgroundJob, first_check["job_id"])
    first_job.status = "dead_letter"; first_job.attempts = 3
    world.db.commit()
    failed_state = task_effect_states(world.db, world.task.id)[
        "task" if kind == "google.tasks.upsert" else "calendar"
    ]
    assert failed_state["reconciliation_status"] == "failed"
    assert failed_state["reconciliation_job_id"] == first_job.id
    rotate_oauth(world, times=2)

    second_check = queue_reconciliation(
        world.db, action_id=queued["action_id"], revision=1, actor=world.user,
    )
    assert second_check["job_id"] != first_check["job_id"]
    assert "credential-3" in world.db.get(BackgroundJob, second_check["job_id"]).idempotency_key

    fake = FakeGoogle(kind)
    rotated_runtime = build_product_runtime(
        sessions=sessions(world), service_factory=lambda *_args: fake,
    )
    reconcile_owner = owner(world, second_check["job_id"], "worker-rotated")
    monkeypatch.setattr("app.provider_actions.product.build_product_runtime", lambda: rotated_runtime)
    with execution_owner(
        reconcile_owner[0], reconcile_owner[1],
        attempt=reconcile_owner[2], locked_at=reconcile_owner[3],
    ):
        result = run_product_reconcile_job(payload)

    assert result["outcome"] == "UNKNOWN"
    assert fake.effects == 0
    state = task_effect_states(world.db, world.task.id)[
        "task" if kind == "google.tasks.upsert" else "calendar"
    ]
    assert state["safe_code"] == "receipt_not_found"
    assert state["reconciliation_status"] == "running"
    assert state["can_confirm_absence"] is True

    with pytest.raises(ProviderActionError, match="outcome_not_reconcilable"):
        resolve_reconciled_absence(
            world.db, action_id=queued["action_id"], revision=1,
            expected_observation_sequence=state["observation_sequence"],
            confirmed_absent=False, actor=world.user,
        )
    membership = world.db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == world.project.id,
        ProjectMember.user_id == world.user.id,
    ))
    membership.role = "editor"; world.db.commit()
    with pytest.raises(ProviderActionError, match="authority_stale"):
        resolve_reconciled_absence(
            world.db, action_id=queued["action_id"], revision=1,
            expected_observation_sequence=state["observation_sequence"],
            confirmed_absent=True, actor=world.user,
        )
    membership.role = "manager"; world.db.commit()

    resolved = resolve_reconciled_absence(
        world.db, action_id=queued["action_id"], revision=1,
        expected_observation_sequence=state["observation_sequence"],
        confirmed_absent=True, actor=world.user,
    )
    assert resolved["status"] == "failed"
    assert resolved["safe_code"] == "human_confirmed_absence_after_rotation"
    assert world.db.get(ProviderAction, (queued["action_id"], 1)).state == "NOT_APPLIED"
    assert world.db.scalar(select(AuditLog).where(
        AuditLog.action == "provider_absence_confirmed",
    )) is not None

    retry = queue_confirmed_action(
        world.db, action_kind=kind, target_id=world.task.id, actor=world.user,
    )
    assert retry["revision"] == 2
    assert fake.effects == 0


def test_rotated_oauth_absence_resolution_rejects_stale_observation_sequence(world):
    queued = queue_confirmed_action(
        world.db, action_kind="google.tasks.upsert", target_id=world.task.id, actor=world.user,
    )
    fake = FakeGoogle("google.tasks.upsert")
    runtime = build_product_runtime(sessions=sessions(world), service_factory=lambda *_args: fake)
    payload = {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1}
    # An adapter failure after dispatch entry produces the required UNKNOWN but
    # no rotated read-only receipt_not_found observation yet.
    runtime.adapter.service_factory = lambda *_args: (_ for _ in ()).throw(RuntimeError("offline"))
    assert runtime.execute_job(payload, owner(world, queued["job_id"], "worker-a"))["outcome"] == "UNKNOWN"
    rotate_oauth(world)

    with pytest.raises(ProviderActionError, match="outcome_not_reconcilable"):
        resolve_reconciled_absence(
            world.db, action_id=queued["action_id"], revision=1,
            expected_observation_sequence=999, confirmed_absent=True, actor=world.user,
        )


def _unknown_task_action(world, kind):
    queued = queue_confirmed_action(
        world.db, action_kind=kind, target_id=world.task.id, actor=world.user,
    )

    def unavailable(*_args):
        raise RuntimeError("synthetic outage before provider response")

    runtime = build_product_runtime(sessions=sessions(world), service_factory=unavailable)
    payload = {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1}
    assert runtime.execute_job(payload, owner(world, queued["job_id"], "worker-unknown"))["outcome"] == "UNKNOWN"
    return queued


def _lookup(world, queued, fake):
    runtime = build_product_runtime(sessions=sessions(world), service_factory=lambda *_args: fake)
    return runtime.reconcile(
        queued["action_id"], queued["revision"],
        actor_id=str(world.user.id), correlation_id="synthetic-recovery-check",
    )


@pytest.mark.parametrize("kind", ["google.tasks.upsert", "google.calendar.upsert"])
@pytest.mark.parametrize("broken_binding", [
    "no_history", "different_subject", "revoked", "changed_epoch", "missing_rotation_history",
])
def test_rotated_lookup_requires_continuous_original_verified_account(world, kind, broken_binding):
    if broken_binding == "no_history":
        # A legacy action must not inherit an identity first verified after it was created.
        world.db.execute(delete(MailboxCredentialGeneration).where(
            MailboxCredentialGeneration.google_token_id == world.token.id,
        ))
        world.db.commit()
    queued = _unknown_task_action(world, kind)
    if broken_binding == "missing_rotation_history":
        world.token.credential_generation += 1
        world.db.commit()
    elif broken_binding == "different_subject":
        world.identity.state = "revoked"
        world.db.commit()
        rotate_oauth(world, subject="different-synthetic-provider-subject")
    else:
        if broken_binding == "changed_epoch":
            world.identity.binding_epoch += 1
            world.db.commit()
        rotate_oauth(world)
        if broken_binding == "revoked":
            world.identity.state = "revoked"
            world.db.commit()

    provider_calls = []

    def forbidden_provider(*_args):
        provider_calls.append(True)
        raise AssertionError("account continuity must be checked before Google I/O")

    runtime = build_product_runtime(sessions=sessions(world), service_factory=forbidden_provider)
    with pytest.raises(ProviderActionError, match="authority_stale"):
        runtime.reconcile(queued["action_id"], 1, actor_id=str(world.user.id), correlation_id="blocked-account")
    assert provider_calls == []
    state = task_effect_states(world.db, world.task.id)["task" if kind == "google.tasks.upsert" else "calendar"]
    assert state["status"] == "unknown" and state["can_confirm_absence"] is False
    assert state["safe_code"] != "receipt_not_found"


@pytest.mark.parametrize("kind", ["google.tasks.upsert", "google.calendar.upsert"])
def test_same_subject_rotation_uses_identity_history_not_equal_generation_numbers(world, kind):
    queued = _unknown_task_action(world, kind)
    other_project = Project(name="Other synthetic credential owner", organization_id=world.org.id)
    world.db.add(other_project); world.db.flush()
    other_token = GoogleOAuthToken(project_id=other_project.id, scopes=SCOPES)
    world.db.add(other_token); world.db.flush()
    MailboxIdentityService().bind_verified_google_subject(
        world.db, organization_id=world.org.id, google_token_id=other_token.id,
        subject=world.identity.account_key,
    )
    world.db.commit()
    rotate_oauth(world)
    world.db.refresh(world.identity)
    assert world.token.credential_generation == 2
    assert world.identity.credential_generation == 3

    fake = FakeGoogle(kind)
    assert _lookup(world, queued, fake)["outcome"] == "UNKNOWN"
    state = task_effect_states(world.db, world.task.id)["task" if kind == "google.tasks.upsert" else "calendar"]
    assert state["safe_code"] == "receipt_not_found"
    assert state["can_confirm_absence"] is True
    assert fake.effects == 0


class PagedLookupGoogle(FakeGoogle):
    def __init__(self, response):
        super().__init__("google.tasks.upsert")
        self.response = response
        self.page_tokens = []

    def list(self, **kwargs):
        self.page_tokens.append(kwargs.get("pageToken"))
        # Make a missing production bound fail promptly rather than hanging the suite.
        if len(self.page_tokens) > 101:
            raise AssertionError("lookup exceeded the synthetic pagination safety bound")
        return Request(self.response(len(self.page_tokens), kwargs.get("pageToken")))


@pytest.mark.parametrize("case, expected_code", [
    ("ambiguous", "provider_lookup_ambiguous"),
    ("ambiguous_later_page", "provider_lookup_ambiguous"),
    ("repeated_page", "provider_lookup_incomplete"),
    ("malformed_items", "provider_lookup_incomplete"),
    ("malformed_page_token", "provider_lookup_incomplete"),
    ("page_limit", "provider_lookup_incomplete"),
])
def test_uncertain_tasks_search_never_authorizes_absence(world, case, expected_code):
    queued = _unknown_task_action(world, "google.tasks.upsert")
    action = world.db.get(ProviderAction, (queued["action_id"], 1))
    marker = "PU-Command: " + sha256(action.idempotency_key.encode("utf-8")).hexdigest()
    rotate_oauth(world)

    def response(index, _token):
        if case == "ambiguous":
            return {"items": [{"id": "first-match", "notes": marker}, {"id": "second-match", "notes": marker}]}
        if case == "ambiguous_later_page":
            if index == 1:
                return {"items": [{"id": "first-match", "notes": marker}], "nextPageToken": "last-page"}
            return {"items": [{"id": "second-match", "notes": marker}]}
        if case == "repeated_page":
            return {"items": [], "nextPageToken": "same-page"}
        if case == "malformed_items":
            return {"items": "not-an-item-list"}
        if case == "malformed_page_token":
            return {"items": [], "nextPageToken": 17}
        return {"items": [], "nextPageToken": f"page-{index}"}

    fake = PagedLookupGoogle(response)
    result = _lookup(world, queued, fake)
    assert result["outcome"] == "UNKNOWN" and result["retry_safe"] is False
    state = task_effect_states(world.db, world.task.id)["task"]
    assert state["safe_code"] == expected_code
    assert state["can_confirm_absence"] is False
    assert fake.effects == 0 and len(fake.page_tokens) <= 100
    with pytest.raises(ProviderActionError, match="outcome_not_reconcilable"):
        resolve_reconciled_absence(
            world.db, action_id=queued["action_id"], revision=1,
            expected_observation_sequence=state["observation_sequence"],
            confirmed_absent=True, actor=world.user,
        )


@pytest.mark.parametrize("repeat_same_id", [False, True])
def test_paginated_tasks_lookup_accepts_one_unique_match_only_after_full_search(world, repeat_same_id):
    queued = _unknown_task_action(world, "google.tasks.upsert")
    action = world.db.get(ProviderAction, (queued["action_id"], 1))
    marker = "PU-Command: " + sha256(action.idempotency_key.encode("utf-8")).hexdigest()
    rotate_oauth(world)
    item = {"id": "verified-task-on-later-page", "notes": marker}

    def response(index, _token):
        if index == 1:
            return {"items": [item] if repeat_same_id else [], "nextPageToken": "last-page"}
        return {"items": [item]}

    fake = PagedLookupGoogle(response)
    assert _lookup(world, queued, fake)["outcome"] == "APPLIED"
    state = task_effect_states(world.db, world.task.id)["task"]
    assert state["external_id"] == item["id"] and state["can_confirm_absence"] is False
    assert fake.page_tokens == [None, "last-page"] and fake.effects == 0


@pytest.mark.parametrize("kind", ["google.tasks.upsert", "google.calendar.upsert"])
@pytest.mark.parametrize("http_status", [401, 403, 429, 500, None])
def test_provider_lookup_errors_are_not_absence_evidence(world, kind, http_status):
    queued = _unknown_task_action(world, kind)
    rotate_oauth(world)
    failure = RuntimeError("synthetic secret-bearing provider detail")
    if http_status is not None:
        failure.resp = SimpleNamespace(status=http_status)

    class FailingLookup(FakeGoogle):
        def list(self, **_kwargs):
            return Request(error=failure)

        def get(self, **_kwargs):
            return Request(error=failure)

    fake = FailingLookup(kind)
    assert _lookup(world, queued, fake)["outcome"] == "UNKNOWN"
    state = task_effect_states(world.db, world.task.id)["task" if kind == "google.tasks.upsert" else "calendar"]
    assert state["safe_code"] == "provider_lookup_failed"
    assert state["can_confirm_absence"] is False and fake.effects == 0
    with pytest.raises(ProviderActionError, match="outcome_not_reconcilable"):
        resolve_reconciled_absence(
            world.db, action_id=queued["action_id"], revision=1,
            expected_observation_sequence=state["observation_sequence"],
            confirmed_absent=True, actor=world.user,
        )


@pytest.mark.parametrize("kind", ["google.tasks.upsert", "google.calendar.upsert"])
def test_account_revoked_during_lookup_cannot_create_absence_evidence(world, kind):
    queued = _unknown_task_action(world, kind)
    rotate_oauth(world)

    class RevokeDuringRead:
        def execute(self):
            with sessions(world).begin() as db:
                db.get(ConnectionIdentity, world.identity.id).state = "revoked"
            if kind == "google.calendar.upsert":
                raise FakeNotFound()
            return {"items": []}

    class RevokingLookup(FakeGoogle):
        def list(self, **_kwargs):
            return RevokeDuringRead()

        def get(self, **_kwargs):
            return RevokeDuringRead()

    fake = RevokingLookup(kind)
    with pytest.raises(ProviderActionError, match="authority_stale"):
        _lookup(world, queued, fake)
    state = task_effect_states(world.db, world.task.id)["task" if kind == "google.tasks.upsert" else "calendar"]
    assert state["status"] == "unknown" and state["can_confirm_absence"] is False
    assert state["safe_code"] != "receipt_not_found" and fake.effects == 0


@pytest.mark.parametrize("kind, api_name, api_version", [
    ("google.tasks.upsert", "tasks", "v1"),
    ("google.calendar.upsert", "calendar", "v3"),
])
def test_default_lookup_refreshes_credentials_only_in_memory(world, monkeypatch, kind, api_name, api_version):
    queued = _unknown_task_action(world, kind)
    world.token.access_token = "synthetic-encrypted-access"
    world.token.refresh_token = "synthetic-encrypted-refresh"
    world.db.commit()
    rotate_oauth(world)
    token_snapshot = (world.token.access_token, world.token.refresh_token, world.token.credential_generation)
    inputs, built = [], []
    fake = FakeGoogle(kind)

    class MemoryCredentials:
        def __init__(self, **kwargs):
            inputs.append(kwargs)
            self.token = kwargs["token"]
            self.refresh_token = kwargs["refresh_token"]

        def refresh(self, _request):
            self.token = "synthetic-refreshed-memory-access"
            self.refresh_token = "synthetic-refreshed-memory-refresh"

    def build(name, version, *, credentials, cache_discovery):
        credentials.refresh(object())
        built.append((name, version, cache_discovery, credentials.token, credentials.refresh_token))
        return fake

    def forbid_legacy_workspace(*_args):
        pytest.fail("lookup must not use the workspace credential writer")

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "synthetic-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "synthetic-client-secret")
    monkeypatch.setattr("app.core.token_crypto.decrypt_token", lambda value: f"decrypted:{value}")
    monkeypatch.setattr("google.oauth2.credentials.Credentials", MemoryCredentials)
    monkeypatch.setattr("googleapiclient.discovery.build", build)
    monkeypatch.setattr("app.provider_actions.product.google_workspace_for_project", forbid_legacy_workspace)
    monkeypatch.setattr("app.provider_actions.product.google_workspace_for_mailbox", forbid_legacy_workspace)
    runtime = build_product_runtime(sessions=sessions(world))
    read_only_service = runtime.adapter._read_only_service

    def without_explicit_commit(action_kind, material, db):
        monkeypatch.setattr(db, "commit", lambda: pytest.fail("credential construction must not commit"))
        return read_only_service(action_kind, material, db)

    monkeypatch.setattr(runtime.adapter, "_read_only_service", without_explicit_commit)
    assert runtime.reconcile(
        queued["action_id"], 1, actor_id=str(world.user.id), correlation_id="memory-only-credentials",
    )["outcome"] == "UNKNOWN"
    assert inputs == [{
        "token": "decrypted:synthetic-encrypted-access",
        "refresh_token": "decrypted:synthetic-encrypted-refresh",
        "token_uri": world.token.token_uri,
        "client_id": "synthetic-client-id", "client_secret": "synthetic-client-secret",
        "scopes": SCOPES.split(),
    }]
    assert built == [(api_name, api_version, False,
                      "synthetic-refreshed-memory-access", "synthetic-refreshed-memory-refresh")]
    with sessions(world)() as db:
        persisted = db.get(GoogleOAuthToken, world.token.id)
        assert (persisted.access_token, persisted.refresh_token, persisted.credential_generation) == token_snapshot
        state = task_effect_states(db, world.task.id)["task" if kind == "google.tasks.upsert" else "calendar"]
        assert state["safe_code"] == "receipt_not_found" and state["can_confirm_absence"] is True
    assert fake.effects == 0


@pytest.mark.parametrize("kind", ["google.tasks.upsert", "google.calendar.upsert"])
def test_read_only_service_rechecks_generation_after_context_capture(world, monkeypatch, kind):
    queued = _unknown_task_action(world, kind)
    world.token.access_token = "synthetic-encrypted-access"
    world.db.commit()
    rotate_oauth(world)
    runtime = build_product_runtime(sessions=sessions(world))
    original_context = runtime.adapter._context
    touched = []

    def forbidden(name):
        def call(*_args, **_kwargs):
            touched.append(name)
            pytest.fail("stale credential snapshot must be rejected before decrypt or build")
        return call

    monkeypatch.setattr("app.core.token_crypto.decrypt_token", forbidden("decrypt"))
    monkeypatch.setattr("google.oauth2.credentials.Credentials", forbidden("credentials"))
    monkeypatch.setattr("googleapiclient.discovery.build", forbidden("build"))

    def context_then_rotate(db, request, *, operation):
        target, material = original_context(db, request, operation=operation)
        # Keep the old row in this identity map while another session advances it.
        cached = db.get(GoogleOAuthToken, material.google_token_id)
        assert cached.credential_generation == material.credential_generation
        with sessions(world).begin() as other_db:
            other_db.get(GoogleOAuthToken, material.google_token_id).credential_generation += 1
        assert cached.credential_generation == material.credential_generation
        return target, material

    monkeypatch.setattr(runtime.adapter, "_context", context_then_rotate)
    request = runtime._request(world.db.get(ProviderAction, (queued["action_id"], 1)))
    with pytest.raises(ProviderActionError, match="credential_stale"):
        runtime.adapter.lookup(request)
    assert touched == []


@pytest.mark.parametrize("kind", ["google.tasks.upsert", "google.calendar.upsert"])
@pytest.mark.parametrize("mismatch", ["token_id", "project_id"])
def test_read_only_service_requires_exact_token_and_project(world, monkeypatch, kind, mismatch):
    world.token.access_token = "synthetic-encrypted-access"
    world.db.commit()
    material = SimpleNamespace(
        google_token_id=world.token.id, project_id=world.project.id,
        credential_generation=world.token.credential_generation,
    )
    if mismatch == "token_id":
        material.google_token_id += 1000
    else:
        material.project_id += 1000
    touched = []

    def forbidden(*_args, **_kwargs):
        touched.append(True)
        pytest.fail("mismatched credential scope must not reach Google client construction")

    monkeypatch.setattr("app.core.token_crypto.decrypt_token", forbidden)
    monkeypatch.setattr("google.oauth2.credentials.Credentials", forbidden)
    monkeypatch.setattr("googleapiclient.discovery.build", forbidden)
    with pytest.raises(ProviderActionError, match="credential_stale"):
        GoogleWorkspaceProviderAdapter._read_only_service(kind, material, world.db)
    assert touched == []


@pytest.mark.parametrize("kind", ["google.tasks.upsert", "google.calendar.upsert"])
def test_empty_lookup_evidence_expires_on_another_reconnect_until_fresh_lookup(world, kind):
    queued = _unknown_task_action(world, kind)
    rotate_oauth(world)
    fake = FakeGoogle(kind)
    assert _lookup(world, queued, fake)["outcome"] == "UNKNOWN"
    effect_name = "task" if kind == "google.tasks.upsert" else "calendar"
    original = task_effect_states(world.db, world.task.id)[effect_name]
    assert original["safe_code"] == "receipt_not_found" and original["can_confirm_absence"] is True

    rotate_oauth(world)
    stale = task_effect_states(world.db, world.task.id)[effect_name]
    assert stale["observation_sequence"] == original["observation_sequence"]
    assert stale["safe_code"] == "receipt_not_found" and stale["can_confirm_absence"] is False
    with pytest.raises(ProviderActionError, match="outcome_not_reconcilable"):
        resolve_reconciled_absence(
            world.db, action_id=queued["action_id"], revision=1,
            expected_observation_sequence=original["observation_sequence"],
            confirmed_absent=True, actor=world.user,
        )
    world.db.rollback()

    assert _lookup(world, queued, fake)["outcome"] == "UNKNOWN"
    current = task_effect_states(world.db, world.task.id)[effect_name]
    assert current["observation_sequence"] > original["observation_sequence"]
    assert current["can_confirm_absence"] is True
    resolved = resolve_reconciled_absence(
        world.db, action_id=queued["action_id"], revision=1,
        expected_observation_sequence=current["observation_sequence"],
        confirmed_absent=True, actor=world.user,
    )
    assert resolved["status"] == "failed"
    assert resolved["safe_code"] == "human_confirmed_absence_after_rotation"
    assert world.db.get(ProviderAction, (queued["action_id"], 2)) is None and fake.effects == 0


def test_process_crash_after_effect_recovers_by_lookup_without_duplicate(world):
    queued = queue_confirmed_action(
        world.db, action_kind="google.calendar.upsert", target_id=world.task.id, actor=world.user,
    )
    fake = FakeGoogle("google.calendar.upsert", crash_after=True)
    runtime = build_product_runtime(sessions=sessions(world), service_factory=lambda *args: fake)
    payload = {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1}
    with pytest.raises(SystemExit):
        runtime.execute_job(payload, owner(world, queued["job_id"], "worker-crashed"))
    assert fake.effects == 1
    assert world.db.get(Task, world.task.id).google_calendar_event_id is None
    fake.crash_after = False
    recovered = runtime.execute_job(payload, owner(world, queued["job_id"], "worker-recovered", 2))
    assert recovered["outcome"] == "APPLIED" and fake.effects == 1


def test_external_id_alone_never_becomes_applied_checkmark(world):
    from app.provider_actions.product import task_effect_states
    world.task.google_task_id = "legacy-id-without-confirmation"
    world.db.commit()
    assert task_effect_states(world.db, world.task.id)["task"]["status"] == "not_requested"


def test_task_and_calendar_keep_independent_receipts_after_partial_failure(world):
    from app.provider_actions.product import task_effect_states, task_overall_state
    queued_task = queue_confirmed_action(
        world.db, action_kind="google.tasks.upsert", target_id=world.task.id, actor=world.user,
    )
    queued_calendar = queue_confirmed_action(
        world.db, action_kind="google.calendar.upsert", target_id=world.task.id, actor=world.user,
    )
    task_runtime = build_product_runtime(
        sessions=sessions(world), service_factory=lambda *_: FakeGoogle("google.tasks.upsert"),
    )
    task_runtime.execute_job(
        {"organization_id": world.org.id, "action_id": queued_task["action_id"], "revision": 1},
        owner(world, queued_task["job_id"], "worker-task"),
    )
    interim = task_effect_states(world.db, world.task.id)
    assert interim["task"]["status"] == "applied"
    assert interim["calendar"]["status"] == "pending"

    def deny_before_effect(*_args):
        raise ProviderPreconditionFailed()

    calendar_runtime = build_product_runtime(sessions=sessions(world), service_factory=deny_before_effect)
    calendar_runtime.execute_job(
        {"organization_id": world.org.id, "action_id": queued_calendar["action_id"], "revision": 1},
        owner(world, queued_calendar["job_id"], "worker-calendar"),
    )
    final = task_effect_states(world.db, world.task.id)
    assert final["task"]["status"] == "applied"
    assert final["calendar"]["status"] == "failed"
    assert task_overall_state(final) == "failed"


def test_later_provider_deletion_removes_confirmed_ui_checkmark_after_reconciliation(world, monkeypatch):
    from app.provider_actions.product import task_effect_states
    queued = queue_confirmed_action(
        world.db, action_kind="google.tasks.upsert", target_id=world.task.id, actor=world.user,
    )
    fake = FakeGoogle("google.tasks.upsert")
    runtime = build_product_runtime(sessions=sessions(world), service_factory=lambda *_: fake)
    payload = {"organization_id": world.org.id, "action_id": queued["action_id"], "revision": 1}
    assert runtime.execute_job(payload, owner(world, queued["job_id"], "worker-created"))["outcome"] == "APPLIED"
    assert task_effect_states(world.db, world.task.id)["task"]["status"] == "applied"

    fake.task_items.clear()  # user deletes the object in Google after successful creation
    check = queue_reconciliation(world.db, action_id=queued["action_id"], revision=1, actor=world.user)
    reconcile_owner = owner(world, check["job_id"], "worker-verify")
    monkeypatch.setattr("app.provider_actions.product.build_product_runtime", lambda: runtime)
    with execution_owner(reconcile_owner[0], reconcile_owner[1],
                         attempt=reconcile_owner[2], locked_at=reconcile_owner[3]):
        result = run_product_reconcile_job(payload)
    assert result["outcome"] == "UNKNOWN"
    assert task_effect_states(world.db, world.task.id)["task"]["status"] == "unknown"


def test_telegram_task_change_queues_previously_confirmed_effects_without_inline_provider_io(world, monkeypatch):
    from app.api.telegram import webhook

    chat_id = 913541
    world.db.add(TelegramChatLink(chat_id=chat_id, project_id=world.project.id,
                                  title="Synthetic admin chat", enabled=True))
    world.db.commit()
    task_id, actor_id = world.task.id, world.user.id
    queued = []
    notices = []
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "synthetic-secret")
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_ID", "78412")
    monkeypatch.setattr("app.api.telegram.SessionLocal", lambda: world.db)
    monkeypatch.setattr("app.api.telegram.notify_telegram_chat", lambda _chat, message: notices.append(message))
    monkeypatch.setattr("app.api.telegram.task_effect_states", lambda *_: {
        "task": {"status": "applied"}, "calendar": {"status": "applied"},
    })

    def capture_queue(_db, *, action_kind, target_id, actor):
        queued.append((action_kind, target_id, actor.id))
        return {"job_id": len(queued)}

    monkeypatch.setattr("app.api.telegram.queue_confirmed_action", capture_queue)

    class Incoming:
        async def json(self):
            return {"message": {"chat": {"id": chat_id}, "from": {"id": 78412},
                                "text": f"/take {task_id}"}}

    assert asyncio.run(webhook(Incoming(), x_telegram_bot_api_secret_token="synthetic-secret")) == {"ok": True}
    assert queued == [
        ("google.tasks.upsert", task_id, actor_id),
        ("google.calendar.upsert", task_id, actor_id),
    ]
    assert any("поставлено в очередь" in notice for notice in notices)
