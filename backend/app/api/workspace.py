from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import os

from fastapi import APIRouter, Depends, HTTPException
from googleapiclient.discovery import build
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.google_drive import credentials_for_project
from app.core.auth import require_project_role, require_user
from app.database import SessionLocal, get_db
from app.models.project import Project
from app.models.user import User
from app.models.workspace import SourceFolder, VirtualNode, WorkspaceSnapshot
from app.organizer_engine.drive import DriveClient
from app.organizer_engine.repository import OrganizerRepository
from app.organizer_engine.planner import build_proposal
from app.organizer_engine.types import DriveFile
from app.document_engine import index_documents
from app.task_engine import create_tasks_from_files
from app.response_engine import create_response_drafts
from app.governance_engine import create_governance_items
from app.google_tasks import sync_tasks_to_google
from app.google_calendar import sync_tasks_to_calendar


router = APIRouter(prefix="/projects", tags=["virtual-workspace"])
_snapshot_workers = ThreadPoolExecutor(max_workers=max(1, int(os.getenv("SNAPSHOT_WORKERS", "1"))))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_snapshot(snapshot_id: int, project_id: int, external_id: str) -> None:
    db = SessionLocal()
    try:
        snapshot = db.get(WorkspaceSnapshot, snapshot_id)
        if snapshot is None or snapshot.status == "ready":
            return
        drive = DriveClient(build("drive", "v3", credentials=credentials_for_project(project_id, db), cache_discovery=False))
        source_meta = drive.get_file_meta(external_id)
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
        if failed is not None:
            failed.status = "failed"
            failed.error_message = str(exc)[:2000]
            db.commit()
    finally:
        db.close()


@router.get("/{project_id}/source-folders/discover")
def discover_source_folders(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """List top-level Drive folders available for independent snapshot queues."""
    require_project_role(db, user, project_id, "viewer")
    service = build("drive", "v3", credentials=credentials_for_project(project_id, db), cache_discovery=False)
    result = service.files().list(
        q="'root' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id,name,modifiedTime,createdTime)", pageSize=1000, orderBy="name",
    ).execute()
    registered = {row.external_id for row in db.scalars(select(SourceFolder).where(SourceFolder.project_id == project_id))}
    return {"folders": [{**item, "registered": item["id"] in registered} for item in result.get("files", [])]}


@router.post("/{project_id}/source-folders/{external_id}/snapshot-queue")
def queue_workspace_snapshot(
    project_id: int,
    external_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    drive = DriveClient(build("drive", "v3", credentials=credentials_for_project(project_id, db), cache_discovery=False))
    source_meta = drive.get_file_meta(external_id)
    if not source_meta.is_folder:
        raise HTTPException(422, "Source object is not a folder")
    source = db.scalar(select(SourceFolder).where(SourceFolder.project_id == project_id, SourceFolder.external_id == external_id))
    if source is None:
        source = SourceFolder(project_id=project_id, external_id=external_id, name=source_meta.name)
        db.add(source); db.flush()
    active = db.scalar(select(WorkspaceSnapshot).where(
        WorkspaceSnapshot.source_folder_id == source.id,
        WorkspaceSnapshot.status == "building",
    ).order_by(WorkspaceSnapshot.id.desc()))
    if active:
        return {"id": active.id, "status": active.status, "source_folder": source.name, "already_queued": True}
    snapshot = WorkspaceSnapshot(project_id=project_id, source_folder_id=source.id, status="building")
    db.add(snapshot); db.commit(); db.refresh(snapshot)
    _snapshot_workers.submit(_build_snapshot, snapshot.id, project_id, external_id)
    return {"id": snapshot.id, "status": snapshot.status, "source_folder": source.name, "already_queued": False}


@router.post("/{project_id}/source-folders/{external_id}/snapshots")
def create_workspace_snapshot(
    project_id: int,
    external_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Capture Drive metadata only; source files are neither moved nor copied."""
    require_project_role(db, user, project_id, "editor")
    if db.get(Project, project_id) is None:
        raise HTTPException(404, "Project not found")

    credentials = credentials_for_project(project_id, db)
    drive = DriveClient(build("drive", "v3", credentials=credentials, cache_discovery=False))
    source_meta = drive.get_file_meta(external_id)
    if not source_meta.is_folder:
        raise HTTPException(422, "Source object is not a folder")

    source = db.scalar(
        select(SourceFolder).where(
            SourceFolder.project_id == project_id,
            SourceFolder.external_id == external_id,
        )
    )
    if source is None:
        source = SourceFolder(project_id=project_id, external_id=external_id, name=source_meta.name)
        db.add(source)
        db.flush()
    else:
        source.name = source_meta.name

    snapshot = WorkspaceSnapshot(project_id=project_id, source_folder_id=source.id, status="building")
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    try:
        items = drive.walk_tree(external_id)
        root = VirtualNode(
            snapshot_id=snapshot.id,
            external_id=source_meta.id,
            parent_external_id=source_meta.parent_id or None,
            name=source_meta.name,
            mime_type=source_meta.mime_type,
            node_type="folder",
            size_bytes=source_meta.size,
            checksum=source_meta.md5_checksum,
            source_modified_at=_parse_time(source_meta.modified_time),
        )
        db.add(root)
        db.add_all(
            VirtualNode(
                snapshot_id=snapshot.id,
                external_id=item.id,
                parent_external_id=item.parent_id or None,
                name=item.name,
                mime_type=item.mime_type,
                node_type="folder" if item.is_folder else "file",
                size_bytes=item.size,
                checksum=item.md5_checksum,
                source_modified_at=_parse_time(item.modified_time),
            )
            for item in items
        )
        snapshot.item_count = len(items) + 1
        snapshot.status = "ready"
        snapshot.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        db.rollback()
        failed = db.get(WorkspaceSnapshot, snapshot.id)
        if failed is not None:
            failed.status = "failed"
            failed.error_message = str(exc)[:2000]
            db.commit()
        raise HTTPException(502, "Could not build workspace snapshot") from exc

    return {"id": snapshot.id, "status": snapshot.status, "item_count": snapshot.item_count, "source_folder": source.name}


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
                "status": snapshot.status,
                "item_count": snapshot.item_count,
                "source_folder": source.name,
                "source_external_id": source.external_id,
                "created_at": snapshot.created_at,
                "completed_at": snapshot.completed_at,
            }
            for snapshot, source in rows
        ]
    }


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
    snapshot = db.get(WorkspaceSnapshot, snapshot_id)
    if snapshot is None or snapshot.project_id != project_id or snapshot.status != "ready":
        raise HTTPException(409, "A ready snapshot is required")
    source = db.get(SourceFolder, snapshot.source_folder_id)
    if source is None:
        raise HTTPException(409, "Snapshot source folder is missing")

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

    nodes = db.scalars(
        select(VirtualNode).where(VirtualNode.snapshot_id == snapshot_id).order_by(VirtualNode.id)
    ).all()
    files = [
        DriveFile(
            id=node.external_id,
            name=node.name,
            mime_type=node.mime_type,
            parent_id=node.parent_external_id or "",
            md5_checksum=node.checksum,
            size=node.size_bytes,
            modified_time=node.source_modified_at.isoformat() if node.source_modified_at else None,
        )
        for node in nodes
    ]
    drive = DriveClient(build("drive", "v3", credentials=credentials_for_project(project_id, db), cache_discovery=False))
    extracted, extraction_failed = drive.populate_content(files)
    project = db.get(Project, project_id)
    session_id = repo.create_session(project_id, source.external_id, source.name)
    repo.update_session(
        session_id,
        copy_folder_id=f"virtual:{snapshot_id}",
        copy_folder_name=f"Виртуальный снимок #{snapshot_id}",
        source_item_count=len(files),
        copy_item_count=0,
        status="analyzing",
        progress=70,
    )
    indexed = index_documents(db, project_id, files, "google_drive_snapshot")
    items = build_proposal(files, project_name=project.name if project else None, confirmed_rules=repo.confirmed_rules())
    tasks = create_tasks_from_files(db, project_id, session_id, files)
    google_synced, _ = sync_tasks_to_google(db, project_id, tasks)
    calendar_synced, _ = sync_tasks_to_calendar(db, project_id, tasks)
    drafts = create_response_drafts(db, project_id, session_id, files)
    risks, decisions = create_governance_items(db, project_id, files)
    proposal_id = repo.create_proposal(
        project_id, session_id, source.name, source.external_id, f"virtual:{snapshot_id}"
    )
    repo.save_items(proposal_id, items)
    repo.update_session(session_id, status="proposed", progress=100)
    return {
        "snapshot_id": snapshot_id,
        "session_id": session_id,
        "proposal_id": proposal_id,
        "mode": "virtual_read_only",
        "originals_modified": False,
        "physical_copies_created": 0,
        "already_analyzed": False,
        "documents": len(indexed),
        "text_extracted": extracted,
        "extraction_failed": extraction_failed,
        "tasks": len(tasks),
        "google_tasks_synced": google_synced,
        "calendar_synced": calendar_synced,
        "drafts": len(drafts),
        "risks": len(risks),
        "decisions": len(decisions),
    }
