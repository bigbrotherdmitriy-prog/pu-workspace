"""Fail-closed Gmail attachment boundary for the shared materialization lifecycle.

This module deliberately owns no materialization table and no queue.  The a05
integration supplies :class:`GmailAttachmentLifecyclePort`; this boundary owns
mailbox re-authorization, provider download adaptation and BackgroundJob wiring.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from typing import BinaryIO, Callable, Collection, ContextManager, Literal, Protocol, runtime_checkable

from sqlalchemy import select

from app.core.auth import ROLE_LEVEL
from app.mailbox_identity.runtime import require_mailbox_authority, runtime_for_message
from app.models.ai_secretary import Message
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_pilot import SourceReference, SourceVersion


GMAIL_ATTACHMENT_JOB_KIND = "gmail.attachment.materialize"
OPAQUE_STAGING_ID = re.compile(r"^[0-9a-f]{32}$")
OPAQUE_INTERNAL_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
MIME_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$")
TERMINAL_JOB_STATUSES = frozenset({"completed", "cancelled", "failed", "dead_letter"})


class GmailAttachmentError(RuntimeError):
    """Content-free error safe for queue classification and logs."""


class GmailAttachmentUnavailable(GmailAttachmentError):
    pass


class GmailAttachmentDenied(ValueError):
    pass


class GmailAttachmentIntegrityError(ValueError):
    pass


class GmailAttachmentCancelled(ValueError):
    pass


def validate_staging_id(value: str) -> str:
    if not isinstance(value, str) or not OPAQUE_STAGING_ID.fullmatch(value):
        raise GmailAttachmentDenied("invalid_staging_id")
    return value


def _positive(value: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_mime(value: str) -> bool:
    return isinstance(value, str) and value == value.lower() and bool(MIME_TYPE.fullmatch(value))


@dataclass(frozen=True, slots=True)
class GmailAttachmentBinding:
    """Persistable a05 input containing no provider locator or user content."""

    organization_id: int
    owner_user_id: int
    project_id: int
    message_id: int
    attachment_index: int
    identity_id: str
    mail_connection_id: str
    credential_generation: int
    binding_epoch: int
    mailbox_flags_record_version: int
    mailbox_authority_version: int
    source_reference_id: str
    source_version_id: str
    mailbox_binding_id: str
    declared_mime_type: str
    declared_size: int
    mode: Literal["CONFIRM"] = "CONFIRM"

    def __post_init__(self) -> None:
        positive = (
            self.organization_id, self.owner_user_id, self.project_id, self.message_id,
            self.credential_generation, self.binding_epoch, self.mailbox_flags_record_version,
            self.mailbox_authority_version,
        )
        opaque = (self.identity_id, self.mail_connection_id, self.source_reference_id,
                  self.source_version_id, self.mailbox_binding_id)
        if (not all(_positive(value) for value in positive)
                or not isinstance(self.attachment_index, int) or isinstance(self.attachment_index, bool)
                or self.attachment_index < 0
                or any(not isinstance(value, str) or not OPAQUE_INTERNAL_ID.fullmatch(value)
                       for value in opaque)
                or not _valid_mime(self.declared_mime_type)
                or not _positive(self.declared_size)
                or self.mode != "CONFIRM"):
            raise GmailAttachmentDenied("invalid_attachment_binding")


@dataclass(slots=True)
class ProviderAttachment:
    stream: BinaryIO = field(repr=False)
    observed_size: int


@runtime_checkable
class GmailAttachmentProvider(Protocol):
    def open(self) -> ProviderAttachment:
        """Perform exactly one provider body read and return a bounded stream."""
        ...


@dataclass(frozen=True, slots=True)
class GmailAttachmentStageResult:
    staging_id: str
    duplicate: bool = False

    def __post_init__(self) -> None:
        validate_staging_id(self.staging_id)


@dataclass(frozen=True, slots=True)
class GmailAttachmentJobClaim:
    job_id: int
    worker_id: str
    attempt: int
    locked_at: datetime


@dataclass(frozen=True, slots=True)
class GmailAttachmentProcessResult:
    status: Literal["completed", "cancelled"]
    document_id: int | None = None
    tasks: int = 0
    drafts: int = 0
    risks: int = 0
    decisions: int = 0

    def __post_init__(self) -> None:
        values = (self.tasks, self.drafts, self.risks, self.decisions)
        if (self.document_id is not None and not _positive(self.document_id)) or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
            raise GmailAttachmentDenied("invalid_process_result")


@runtime_checkable
class GmailAttachmentLifecyclePort(Protocol):
    """Adapter implemented by a05 without exposing its shared model here.

    ``provider.open`` is guarded immediately before download. ``authorize_read``
    MUST be called before every ciphertext/plaintext read and immediately before
    committing derived output. All methods are idempotent for ``staging_id``.
    """

    def admit_and_stage(self, db, binding: GmailAttachmentBinding,
                        provider: GmailAttachmentProvider) -> GmailAttachmentStageResult: ...

    def describe(self, db, staging_id: str) -> GmailAttachmentBinding: ...

    def process(self, db, staging_id: str, claim: GmailAttachmentJobClaim,
                authorize_read: Callable[[], ContextManager[None]]) -> GmailAttachmentProcessResult: ...

    def on_job_outcome(self, db, staging_id: str, status: str) -> None: ...

    def recover_pending(self, db, limit: int) -> Collection[str]:
        """Run retention/purge recovery and return dispatchable staging IDs."""
        ...


_lifecycle: GmailAttachmentLifecyclePort | None = None


def install_gmail_attachment_lifecycle(port: GmailAttachmentLifecyclePort | None) -> None:
    """Composition hook for a05; installing ``None`` restores default deny."""
    global _lifecycle
    if port is not None and not isinstance(port, GmailAttachmentLifecyclePort):
        raise TypeError("invalid_gmail_attachment_lifecycle")
    _lifecycle = port


def configured_gmail_attachment_lifecycle() -> GmailAttachmentLifecyclePort:
    if _lifecycle is None:
        raise GmailAttachmentUnavailable("gmail_attachment_staging_unavailable")
    return _lifecycle


def _attachment_metadata(message: Message, index: int) -> dict:
    try:
        values = json.loads(message.attachments_json or "[]")
        metadata = values[index]
    except (TypeError, ValueError, IndexError, KeyError, json.JSONDecodeError):
        raise GmailAttachmentDenied("attachment_unavailable") from None
    if not isinstance(values, list) or not isinstance(metadata, dict):
        raise GmailAttachmentDenied("attachment_unavailable")
    return metadata


def attachment_declaration(message: Message, index: int, *, max_bytes: int) -> tuple[str, int, str]:
    metadata = _attachment_metadata(message, index)
    mime_type, size, attachment_id = metadata.get("mime_type"), metadata.get("size"), metadata.get("attachment_id")
    if (not _valid_mime(mime_type) or not _positive(size) or size > max_bytes
            or not isinstance(attachment_id, str) or not attachment_id or len(attachment_id) > 1000):
        raise GmailAttachmentDenied("attachment_policy_denied")
    return mime_type, size, attachment_id


def validate_gmail_attachment_binding(db, binding: GmailAttachmentBinding, *, max_bytes: int) -> None:
    """Re-evaluate every persisted authority and observation pin, fail closed."""
    db.expire_all()
    if binding.mode != "CONFIRM" or binding.declared_size > max_bytes:
        raise GmailAttachmentDenied("attachment_policy_denied")
    owner = db.get(User, binding.owner_user_id)
    project = db.get(Project, binding.project_id)
    message = db.get(Message, binding.message_id)
    if (not owner or not project or not message
            or project.organization_id != binding.organization_id
            or message.organization_id != binding.organization_id
            or message.project_id != binding.project_id or message.source_type != "email"):
        raise GmailAttachmentDenied("attachment_scope_denied")
    if not owner.is_admin:
        role = db.scalar(select(ProjectMember.role).where(
            ProjectMember.project_id == binding.project_id,
            ProjectMember.user_id == binding.owner_user_id,
        ))
        if ROLE_LEVEL.get(role or "", 0) < ROLE_LEVEL["editor"]:
            raise GmailAttachmentDenied("attachment_scope_denied")
    try:
        runtime = runtime_for_message(db, message, actor=owner, action=True)
    except ValueError:
        raise GmailAttachmentDenied("attachment_authority_denied") from None
    if (runtime is None
            or runtime.organization_id != binding.organization_id
            or runtime.identity_id != binding.identity_id
            or runtime.mail_connection_id != binding.mail_connection_id
            or runtime.generation != binding.credential_generation
            or runtime.binding_epoch != binding.binding_epoch
            or runtime.source_reference_id != binding.source_reference_id
            or runtime.source_version_id != binding.source_version_id
            or runtime.binding_id != binding.mailbox_binding_id
            or not runtime.flags.primary_read or not runtime.flags.actions
            or runtime.flags.record_version != binding.mailbox_flags_record_version):
        raise GmailAttachmentDenied("attachment_authority_denied")
    try:
        authority = require_mailbox_authority(
            db, runtime=runtime, actor=owner, permission="action",
            expected_version=binding.mailbox_authority_version,
        )
    except ValueError:
        raise GmailAttachmentDenied("attachment_authority_denied") from None
    if authority.authority_version != binding.mailbox_authority_version:
        raise GmailAttachmentDenied("attachment_authority_denied")
    source = db.get(SourceReference, binding.source_reference_id)
    version = db.get(SourceVersion, binding.source_version_id)
    observed_locator = version.locator_at_observation if version else None
    if (not source or source.sync_state != "observed"
            or not version or version.consistency not in {"metadata_only", "revision_bound", "digest_observed"}
            or not isinstance(observed_locator, dict)
            or observed_locator.get("kind") != "gmail_message"
            or observed_locator.get("provider_message_id") != runtime.provider_message_id):
        raise GmailAttachmentDenied("provider_origin_unobserved")
    mime_type, size, _ = attachment_declaration(message, binding.attachment_index, max_bytes=max_bytes)
    if mime_type != binding.declared_mime_type or size != binding.declared_size:
        raise GmailAttachmentDenied("attachment_metadata_changed")


class GmailProviderDownloadAdapter:
    """Ephemeral Gmail locator adapter; repr and errors never reveal raw IDs/data."""

    def __init__(self, service, *, provider_message_id: str, provider_attachment_id: str,
                 expected_size: int, max_bytes: int):
        self._service = service
        self._message_id = provider_message_id
        self._attachment_id = provider_attachment_id
        self._expected_size = expected_size
        self._max_bytes = max_bytes
        self._opened = False

    def __repr__(self) -> str:
        return "<GmailProviderDownloadAdapter redacted>"

    def open(self) -> ProviderAttachment:
        if self._opened:
            raise GmailAttachmentDenied("provider_attachment_already_opened")
        self._opened = True
        payload = self._service.users().messages().attachments().get(
            userId="me", messageId=self._message_id, id=self._attachment_id,
        ).execute()
        encoded = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(encoded, str):
            raise GmailAttachmentIntegrityError("provider_attachment_invalid")
        if len(encoded) > ((self._max_bytes + 2) // 3) * 4 + 2:
            raise GmailAttachmentIntegrityError("provider_attachment_too_large")
        try:
            data = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        except (ValueError, binascii.Error):
            raise GmailAttachmentIntegrityError("provider_attachment_invalid") from None
        observed = payload.get("size", len(data))
        if (not _positive(observed) or observed != len(data) or len(data) != self._expected_size
                or len(data) > self._max_bytes):
            raise GmailAttachmentIntegrityError("provider_attachment_size_mismatch")
        return ProviderAttachment(stream=BytesIO(data), observed_size=len(data))


class _PreDownloadGuard:
    def __init__(self, binding: GmailAttachmentBinding, provider: GmailAttachmentProvider, max_bytes: int):
        self._binding, self._provider, self._max_bytes = binding, provider, max_bytes

    def __repr__(self) -> str:
        return "<GmailAttachmentProvider guarded>"

    def open(self) -> ProviderAttachment:
        # Use an independent transaction so concurrent revoke/rotation is visible
        # and authorization cannot expire a05's in-progress lifecycle objects.
        from app.database import SessionLocal
        with SessionLocal() as auth_db:
            validate_gmail_attachment_binding(auth_db, self._binding, max_bytes=self._max_bytes)
            return self._provider.open()


def stage_gmail_attachment(db, binding: GmailAttachmentBinding,
                           provider: GmailAttachmentProvider, *, max_bytes: int) -> GmailAttachmentStageResult:
    validate_gmail_attachment_binding(db, binding, max_bytes=max_bytes)
    lifecycle = configured_gmail_attachment_lifecycle()
    result = lifecycle.admit_and_stage(db, binding, _PreDownloadGuard(binding, provider, max_bytes))
    if not isinstance(result, GmailAttachmentStageResult):
        raise GmailAttachmentDenied("invalid_stage_result")
    return result


def enqueue_staged_gmail_attachment(db, staging_id: str):
    from app.jobs.queue import enqueue
    staging_id = validate_staging_id(staging_id)
    return enqueue(db, GMAIL_ATTACHMENT_JOB_KIND, {"staging_id": staging_id},
                   idempotency_key=f"{GMAIL_ATTACHMENT_JOB_KIND}:{staging_id}")


def _claim() -> GmailAttachmentJobClaim:
    from app.jobs.queue import current_execution_claim
    value = current_execution_claim()
    if (not isinstance(value, tuple) or len(value) != 4 or not _positive(value[0])
            or not isinstance(value[1], str) or not value[1] or not _positive(value[2])
            or value[3] is None):
        raise GmailAttachmentDenied("missing_job_claim")
    return GmailAttachmentJobClaim(value[0], value[1], value[2], value[3])


def _validate_job_claim(db, claim: GmailAttachmentJobClaim, staging_id: str) -> bool:
    from app.jobs.queue import utcnow
    from app.models.job import BackgroundJob
    db.expire_all()
    job = db.get(BackgroundJob, claim.job_id)
    lease = job.lease_expires_at if job else None
    if lease is not None and lease.tzinfo is None:
        from datetime import timezone
        lease = lease.replace(tzinfo=timezone.utc)
    if (not job or job.kind != GMAIL_ATTACHMENT_JOB_KIND
            or job.payload != {"staging_id": staging_id}
            or job.status != "running" or job.worker_id != claim.worker_id
            or job.attempts != claim.attempt or job.locked_at != claim.locked_at
            or lease is None or lease <= utcnow()):
        raise GmailAttachmentDenied("stale_job_claim")
    return bool(isinstance(job.result, dict) and job.result.get("cancel_requested"))


def run_gmail_attachment_job(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"staging_id"}:
        raise GmailAttachmentDenied("invalid_job_payload")
    staging_id, claim = validate_staging_id(payload["staging_id"]), _claim()
    lifecycle = configured_gmail_attachment_lifecycle()
    from app.database import SessionLocal
    with SessionLocal() as db:
        pinned = lifecycle.describe(db, staging_id)
        if not isinstance(pinned, GmailAttachmentBinding):
            raise GmailAttachmentDenied("invalid_staging_binding")

        @contextmanager
        def authorize_read():
            with SessionLocal() as auth_db:
                if _validate_job_claim(auth_db, claim, staging_id):
                    raise GmailAttachmentCancelled("job_cancelled")
                current = lifecycle.describe(auth_db, staging_id)
                if current != pinned:
                    raise GmailAttachmentDenied("staging_binding_changed")
                validate_gmail_attachment_binding(auth_db, current, max_bytes=_gmail_max_bytes())
                yield

        try:
            with authorize_read():
                pass
            outcome = lifecycle.process(db, staging_id, claim, authorize_read)
        except GmailAttachmentCancelled:
            outcome = GmailAttachmentProcessResult("cancelled")
        if not isinstance(outcome, GmailAttachmentProcessResult):
            raise GmailAttachmentDenied("invalid_process_result")
        if outcome.status != "cancelled":
            try:
                with authorize_read():
                    pass
            except GmailAttachmentCancelled:
                outcome = GmailAttachmentProcessResult("cancelled")
    return {
        "staging_id": staging_id, "status": outcome.status,
        "document_id": outcome.document_id, "tasks": outcome.tasks,
        "drafts": outcome.drafts, "risks": outcome.risks, "decisions": outcome.decisions,
        "cancelled": outcome.status == "cancelled",
    }


def notify_gmail_attachment_job_outcome(payload: dict, status: str) -> None:
    if (not isinstance(payload, dict) or set(payload) != {"staging_id"}
            or status not in TERMINAL_JOB_STATUSES | {"retrying"}):
        raise GmailAttachmentDenied("invalid_job_outcome")
    lifecycle = configured_gmail_attachment_lifecycle()
    from app.database import SessionLocal
    with SessionLocal() as db:
        lifecycle.on_job_outcome(db, validate_staging_id(payload["staging_id"]), status)


def recover_gmail_attachment_jobs(*, limit: int = 100) -> int:
    if not _positive(limit) or limit > 1000:
        raise GmailAttachmentDenied("invalid_recovery_limit")
    try:
        lifecycle = configured_gmail_attachment_lifecycle()
    except GmailAttachmentUnavailable:
        return 0
    from app.database import SessionLocal
    recovered = 0
    with SessionLocal() as db:
        staging_ids = tuple(lifecycle.recover_pending(db, limit))
        if len(staging_ids) > limit or len(set(staging_ids)) != len(staging_ids):
            raise GmailAttachmentDenied("invalid_recovery_result")
        for staging_id in staging_ids:
            job = enqueue_staged_gmail_attachment(db, validate_staging_id(staging_id))
            recovered += int(job.status in {"queued", "retrying"})
    return recovered


def _gmail_max_bytes() -> int:
    import os
    return int(os.getenv("GMAIL_ATTACHMENT_MAX_BYTES", str(10 * 1024 * 1024)))
