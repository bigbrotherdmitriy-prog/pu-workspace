from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import work_log as svc
from app.core.auth import require_project_role, require_user
from app.database import get_db

router = APIRouter(prefix="/projects", tags=["work-log"])


class WorkLogBody(BaseModel):
    work_date: str | None = None
    work_name: str | None = None
    object_name: str | None = None
    unit: str | None = None
    qty: str | float | None = None
    location: str | None = None
    weather: str | None = None
    workers: int | None = None
    note: str | None = None
    rd_spec_item_id: int | None = None
    schedule_item_id: int | None = None


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc) or "Не найдено")
    return HTTPException(422, str(exc))


@router.get("/{project_id}/work-log")
def list_work_log(
    project_id: int,
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    q: str | None = Query(default=None),
    object_name: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    try:
        return {
            "entries": svc.list_entries(db, project_id, date_from, date_to, q, object_name),
            "summary": svc.summary(db, project_id, date_from, date_to),
        }
    except ValueError as exc:
        raise _translate(exc) from exc


@router.get("/{project_id}/work-log/suggestions")
def work_log_suggestions(project_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    return svc.suggestions(db, project_id)


@router.get("/{project_id}/work-log/export.xlsx")
def export_work_log(
    project_id: int,
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    try:
        data = svc.to_xlsx(db, project_id, date_from, date_to)
    except ValueError as exc:
        raise _translate(exc) from exc
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''%D0%96%D1%83%D1%80%D0%BD%D0%B0%D0%BB%20%D1%80%D0%B0%D0%B1%D0%BE%D1%82.xlsx"},
    )


@router.post("/{project_id}/work-log")
def add_work_log(project_id: int, body: WorkLogBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    try:
        entry = svc.add_entry(db, project_id, getattr(user, "id", None), body.model_dump(exclude_unset=True))
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise _translate(exc) from exc
    db.commit()
    return entry


@router.patch("/{project_id}/work-log/{entry_id}")
def update_work_log(project_id: int, entry_id: int, body: WorkLogBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    try:
        entry = svc.update_entry(db, project_id, entry_id, body.model_dump(exclude_unset=True))
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise _translate(exc) from exc
    db.commit()
    return entry


@router.delete("/{project_id}/work-log/{entry_id}")
def delete_work_log(project_id: int, entry_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    try:
        svc.delete_entry(db, project_id, entry_id)
    except LookupError as exc:
        db.rollback()
        raise _translate(exc) from exc
    db.commit()
    return {"deleted": entry_id}
