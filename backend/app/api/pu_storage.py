from __future__ import annotations

import io
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.integrations.contracts import (
    MutableStorageAdapter,
    StorageAccessDenied,
    StorageQuotaExceeded,
    StorageUnavailable,
)
from app.integrations.pu_storage import PuStorageAdapter
from app.models.drive_connection import DriveConnection

router = APIRouter(prefix="/projects", tags=["pu-storage"])

PU_PROVIDER = "pu_storage"
PU_ROOT_ID = "pu:/"
PU_ROOT_NAME = "Хранилище PU Workspace"
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_ZIP_BYTES = 200 * 1024 * 1024


class CreateFolderBody(BaseModel):
    parent_id: str
    name: str


def _adapter(project_id: int) -> PuStorageAdapter:
    return PuStorageAdapter(project_id)


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, StorageAccessDenied):
        return HTTPException(403, "Доступ к хранилищу запрещён")
    if isinstance(exc, StorageQuotaExceeded):
        return HTTPException(413, "Не хватает места в хранилище")
    if isinstance(exc, (LookupError, FileNotFoundError)):
        return HTTPException(404, "Объект не найден")
    if isinstance(exc, StorageUnavailable):
        return HTTPException(503, "Хранилище недоступно")
    return HTTPException(500, "Ошибка хранилища")


@router.post("/{project_id}/pu-storage/connect")
def connect_pu_storage(
    project_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    connection = db.scalar(
        select(DriveConnection).where(DriveConnection.project_id == project_id)
    )
    if connection is not None and connection.provider != PU_PROVIDER:
        raise HTTPException(
            409,
            "У проекта уже подключено другое хранилище. Сначала отключите его.",
        )
    adapter = _adapter(project_id)
    try:
        adapter._ensure_project_root()  # noqa: SLF001 - ensure storage exists
    except StorageAccessDenied as exc:
        raise _translate(exc) from exc
    if connection is None:
        connection = DriveConnection(
            project_id=project_id,
            provider=PU_PROVIDER,
            account_email=f"pu-storage+project{project_id}@localhost",
            root_folder_id=PU_ROOT_ID,
            root_display_name=PU_ROOT_NAME,
            connection_id=None,
            status="connected",
        )
        db.add(connection)
    else:
        connection.root_folder_id = PU_ROOT_ID
        connection.root_display_name = PU_ROOT_NAME
        connection.connection_id = None
        connection.status = "connected"
    db.commit()
    return {
        "provider": PU_PROVIDER,
        "root_folder_id": PU_ROOT_ID,
        "root_display_name": PU_ROOT_NAME,
    }


@router.get("/{project_id}/pu-storage/files")
def list_pu_storage_files(
    project_id: int,
    folder_id: str = Query(default=PU_ROOT_ID),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    adapter = _adapter(project_id)
    try:
        children = adapter.list_children(folder_id)
        used, quota = adapter.usage()
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    return {
        "files": [
            {
                "id": item.id,
                "name": item.name,
                "is_folder": item.is_folder,
                "size": item.size,
                "modified_time": item.modified_time,
                "mime_type": item.mime_type,
            }
            for item in children
        ],
        "usage": {"used_bytes": used, "quota_bytes": quota},
    }


@router.post("/{project_id}/pu-storage/folders")
def create_pu_storage_folder(
    project_id: int,
    body: CreateFolderBody,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    adapter = _adapter(project_id)
    try:
        folder_id = adapter.create_folder(body.name, body.parent_id)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    return {"id": folder_id}


@router.post("/{project_id}/pu-storage/upload")
async def upload_pu_storage_file(
    project_id: int,
    request: Request,
    name: str = Query(..., min_length=1, max_length=255),
    parent_id: str = Query(default=PU_ROOT_ID),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """Загрузка одного файла сырым телом запроса (без multipart)."""
    require_project_role(db, user, project_id, "manager")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_FILE_BYTES:
        raise HTTPException(413, "Файл слишком большой")
    data = await request.body()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "Файл слишком большой")
    adapter = _adapter(project_id)
    try:
        object_id = adapter.write_file(parent_id, name, data)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    return {"uploaded": [{"id": object_id, "name": object_id.rsplit("/", 1)[-1], "size": len(data)}]}


def _content_disposition(filename: str) -> str:
    encoded = quote(filename)
    return f"attachment; filename*=UTF-8''{encoded}"


def _zip_folder(adapter: PuStorageAdapter, folder_id: str) -> bytes:
    items = adapter.walk_tree(folder_id)
    buffer = io.BytesIO()
    total = 0
    base = folder_id.rstrip("/") + "/"
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in items:
            if item.is_folder:
                continue
            try:
                data, _ = adapter.read_bytes(item.id, MAX_ZIP_BYTES)
            except FileNotFoundError:
                continue
            total += len(data)
            if total > MAX_ZIP_BYTES:
                raise HTTPException(413, "Папка слишком большая для выгрузки")
            arcname = item.id[len(base):] if item.id.startswith(base) else item.name
            zf.writestr(arcname, data)
    return buffer.getvalue()


@router.get("/{project_id}/pu-storage/download")
def download_pu_storage_file(
    project_id: int,
    object_id: str = Query(...),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    adapter = _adapter(project_id)
    try:
        meta = adapter.get_object(object_id)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    if meta.is_folder:
        try:
            content = _zip_folder(adapter, object_id)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc
        filename = f"{meta.name or 'folder'}.zip"
        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": _content_disposition(filename)},
        )
    try:
        data, mime = adapter.read_bytes(object_id, MAX_FILE_BYTES)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    return Response(
        content=data,
        media_type=mime,
        headers={"Content-Disposition": _content_disposition(meta.name)},
    )
