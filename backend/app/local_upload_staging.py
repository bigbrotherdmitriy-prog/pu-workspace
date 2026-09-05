"""Fail-closed orchestration for local uploads through encrypted staging.

This module deliberately does not own persistence. The v5.4 materialization
lifecycle supplies ``LocalUploadLifecycle``; the existing ``BackgroundJob``
table remains the only queue. Until that port is installed, ingress and worker
execution fail closed.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import PurePosixPath
from threading import RLock
from typing import Any, Callable, Literal, Mapping, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs.queue import current_execution_claim, enqueue
from app.jobs.queue import utcnow as queue_now
from app.models.job import BackgroundJob
from app.staging import KekRef, StagingDescriptor, StagingStorage, new_fence, new_object_id
from app.staging.contracts import StagingError, StagingIntegrityError

log = logging.getLogger("pu.local_upload_staging")

JOB_KIND = "local_upload.process"
ALLOWED_JOB_KEYS = frozenset({"staging_id"})
ALLOWED_RESULT_KEYS = frozenset({
    "processed", "skipped", "tasks", "risks", "decisions", "drafts", "documents",
})
DEFAULT_ALLOWED_MIME_TYPES = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "text/markdown",
    "text/plain",
})


class LocalUploadStagingError(RuntimeError):
    """Stable error without document content, names, paths, or secrets."""


class LocalUploadUnavailable(LocalUploadStagingError):
    pass


class LocalUploadAdmissionDenied(LocalUploadStagingError):
    pass


class LocalUploadConflict(LocalUploadStagingError):
    pass


class LocalUploadCancelled(LocalUploadStagingError):
    pass


@dataclass(frozen=True, slots=True)
class UploadScope:
    owner_id: int
    project_id: int

    def __post_init__(self) -> None:
        if isinstance(self.owner_id, bool) or not isinstance(self.owner_id, int) or self.owner_id <= 0:
            raise LocalUploadAdmissionDenied("invalid_owner_scope")
        if isinstance(self.project_id, bool) or not isinstance(self.project_id, int) or self.project_id <= 0:
            raise LocalUploadAdmissionDenied("invalid_project_scope")


@dataclass(frozen=True, slots=True)
class UploadCandidate:
    display_name: str
    mime_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class UploadReservation:
    staging_id: str
    object_id: str
    fence: str
    fingerprint: str
    state: str
    descriptor: StagingDescriptor | None = None
    job_id: int | None = None


@dataclass(frozen=True, slots=True)
class MaterializedUpload:
    staging_id: str
    scope: UploadScope
    display_name: str
    mime_type: str
    checksum: str
    size: int
    descriptor: StagingDescriptor
    job_id: int
    source_version_id: str | None = None


@dataclass(frozen=True, slots=True)
class FinalizedUpload:
    """Durable cleanup recovery returned after a worker restart."""

    staging_id: str
    scope: UploadScope
    descriptor: StagingDescriptor | None
    job_id: int
    outcome: Literal["completed", "cancelled"]
    result: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CleanupDecision:
    delete_ciphertext: bool
    retention_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class EnqueuedUpload:
    staging_id: str
    job_id: int
    status: str


@runtime_checkable
class LocalUploadLifecycle(Protocol):
    """Persistence/CAS boundary implemented by the a05 lifecycle owner."""

    def reserve(
        self, session: Any, *, scope: UploadScope, request_key: str,
        object_id: str, fence: str, fingerprint: str, display_name: str,
        mime_type: str, checksum: str, size: int, expires_at: datetime,
    ) -> UploadReservation: ...

    def publish(
        self, session: Any, *, scope: UploadScope, staging_id: str,
        descriptor: StagingDescriptor, checksum: str, size: int,
    ) -> UploadReservation: ...

    def bind_job(
        self, session: Any, *, scope: UploadScope, staging_id: str, job_id: int,
    ) -> UploadReservation: ...

    def load_for_processing(
        self, session: Any, *, staging_id: str, job_id: int,
        claim: tuple[Any, ...],
    ) -> MaterializedUpload | FinalizedUpload: ...

    def finalize(
        self, session: Any, *, scope: UploadScope, staging_id: str, job_id: int,
        claim: tuple[Any, ...], outcome: Literal["completed", "cancelled", "failed"],
        result: Mapping[str, Any] | None = None,
    ) -> CleanupDecision: ...

    def complete_cleanup(
        self, session: Any, *, scope: UploadScope, staging_id: str, job_id: int,
        claim: tuple[Any, ...],
    ) -> None: ...

    def recover_retention(self, session_factory: Callable[[], Any], *, limit: int) -> int: ...


class LocalUploadLifecycleAdapter:
    """Fail-closed seam for the future a05 lifecycle implementation.

    The local-upload slice cannot import a05 before its schema and composition
    are integrated.  Composition must therefore wrap the a05-owned backend in
    this adapter.  A missing method, an incompatible return value, or any
    backend exception becomes one stable, content-free availability error.
    """

    def __init__(self, backend: Any | None) -> None:
        self.__backend = backend

    def _call(self, method: str, expected: type, *args: Any, **kwargs: Any) -> Any:
        function = getattr(self.__backend, method, None)
        if not callable(function):
            raise LocalUploadUnavailable("local_upload_lifecycle_unavailable")
        try:
            result = function(*args, **kwargs)
        except Exception:
            # Never propagate a lifecycle/DB exception: its text may contain a
            # filename, locator, SQL value, key reference, or document data.
            raise LocalUploadUnavailable("local_upload_lifecycle_unavailable") from None
        if not isinstance(result, expected):
            raise LocalUploadUnavailable("local_upload_lifecycle_unavailable")
        return result

    def reserve(self, session: Any, **kwargs: Any) -> UploadReservation:
        return self._call("reserve", UploadReservation, session, **kwargs)

    def publish(self, session: Any, **kwargs: Any) -> UploadReservation:
        return self._call("publish", UploadReservation, session, **kwargs)

    def bind_job(self, session: Any, **kwargs: Any) -> UploadReservation:
        return self._call("bind_job", UploadReservation, session, **kwargs)

    def load_for_processing(
        self, session: Any, **kwargs: Any,
    ) -> MaterializedUpload | FinalizedUpload:
        result = self._call("load_for_processing", object, session, **kwargs)
        if not isinstance(result, (MaterializedUpload, FinalizedUpload)):
            raise LocalUploadUnavailable("local_upload_lifecycle_unavailable")
        return result

    def finalize(self, session: Any, **kwargs: Any) -> CleanupDecision:
        decision = self._call("finalize", CleanupDecision, session, **kwargs)
        if type(decision.delete_ciphertext) is not bool:
            raise LocalUploadUnavailable("local_upload_lifecycle_unavailable")
        if decision.retention_until is not None and (
            not isinstance(decision.retention_until, datetime)
            or decision.retention_until.tzinfo is None
        ):
            raise LocalUploadUnavailable("local_upload_lifecycle_unavailable")
        return decision

    def complete_cleanup(self, session: Any, **kwargs: Any) -> None:
        function = getattr(self.__backend, "complete_cleanup", None)
        if not callable(function):
            raise LocalUploadUnavailable("local_upload_lifecycle_unavailable")
        try:
            result = function(session, **kwargs)
        except Exception:
            raise LocalUploadUnavailable("local_upload_lifecycle_unavailable") from None
        if result is not None:
            raise LocalUploadUnavailable("local_upload_lifecycle_unavailable")

    def recover_retention(self, session_factory: Callable[[], Any], *, limit: int) -> int:
        result = self._call("recover_retention", int, session_factory, limit=limit)
        if type(result) is not int or not 0 <= result <= limit:
            raise LocalUploadUnavailable("local_upload_lifecycle_unavailable")
        return result


@runtime_checkable
class LocalUploadProcessor(Protocol):
    def process(
        self, session: Any, *, record: MaterializedUpload, content: bytes,
        operation_key: str,
    ) -> Mapping[str, Any]: ...


class LocalUploadBusinessProcessor:
    """Existing local-only document pipeline, invoked only by a durable worker.

    No Telegram notification or external AI/provider call is made here.  The
    stable staging identity makes retries converge on the existing document
    and task deduplication boundaries.
    """

    def process(
        self, session: Any, *, record: MaterializedUpload, content: bytes,
        operation_key: str,
    ) -> Mapping[str, Any]:
        from app.document_engine import index_documents
        from app.governance_engine import create_governance_items
        from app.organizer_engine.content import extract_text
        from app.organizer_engine.types import DriveFile
        from app.response_engine import create_response_drafts
        from app.task_engine import create_tasks_from_files

        text = extract_text(content, record.mime_type, record.display_name)
        if not text:
            return {
                "processed": 0, "skipped": 1, "tasks": 0,
                "risks": 0, "decisions": 0, "drafts": 0, "documents": [],
            }
        item = DriveFile(
            id=f"local:{record.staging_id}",
            name=record.display_name,
            mime_type=record.mime_type,
            parent_id="local-upload",
            md5_checksum=record.checksum,
            size=record.size,
            content_text=text,
        )
        files = [item]
        documents = index_documents(
            session,
            record.scope.project_id,
            files,
            "local_upload",
            exact_source_versions=(
                {item.id: record.source_version_id} if record.source_version_id else None
            ),
        )
        tasks = create_tasks_from_files(
            session, record.scope.project_id, None, files, source_type="local_upload",
        )
        drafts = create_response_drafts(session, record.scope.project_id, None, files)
        risks, decisions = create_governance_items(
            session, record.scope.project_id, files, source_type="local_upload",
        )
        return {
            "processed": 1,
            "skipped": 0,
            "tasks": len(tasks),
            "risks": len(risks),
            "decisions": len(decisions),
            "drafts": len(drafts),
            "documents": [int(row.id) for row in documents],
        }


@dataclass(frozen=True, slots=True)
class LocalUploadRuntime:
    storage: StagingStorage
    lifecycle: LocalUploadLifecycleAdapter
    processor: LocalUploadProcessor
    session_factory: Callable[[], Any]
    kek: KekRef
    max_file_bytes: int
    allowed_mime_types: frozenset[str] = DEFAULT_ALLOWED_MIME_TYPES
    retention: timedelta = timedelta(hours=24)

    def __post_init__(self) -> None:
        if not isinstance(self.storage, StagingStorage):
            raise TypeError("invalid_staging_storage")
        if not isinstance(self.lifecycle, LocalUploadLifecycleAdapter):
            raise TypeError("invalid_lifecycle_port")
        if not isinstance(self.processor, LocalUploadProcessor):
            raise TypeError("invalid_processor_port")
        if not callable(self.session_factory):
            raise TypeError("invalid_session_factory")
        if isinstance(self.max_file_bytes, bool) or self.max_file_bytes <= 0:
            raise ValueError("invalid_file_limit")


_runtime: LocalUploadRuntime | None = None
_runtime_lock = RLock()


def configure_local_upload_runtime(runtime: LocalUploadRuntime | None) -> None:
    """Install identical explicit wiring in API and worker processes."""
    global _runtime
    with _runtime_lock:
        _runtime = runtime


def get_local_upload_runtime() -> LocalUploadRuntime:
    with _runtime_lock:
        if _runtime is None:
            raise LocalUploadUnavailable("local_upload_staging_unavailable")
        return _runtime


def canonical_mime(value: str) -> str:
    if not isinstance(value, str):
        raise LocalUploadAdmissionDenied("unsupported_mime_type")
    mime = value.partition(";")[0].strip().lower()
    if not mime or len(mime) > 255:
        raise LocalUploadAdmissionDenied("unsupported_mime_type")
    return mime


def safe_display_name(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise LocalUploadAdmissionDenied("invalid_filename")
    name = PurePosixPath(value.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."} or len(name) > 255:
        raise LocalUploadAdmissionDenied("invalid_filename")
    return name


def admit_candidate(
    display_name: str, mime_type: str, content: bytes, *, max_file_bytes: int,
    allowed_mime_types: frozenset[str],
) -> UploadCandidate:
    name = safe_display_name(display_name)
    mime = canonical_mime(mime_type)
    if mime not in allowed_mime_types:
        raise LocalUploadAdmissionDenied("unsupported_mime_type")
    if not isinstance(content, bytes):
        raise LocalUploadAdmissionDenied("invalid_content_stream")
    if len(content) > max_file_bytes:
        raise LocalUploadAdmissionDenied("file_too_large")
    return UploadCandidate(display_name=name, mime_type=mime, content=content)


def _request_digest(scope: UploadScope, request_key: str, index: int) -> str:
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 255:
        raise LocalUploadAdmissionDenied("invalid_idempotency_key")
    raw = f"v54-local-upload\x00{scope.owner_id}\x00{scope.project_id}\x00{request_key}\x00{index}".encode()
    return hashlib.sha256(raw).hexdigest()


def _fingerprint(candidate: UploadCandidate) -> tuple[str, str]:
    checksum = hashlib.sha256(candidate.content).hexdigest()
    raw = b"\x00".join((candidate.display_name.encode(), candidate.mime_type.encode(), checksum.encode()))
    return hashlib.sha256(raw).hexdigest(), checksum


def _opaque_hex(value: Any, *, length: int, error: str) -> str:
    if (
        not isinstance(value, str) or len(value) != length
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise LocalUploadAdmissionDenied(error)
    return value


def _validate_reservation(
    reservation: UploadReservation, *, fingerprint: str,
) -> UploadReservation:
    if not isinstance(reservation, UploadReservation):
        raise LocalUploadConflict("invalid_lifecycle_reservation")
    _opaque_hex(reservation.staging_id, length=32, error="invalid_staging_id")
    _opaque_hex(reservation.object_id, length=32, error="invalid_object_id")
    _opaque_hex(reservation.fence, length=32, error="invalid_fence")
    _opaque_hex(reservation.fingerprint, length=64, error="invalid_fingerprint")
    if not hmac.compare_digest(reservation.fingerprint, fingerprint):
        raise LocalUploadConflict("idempotency_conflict")
    if reservation.job_id is not None and (
        isinstance(reservation.job_id, bool)
        or not isinstance(reservation.job_id, int)
        or reservation.job_id <= 0
    ):
        raise LocalUploadConflict("invalid_job_binding")
    if (
        reservation.descriptor is not None
        and reservation.descriptor.object_id != reservation.object_id
    ):
        raise LocalUploadConflict("descriptor_binding_conflict")
    return reservation


def _job_payload(staging_id: str) -> dict[str, Any]:
    """The queue sees one opaque business ID and no file-derived metadata."""
    return {"staging_id": staging_id}


def _commit(session: Any, error: str) -> None:
    commit = getattr(session, "commit", None)
    if not callable(commit):
        raise LocalUploadUnavailable(error)
    try:
        commit()
    except Exception:
        rollback = getattr(session, "rollback", None)
        if callable(rollback):
            try:
                rollback()
            except Exception:
                pass
        raise LocalUploadUnavailable(error) from None


def stage_and_enqueue(
    session: Any, *, runtime: LocalUploadRuntime, scope: UploadScope,
    candidate: UploadCandidate, request_key: str, index: int,
) -> EnqueuedUpload:
    fingerprint, checksum = _fingerprint(candidate)
    scoped_key = _request_digest(scope, request_key, index)
    reservation = runtime.lifecycle.reserve(
        session, scope=scope, request_key=scoped_key, object_id=new_object_id(),
        fence=new_fence(), fingerprint=fingerprint,
        display_name=candidate.display_name, mime_type=candidate.mime_type,
        checksum=checksum, size=len(candidate.content),
        expires_at=datetime.now(timezone.utc) + runtime.retention,
    )
    reservation = _validate_reservation(reservation, fingerprint=fingerprint)
    if reservation.job_id is not None:
        _commit(session, "local_upload_existing_binding_unavailable")
        return EnqueuedUpload(reservation.staging_id, reservation.job_id, "already_queued")

    # Admission and its write fence must be durable before ciphertext I/O.
    # A restart reuses the same object/fence and the storage write is recoverable.
    _commit(session, "local_upload_admission_unavailable")

    descriptor = reservation.descriptor
    if descriptor is None:
        try:
            descriptor = runtime.storage.write(
                reservation.object_id, BytesIO(candidate.content),
                max_bytes=runtime.max_file_bytes, kek=runtime.kek,
                fence=reservation.fence,
            )
        except StagingError as exc:
            raise LocalUploadStagingError(exc.__class__.__name__) from None
        reservation = runtime.lifecycle.publish(
            session, scope=scope, staging_id=reservation.staging_id,
            descriptor=descriptor, checksum=checksum, size=len(candidate.content),
        )
        reservation = _validate_reservation(reservation, fingerprint=fingerprint)
        if reservation.descriptor != descriptor:
            raise LocalUploadConflict("descriptor_binding_conflict")

    # The exact representation/source-version binding must exist before the
    # queue can make this materialization visible to a worker.
    _commit(session, "local_upload_publication_unavailable")

    payload = _job_payload(reservation.staging_id)
    if set(payload) != ALLOWED_JOB_KEYS:
        raise LocalUploadStagingError("unsafe_job_payload")
    try:
        job = enqueue(
            session, JOB_KIND, payload,
            idempotency_key=f"local-upload:{scoped_key}",
        )
        job_id = int(job.id)
    except Exception:
        raise LocalUploadUnavailable("local_upload_queue_unavailable") from None
    if isinstance(getattr(job, "id", None), bool) or job_id <= 0:
        raise LocalUploadUnavailable("local_upload_queue_unavailable")
    bound = runtime.lifecycle.bind_job(
        session, scope=scope, staging_id=reservation.staging_id, job_id=job_id,
    )
    bound = _validate_reservation(bound, fingerprint=fingerprint)
    if bound.job_id != job_id:
        raise LocalUploadConflict("job_binding_conflict")
    # enqueue() commits the published lifecycle row and BackgroundJob.  The
    # binding is a second, explicit durable transition; a retry can repair the
    # narrow crash gap using the same idempotency key.
    _commit(session, "local_upload_job_binding_unavailable")
    status = getattr(job, "status", None)
    if status not in {"queued", "retrying", "running"}:
        raise LocalUploadUnavailable("local_upload_queue_unavailable")
    return EnqueuedUpload(bound.staging_id, job_id, str(status))


def _validated_payload(payload: Mapping[str, Any]) -> str:
    if not isinstance(payload, Mapping) or set(payload) != ALLOWED_JOB_KEYS:
        raise LocalUploadAdmissionDenied("unsafe_job_payload")
    staging_id = payload["staging_id"]
    _opaque_hex(staging_id, length=32, error="invalid_staging_id")
    return staging_id


def _validated_materialization(
    record: MaterializedUpload, *, staging_id: str, job_id: int,
    runtime: LocalUploadRuntime,
) -> MaterializedUpload:
    if (
        not isinstance(record, MaterializedUpload)
        or record.staging_id != staging_id
        or record.job_id != job_id
        or not isinstance(record.scope, UploadScope)
        or safe_display_name(record.display_name) != record.display_name
        or canonical_mime(record.mime_type) != record.mime_type
        or record.mime_type not in runtime.allowed_mime_types
        or isinstance(record.size, bool)
        or not isinstance(record.size, int)
        or not 0 <= record.size <= runtime.max_file_bytes
        or not isinstance(record.descriptor, StagingDescriptor)
        or record.descriptor.kek != runtime.kek
        or isinstance(record.descriptor.format_version, bool)
        or not isinstance(record.descriptor.format_version, int)
        or record.descriptor.format_version <= 0
        or isinstance(record.descriptor.chunk_size, bool)
        or not isinstance(record.descriptor.chunk_size, int)
        or record.descriptor.chunk_size <= 0
        or not isinstance(record.descriptor.wrapped_dek, str)
        or not 1 <= len(record.descriptor.wrapped_dek) <= 255
    ):
        raise LocalUploadConflict("materialization_binding_conflict")
    try:
        _opaque_hex(record.checksum, length=64, error="invalid_checksum")
        _opaque_hex(record.descriptor.object_id, length=32, error="invalid_object_id")
    except LocalUploadAdmissionDenied:
        raise LocalUploadConflict("materialization_binding_conflict") from None
    return record


def cleanup_finalized_upload(
    runtime: LocalUploadRuntime, record: MaterializedUpload, decision: CleanupDecision,
) -> bool:
    """Idempotent cleanup hook for the worker and restart reconciliation.

    The lifecycle remains the authority: callers may delete ciphertext only
    after a durable finalization decision says so.  Replaying this hook after a
    crash is safe because ``StagingStorage.delete`` is idempotent.
    """
    if decision.delete_ciphertext:
        runtime.storage.delete(record.descriptor.object_id)
        return True
    return False


def recover_local_upload_retention(*, limit: int = 50) -> int:
    """Run the bounded service-owned retention hook when rollout is configured."""
    if type(limit) is not int or not 1 <= limit <= 500:
        raise LocalUploadUnavailable("invalid_retention_limit")
    with _runtime_lock:
        runtime = _runtime
    if runtime is None:
        return 0
    return runtime.lifecycle.recover_retention(runtime.session_factory, limit=limit)


def _validated_result(result: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(result, Mapping) or set(result) != ALLOWED_RESULT_KEYS:
        raise LocalUploadStagingError("unsafe_processor_result")
    counts: dict[str, Any] = {}
    for key in ALLOWED_RESULT_KEYS - {"documents"}:
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise LocalUploadStagingError("unsafe_processor_result")
        counts[key] = value
    documents = result["documents"]
    if (
        not isinstance(documents, (list, tuple))
        or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in documents)
    ):
        raise LocalUploadStagingError("unsafe_processor_result")
    counts["documents"] = list(documents)
    return counts


class _LeaseFencedSession:
    """Proxy legacy helpers while holding the exact job claim through each write.

    A ``FOR UPDATE`` lock and the subsequent business commit are one transaction,
    so lease recovery cannot overlap the durable write.  The proxy is intentionally
    private: it adds no queue or alternative ownership token.
    """

    def __init__(self, session: Any, claim: tuple[Any, ...]) -> None:
        self._session = session
        self._claim = claim

    def _require_live_claim(self) -> None:
        if not isinstance(self._session, Session):
            return
        job_id, worker_id, attempt, locked_at = self._claim
        no_autoflush = getattr(self._session, "no_autoflush", None)
        context = no_autoflush if no_autoflush is not None else _NullContext()
        with context:
            job = self._session.scalar(
                select(BackgroundJob).where(BackgroundJob.id == job_id)
                .with_for_update().execution_options(populate_existing=True)
            )
        if (
            job is None or job.status != "running" or job.worker_id != worker_id
            or job.attempts != attempt or job.locked_at is None or locked_at is None
            or _as_utc(job.locked_at) != _as_utc(locked_at)
            or job.lease_expires_at is None
            or _as_utc(job.lease_expires_at) <= queue_now()
            or job.cancelled_at is not None
        ):
            self._session.rollback()
            raise LocalUploadUnavailable("local_upload_claim_lost")

    def flush(self, *args: Any, **kwargs: Any) -> Any:
        self._require_live_claim()
        return self._session.flush(*args, **kwargs)

    def commit(self) -> None:
        self._require_live_claim()
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


class _NullContext:
    def __enter__(self):
        return None

    def __exit__(self, *_args):
        return False


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def run_local_upload_job(payload: Mapping[str, Any]) -> dict[str, Any]:
    runtime = get_local_upload_runtime()
    staging_id = _validated_payload(payload)
    claim = current_execution_claim()
    if claim is None:
        raise LocalUploadUnavailable("missing_job_claim")
    if isinstance(claim[0], bool):
        raise LocalUploadUnavailable("invalid_job_claim")
    try:
        job_id = int(claim[0])
    except (TypeError, ValueError):
        raise LocalUploadUnavailable("invalid_job_claim") from None
    if job_id <= 0:
        raise LocalUploadUnavailable("invalid_job_claim")
    with runtime.session_factory() as session:
        loaded = runtime.lifecycle.load_for_processing(
            session, staging_id=staging_id, job_id=job_id, claim=claim,
        )
        if isinstance(loaded, FinalizedUpload):
            _commit(session, "local_upload_read_authorization_unavailable")
            if loaded.descriptor is not None:
                cleanup_finalized_upload(runtime, MaterializedUpload(
                    staging_id=loaded.staging_id, scope=loaded.scope,
                    display_name="recovery", mime_type="text/plain", checksum="0" * 64,
                    size=0, descriptor=loaded.descriptor, job_id=loaded.job_id,
                ), CleanupDecision(True))
                runtime.lifecycle.complete_cleanup(
                    session, scope=loaded.scope, staging_id=staging_id,
                    job_id=job_id, claim=claim,
                )
                _commit(session, "local_upload_cleanup_ack_unavailable")
            if loaded.outcome == "cancelled":
                return {"cancelled": True, "staging_id": staging_id}
            return {"staging_id": staging_id, **_validated_result(loaded.result or {})}
        record = _validated_materialization(
            loaded, staging_id=staging_id, job_id=job_id, runtime=runtime,
        )
        # Commit the live lease/source/version authorization before plaintext read.
        _commit(session, "local_upload_read_authorization_unavailable")
        try:
            content = b"".join(runtime.storage.read_chunks(record.descriptor, max_bytes=runtime.max_file_bytes))
            actual = hashlib.sha256(content).hexdigest()
            if len(content) != record.size or not hmac.compare_digest(actual, record.checksum):
                raise StagingIntegrityError("plaintext_integrity_mismatch")
            fenced_session = _LeaseFencedSession(session, claim)
            result = _validated_result(runtime.processor.process(
                fenced_session, record=record, content=content,
                operation_key=f"local-upload:{staging_id}",
            ))
        except LocalUploadCancelled:
            rollback = getattr(session, "rollback", None)
            if callable(rollback):
                rollback()
            decision = runtime.lifecycle.finalize(
                session, scope=record.scope, staging_id=staging_id, job_id=job_id,
                claim=claim, outcome="cancelled",
            )
            _commit(session, "local_upload_finalization_unavailable")
            cleanup_finalized_upload(runtime, record, decision)
            runtime.lifecycle.complete_cleanup(
                session, scope=record.scope, staging_id=staging_id,
                job_id=job_id, claim=claim,
            )
            _commit(session, "local_upload_cleanup_ack_unavailable")
            return {"cancelled": True, "staging_id": staging_id}
        except Exception:
            rollback = getattr(session, "rollback", None)
            if callable(rollback):
                rollback()
            decision = runtime.lifecycle.finalize(
                session, scope=record.scope, staging_id=staging_id, job_id=job_id,
                claim=claim, outcome="failed",
            )
            _commit(session, "local_upload_finalization_unavailable")
            cleanup_finalized_upload(runtime, record, decision)
            log.warning("Local upload job failed; error_type=processing_failure")
            raise
        # Business rows converge on operation_key and commit before lifecycle
        # finalization, so a crash can safely resume cleanup without reprocessing.
        _commit(fenced_session, "local_upload_processing_commit_unavailable")
        decision = runtime.lifecycle.finalize(
            session, scope=record.scope, staging_id=staging_id, job_id=job_id,
            claim=claim, outcome="completed", result=result,
        )
        if not decision.delete_ciphertext:
            raise LocalUploadStagingError("completed_cleanup_not_authorized")
        _commit(session, "local_upload_finalization_unavailable")
        cleanup_finalized_upload(runtime, record, decision)
        runtime.lifecycle.complete_cleanup(
            session, scope=record.scope, staging_id=staging_id,
            job_id=job_id, claim=claim,
        )
        _commit(session, "local_upload_cleanup_ack_unavailable")
        return {"staging_id": staging_id, **result}
