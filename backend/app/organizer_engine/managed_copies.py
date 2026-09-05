"""Durable, fail-closed lifecycle for system-created safe copies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
import hashlib
import json

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.integrations.storage import project_storage_connection, storage_for_project
from app.jobs.queue import current_execution_claim, utcnow
from app.models.audit_log import AuditLog
from app.models.job import BackgroundJob
from app.models.organizer import OrganizerSession
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.workspace import SourceFolder, WorkspaceSnapshot


ITEM_RECEIPT = "managed_copy_cleanup_item"
FINAL_RECEIPT = "managed_copy_cleanup_completed"
SCHEMA = "managed-copy-cleanup/v1"


@dataclass(frozen=True, slots=True)
class ManagedCopy:
    identity: str
    project_id: int
    provider: str
    connection_id: str | None
    connection_row_id: int
    folder_id: str
    source_revision: str
    session_id: int
    copy_folder_id: str


def canonical_provider(value: str) -> str:
    return "google_drive" if value == "google_workspace" else value


def managed_copy_identity(
    *, project_id: int, provider: str, connection_id: str | None,
    connection_row_id: int, folder_id: str, source_revision: str,
) -> str:
    payload = [
        str(project_id), canonical_provider(provider), connection_id or "",
        str(connection_row_id), folder_id, source_revision,
    ]
    return hashlib.sha256("\x1f".join(payload).encode("utf-8")).hexdigest()


def snapshot_copy_key(snapshot: WorkspaceSnapshot, source: SourceFolder) -> str:
    binding = dict((snapshot.analysis_result or {}).get("storage_binding") or {})
    if (
        int(binding.get("project_id") or -1) != snapshot.project_id
        or str(binding.get("folder_id") or "") != source.external_id
        or not binding.get("provider")
        or binding.get("connection_row_id") is None
    ):
        raise ValueError("managed_copy_binding_unavailable")
    identity = managed_copy_identity(
        project_id=snapshot.project_id,
        provider=str(binding["provider"]),
        connection_id=(str(binding["connection_id"]) if binding.get("connection_id") is not None else None),
        connection_row_id=int(binding["connection_row_id"]),
        folder_id=source.external_id,
        source_revision=str(snapshot.id),
    )
    return f"managed-{identity[:32]}"


def _audit_payloads(db: Session, project_id: int, action: str) -> list[dict]:
    rows = db.scalars(select(AuditLog).where(
        AuditLog.action == action,
        AuditLog.entity_type == "project",
        AuditLog.entity_id == project_id,
    ).order_by(AuditLog.id)).all()
    result: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row.details or "")
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("schema") == SCHEMA:
            result.append(payload)
    return result


def managed_copies(db: Session, project_id: int, *, include_cleaned: bool = False) -> tuple[ManagedCopy, ...]:
    """Return only copies with exact snapshot, session and storage-binding proof."""
    cleaned = {
        str(payload.get("identity"))
        for payload in _audit_payloads(db, project_id, ITEM_RECEIPT)
        if payload.get("identity")
    }
    snapshots = db.scalars(select(WorkspaceSnapshot).where(
        WorkspaceSnapshot.project_id == project_id,
    ).order_by(WorkspaceSnapshot.id)).all()
    found: dict[str, ManagedCopy] = {}
    used_copy_ids: dict[str, str] = {}
    # An object bound as any project's original is never a cleanup target.
    originals = set(db.scalars(select(SourceFolder.external_id)).all())
    originals.update(db.scalars(select(OrganizerSession.source_folder_id)).all())
    for snapshot in snapshots:
        result = dict(snapshot.analysis_result or {})
        binding = dict(result.get("storage_binding") or {})
        if result.get("mode") != "safe_copy" or not result.get("organizer_session_id"):
            continue
        session = db.get(OrganizerSession, int(result["organizer_session_id"]))
        source = db.get(SourceFolder, snapshot.source_folder_id)
        if session is None or source is None or session.project_id != project_id or source.project_id != project_id:
            continue
        copy_id = str(session.copy_folder_id or "")
        if (
            not copy_id or copy_id in {"manual", session.source_folder_id}
            or copy_id in originals
            or copy_id.startswith("virtual:")
            or session.source_folder_id != source.external_id
            or (result.get("copy_folder_id") and str(result["copy_folder_id"]) != copy_id)
            or int(binding.get("project_id") or -1) != project_id
            or str(binding.get("folder_id") or "") != source.external_id
            or not binding.get("provider")
            or canonical_provider(source.provider) != canonical_provider(str(binding.get("provider")))
            or binding.get("connection_row_id") is None
        ):
            continue
        identity = managed_copy_identity(
            project_id=project_id,
            provider=str(binding["provider"]),
            connection_id=(str(binding["connection_id"]) if binding.get("connection_id") is not None else None),
            connection_row_id=int(binding["connection_row_id"]),
            folder_id=source.external_id,
            source_revision=str(snapshot.id),
        )
        # One provider object cannot silently represent two managed identities.
        if copy_id in used_copy_ids and used_copy_ids[copy_id] != identity:
            raise ValueError("managed_copy_identity_conflict")
        used_copy_ids[copy_id] = identity
        item = ManagedCopy(
            identity=identity,
            project_id=project_id,
            provider=canonical_provider(str(binding["provider"])),
            connection_id=(str(binding["connection_id"]) if binding.get("connection_id") is not None else None),
            connection_row_id=int(binding["connection_row_id"]),
            folder_id=source.external_id,
            source_revision=str(snapshot.id),
            session_id=session.id,
            copy_folder_id=copy_id,
        )
        if include_cleaned or identity not in cleaned:
            found[identity] = item
    return tuple(found[key] for key in sorted(found))


def cleanup_version(items: tuple[ManagedCopy, ...]) -> str:
    material = "\x1e".join(f"{item.identity}:{item.copy_folder_id}" for item in items)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _require_cleanup_capability(provider: str) -> None:
    # Inspect the adapter class without constructing clients or resolving tokens.
    # storage_for_project can refresh credentials, which is already provider I/O.
    from app.integrations.yandex_disk import YandexDiskStorageAdapter
    from app.organizer_engine.drive import DriveClient
    adapter = {"google_drive": DriveClient, "yandex_disk": YandexDiskStorageAdapter}.get(canonical_provider(provider))
    if adapter is None or getattr(adapter, "supports_managed_copy_cleanup", False) is not True:
        raise ValueError("managed_copy_cleanup_capability_unavailable")


def _worker_guard(db: Session, payload: dict, claim: tuple, *, lock: bool = False) -> BackgroundJob:
    job_id, worker_id, attempt, locked_at = claim
    query = select(BackgroundJob).where(BackgroundJob.id == job_id).execution_options(populate_existing=True)
    job = db.scalar(query.with_for_update() if lock else query)
    if (
        job is None or job.kind != "workspace.safe_copy_cleanup" or job.payload != payload
        or job.status != "running" or job.worker_id != worker_id or job.attempts != attempt
        or job.locked_at is None or _utc(job.locked_at) != _utc(locked_at)
        or job.lease_expires_at is None or _utc(job.lease_expires_at) <= utcnow()
        or job.cancelled_at is not None or job.completed_at is not None
        or (job.result or {}).get("cancel_requested") or (job.result or {}).get("cancelled")
    ):
        raise ValueError("managed_copy_cleanup_worker_fence_lost")
    return job


def _context_guard(db: Session, payload: dict, claim: tuple, project_binding=None):
    # The project lock serializes cleanup commands, including the HTTP command's
    # inventory check. Release it after each receipt, never between effect and receipt.
    # Refresh ORM objects so checks cannot reuse a pre-effect identity map.
    db.expire_all()
    project_id = payload["project_id"]
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise ValueError("managed_copy_project_unavailable")
    binding = (project.organization_id, project.archived_at)
    if project_binding is not None and binding != project_binding:
        raise ValueError("managed_copy_project_changed")
    job = _worker_guard(db, payload, claim)
    # The existing enqueue key is the durable requester binding. Do not infer a
    # requester from whichever owner happens to remain on the project today.
    prefix = f"workspace.safe_copy_cleanup:{project_id}:"
    key = job.idempotency_key or ""
    actor, separator, command = key.removeprefix(prefix).partition(":")
    if not key.startswith(prefix) or not separator or not actor.isdecimal() or command != payload["command_key"]:
        raise ValueError("managed_copy_cleanup_worker_command_unbound")
    user = db.get(User, int(actor))
    role = db.scalar(select(ProjectMember.role).where(
        ProjectMember.project_id == project_id, ProjectMember.user_id == int(actor)))
    if user is None or (not user.is_admin and role != "owner"):
        raise ValueError("managed_copy_cleanup_owner_unavailable")
    # Older active commands win deterministically. A later command must recheck
    # the inventory after the older command commits; it cannot share its receipt.
    older = db.scalar(select(BackgroundJob.id).where(
        BackgroundJob.kind == "workspace.safe_copy_cleanup", BackgroundJob.id < job.id,
        BackgroundJob.status.in_(("queued", "running", "retrying")),
        BackgroundJob.payload["project_id"].as_integer() == project_id).limit(1))
    if older is not None:
        raise ValueError("managed_copy_cleanup_command_conflict")
    connection = project_storage_connection(project_id, db)
    if connection is None or connection.status != "connected":
        raise ValueError("managed_copy_connection_unavailable")
    return job, binding, connection


def _own_receipt(db: Session, receipt: dict, payload: dict, job_id: int) -> bool:
    if receipt.get("command_key") != payload["command_key"]:
        return False
    if receipt.get("job_id") is not None:
        if receipt["job_id"] != job_id:
            return False
    else:
        # Old receipts did not store the job ID. Accept them only if the durable
        # command resolves uniquely, including completed jobs and other actors.
        jobs = db.scalars(select(BackgroundJob.id).where(
            BackgroundJob.kind == "workspace.safe_copy_cleanup",
            BackgroundJob.payload["project_id"].as_integer() == payload["project_id"],
            BackgroundJob.payload["command_key"].as_string() == payload["command_key"])).all()
        if jobs != [job_id]:
            raise ValueError("managed_copy_cleanup_command_conflict")
    if receipt.get("cleanup_version", payload["cleanup_version"]) != payload["cleanup_version"]:
        raise ValueError("managed_copy_cleanup_version_conflict")
    return True


def _inventory_guard(db: Session, payload: dict, job_id: int, connection):
    receipts = _audit_payloads(db, payload["project_id"], ITEM_RECEIPT)
    other_cleaned = {row.get("identity") for row in receipts if not _own_receipt(db, row, payload, job_id)}
    cleaned = {row.get("identity") for row in receipts}
    items = tuple(item for item in managed_copies(db, payload["project_id"], include_cleaned=True)
                  if item.identity not in other_cleaned)
    if cleanup_version(items) != payload["cleanup_version"]:
        raise ValueError("managed_copy_cleanup_version_conflict")
    for item in items:
        if (canonical_provider(connection.provider) != item.provider
            or connection.id != item.connection_row_id or connection.connection_id != item.connection_id):
            raise ValueError("managed_copy_connection_changed")
    return items, cleaned


def _commit_receipt(db: Session, payload: dict, claim: tuple, details: dict, action: str):
    # Lock and recheck the exact attempt after the external call. The conditional
    # write holds the queue row through receipt commit, fencing cancellation,
    # recovery and reassignment in that transaction. No remote atomicity is claimed.
    job = _worker_guard(db, payload, claim, lock=True)
    job_id, worker_id, attempt, locked_at = claim
    changed = db.execute(update(BackgroundJob).where(
        BackgroundJob.id == job_id, BackgroundJob.worker_id == worker_id,
        BackgroundJob.status == "running", BackgroundJob.attempts == attempt,
        BackgroundJob.locked_at == locked_at, BackgroundJob.lease_expires_at > utcnow(),
        BackgroundJob.cancelled_at.is_(None), BackgroundJob.completed_at.is_(None),
        func.coalesce(BackgroundJob.result["cancel_requested"].as_boolean(), False).is_(False),
        func.coalesce(BackgroundJob.result["cancelled"].as_boolean(), False).is_(False),
    ).values(updated_at=BackgroundJob.updated_at).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise ValueError("managed_copy_cleanup_worker_fence_lost")
    db.add(AuditLog(action=action, entity_type="project", entity_id=payload["project_id"],
        details=json.dumps({"schema": SCHEMA, "command_key": payload["command_key"],
            "cleanup_version": payload["cleanup_version"], "job_id": job.id,
            "attempt": attempt, **details}, sort_keys=True, separators=(",", ":"))))
    db.commit()


def run_managed_copy_cleanup(payload: dict) -> dict:
    allowed = {"project_id", "cleanup_version", "command_key"}
    if set(payload) != allowed:
        raise ValueError("invalid_managed_copy_cleanup_payload")
    project_id = int(payload["project_id"])
    expected_version = str(payload["cleanup_version"])
    command_key = str(payload["command_key"])
    if len(command_key) < 8 or len(command_key) > 200:
        raise ValueError("invalid_managed_copy_cleanup_command")
    claim = current_execution_claim()
    if claim is None or len(claim) != 4 or claim[2] is None or claim[3] is None:
        raise ValueError("managed_copy_cleanup_worker_claim_required")
    with SessionLocal() as db:
        # Check before taking a potentially contended project lock as well as after.
        _worker_guard(db, payload, claim)
        job, project_binding, connection = _context_guard(db, payload, claim)
        all_items, cleaned = _inventory_guard(db, payload, job.id, connection)
        for prior in _audit_payloads(db, project_id, FINAL_RECEIPT):
            if _own_receipt(db, prior, payload, job.id):
                if (prior.get("cleanup_version") != expected_version
                    or type(prior.get("trashed")) is not int or prior["trashed"] != len(all_items)
                    or prior.get("originals_affected") is not False
                    or any(item.identity not in cleaned for item in all_items)):
                    raise ValueError("managed_copy_cleanup_version_conflict")
                return {"project_id": project_id, "trashed": int(prior["trashed"]),
                    "message": "Копии удалены, можете архивировать проект",
                    "originals_affected": False, "idempotent_replay": True}
        while True:
            job, _, connection = _context_guard(db, payload, claim, project_binding)
            current, cleaned = _inventory_guard(db, payload, job.id, connection)
            pending = [item for item in current if item.identity not in cleaned]
            if not pending:
                break
            item = pending[0]
            _require_cleanup_capability(connection.provider)
            drive = storage_for_project(project_id, db)
            if not bool(getattr(drive, "supports_managed_copy_cleanup", False)):
                raise ValueError("managed_copy_cleanup_capability_unavailable")
            # Adapter construction may refresh credentials and commit. Reacquire
            # the lock and current binding immediately before the provider effect.
            job, _, connection = _context_guard(db, payload, claim, project_binding)
            current, cleaned = _inventory_guard(db, payload, job.id, connection)
            if item not in current or item.identity in cleaned:
                raise ValueError("managed_copy_cleanup_version_conflict")
            # Inventory and authorization reads may themselves outlive the lease.
            _worker_guard(db, payload, claim)
            drive.trash_safe_copy(item.copy_folder_id)
            job, _, connection = _context_guard(db, payload, claim, project_binding)
            _inventory_guard(db, payload, job.id, connection)
            _commit_receipt(db, payload, claim, {"identity": item.identity,
                "provider": item.provider, "source_revision": item.source_revision}, ITEM_RECEIPT)
        total = len(all_items)
        _commit_receipt(db, payload, claim, {"trashed": total, "originals_affected": False}, FINAL_RECEIPT)
        return {
            "project_id": project_id,
            "trashed": total,
            "message": "Копии удалены, можете архивировать проект",
            "originals_affected": False,
            "idempotent_replay": False,
        }
