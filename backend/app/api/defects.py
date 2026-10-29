from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import defects as svc
from app.core.auth import require_project_role, require_user
from app.database import get_db

router = APIRouter(prefix="/projects", tags=["defects"])


class DefectBody(BaseModel):
    title: str | None = None
    description: str | None = None
    object_name: str | None = None
    location: str | None = None
    priority: str | None = None
    responsible: str | None = None
    due_date: str | None = None


class StatusBody(BaseModel):
    status: str
    comment: str | None = None


class CommentBody(BaseModel):
    text: str


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(404 if isinstance(exc, LookupError) else 422, str(exc))


@router.get("/{project_id}/defects")
def list_defects(project_id: int, status: str | None = Query(default=None), q: str | None = Query(default=None),
                 db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    return svc.list_defects(db, project_id, status, q)


@router.get("/{project_id}/defects/export.xlsx")
def export_defects(project_id: int, status: str | None = Query(default=None), db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    return Response(content=svc.to_xlsx(db, project_id, status),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=\"defects.xlsx\""})


@router.post("/{project_id}/defects")
def create_defect(project_id: int, body: DefectBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    try:
        res = svc.create_defect(db, project_id, getattr(user, "id", None), body.model_dump(exclude_unset=True))
    except ValueError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return res


@router.get("/{project_id}/defects/{defect_id}")
def get_defect(project_id: int, defect_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    try:
        return svc.get_defect(db, project_id, defect_id)
    except LookupError as exc:
        raise _fail(exc) from exc


@router.patch("/{project_id}/defects/{defect_id}")
def update_defect(project_id: int, defect_id: int, body: DefectBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    try:
        res = svc.update_defect(db, project_id, defect_id, body.model_dump(exclude_unset=True))
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return res


@router.post("/{project_id}/defects/{defect_id}/status")
def change_status(project_id: int, defect_id: int, body: StatusBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    try:
        res = svc.change_status(db, project_id, getattr(user, "id", None), defect_id, body.status, body.comment)
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return res


@router.post("/{project_id}/defects/{defect_id}/comments")
def add_comment(project_id: int, defect_id: int, body: CommentBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    try:
        res = svc.add_comment(db, project_id, getattr(user, "id", None), defect_id, body.text)
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return res


@router.post("/{project_id}/defects/{defect_id}/photos")
async def upload_photo(
    project_id: int, defect_id: int, request: Request,
    name: str = Query(default="photo", max_length=300), kind: str = Query(default="before"),
    db: Session = Depends(get_db), user=Depends(require_user),
):
    require_project_role(db, user, project_id, "editor")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > svc.MAX_PHOTO_BYTES + 1024:
        raise HTTPException(413, "Фото больше 10 МБ")
    data = await request.body()
    try:
        res = svc.add_photo(db, project_id, defect_id, kind, name, request.headers.get("content-type"), data)
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return res


@router.get("/{project_id}/defects/{defect_id}/photos/{photo_id}")
def download_photo(project_id: int, defect_id: int, photo_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    try:
        p = svc.get_photo(db, project_id, defect_id, photo_id)
    except LookupError as exc:
        raise _fail(exc) from exc
    return Response(content=p.data, media_type=p.mime_type, headers={"Content-Disposition": "inline; filename=\"photo\"", "Cache-Control": "private, max-age=3600"})


@router.delete("/{project_id}/defects/{defect_id}/photos/{photo_id}")
def delete_photo(project_id: int, defect_id: int, photo_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    try:
        res = svc.delete_photo(db, project_id, defect_id, photo_id)
    except LookupError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return res


@router.delete("/{project_id}/defects/{defect_id}")
def delete_defect(project_id: int, defect_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    try:
        svc.delete_defect(db, project_id, defect_id)
    except LookupError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return {"deleted": defect_id}
