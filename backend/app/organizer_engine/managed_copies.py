"""Durable, fail-closed lifecycle for system-created safe copies."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.integrations.storage import project_storage_connection, storage_for_project
from app.models.audit_log import AuditLog
from app.models.organizer import OrganizerSession
from app.models.project import Project
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


def _final_receipt(db: Session, project_id: int, command_key: str) -> dict | None:
    for payload in _audit_payloads(db, project_id, FINAL_RECEIPT):
        if payload.get("command_key") == command_key:
            return payload
    return None


def run_managed_copy_cleanup(payload: dict) -> dict:
    allowed = {"project_id", "cleanup_version", "command_key"}
    if set(payload) != allowed:
        raise ValueError("invalid_managed_copy_cleanup_payload")
    project_id = int(payload["project_id"])
    expected_version = str(payload["cleanup_version"])
    command_key = str(payload["command_key"])
    if len(command_key) < 8 or len(command_key) > 200:
        raise ValueError("invalid_managed_copy_cleanup_command")

    with SessionLocal() as db:
        prior = _final_receipt(db, project_id, command_key)
        if prior is not None:
            return {
                "project_id": project_id,
                "trashed": int(prior["trashed"]),
                "message": "Копии удалены, можете архивировать проект",
                "originals_affected": False,
                "idempotent_replay": True,
            }
        project = db.get(Project, project_id)
        if project is None:
            raise ValueError("managed_copy_project_unavailable")
        receipts = _audit_payloads(db, project_id, ITEM_RECEIPT)
        previously_cleaned = {
            str(row.get("identity")) for row in receipts
            if row.get("command_key") != command_key
        }
        all_items = tuple(
            item for item in managed_copies(db, project_id, include_cleaned=True)
            if item.identity not in previously_cleaned
        )
        if cleanup_version(all_items) != expected_version:
            raise ValueError("managed_copy_cleanup_version_conflict")
        items = managed_copies(db, project_id)
        connection = project_storage_connection(project_id, db)
        if connection is None:
            raise ValueError("managed_copy_connection_unavailable")
        for item in items:
            if (
                canonical_provider(connection.provider) != item.provider
                or connection.id != item.connection_row_id
                or connection.connection_id != item.connection_id
            ):
                raise ValueError("managed_copy_connection_changed")
        drive = storage_for_project(project_id, db) if items else None
        if items and not bool(getattr(drive, "supports_managed_copy_cleanup", False)):
            # A provider adapter must explicitly prove that retry/reconciliation of
            # its delete operation is safe.  Missing capability is a hard deny:
            # otherwise a worker crash could turn a retry into an untracked effect.
            raise ValueError("managed_copy_cleanup_capability_unavailable")

        already_cleaned = {
            str(row.get("identity")) for row in _audit_payloads(db, project_id, ITEM_RECEIPT)
        }
        trashed = 0
        for item in items:
            if item.identity in already_cleaned:
                continue
            drive.trash_safe_copy(item.copy_folder_id)
            db.add(AuditLog(
                action=ITEM_RECEIPT,
                entity_type="project",
                entity_id=project_id,
                details=json.dumps({
                    "schema": SCHEMA,
                    "identity": item.identity,
                    "command_key": command_key,
                    "provider": item.provider,
                    "source_revision": item.source_revision,
                }, sort_keys=True, separators=(",", ":")),
            ))
            db.commit()
            already_cleaned.add(item.identity)
            trashed += 1

        total = len(all_items)
        db.add(AuditLog(
            action=FINAL_RECEIPT,
            entity_type="project",
            entity_id=project_id,
            details=json.dumps({
                "schema": SCHEMA,
                "command_key": command_key,
                "cleanup_version": expected_version,
                "trashed": total,
                "originals_affected": False,
            }, sort_keys=True, separators=(",", ":")),
        ))
        db.commit()
        return {
            "project_id": project_id,
            "trashed": total,
            "message": "Копии удалены, можете архивировать проект",
            "originals_affected": False,
            "idempotent_replay": False,
        }
