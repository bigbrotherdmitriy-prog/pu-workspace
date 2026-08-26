from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.google_drive import credentials_for_project
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.project import Project
from app.models.user import User
from app.models.workspace import SourceFolder, VirtualNode, WorkspaceSnapshot
from app.organizer_engine.drive import DriveClient


router = APIRouter(prefix="/projects", tags=["virtual-workspace"])


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
