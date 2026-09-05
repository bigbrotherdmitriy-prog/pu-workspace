from datetime import datetime, timezone
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.integrations.storage import project_storage_connection, storage_for_project, validate_storage_locator
from app.core.auth import require_project_role, require_user
from app.database import SessionLocal, get_db
from app.models.project import Project
from app.models.user import User
from app.models.drive_connection import DriveConnection
from app.models.google_token import GoogleOAuthToken
from app.models.job import BackgroundJob
from app.models.workspace import SourceFolder, VirtualNode, WorkspaceSnapshot
from app.organizer_engine.drive import DriveClient
from app.organizer_engine.repository import OrganizerRepository
from app.organizer_engine.planner import build_proposal
from app.organizer_engine.types import DriveFile
from app.organizer_engine.content import extract_text
from app.document_engine import index_documents
from app.task_engine import create_tasks_from_files
from app.response_engine import create_response_drafts
from app.governance_engine import create_governance_items


router = APIRouter(prefix="/projects", tags=["virtual-workspace"])


class ManagedWorkspaceCreate(BaseModel):
    parent_folder_id: str = Field(default="root", min_length=1, max_length=255)


MANAGED_PROJECT_STRUCTURE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("00_Управление", ("01_Совещания", "02_Решения", "03_Риски")),
    ("01_Договоры", ("01_Заказчик", "02_Субподрядчики", "03_Поставщики")),
    ("02_Проектная_документация", ()),
    ("03_ГПР", ()),
    ("04_Финансы", ("01_Бюджет", "02_ДДС", "03_Счета_и_оплаты")),
    ("05_Переписка", ("01_Входящие", "02_Исходящие")),
    ("06_Исполнение", ("01_Акты", "02_Фото", "03_Исполнительная_документация")),
    ("99_Архив", ()),
)


def _ensure_child_folder(drive: DriveClient, parent_id: str, name: str) -> tuple[str, bool]:
    existing = next((item for item in drive.list_children(parent_id) if item.is_folder and item.name == name), None)
    return (existing.id, False) if existing else (drive.create_folder(name, parent_id), True)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _drive_folder_breadcrumb(service, folder_id: str) -> list[dict[str, str]]:
    """Return a bounded root-to-folder path without mutating Drive."""
    if folder_id == "root":
        return [{"id": "root", "name": "Мой диск"}]
    trail: list[dict[str, str]] = []
    current = folder_id
    visited: set[str] = set()
    for _ in range(50):
        if current == "root" or current in visited:
            break
        visited.add(current)
        item = service.files().get(
            fileId=current,
            fields="id,name,parents",
            supportsAllDrives=True,
        ).execute()
        trail.append({"id": item["id"], "name": item.get("name") or "Папка"})
        current = (item.get("parents") or ["root"])[0]
    trail.reverse()
    return [{"id": "root", "name": "Мой диск"}, *trail]


def _storage_breadcrumb(adapter, folder_id: str) -> list[dict[str, str]]:
    root = ("app:/" if folder_id.startswith("app:/") else "disk:/") if adapter.provider == "yandex_disk" else "root"
    if folder_id in {root, "root", "/"}:
        return [{"id": root, "name": "Яндекс Диск" if adapter.provider == "yandex_disk" else "Мой диск"}]
    trail: list[dict[str, str]] = []
    current = folder_id
    visited: set[str] = set()
    for _ in range(100):
        if not current or current == root:
            break
        if current in visited:
            raise HTTPException(409, "Storage folder ancestry contains a cycle")
        visited.add(current)
        item = adapter.get_object(current)
        trail.append({"id": item.id, "name": item.name})
        current = item.parent_id
    else:
        raise HTTPException(422, "Storage folder ancestry exceeds 100 levels")
    trail.reverse()
    return [{"id": root, "name": "Яндекс Диск" if adapter.provider == "yandex_disk" else "Мой диск"}, *trail]


def _selected_connection(project_id, db, provider=None, connection_id=None, *, allow_google_oauth=False):
    if db.get(Project, project_id) is None:
        raise HTTPException(404, "Project not found")
    connection = project_storage_connection(project_id, db)
    if connection is None and allow_google_oauth:
        # Google OAuth historically stores only a project-scoped token. Browsing
        # must remain read-only; persist a connection only on explicit confirmation.
        token = db.scalar(select(GoogleOAuthToken).where(GoogleOAuthToken.project_id == project_id))
        if token is not None and token.access_token:
            connection = DriveConnection(project_id=project_id, provider="google_drive",
                                         connection_id=f"google-token:{token.id}", account_email="",
                                         root_folder_id="root", status="connected")
    if connection is None or connection.status == "disconnected":
        raise HTTPException(409, "Configure storage for this project before selecting a folder")
    canonical = lambda value: "google_drive" if value == "google_workspace" else value
    if provider is not None and canonical(provider) != canonical(connection.provider):
        raise HTTPException(409, "Storage provider changed; reopen folder selection")
    if connection_id is not None and connection_id != connection.connection_id:
        raise HTTPException(409, "Storage connection changed; reopen folder selection")
    return connection


def _binding(connection, external_id):
    return {"project_id": connection.project_id, "provider": connection.provider,
            "connection_id": connection.connection_id, "connection_row_id": connection.id,
            "folder_id": external_id}


def _locked_snapshot(db, snapshot_id):
    return db.scalar(select(WorkspaceSnapshot).where(WorkspaceSnapshot.id == snapshot_id).with_for_update())


def _active_snapshot_job(db, snapshot_id, project_id):
    return db.scalar(select(BackgroundJob).where(
        BackgroundJob.kind.in_(("workspace.snapshot", "workspace.analysis", "workspace.safe_copy")),
        BackgroundJob.status.in_(("queued", "running", "retrying")),
        BackgroundJob.payload["snapshot_id"].as_integer() == snapshot_id,
        BackgroundJob.payload["project_id"].as_integer() == project_id,
    ).order_by(BackgroundJob.id).limit(1))


def _require_idle_snapshot(db, snapshot_id, project_id):
    if _active_snapshot_job(db, snapshot_id, project_id) is not None:
        raise HTTPException(409, "Snapshot already has queued, running or retrying work; wait for completion")


def _validate_snapshot_target(db, snapshot, project_id, external_id=None):
    if snapshot is None or snapshot.project_id != project_id:
        raise HTTPException(409, "Snapshot does not belong to the requested project")
    source = db.get(SourceFolder, snapshot.source_folder_id)
    if source is None or source.project_id != project_id or (external_id is not None and source.external_id != external_id):
        raise HTTPException(409, "Snapshot source does not match the requested project and folder")
    pinned = (snapshot.analysis_result or {}).get("storage_binding")
    if pinned:
        connection = _selected_connection(project_id, db)
        canonical = lambda value: "google_drive" if value == "google_workspace" else value
        if (pinned != _binding(connection, source.external_id)
                or canonical(source.provider) != canonical(connection.provider)):
            raise HTTPException(409, "Snapshot storage connection changed; reconnect the original connection")
    return source


def _populate_content(adapter, items: list[DriveFile]) -> tuple[int, int]:
    if hasattr(adapter, "populate_content"):
        return adapter.populate_content(items)
    extracted = failed = 0
    for item in items:
        if item.is_folder:
            continue
        try:
            raw, mime = adapter.read_bytes(item.id, 4 * 1024 * 1024)
            item.content_text = extract_text(raw, mime, item.name)
            extracted += int(bool(item.content_text))
        except Exception:
            failed += 1
    return extracted, failed


def _run_safe_copy_pipeline(snapshot_id: int, session_id: int, project_id: int, source_folder_id: str, raise_errors: bool = False) -> None:
    """Organize one explicitly selected folder copy and mirror its result on the snapshot."""
    from app.organizer import _scan_worker

    with SessionLocal() as check_db:
        _validate_snapshot_target(check_db, check_db.get(WorkspaceSnapshot, snapshot_id), project_id, source_folder_id)
        session = OrganizerRepository(check_db).get_session(session_id)
        if session is None or session["project_id"] != project_id or session["source_folder_id"] != source_folder_id:
            raise HTTPException(409, "Organizer session does not match the snapshot target")
    _scan_worker(session_id, project_id, source_folder_id, auto_apply=True, raise_errors=raise_errors)
    db = SessionLocal()
    try:
        snapshot = db.get(WorkspaceSnapshot, snapshot_id)
        repo = OrganizerRepository(db)
        session = repo.get_session(session_id)
        if snapshot is None or session is None:
            return
        proposal = repo.proposal_for_session(session_id)
        succeeded = session["status"] in {"proposed", "applied"}
        snapshot.analysis_status = "ready" if succeeded else "failed"
        snapshot.analysis_error = None if succeeded else "Safe-copy organization failed. Verify the selected connection and retry."
        snapshot.analysis_result = {
            "storage_binding": (snapshot.analysis_result or {}).get("storage_binding"),
            "mode": "safe_copy",
            "organizer_session_id": session_id,
            "proposal_id": proposal["id"] if proposal else None,
            "status": session["status"],
            "copy_folder_id": session["copy_folder_id"],
            "copy_folder_name": session["copy_folder_name"],
            "source_item_count": session["source_item_count"],
            "copy_item_count": session["copy_item_count"],
            "originals_modified": False,
        }
        db.commit()
    finally:
        db.close()


def _start_safe_copy_pipeline(snapshot_id: int, project_id: int, source_folder_id: str, source_name: str) -> int | None:
    """Idempotently queue copy creation, content analysis and high-confidence renaming."""
    db = SessionLocal()
    try:
        snapshot = _locked_snapshot(db, snapshot_id)
        if snapshot is None:
            return None
        _validate_snapshot_target(db, snapshot, project_id, source_folder_id)
        queued = db.scalar(select(BackgroundJob).where(
            BackgroundJob.idempotency_key == f"workspace.safe_copy:{snapshot_id}"))
        if queued is not None:
            payload = queued.payload or {}
            session_id = payload.get("session_id")
            session = OrganizerRepository(db).get_session(session_id) if session_id is not None else None
            if (payload.get("project_id") != project_id or payload.get("source_folder_id") != source_folder_id
                    or session is None or session["project_id"] != project_id or session["source_folder_id"] != source_folder_id):
                raise HTTPException(409, "Existing safe-copy job does not match the snapshot target")
            return int(session_id)
        existing = snapshot.analysis_result or {}
        if existing.get("organizer_session_id"):
            session_id = int(existing["organizer_session_id"])
        else:
            repo = OrganizerRepository(db)
            session_id = repo.create_session(project_id, source_folder_id, source_name)
            snapshot.analysis_status = "analyzing"
            snapshot.analysis_error = None
            snapshot.analysis_result = {
                "storage_binding": existing.get("storage_binding"),
                "mode": "safe_copy",
                "organizer_session_id": session_id,
                "status": "queued",
                "originals_modified": False,
            }
        from app.jobs.queue import enqueue
        enqueue(db, "workspace.safe_copy", {
            "snapshot_id": snapshot_id, "session_id": session_id,
            "project_id": project_id, "source_folder_id": source_folder_id,
        }, idempotency_key=f"workspace.safe_copy:{snapshot_id}")
    finally:
        db.close()
    return session_id


def _build_snapshot(snapshot_id: int, project_id: int, external_id: str, raise_errors: bool = False) -> None:
    db = SessionLocal()
    target_valid = False
    try:
        snapshot = db.get(WorkspaceSnapshot, snapshot_id)
        # Reject a forged target before changing even the failure state of another project.
        if snapshot is None or snapshot.project_id != project_id:
            raise HTTPException(409, "Snapshot does not belong to the requested project")
        target_valid = True
        source = _validate_snapshot_target(db, snapshot, project_id, external_id)
        if snapshot.status != "ready":
            drive = storage_for_project(project_id, db)
            source_meta = drive.get_object(external_id)
            items = drive.walk_tree(external_id)
            db.add(VirtualNode(
                snapshot_id=snapshot.id, external_id=source_meta.id,
                parent_external_id=source_meta.parent_id or None, name=source_meta.name,
                mime_type=source_meta.mime_type, node_type="folder", size_bytes=source_meta.size,
                checksum=source_meta.md5_checksum, source_modified_at=_parse_time(source_meta.modified_time),
            ))
            db.add_all(VirtualNode(
                snapshot_id=snapshot.id, external_id=item.id,
                parent_external_id=item.parent_id or None, name=item.name, mime_type=item.mime_type,
                node_type="folder" if item.is_folder else "file", size_bytes=item.size,
                checksum=item.md5_checksum, source_modified_at=_parse_time(item.modified_time),
            ) for item in items)
            snapshot.item_count = len(items) + 1
            snapshot.status = "ready"
            snapshot.completed_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as exc:
        db.rollback()
        failed = db.get(WorkspaceSnapshot, snapshot_id)
        if failed is not None and target_valid:
            failed.status = "dead_letter" if failed.retry_count >= 2 else "failed"
            failed.error_message = "Storage snapshot failed. Verify the selected project, connection and folder, then retry."
            db.commit()
        if raise_errors:
            raise
    finally:
        db.close()


def recover_incomplete_snapshots() -> int:
    """Resume metadata-only snapshot jobs interrupted by a process restart."""
    db = SessionLocal()
    try:
        rows = db.execute(
            select(WorkspaceSnapshot.id, WorkspaceSnapshot.project_id, SourceFolder.external_id)
            .join(SourceFolder, SourceFolder.id == WorkspaceSnapshot.source_folder_id)
            .where(WorkspaceSnapshot.status == "building")
            .order_by(WorkspaceSnapshot.id)
        ).all()
    finally:
        db.close()
    for snapshot_id, project_id, external_id in rows:
        _enqueue_snapshot(snapshot_id, project_id, external_id)
    return len(rows)


def recover_incomplete_analyses() -> int:
    db = SessionLocal()
    try:
        snapshots = db.scalars(select(WorkspaceSnapshot).where(
            WorkspaceSnapshot.analysis_status == "analyzing",
        )).all()
        # Safe-copy sessions are resumed by recover_incomplete_scans(). Starting
        # the legacy virtual analyzer as well would duplicate documents/tasks.
        rows = [
            (snapshot.id, snapshot.project_id)
            for snapshot in snapshots
            if (snapshot.analysis_result or {}).get("mode") != "safe_copy"
        ]
    finally:
        db.close()
    for snapshot_id, project_id in rows:
        _enqueue_analysis(snapshot_id, project_id)
    return len(rows)


@router.post("/{project_id}/managed-workspace")
def create_managed_workspace(
    project_id: int,
    payload: ManagedWorkspaceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Create an idempotent permanent project tree for a new project."""
    require_project_role(db, user, project_id, "manager")
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    drive = storage_for_project(project_id, db)
    if not isinstance(drive, DriveClient):
        raise HTTPException(422, "Managed workspace templates are currently available for Google Drive only")
    source = db.scalar(select(SourceFolder).where(
        SourceFolder.project_id == project_id,
        SourceFolder.provider == "google_drive_managed",
    ).order_by(SourceFolder.id.desc()))
    created_count = 0
    if source is None:
        root_id = drive.create_folder(f"PU Workspace — {project.name}", payload.parent_folder_id)
        for row in db.scalars(select(SourceFolder).where(SourceFolder.project_id == project_id)):
            row.is_primary = False
        source = SourceFolder(
            project_id=project_id, external_id=root_id, name=f"PU Workspace — {project.name}",
            provider="google_drive_managed", is_primary=True,
        )
        db.add(source); db.commit(); db.refresh(source)
        created_count += 1
    for top_name, child_names in MANAGED_PROJECT_STRUCTURE:
        top_id, created = _ensure_child_folder(drive, source.external_id, top_name)
        created_count += int(created)
        for child_name in child_names:
            _, child_created = _ensure_child_folder(drive, top_id, child_name)
            created_count += int(child_created)
    snapshot = db.scalar(select(WorkspaceSnapshot).where(
        WorkspaceSnapshot.project_id == project_id,
        WorkspaceSnapshot.source_folder_id == source.id,
    ).order_by(WorkspaceSnapshot.id.desc()))
    if snapshot is None:
        snapshot = WorkspaceSnapshot(
            project_id=project_id, source_folder_id=source.id, status="ready",
            item_count=sum(1 + len(children) for _, children in MANAGED_PROJECT_STRUCTURE),
            analysis_status="ready", completed_at=datetime.now(timezone.utc),
            analysis_result={"mode": "managed_template", "originals_modified": False},
        )
        db.add(snapshot)
    db.commit()
    return {
        "project_id": project_id, "folder_id": source.external_id, "folder_name": source.name,
        "created_folders": created_count, "structure_folders": snapshot.item_count,
        "mode": "managed_template", "originals_modified": False,
    }


def _analyze_snapshot_worker(snapshot_id: int, project_id: int, raise_errors: bool = False) -> None:
    db = SessionLocal()
    try:
        snapshot = db.get(WorkspaceSnapshot, snapshot_id)
        if snapshot is None or snapshot.project_id != project_id or snapshot.status != "ready":
            raise ValueError("A ready snapshot is required")
        source = db.get(SourceFolder, snapshot.source_folder_id)
        if source is None:
            raise ValueError("Snapshot source folder is missing")
        _validate_snapshot_target(db, snapshot, project_id, source.external_id)
        if snapshot.analysis_status == "ready":
            return
        repo = OrganizerRepository(db)
        nodes = db.scalars(select(VirtualNode).where(VirtualNode.snapshot_id == snapshot_id).order_by(VirtualNode.id)).all()
        files = [DriveFile(
            id=node.external_id, name=node.name, mime_type=node.mime_type,
            parent_id=node.parent_external_id or "", md5_checksum=node.checksum,
            size=node.size_bytes, modified_time=node.source_modified_at.isoformat() if node.source_modified_at else None,
        ) for node in nodes]
        drive = storage_for_project(project_id, db)
        extracted, extraction_failed = _populate_content(drive, files)
        project = db.get(Project, project_id)
        session_id = repo.create_session(project_id, source.external_id, source.name)
        repo.update_session(session_id, copy_folder_id=f"virtual:{snapshot_id}", copy_folder_name=f"Виртуальный снимок #{snapshot_id}", source_item_count=len(files), copy_item_count=0, status="analyzing", progress=70)
        indexed = index_documents(db, project_id, files, f"{drive.provider}_snapshot")
        items = build_proposal(files, project_name=project.name if project else None, confirmed_rules=repo.confirmed_rules())
        tasks = create_tasks_from_files(db, project_id, session_id, files)
        google_synced = calendar_synced = 0
        drafts = create_response_drafts(db, project_id, session_id, files)
        risks, decisions = create_governance_items(db, project_id, files)
        proposal_id = repo.create_proposal(project_id, session_id, source.name, source.external_id, f"virtual:{snapshot_id}")
        repo.save_items(proposal_id, items)
        repo.update_session(session_id, status="proposed", progress=100)
        analysis_result = {
            "storage_binding": (snapshot.analysis_result or {}).get("storage_binding"),
            "status": "ready", "documents": len(indexed), "text_extracted": extracted,
            "extraction_failed": extraction_failed, "tasks": len(tasks),
            "google_tasks_synced": google_synced, "calendar_synced": calendar_synced,
            "drafts": len(drafts), "risks": len(risks), "decisions": len(decisions),
        }
        snapshot.analysis_status = "ready"
        snapshot.analysis_result = analysis_result
        snapshot.analysis_error = None
        db.commit()
    except Exception as exc:
        db.rollback()
        failed = db.get(WorkspaceSnapshot, snapshot_id)
        if failed is not None and failed.project_id == project_id:
            failed.analysis_status = "dead_letter" if failed.analysis_retry_count >= 2 else "failed"
            failed.analysis_error = "Storage analysis failed. Verify the selected project and connection, then retry."
            db.commit()
        if raise_errors:
            raise
    finally:
        db.close()


def _enqueue_snapshot(snapshot_id: int, project_id: int, external_id: str, *, force: bool = False) -> int:
    from app.jobs.queue import enqueue
    with SessionLocal() as db:
        snapshot = _locked_snapshot(db, snapshot_id)
        _validate_snapshot_target(db, snapshot, project_id, external_id)
        active = _active_snapshot_job(db, snapshot_id, project_id)
        if active is not None:
            return active.id
        job = enqueue(db, "workspace.snapshot", {
            "snapshot_id": snapshot_id, "project_id": project_id, "external_id": external_id,
        }, idempotency_key=None if force else f"workspace.snapshot:{snapshot_id}")
        return job.id


def _enqueue_analysis(snapshot_id: int, project_id: int, *, force: bool = False) -> int:
    from app.jobs.queue import enqueue
    with SessionLocal() as db:
        snapshot = _locked_snapshot(db, snapshot_id)
        _validate_snapshot_target(db, snapshot, project_id)
        active = _active_snapshot_job(db, snapshot_id, project_id)
        if active is not None:
            return active.id
        job = enqueue(db, "workspace.analysis", {
            "snapshot_id": snapshot_id, "project_id": project_id,
        }, idempotency_key=None if force else f"workspace.analysis:{snapshot_id}")
        return job.id


@router.get("/{project_id}/source-folders/discover")
def discover_source_folders(
    project_id: int,
    folder_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    provider: str | None = None,
    connection_id: str | None = None,
):
    """Browse the selected provider at any depth for independent snapshot queues."""
    require_project_role(db, user, project_id, "viewer")
    connection = _selected_connection(project_id, db, provider, connection_id, allow_google_oauth=True)
    folder_id = connection.root_folder_id if folder_id is None else folder_id
    if connection.provider == "yandex_disk" and folder_id == "root":
        folder_id = "disk:/"  # Explicit legacy navigation request, never confirmation.
    validate_storage_locator(connection.provider, folder_id)
    adapter = storage_for_project(project_id, db)
    provider_folders = [item for item in adapter.list_children(folder_id) if item.is_folder]
    sources = list(db.scalars(select(SourceFolder).where(SourceFolder.project_id == project_id, SourceFolder.provider == adapter.provider)))
    source_by_external = {row.external_id: row for row in sources}
    latest_by_source: dict[int, WorkspaceSnapshot] = {}
    for snapshot in db.scalars(select(WorkspaceSnapshot).where(WorkspaceSnapshot.project_id == project_id).order_by(WorkspaceSnapshot.id.desc())):
        latest_by_source.setdefault(snapshot.source_folder_id, snapshot)
    analyzed_snapshot_ids = {
        int(value.removeprefix("virtual:"))
        for value in db.scalars(
            text("SELECT copy_folder_id FROM organizer_proposals WHERE project_id=:project_id AND copy_folder_id LIKE 'virtual:%'"),
            {"project_id": project_id},
        )
        if value and value.removeprefix("virtual:").isdigit()
    }
    snapshot_jobs: dict[int, BackgroundJob] = {}
    jobs = db.scalars(select(BackgroundJob).where(
        BackgroundJob.kind.in_(("workspace.snapshot", "workspace.analysis", "workspace.safe_copy")),
        BackgroundJob.payload["project_id"].as_integer() == project_id,
    ).order_by(BackgroundJob.id.desc()).limit(1500)).all()
    for job in jobs:
        snapshot_id = (job.payload or {}).get("snapshot_id")
        if isinstance(snapshot_id, int):
            snapshot_jobs.setdefault(snapshot_id, job)
    folders = []
    for item in provider_folders:
        source = source_by_external.get(item.id)
        snapshot = latest_by_source.get(source.id) if source else None
        job = snapshot_jobs.get(snapshot.id) if snapshot else None
        folders.append({"id": item.id, "name": item.name, "modifiedTime": item.modified_time, "provider": adapter.provider, "registered": source is not None,
            "is_primary": bool(source.is_primary) if source else False,
            "snapshot_id": snapshot.id if snapshot else None,
            "snapshot_status": snapshot.status if snapshot else None,
            "item_count": snapshot.item_count if snapshot else None,
            "analyzed": snapshot.id in analyzed_snapshot_ids if snapshot else False,
            "analysis_status": snapshot.analysis_status if snapshot else None,
            "analysis_result": snapshot.analysis_result if snapshot else None,
            "analysis_error": snapshot.analysis_error if snapshot else None,
            "job_id": job.id if job else None,
            "job_status": job.status if job else None,
            "job_progress": job.progress if job else None})
    return {
        "project_id": project_id,
        "connection_id": connection.connection_id,
        "connection_row_id": connection.id,
        "folder_id": folder_id,
        "provider": adapter.provider,
        "breadcrumbs": _storage_breadcrumb(adapter, folder_id),
        "folders": folders,
    }


@router.post("/{project_id}/source-folders/{external_id:path}/primary")
def set_primary_source_folder(
    project_id: int,
    external_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    source = db.scalar(select(SourceFolder).where(SourceFolder.project_id == project_id, SourceFolder.external_id == external_id))
    if source is None:
        raise HTTPException(404, "Source folder is not registered")
    for row in db.scalars(select(SourceFolder).where(SourceFolder.project_id == project_id)):
        row.is_primary = row.id == source.id
    db.commit()
    return {"id": source.id, "external_id": source.external_id, "name": source.name, "is_primary": True}


@router.post("/{project_id}/source-folders/{external_id:path}/snapshot-queue")
def queue_workspace_snapshot(
    project_id: int,
    external_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    provider: str | None = None,
    connection_id: str | None = None,
    refresh: bool = Query(False),
):
    require_project_role(db, user, project_id, "manager")
    # Serialize confirmations for a project on PostgreSQL, including source creation.
    db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    connection = _selected_connection(project_id, db, provider, connection_id, allow_google_oauth=True)
    validate_storage_locator(connection.provider, external_id)
    source = db.scalar(select(SourceFolder).where(SourceFolder.project_id == project_id, SourceFolder.external_id == external_id))
    selected_provider = "google_drive" if connection.provider == "google_workspace" else connection.provider
    if source is not None and source.provider != selected_provider:
        raise HTTPException(409, "Folder locator is already registered for a different provider")
    active = db.scalar(select(WorkspaceSnapshot).where(
        WorkspaceSnapshot.source_folder_id == source.id,
    ).order_by(WorkspaceSnapshot.id.desc())) if source else None
    from app.jobs.queue import enqueue
    from app.models.job import BackgroundJob
    def response(snapshot, job_id, repeated):
        return {"id": snapshot.id, "status": snapshot.status, "source_folder": source.name,
                "already_queued": repeated, "job_id": job_id, **_binding(connection, external_id)}
    active_job = _active_snapshot_job(db, active.id, project_id) if active else None
    # Ordinary retries remain idempotent. A deliberate refresh may supersede a
    # completed metadata snapshot so newly added or changed provider objects are
    # observed, but it never creates concurrent work for the same source.
    reuse_active = bool(active) and (
        not refresh or active.status != "ready" or active_job is not None
    )
    if reuse_active:
        _validate_snapshot_target(db, active, project_id, external_id)
        job = active_job or db.scalar(select(BackgroundJob).where(
            BackgroundJob.idempotency_key == f"workspace.snapshot:{active.id}"
        ))
        if job is None and active.status == "building":
            job = enqueue(db, "workspace.snapshot", {
                "snapshot_id": active.id, "project_id": project_id, "external_id": external_id,
            }, idempotency_key=f"workspace.snapshot:{active.id}")
        connection.root_folder_id = external_id
        connection.root_display_name = source.name
        db.commit()
        return response(active, job.id if job else None, True)
    drive = storage_for_project(project_id, db)
    source_meta = drive.get_object(external_id)
    if not source_meta.is_folder:
        raise HTTPException(422, "Source object is not a folder")
    if source is None:
        has_source = db.scalar(select(SourceFolder.id).where(SourceFolder.project_id == project_id).limit(1)) is not None
        source = SourceFolder(project_id=project_id, external_id=external_id, name=source_meta.name, provider=drive.provider, is_primary=not has_source)
        db.add(source)
    else:
        # The provider may rename a selected folder between snapshots. Keep the
        # stable locator while refreshing its display metadata.
        source.name = source_meta.name
    if connection.id is None:
        db.add(connection)
    db.flush()
    connection.root_folder_id = external_id
    connection.root_display_name = source_meta.name
    snapshot = WorkspaceSnapshot(project_id=project_id, source_folder_id=source.id, status="building",
                                 analysis_result={"storage_binding": _binding(connection, external_id)})
    db.add(snapshot); db.flush()
    # enqueue commits the binding, snapshot and job together using its public contract.
    job = enqueue(db, "workspace.snapshot", {
        "snapshot_id": snapshot.id, "project_id": project_id, "external_id": external_id,
    }, idempotency_key=f"workspace.snapshot:{snapshot.id}")
    return response(snapshot, job.id, False)


@router.post("/{project_id}/source-folders/snapshot-queue-all")
def queue_all_workspace_snapshots(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Queue missing top-level provider folders for sequential metadata-only snapshots."""
    require_project_role(db, user, project_id, "manager")
    if db.get(Project, project_id) is None:
        raise HTTPException(404, "Project not found")
    adapter = storage_for_project(project_id, db)
    connection = project_storage_connection(project_id, db)
    root = connection.root_folder_id if connection else "root"
    items = [item for item in adapter.list_children(root) if item.is_folder]
    sources = list(db.scalars(select(SourceFolder).where(SourceFolder.project_id == project_id)))
    source_by_external = {row.external_id: row for row in sources}
    queued: list[tuple[int, str, str]] = []
    skipped = 0
    for item in items:
        source = source_by_external.get(item.id)
        if source is None:
            source = SourceFolder(
                project_id=project_id, external_id=item.id, name=item.name, provider=adapter.provider,
                is_primary=not source_by_external,
            )
            db.add(source)
            db.flush()
            source_by_external[item.id] = source
        latest = db.scalar(select(WorkspaceSnapshot).where(
            WorkspaceSnapshot.source_folder_id == source.id,
        ).order_by(WorkspaceSnapshot.id.desc()))
        if latest is not None and latest.status in {"building", "ready"}:
            skipped += 1
            continue
        snapshot = WorkspaceSnapshot(project_id=project_id, source_folder_id=source.id, status="building")
        db.add(snapshot)
        db.flush()
        queued.append((snapshot.id, item.id, item.name))
    db.commit()
    for snapshot_id, external_id, _ in queued:
        _enqueue_snapshot(snapshot_id, project_id, external_id)
    return {
        "queued": len(queued), "skipped": skipped,
        "folders": [{"snapshot_id": snapshot_id, "external_id": external_id, "name": name} for snapshot_id, external_id, name in queued],
        "originals_modified": False,
    }


@router.post("/{project_id}/source-folders/{external_id:path}/snapshots")
def create_workspace_snapshot(
    project_id: int,
    external_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Compatibility route: snapshot construction is always durable."""
    return queue_workspace_snapshot(project_id, external_id, db, user)


@router.get("/{project_id}/snapshots")
def list_workspace_snapshots(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    rows = db.execute(
        select(WorkspaceSnapshot, SourceFolder)
        .join(SourceFolder, SourceFolder.id == WorkspaceSnapshot.source_folder_id)
        .where(WorkspaceSnapshot.project_id == project_id)
        .order_by(WorkspaceSnapshot.id.desc())
    ).all()
    return {
        "snapshots": [
            {
                "id": snapshot.id,
                "project_id": snapshot.project_id,
                "provider": source.provider,
                "storage_binding": (snapshot.analysis_result or {}).get("storage_binding"),
                "status": snapshot.status,
                "item_count": snapshot.item_count,
                "source_folder": source.name,
                "source_external_id": source.external_id,
                "is_primary": source.is_primary,
                "analyzed": db.execute(text("""
                    SELECT EXISTS(
                        SELECT 1 FROM organizer_proposals
                        WHERE project_id=:project_id AND copy_folder_id=:copy_folder_id
                    )
                """), {"project_id": project_id, "copy_folder_id": f"virtual:{snapshot.id}"}).scalar_one(),
                "analysis_status": snapshot.analysis_status,
                "analysis_result": snapshot.analysis_result,
                "analysis_error": snapshot.analysis_error,
                "retry_count": snapshot.retry_count,
                "analysis_retry_count": snapshot.analysis_retry_count,
                "created_at": snapshot.created_at,
                "completed_at": snapshot.completed_at,
            }
            for snapshot, source in rows
        ]
    }


@router.get("/{project_id}/processing-queue")
def processing_queue(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Observable project queue with explicit failed and dead-letter work."""
    require_project_role(db, user, project_id, "viewer")
    snapshots = list(db.scalars(select(WorkspaceSnapshot).where(WorkspaceSnapshot.project_id == project_id).order_by(WorkspaceSnapshot.id.desc()).limit(500)))
    jobs = list(db.scalars(select(BackgroundJob).where(
        BackgroundJob.kind.in_(("workspace.snapshot", "workspace.analysis", "workspace.safe_copy")),
        BackgroundJob.payload["project_id"].as_integer() == project_id,
    ).order_by(BackgroundJob.id.desc()).limit(1500)))
    latest_job_by_snapshot: dict[int, BackgroundJob] = {}
    for job in jobs:
        snapshot_id = (job.payload or {}).get("snapshot_id")
        if isinstance(snapshot_id, int):
            latest_job_by_snapshot.setdefault(snapshot_id, job)
    sessions = db.execute(text("""
        SELECT s.id,s.status,s.progress,s.error_message,s.retry_count,s.created_at,s.updated_at,
               s.source_item_count,s.copy_item_count,s.processed_item_count,s.copy_folder_id,
               CASE WHEN s.status='queued' THEN (
                   SELECT count(1) FROM organizer_sessions queued
                   WHERE queued.status='queued' AND queued.id <= s.id
               ) END AS queue_position
        FROM organizer_sessions s WHERE s.project_id=:project_id ORDER BY s.id DESC LIMIT 500
    """), {"project_id": project_id}).mappings().all()
    return {
        "summary": {
            "active": sum(x.status in {"building"} or x.analysis_status == "analyzing" for x in snapshots) + sum(x["status"] in {"queued", "scanning", "analyzing"} for x in sessions),
            "failed": sum(x.status == "failed" or x.analysis_status == "failed" for x in snapshots) + sum(x["status"] == "failed" for x in sessions),
            "dead_letter": sum(x.status == "dead_letter" or x.analysis_status == "dead_letter" for x in snapshots) + sum(x["status"] == "dead_letter" for x in sessions),
        },
        "snapshots": [{"id": x.id, "status": x.status, "analysis_status": x.analysis_status,
                       "retry_count": x.retry_count, "analysis_retry_count": x.analysis_retry_count,
                       "error": x.error_message or x.analysis_error,
                       "job_id": latest_job_by_snapshot[x.id].id if x.id in latest_job_by_snapshot else None,
                       "job_status": latest_job_by_snapshot[x.id].status if x.id in latest_job_by_snapshot else None,
                       "job_progress": latest_job_by_snapshot[x.id].progress if x.id in latest_job_by_snapshot else None,
                       "duration_ms": latest_job_by_snapshot[x.id].duration_ms if x.id in latest_job_by_snapshot else None,
                       } for x in snapshots],
        "sessions": [dict(x) for x in sessions],
    }


@router.post("/{project_id}/snapshots/{snapshot_id}/retry-build")
def retry_snapshot_build(project_id: int, snapshot_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    snapshot = _locked_snapshot(db, snapshot_id)
    if snapshot is None or snapshot.project_id != project_id:
        raise HTTPException(404, "Snapshot not found")
    if snapshot.status != "failed" or snapshot.retry_count >= 2:
        raise HTTPException(409, "Only a failed snapshot with fewer than three attempts can be retried")
    source = db.get(SourceFolder, snapshot.source_folder_id)
    if source is None:
        raise HTTPException(409, "Snapshot source folder is missing")
    _validate_snapshot_target(db, snapshot, project_id, source.external_id)
    _require_idle_snapshot(db, snapshot_id, project_id)
    snapshot.status = "building"
    snapshot.error_message = None
    snapshot.retry_count += 1
    from app.jobs.queue import enqueue
    enqueue(db, "workspace.snapshot", {
        "snapshot_id": snapshot.id, "project_id": project_id, "external_id": source.external_id,
    }, idempotency_key=f"workspace.snapshot:{snapshot.id}:manual:{snapshot.retry_count}")
    return {"snapshot_id": snapshot.id, "status": snapshot.status, "retry_count": snapshot.retry_count}


@router.get("/{project_id}/snapshots/{snapshot_id}/nodes")
def list_virtual_nodes(
    project_id: int,
    snapshot_id: int,
    parent_external_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    snapshot = db.get(WorkspaceSnapshot, snapshot_id)
    if snapshot is None or snapshot.project_id != project_id:
        raise HTTPException(404, "Snapshot not found")
    query = select(VirtualNode).where(VirtualNode.snapshot_id == snapshot_id)
    if parent_external_id is not None:
        query = query.where(VirtualNode.parent_external_id == parent_external_id)
    nodes = db.scalars(query.order_by(VirtualNode.node_type, VirtualNode.name).limit(5000)).all()
    return {
        "snapshot_id": snapshot_id,
        "status": snapshot.status,
        "nodes": [
            {
                "id": node.id,
                "external_id": node.external_id,
                "parent_external_id": node.parent_external_id,
                "name": node.name,
                "mime_type": node.mime_type,
                "node_type": node.node_type,
                "size_bytes": node.size_bytes,
                "checksum": node.checksum,
                "source_modified_at": node.source_modified_at,
            }
            for node in nodes
        ],
    }


@router.post("/{project_id}/snapshots/{snapshot_id}/analyze")
def analyze_workspace_snapshot(
    project_id: int,
    snapshot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Analyze a metadata snapshot read-only; no Drive copy or mutation is performed."""
    require_project_role(db, user, project_id, "manager")
    snapshot = _locked_snapshot(db, snapshot_id)
    if snapshot is None or snapshot.project_id != project_id or snapshot.status != "ready":
        raise HTTPException(409, "A ready snapshot is required")
    source = db.get(SourceFolder, snapshot.source_folder_id)
    if source is None:
        raise HTTPException(409, "Snapshot source folder is missing")

    _validate_snapshot_target(db, snapshot, project_id, source.external_id)

    repo = OrganizerRepository(db)
    existing_proposal = db.execute(text("""
        SELECT id,session_id FROM organizer_proposals
        WHERE project_id=:project_id AND copy_folder_id=:copy_folder_id
        ORDER BY id DESC LIMIT 1
    """), {"project_id": project_id, "copy_folder_id": f"virtual:{snapshot_id}"}).mappings().first()
    if existing_proposal:
        return {
            "snapshot_id": snapshot_id,
            "session_id": existing_proposal["session_id"],
            "proposal_id": existing_proposal["id"],
            "mode": "virtual_read_only",
            "originals_modified": False,
            "physical_copies_created": 0,
            "already_analyzed": True,
        }
    if snapshot.analysis_status == "analyzing":
        return {"snapshot_id": snapshot_id, "status": "analyzing", "already_queued": True}
    _require_idle_snapshot(db, snapshot_id, project_id)
    if snapshot.analysis_status == "dead_letter" or (
        snapshot.analysis_retry_count >= 2 and snapshot.analysis_status == "failed"
    ):
        raise HTTPException(409, "Analysis exhausted three attempts and is in the dead-letter queue")
    if snapshot.analysis_status == "failed":
        snapshot.analysis_retry_count += 1
    snapshot.analysis_status = "analyzing"
    snapshot.analysis_result = {"storage_binding": (snapshot.analysis_result or {}).get("storage_binding")}
    snapshot.analysis_error = None
    from app.jobs.queue import enqueue
    enqueue(db, "workspace.analysis", {"snapshot_id": snapshot_id, "project_id": project_id},
            idempotency_key=f"workspace.analysis:{snapshot_id}:manual:{snapshot.analysis_retry_count}")
    return {"snapshot_id": snapshot_id, "status": "analyzing", "already_queued": False}


@router.post("/{project_id}/snapshots/{snapshot_id}/standardize")
def standardize_workspace_snapshot(
    project_id: int,
    snapshot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Create and organize a safe Drive copy for an already-built snapshot."""
    require_project_role(db, user, project_id, "manager")
    snapshot = db.get(WorkspaceSnapshot, snapshot_id)
    if snapshot is None or snapshot.project_id != project_id or snapshot.status != "ready":
        raise HTTPException(409, "A ready snapshot is required")
    source = db.get(SourceFolder, snapshot.source_folder_id)
    if source is None:
        raise HTTPException(409, "Snapshot source folder is missing")
    existing = snapshot.analysis_result or {}
    if existing.get("mode") == "safe_copy" and existing.get("organizer_session_id"):
        return {"snapshot_id": snapshot.id, "session_id": existing["organizer_session_id"],
                "status": snapshot.analysis_status, "already_queued": True}
    session_id = _start_safe_copy_pipeline(snapshot.id, project_id, source.external_id, source.name)
    return {"snapshot_id": snapshot.id, "session_id": session_id,
            "status": "analyzing", "already_queued": False, "originals_modified": False}
