from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.executive_docs import object_report, requirements_from_contract, scan_source
from app.models.executive_docs import IdRequirement, IdSource

router = APIRouter(prefix="/projects", tags=["executive-docs"])


class SourceCreate(BaseModel):
    object_name: str
    folder_id: str
    provider: str = "google_drive"
    folder_name: str | None = None
    contract_id: int | None = None


class RequirementsBuild(BaseModel):
    contract_id: int | None = None
    object_name: str | None = None
    sections: list[str]


@router.get("/{project_id}/executive-docs/sources")
def list_sources(
    project_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    sources = db.query(IdSource).filter(IdSource.project_id == project_id).all()
    return [
        {
            "id": s.id,
            "object_name": s.object_name,
            "provider": s.provider,
            "folder_id": s.folder_id,
            "folder_name": s.folder_name,
            "contract_id": s.contract_id,
            "last_scan_at": s.last_scan_at.isoformat() if s.last_scan_at else None,
        }
        for s in sources
    ]


@router.post("/{project_id}/executive-docs/sources")
def create_source(
    project_id: int,
    body: SourceCreate,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    existing = db.query(IdSource).filter_by(
        project_id=project_id, provider=body.provider, folder_id=body.folder_id,
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Source already exists")
    source = IdSource(
        project_id=project_id,
        provider=body.provider,
        folder_id=body.folder_id,
        folder_name=body.folder_name,
        object_name=body.object_name,
        contract_id=body.contract_id,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return {"id": source.id}


@router.post("/{project_id}/executive-docs/requirements/build")
def build_requirements(
    project_id: int,
    body: RequirementsBuild,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    count = requirements_from_contract(
        db, project_id, body.contract_id, body.object_name, body.sections,
    )
    db.commit()
    return {"created": count}


@router.get("/{project_id}/executive-docs/requirements")
def list_requirements(
    project_id: int,
    object_name: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    q = db.query(IdRequirement).filter(IdRequirement.project_id == project_id)
    if object_name is not None:
        q = q.filter(IdRequirement.object_name == object_name)
    reqs = q.all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "doc_kind": r.doc_kind,
            "section": r.section,
            "copies": r.copies,
            "required_if_applicable": r.required_if_applicable,
            "source": r.source,
            "norm_ref": r.norm_ref,
            "object_name": r.object_name,
            "contract_id": r.contract_id,
        }
        for r in reqs
    ]


@router.post("/{project_id}/executive-docs/sources/{source_id}/scan")
def scan_one_source(
    project_id: int,
    source_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    source = db.query(IdSource).filter(
        IdSource.id == source_id, IdSource.project_id == project_id,
    ).first()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    from app.integrations.storage import storage_for_project
    storage = storage_for_project(project_id, db)
    scan_source(db, source, storage)
    report = object_report(db, project_id, source.object_name)
    db.commit()
    return report


@router.get("/{project_id}/executive-docs/report")
def get_report(
    project_id: int,
    object_name: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    if object_name is not None:
        names = [object_name]
    else:
        sources = db.query(IdSource).filter(IdSource.project_id == project_id).all()
        seen: set[str] = set()
        names = []
        for s in sources:
            if s.object_name and s.object_name not in seen:
                seen.add(s.object_name)
                names.append(s.object_name)
    objects = [object_report(db, project_id, n) for n in names]
    return {"objects": objects}


MAX_ID_UPLOAD_BYTES = 50 * 1024 * 1024


def _write_to_source_folder(project_id: int, db, source, name: str, data: bytes) -> str:
    """Кладёт файл в папку ИД объекта в том хранилище, где она лежит, и возвращает id файла."""
    import io
    import mimetypes

    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if source.provider == "pu_storage":
        from app.integrations.pu_storage import PuStorageAdapter

        return PuStorageAdapter(project_id).write_file(source.folder_id, name, data, mime)
    if source.provider in {"google_drive", "google_workspace"}:
        from googleapiclient.http import MediaIoBaseUpload
        from app.api.google_drive import drive_service_for_project

        service = drive_service_for_project(project_id, db)
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
        created = service.files().create(
            body={"name": name, "parents": [source.folder_id]},
            media_body=media, fields="id", supportsAllDrives=True,
        ).execute()
        return str(created["id"])
    raise HTTPException(status_code=409, detail="Загрузка в это хранилище не поддерживается")


@router.post("/{project_id}/executive-docs/sources/{source_id}/upload")
async def upload_to_source(
    project_id: int,
    source_id: int,
    request: Request,
    name: str = Query(..., min_length=1, max_length=255),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """Загрузка файла прямо в папку ИД объекта (сырое тело запроса) и пересчёт готовности."""
    require_project_role(db, user, project_id, "manager")
    source = db.query(IdSource).filter(
        IdSource.id == source_id, IdSource.project_id == project_id,
    ).first()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_ID_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Файл слишком большой")
    data = await request.body()
    if not data:
        raise HTTPException(status_code=422, detail="Пустой файл")
    if len(data) > MAX_ID_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Файл слишком большой")
    safe_name = name.replace("/", "_").replace("\\", "_").strip()
    if not safe_name or safe_name in {".", ".."}:
        raise HTTPException(status_code=422, detail="Некорректное имя файла")
    file_id = _write_to_source_folder(project_id, db, source, safe_name, data)
    from app.integrations.storage import storage_for_project

    scan_source(db, source, storage_for_project(project_id, db))
    report = object_report(db, project_id, source.object_name)
    db.commit()
    return {"file_id": file_id, "report": report}
