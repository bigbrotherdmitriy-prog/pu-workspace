"""Durable CONFIRM-only bridge for existing Google Workspace mutations.

The queue transports only an organization/action/revision tuple.  Human-visible
content stays in its existing domain row and is re-hashed immediately before a
provider effect.  A process restart therefore cannot silently execute edited
content under an old approval.
"""
from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from hashlib import sha256
from typing import Callable

from sqlalchemy import select, text

from app.google_calendar import CALENDAR_SCOPE, event_payload
from app.google_tasks import TASKS_SCOPE, task_payload
from app.integrations.external_resources import external_id_for, record_external_resource
from app.integrations.google_workspace import google_workspace_for_mailbox, google_workspace_for_project
from app.jobs.queue import current_execution_claim, enqueue
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.google_token import GoogleOAuthToken
from app.models.job import BackgroundJob
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.user import User
from app.models.v54_provider_action import (
    ProviderAction, ProviderActionApproval, ProviderDispatchOutbox,
    ProviderOutcomeObservation,
)
from app.provider_actions.contracts import (
    ActionEnvelope, LiveAuthority, ProviderActionError, ProviderPreconditionFailed,
    ProviderReceipt, ProviderRequest,
)
from app.provider_actions.runtime import PRODUCT_KIND, ProviderActionRuntime


_MANAGER_ROLES = frozenset({"manager", "owner"})
_ACTION_KINDS = frozenset({
    "gmail.message.send", "google.tasks.upsert", "google.calendar.upsert",
})
RECONCILE_KIND = "provider.action.reconcile"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(value: dict) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _role(db, project_id: int, user_id: int) -> str | None:
    return db.scalar(select(ProjectMember.role).where(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user_id,
    ))


def _require_human_manager(db, project_id: int, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or _role(db, project_id, user_id) not in _MANAGER_ROLES:
        raise ProviderActionError("authority_stale")
    return user


@dataclass(frozen=True)
class _Material:
    organization_id: int
    project_id: int
    mailbox_key: str
    authority_epoch: int
    capability_version: int
    credential_generation: int
    evidence_pins: tuple[str, ...]
    payload_hash: str
    target_id: int
    provider_thread_id: str | None = None
    google_token_id: int | None = None


def _project_token_material(db, *, project: Project, actor_id: int, action_kind: str,
                            payload_hash: str, target_id: int, evidence_pins: tuple[str, ...]) -> _Material:
    _require_human_manager(db, project.id, actor_id)
    token = db.scalar(select(GoogleOAuthToken).where(GoogleOAuthToken.project_id == project.id))
    required_scope = CALENDAR_SCOPE if action_kind == "google.calendar.upsert" else TASKS_SCOPE
    if action_kind == "gmail.message.send":
        required_scope = "https://www.googleapis.com/auth/gmail.send"
    scopes = frozenset((token.scopes or "").split()) if token else frozenset()
    if token is None or required_scope not in scopes:
        raise ProviderActionError("capability_stale")
    version = int(project.record_version or 0)
    if version <= 0:
        raise ProviderActionError("authority_stale")
    return _Material(
        organization_id=project.organization_id, project_id=project.id,
        mailbox_key=_digest(f"google-workspace:{project.organization_id}:{project.id}:{token.id}"),
        authority_epoch=version, capability_version=1, credential_generation=token.id,
        evidence_pins=evidence_pins, payload_hash=payload_hash, target_id=target_id,
        google_token_id=token.id,
    )


def _gmail_payload(draft: ResponseDraft, recipient: str, thread_id: str | None) -> dict:
    subject = draft.subject if draft.recipient_to or draft.subject.lower().startswith("re:") else f"Re: {draft.subject}"
    return {
        "kind": "gmail.message.send", "version": 1, "draft_id": draft.id,
        "project_id": draft.project_id, "message_id": draft.message_id,
        "recipient": recipient, "subject": subject, "body": draft.body,
        "thread_id": thread_id,
    }


def _gmail_material(db, draft: ResponseDraft, actor_id: int) -> _Material:
    project = db.get(Project, draft.project_id)
    if project is None or project.archived_at is not None:
        raise ProviderActionError("project_scope_mismatch")
    actor = _require_human_manager(db, project.id, actor_id)
    source = db.get(Message, draft.message_id) if draft.message_id else None
    recipient = (draft.recipient_to or (parseaddr(source.source_sender)[1] if source else "")).strip().casefold()
    if not recipient or parseaddr(recipient)[1].casefold() != recipient or recipient.count("@") != 1:
        raise ProviderActionError("payload_stale")
    thread_id = source.source_thread_id if source else None
    if source is not None and source.mail_connection_id:
        from app.mailbox_identity.runtime import require_mailbox_authority, runtime_for_message
        runtime = runtime_for_message(db, source, actor=actor, action=True)
        if runtime is None:
            raise ProviderActionError("mailbox_scope_mismatch")
        authority = require_mailbox_authority(db, runtime=runtime, actor=actor, permission="action")
        token = db.get(GoogleOAuthToken, runtime.google_token_id)
        if token is None or "https://www.googleapis.com/auth/gmail.send" not in set((token.scopes or "").split()):
            raise ProviderActionError("capability_stale")
        thread_id = runtime.provider_thread_id
        pins = (f"source:{runtime.source_reference_id}@{runtime.source_version_id}",
                f"draft:{draft.id}@{draft.source_excerpt_hash}")
        return _Material(
            organization_id=project.organization_id, project_id=project.id,
            mailbox_key=_digest(f"gmail:{runtime.identity_id}:{runtime.mail_connection_id}"),
            authority_epoch=authority.authority_version,
            capability_version=runtime.flags.record_version,
            credential_generation=runtime.generation, evidence_pins=pins,
            payload_hash=_canonical_hash(_gmail_payload(draft, recipient, thread_id)),
            target_id=draft.id, provider_thread_id=thread_id,
            google_token_id=runtime.google_token_id,
        )
    pins = (f"draft:{draft.id}@{draft.source_excerpt_hash}",)
    return _project_token_material(
        db, project=project, actor_id=actor_id, action_kind="gmail.message.send",
        payload_hash=_canonical_hash(_gmail_payload(draft, recipient, thread_id)),
        target_id=draft.id, evidence_pins=pins,
    )


def _task_payload(task: Task, action_kind: str) -> dict:
    body = task_payload(task) if action_kind == "google.tasks.upsert" else event_payload(task)
    return {
        "kind": action_kind, "version": 1, "task_id": task.id,
        "project_id": task.project_id, "record_version": int(task.record_version or 1),
        "body": body,
    }


def _task_material(db, task: Task, actor_id: int, action_kind: str) -> _Material:
    project = db.get(Project, task.project_id)
    if project is None or project.archived_at is not None:
        raise ProviderActionError("project_scope_mismatch")
    if action_kind == "google.calendar.upsert" and not task.due_date:
        raise ProviderActionError("payload_stale")
    pins = (f"task:{task.id}:source@{task.source_excerpt_hash}",)
    return _project_token_material(
        db, project=project, actor_id=actor_id, action_kind=action_kind,
        payload_hash=_canonical_hash(_task_payload(task, action_kind)),
        target_id=task.id, evidence_pins=pins,
    )


def _material_for(db, *, action_kind: str, target_id: int, actor_id: int) -> _Material:
    if action_kind == "gmail.message.send":
        draft = db.get(ResponseDraft, target_id)
        if draft is None or draft.status not in {"approved", "sending", "sent"}:
            raise ProviderActionError("payload_stale")
        return _gmail_material(db, draft, actor_id)
    task = db.get(Task, target_id)
    if task is None:
        raise ProviderActionError("payload_stale")
    return _task_material(db, task, actor_id, action_kind)


def _target_id(action_id: str, kind: str) -> int:
    prefix = {"gmail.message.send": "gmail-draft-", "google.tasks.upsert": "google-task-",
              "google.calendar.upsert": "google-calendar-"}[kind]
    value = action_id.removeprefix(prefix)
    if not value.isdigit() or action_id != prefix + value:
        raise ProviderActionError("dispatch_binding_mismatch")
    return int(value)


def _lock_action_stream(db, organization_id: int, action_id: str) -> None:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        key = int.from_bytes(sha256(f"{organization_id}:{action_id}".encode()).digest()[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def queue_confirmed_action(db, *, action_kind: str, target_id: int, actor: User) -> dict:
    """Seal exact current content, record exact approval and enqueue once."""
    if action_kind not in _ACTION_KINDS or not actor or not actor.id:
        raise ProviderActionError("invalid_envelope")
    prefix = {"gmail.message.send": "gmail-draft", "google.tasks.upsert": "google-task",
              "google.calendar.upsert": "google-calendar"}[action_kind]
    action_id = f"{prefix}-{target_id}"
    preliminary = _material_for(db, action_kind=action_kind, target_id=target_id, actor_id=actor.id)
    _lock_action_stream(db, preliminary.organization_id, action_id)
    rows = list(db.scalars(select(ProviderAction).where(
        ProviderAction.organization_id == preliminary.organization_id,
        ProviderAction.action_id == action_id,
    ).order_by(ProviderAction.revision.desc()).with_for_update()))
    latest = rows[0] if rows else None
    if latest and latest.payload_hash == preliminary.payload_hash:
        outbox = db.get(ProviderDispatchOutbox, (latest.action_id, latest.revision))
        if outbox is None or outbox.envelope_hash != latest.envelope_hash:
            raise ProviderActionError("dispatch_binding_mismatch")
        expected_payload = {
            "organization_id": latest.organization_id,
            "action_id": latest.action_id,
            "revision": latest.revision,
        }
        existing_job = (db.get(BackgroundJob, outbox.job_id) if outbox.job_id else
                        db.scalar(select(BackgroundJob).where(
                            BackgroundJob.idempotency_key == latest.idempotency_key,
                        )))
        if existing_job is None:
            existing_job = enqueue(
                db, PRODUCT_KIND, expected_payload,
                idempotency_key=latest.idempotency_key, max_attempts=3,
            )
        if existing_job.kind != PRODUCT_KIND or existing_job.payload != expected_payload:
            raise ProviderActionError("dispatch_binding_mismatch")
        if outbox.job_id is None:
            outbox = db.get(ProviderDispatchOutbox, (latest.action_id, latest.revision))
            outbox.job_id = existing_job.id
            db.commit()
        return {"action_id": latest.action_id, "revision": latest.revision,
                "job_id": existing_job.id, "already_queued": True}
    revision = (latest.revision + 1) if latest else 1
    material = _material_for(db, action_kind=action_kind, target_id=target_id, actor_id=actor.id)
    envelope = ActionEnvelope(
        action_id=action_id, revision=revision, organization_id=material.organization_id,
        project_id=material.project_id, mailbox_key=material.mailbox_key,
        provider="google_workspace", mode="CONFIRM", synthetic_only=False,
        action_kind=action_kind, reversibility="IRREVERSIBLE" if action_kind == "gmail.message.send" else "REVERSIBLE",
        payload_hash=material.payload_hash, command_key=f"product:{action_id}:v{revision}",
        idempotency_key=f"provider:{preliminary.organization_id}:{action_id}:v{revision}",
        context_revision=1, evidence_pins=material.evidence_pins,
        authority_epoch=material.authority_epoch, capability_version=material.capability_version,
        credential_generation=material.credential_generation,
    )
    values = asdict(envelope); values["evidence_pins"] = list(envelope.evidence_pins)
    row = ProviderAction(**values, envelope_hash=envelope.envelope_hash, state="READY",
                         created_by=str(actor.id), created_at=_now())
    approval_id = f"human-{_digest(f'{actor.id}:{envelope.envelope_hash}')[:40]}"
    approval = ProviderActionApproval(
        id=approval_id, action_id=action_id, revision=revision,
        organization_id=material.organization_id, project_id=material.project_id,
        mailbox_key=material.mailbox_key, command_key=envelope.command_key,
        idempotency_key=envelope.idempotency_key, payload_hash=envelope.payload_hash,
        envelope_hash=envelope.envelope_hash, authority_epoch=envelope.authority_epoch,
        capability_version=envelope.capability_version,
        credential_generation=envelope.credential_generation, state="GRANTED",
        approved_by=str(actor.id), granted_at=_now(), expires_at=_now() + timedelta(hours=1),
    )
    outbox = ProviderDispatchOutbox(
        action_id=action_id, revision=revision, organization_id=material.organization_id,
        approval_id=approval_id, envelope_hash=envelope.envelope_hash, pending=True,
        created_at=_now(),
    )
    db.add_all([row, approval, outbox])
    ProviderActionRuntime._audit(db, "action_frozen", action_id, revision, actor.id,
                                 f"confirm-{action_id}", envelope_hash=envelope.envelope_hash)
    ProviderActionRuntime._audit(db, "approval_granted", action_id, revision, actor.id,
                                 f"confirm-{action_id}", approval_id=approval_id)
    ProviderActionRuntime._audit(db, "dispatch_requested", action_id, revision, actor.id,
                                 f"confirm-{action_id}", approval_id=approval_id)
    db.flush()
    payload = {"organization_id": material.organization_id, "action_id": action_id, "revision": revision}
    job = enqueue(db, PRODUCT_KIND, payload, idempotency_key=envelope.idempotency_key, max_attempts=3)
    if job.kind != PRODUCT_KIND or job.payload != payload:
        raise ProviderActionError("dispatch_binding_mismatch")
    outbox = db.get(ProviderDispatchOutbox, (action_id, revision))
    outbox.job_id = job.id
    db.commit()
    return {"action_id": action_id, "revision": revision, "job_id": job.id, "already_queued": False}


class ProductAuthorityResolver:
    """Rebuild and compare live project/mailbox authority before T2 and lookup."""

    def __init__(self, sessions, clock=_now):
        self.sessions, self.clock = sessions, clock

    def resolve(self, envelope: ActionEnvelope, *, operation: str) -> LiveAuthority:
        if operation not in {"dispatch", "reconcile"}:
            raise ProviderActionError("authority_stale")
        with self.sessions.begin() as db:
            approval = db.scalar(select(ProviderActionApproval).where(
                ProviderActionApproval.action_id == envelope.action_id,
                ProviderActionApproval.revision == envelope.revision,
            ))
            if approval is None or not approval.approved_by.isdigit():
                raise ProviderActionError("approval_mismatch")
            material = _material_for(
                db, action_kind=envelope.action_kind,
                target_id=_target_id(envelope.action_id, envelope.action_kind),
                actor_id=int(approval.approved_by),
            )
        expected = (envelope.organization_id, envelope.project_id, envelope.mailbox_key,
                    envelope.authority_epoch, envelope.capability_version,
                    envelope.credential_generation, envelope.evidence_pins, envelope.payload_hash)
        actual = (material.organization_id, material.project_id, material.mailbox_key,
                  material.authority_epoch, material.capability_version,
                  material.credential_generation, material.evidence_pins, material.payload_hash)
        if expected != actual:
            raise ProviderActionError("authority_stale")
        return LiveAuthority(
            organization_id=material.organization_id, project_id=material.project_id,
            mailbox_key=material.mailbox_key, authority_epoch=material.authority_epoch,
            capability_version=material.capability_version,
            credential_generation=material.credential_generation,
            evidence_pins=material.evidence_pins, valid_until=self.clock() + timedelta(minutes=5),
            can_dispatch=True, can_reconcile=True,
        )


class GoogleWorkspaceProviderAdapter:
    name = "google_workspace"

    def __init__(self, sessions, service_factory: Callable | None = None):
        self.sessions = sessions
        self.service_factory = service_factory or self._service

    @staticmethod
    def _service(kind: str, material: _Material, db):
        workspace = (google_workspace_for_mailbox(material.google_token_id, db)
                     if kind == "gmail.message.send" and material.google_token_id
                     else google_workspace_for_project(material.project_id, db))
        if kind == "gmail.message.send":
            return workspace.service("gmail", "v1")
        if kind == "google.tasks.upsert":
            return workspace.service("tasks", "v1")
        return workspace.service("calendar", "v3")

    @staticmethod
    def _receipt(request: ProviderRequest, outcome: str, external_ref: str | None = None):
        return ProviderReceipt(
            action_id=request.action_id, revision=request.revision,
            organization_id=request.organization_id, project_id=request.project_id,
            mailbox_key=request.mailbox_key, command_key=request.command_key,
            idempotency_key=request.idempotency_key, payload_hash=request.payload_hash,
            outcome=outcome, external_ref=external_ref,
        )

    def _context(self, db, request: ProviderRequest):
        approval = db.scalar(select(ProviderActionApproval).where(
            ProviderActionApproval.action_id == request.action_id,
            ProviderActionApproval.revision == request.revision,
        ))
        if approval is None or not approval.approved_by.isdigit():
            raise ProviderPreconditionFailed()
        target_id = _target_id(request.action_id, request.action_kind)
        try:
            material = _material_for(db, action_kind=request.action_kind,
                                     target_id=target_id, actor_id=int(approval.approved_by))
        except ProviderActionError as exc:
            raise ProviderPreconditionFailed() from exc
        if (material.organization_id, material.project_id, material.mailbox_key,
                material.capability_version, material.credential_generation,
                material.payload_hash) != (
                request.organization_id, request.project_id, request.mailbox_key,
                request.capability_version, request.credential_generation,
                request.payload_hash):
            raise ProviderPreconditionFailed()
        return target_id, material

    @staticmethod
    def _gmail_message_id(request: ProviderRequest) -> str:
        return f"<puw-{_digest(request.idempotency_key)[:40]}@actions.pu-workspace.invalid>"

    def dispatch(self, request: ProviderRequest) -> ProviderReceipt:
        with self.sessions.begin() as db:
            target_id, material = self._context(db, request)
            service = self.service_factory(request.action_kind, material, db)
            if request.action_kind == "gmail.message.send":
                draft = db.get(ResponseDraft, target_id)
                if draft.sent_external_id:
                    return self._receipt(request, "APPLIED", draft.sent_external_id)
                recipient = (draft.recipient_to or parseaddr(db.get(Message, draft.message_id).source_sender)[1]).strip().casefold()
                canonical = _gmail_payload(draft, recipient, material.provider_thread_id)
                message = EmailMessage(); message["To"] = recipient; message["Subject"] = canonical["subject"]
                message["Message-ID"] = self._gmail_message_id(request); message.set_content(draft.body)
                body = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")}
                if material.provider_thread_id:
                    body["threadId"] = material.provider_thread_id
                sent = service.users().messages().send(userId="me", body=body).execute()
                external_id = str(sent["id"])
                draft.sent_external_id = external_id; draft.sent_at = _now(); draft.status = "sent"
                record_external_resource(db, project_id=draft.project_id, entity_type="response_draft",
                                         entity_id=draft.id, provider="google_workspace",
                                         resource_type="gmail_message", external_id=external_id)
            elif request.action_kind == "google.tasks.upsert":
                task = db.get(Task, target_id); body = task_payload(task)
                marker = f"PU-Command: {_digest(request.idempotency_key)}"
                body["notes"] = f"{body.get('notes', '')}\n{marker}".strip()
                body["status"] = "completed" if task.status == "completed" else "needsAction"
                external_id = external_id_for(db, entity_type="task", entity_id=task.id,
                    provider="google_workspace", resource_type="task", legacy_id=task.google_task_id)
                container = task.google_task_list_id or "@default"
                if external_id:
                    result = service.tasks().patch(tasklist=container, task=external_id, body=body).execute()
                else:
                    result = service.tasks().insert(tasklist="@default", body=body).execute()
                    external_id = str(result["id"]); container = "@default"
                task.google_task_id = external_id; task.google_task_list_id = container
                task.google_sync_error = None; task.google_synced_at = _now()
                record_external_resource(db, project_id=task.project_id, entity_type="task", entity_id=task.id,
                    provider="google_workspace", resource_type="task", external_id=external_id,
                    container_id=container)
            else:
                task = db.get(Task, target_id); body = event_payload(task)
                deterministic_id = _digest(request.idempotency_key)[:32]
                body["extendedProperties"] = {"private": {"puCommand": _digest(request.idempotency_key)}}
                external_id = external_id_for(db, entity_type="task", entity_id=task.id,
                    provider="google_workspace", resource_type="calendar_event",
                    legacy_id=task.google_calendar_event_id)
                if external_id:
                    service.events().patch(calendarId="primary", eventId=external_id, body=body).execute()
                else:
                    body["id"] = deterministic_id
                    result = service.events().insert(calendarId="primary", body=body).execute()
                    external_id = str(result.get("id") or deterministic_id)
                task.google_calendar_event_id = external_id; task.google_calendar_sync_error = None
                task.google_calendar_synced_at = _now()
                record_external_resource(db, project_id=task.project_id, entity_type="task", entity_id=task.id,
                    provider="google_workspace", resource_type="calendar_event", external_id=external_id)
            db.add(AuditLog(action="provider_effect_applied", entity_type="provider_action",
                            entity_id=None, details=f"kind={request.action_kind};status=applied"))
            return self._receipt(request, "APPLIED", external_id)

    def lookup(self, request: ProviderRequest) -> ProviderReceipt | None:
        with self.sessions.begin() as db:
            target_id, material = self._context(db, request)
            service = self.service_factory(request.action_kind, material, db)
            external_id = None
            if request.action_kind == "gmail.message.send":
                draft = db.get(ResponseDraft, target_id)
                external_id = draft.sent_external_id
                if not external_id:
                    page = service.users().messages().list(
                        userId="me", q=f"rfc822msgid:{self._gmail_message_id(request)}", maxResults=2,
                    ).execute()
                    hits = page.get("messages") or []
                    if len(hits) == 1:
                        external_id = str(hits[0]["id"]); draft.sent_external_id = external_id
                        draft.sent_at = _now(); draft.status = "sent"
            elif request.action_kind == "google.tasks.upsert":
                task = db.get(Task, target_id)
                external_id = external_id_for(db, entity_type="task", entity_id=task.id,
                    provider="google_workspace", resource_type="task", legacy_id=task.google_task_id)
                if external_id:
                    try:
                        found = service.tasks().get(
                            tasklist=task.google_task_list_id or "@default", task=external_id,
                        ).execute()
                        external_id = str(found.get("id") or external_id)
                    except Exception as exc:
                        if getattr(getattr(exc, "resp", None), "status", None) == 404:
                            return None
                        raise
                else:
                    marker = f"PU-Command: {_digest(request.idempotency_key)}"
                    page = service.tasks().list(tasklist="@default", maxResults=100,
                                                showCompleted=True, showHidden=True).execute()
                    hits = [item for item in page.get("items", []) if marker in str(item.get("notes") or "")]
                    if len(hits) == 1:
                        external_id = str(hits[0]["id"]); task.google_task_id = external_id
                        task.google_task_list_id = "@default"
            else:
                task = db.get(Task, target_id)
                external_id = external_id_for(db, entity_type="task", entity_id=task.id,
                    provider="google_workspace", resource_type="calendar_event",
                    legacy_id=task.google_calendar_event_id) or _digest(request.idempotency_key)[:32]
                try:
                    found = service.events().get(calendarId="primary", eventId=external_id).execute()
                    external_id = str(found.get("id") or external_id)
                except Exception as exc:
                    if getattr(getattr(exc, "resp", None), "status", None) == 404:
                        return None
                    raise
            if not external_id:
                return None
            if request.action_kind != "gmail.message.send":
                task = db.get(Task, target_id)
                resource = "task" if request.action_kind == "google.tasks.upsert" else "calendar_event"
                record_external_resource(db, project_id=task.project_id, entity_type="task", entity_id=task.id,
                    provider="google_workspace", resource_type=resource, external_id=external_id,
                    container_id="@default" if resource == "task" else None)
            return self._receipt(request, "APPLIED", external_id)


def build_product_runtime(*, sessions=None, service_factory=None):
    if sessions is None:
        from app.database import SessionLocal
        sessions = SessionLocal
    return ProductProviderActionRuntime(
        sessions=sessions,
        adapter=GoogleWorkspaceProviderAdapter(sessions, service_factory=service_factory),
        authority=ProductAuthorityResolver(sessions), allow_product=True,
    )


def run_product_job(payload: dict) -> dict:
    owner = current_execution_claim()
    if owner is None:
        raise ProviderActionError("dispatch_binding_mismatch")
    return build_product_runtime().execute_job(payload, owner)


def queue_reconciliation(db, *, action_id: str, revision: int, actor: User) -> dict:
    row = db.scalar(select(ProviderAction).where(
        ProviderAction.action_id == action_id, ProviderAction.revision == revision,
    ).execution_options(populate_existing=True).with_for_update())
    if row is None or row.provider != "google_workspace" or row.state != "UNKNOWN":
        raise ProviderActionError("outcome_not_reconcilable")
    _require_human_manager(db, row.project_id, actor.id)
    latest = db.scalar(select(ProviderOutcomeObservation).where(
        ProviderOutcomeObservation.action_id == action_id,
        ProviderOutcomeObservation.revision == revision,
    ).order_by(ProviderOutcomeObservation.sequence.desc()).limit(1))
    if latest is None or latest.outcome != "UNKNOWN":
        raise ProviderActionError("outcome_not_reconcilable")
    payload = {"organization_id": row.organization_id, "action_id": action_id, "revision": revision}
    key = f"provider-reconcile:{row.organization_id}:{action_id}:{revision}:{latest.sequence}"
    job = enqueue(db, RECONCILE_KIND, payload, idempotency_key=key, max_attempts=3)
    if job.kind != RECONCILE_KIND or job.payload != payload:
        raise ProviderActionError("dispatch_binding_mismatch")
    return {"action_id": action_id, "revision": revision, "job_id": job.id,
            "already_queued": job.attempts > 0 or job.status != "queued"}


def run_product_reconcile_job(payload: dict) -> dict:
    if set(payload) != {"organization_id", "action_id", "revision"}:
        raise ProviderActionError("dispatch_binding_mismatch")
    owner = current_execution_claim()
    if owner is None:
        raise ProviderActionError("dispatch_binding_mismatch")
    job_id, worker_id, attempt, locked_at = owner
    runtime = build_product_runtime()
    with runtime.sessions() as db:
        job = db.get(BackgroundJob, job_id)
        if (job is None or job.kind != RECONCILE_KIND or job.payload != payload
                or job.status != "running" or job.worker_id != worker_id
                or job.attempts != attempt or _aware(job.locked_at) != _aware(locked_at)
                or not job.lease_expires_at or _aware(job.lease_expires_at) <= _now()):
            raise ProviderActionError("dispatch_binding_mismatch")
        row = db.scalar(select(ProviderAction).where(
            ProviderAction.organization_id == payload["organization_id"],
            ProviderAction.action_id == payload["action_id"],
            ProviderAction.revision == payload["revision"],
        ))
        approval = db.scalar(select(ProviderActionApproval).where(
            ProviderActionApproval.action_id == payload["action_id"],
            ProviderActionApproval.revision == payload["revision"],
        ))
        if row is None or approval is None:
            raise ProviderActionError("dispatch_binding_mismatch")
        actor_id = approval.approved_by
    return runtime.reconcile(
        payload["action_id"], payload["revision"], actor_id=actor_id,
        correlation_id=f"job-reconcile-{payload['action_id']}-{payload['revision']}",
    )


def _project_outcome(sessions, payload: dict, result: dict) -> None:
    """Update legacy display state after the durable ledger is authoritative."""
    with sessions.begin() as db:
        row = db.scalar(select(ProviderAction).where(
            ProviderAction.organization_id == payload["organization_id"],
            ProviderAction.action_id == payload["action_id"],
            ProviderAction.revision == payload["revision"],
        ))
        if row is None:
            return
        target_id = _target_id(row.action_id, row.action_kind)
        outcome = result.get("outcome")
        if row.action_kind == "gmail.message.send":
            draft = db.get(ResponseDraft, target_id)
            if draft and outcome == "NOT_APPLIED" and draft.status == "sending":
                draft.status = "approved"
            return
        task = db.get(Task, target_id)
        if task:
            task.external_action_status = {
                "APPLIED": "executed", "NOT_APPLIED": "failed", "UNKNOWN": "unknown",
            }.get(outcome, task.external_action_status)


class ProductProviderActionRuntime(ProviderActionRuntime):
    def execute_job(self, payload: dict, owner: tuple):
        result = super().execute_job(payload, owner)
        _project_outcome(self.sessions, payload, result)
        return result

    def reconcile(self, action_id: str, revision: int, *, actor_id: str, correlation_id: str):
        result = super().reconcile(
            action_id, revision, actor_id=actor_id, correlation_id=correlation_id,
        )
        with self.sessions() as db:
            row = db.scalar(select(ProviderAction).where(
                ProviderAction.action_id == action_id, ProviderAction.revision == revision,
            ))
            if row is None:
                raise ProviderActionError("dispatch_binding_mismatch")
            payload = {"organization_id": row.organization_id,
                       "action_id": action_id, "revision": revision}
        _project_outcome(self.sessions, payload, result)
        return result
