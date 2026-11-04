from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, Header
from pydantic import BaseModel, Field, StrictInt
from sqlalchemy.orm import Session

from app import defects as svc
from app.core.auth import require_project_role, require_user
from app.bounded_body import read_body_limited
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


class RdVersionBody(BaseModel):
    rd_version_id: Annotated[StrictInt, Field(gt=0)]


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


@router.post("/{project_id}/defects/{defect_id}/rd-version")
def bind_rd_version(project_id: int, defect_id: int, body: RdVersionBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    try:
        res = svc.bind_rd_version(db, project_id, defect_id, body.rd_version_id)
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
    data = await read_body_limited(request, svc.MAX_PHOTO_BYTES, "Фото больше 10 МБ")
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


class DrawingPointBody(BaseModel):
    rd_version_id: Annotated[StrictInt, Field(gt=0, le=2147483647)]
    page: Annotated[StrictInt, Field(gt=0, le=2147483647)]
    x: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    y: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


@router.post('/{project_id}/defects/{defect_id}/drawing-point')
def attach_drawing_point(project_id: int, defect_id: int, body: DrawingPointBody, expected_user_id: int | None = Header(None, alias='X-PU-Expected-User', gt=0), db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db,user,project_id,'editor')
    if expected_user_id is not None:
        from app.mobile_create_owner import require_create_owner
        require_create_owner(user, expected_user_id)
    from app.drawing_points import bind_point
    try:
        result=bind_point(db,project_id,defect_id,version_id=body.rd_version_id,page=body.page,x=body.x,y=body.y)
    except (ValueError,LookupError) as exc:
        db.rollback();raise _fail(exc) from exc
    db.commit()
    return result


@router.post('/{project_id}/defects/{defect_id}/photos/idempotent')
async def upload_photo_idempotent(project_id: int, defect_id: int, request: Request,
        expected_user_id: int = Header(..., alias='X-PU-Expected-User', gt=0),
        operation_key: str = Header(..., alias='X-PU-Photo-Operation', min_length=8, max_length=160),
        name: str = Query(..., min_length=1, max_length=300), kind: str = Query('before'),
        db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, 'editor')
    from app.mobile_create_owner import require_create_owner
    from app.defect_photo_receipts import save_photo, PhotoReceiptConflict
    require_create_owner(user, expected_user_id)
    data = await read_body_limited(request, svc.MAX_PHOTO_BYTES, 'Фото больше 10 МБ')
    try:
        result = save_photo(db, project_id, defect_id, user.id, operation_key=operation_key,
            kind=kind, filename=name, mime=request.headers.get('content-type'), data=data)
    except PhotoReceiptConflict as exc:
        db.rollback(); raise HTTPException(409, str(exc)) from exc
    except (ValueError, LookupError) as exc:
        db.rollback(); raise _fail(exc) from exc
    db.commit()
    return result
