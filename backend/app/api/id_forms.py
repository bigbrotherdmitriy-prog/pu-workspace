from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import equipment_test_act, id_handover, work_log_form
from app.core.auth import require_project_role, require_user
from app.database import get_db

router = APIRouter(prefix="/projects", tags=["id-forms"])

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class EquipmentTestBody(BaseModel):
    header: dict[str, str | None] = {}
    items: list[dict] = []


class WorkLogFormBody(BaseModel):
    header: dict[str, str | None] = {}


def _docx(content: bytes, ascii_name: str, utf_name: str) -> Response:
    return Response(
        content=content, media_type=DOCX,
        headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf_name}"},
    )


@router.post("/{project_id}/executive-docs/equipment-test-act.docx")
def equipment_test_act_docx(
    project_id: int, body: EquipmentTestBody, db: Session = Depends(get_db), user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    try:
        content = equipment_test_act.render(body.header, body.items)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _docx(content, "equipment-test-act.docx", "%D0%90%D0%BA%D1%82%20%D0%B8%D1%81%D0%BF%D1%8B%D1%82%D0%B0%D0%BD%D0%B8%D1%8F%20%D0%BE%D0%B1%D0%BE%D1%80%D1%83%D0%B4%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D1%8F.docx")


@router.post("/{project_id}/work-log/form.docx")
def work_log_form_docx(
    project_id: int,
    body: WorkLogFormBody,
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    try:
        content = work_log_form.render(db, project_id, body.header, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _docx(content, "work-log.docx", "%D0%9E%D0%B1%D1%89%D0%B8%D0%B9%20%D0%B6%D1%83%D1%80%D0%BD%D0%B0%D0%BB%20%D1%80%D0%B0%D0%B1%D0%BE%D1%82.docx")


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class HandoverBody(BaseModel):
    header: dict[str, str | None] = {}
    items: list[dict] = []
    object_name: str | None = None


def _file(content: bytes, media: str, ascii_name: str) -> Response:
    return Response(content=content, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\""})


@router.get("/{project_id}/executive-docs/handover/defaults")
def handover_defaults(project_id: int, object_name: str | None = None, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    return id_handover.default_header(db, project_id, object_name)


@router.post("/{project_id}/executive-docs/equipment-register.{fmt}")
def equipment_register(project_id: int, fmt: str, body: HandoverBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    if fmt not in ("docx", "xlsx"):
        raise HTTPException(404, "Формат не поддерживается")
    rows = id_handover.equipment_rows(db, project_id, body.object_name)
    try:
        if fmt == "docx":
            return _file(id_handover.render_equipment_register(body.header, rows), DOCX, "equipment-register.docx")
        return _file(id_handover.render_equipment_register_xlsx(body.header, rows), XLSX, "equipment-register.xlsx")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{project_id}/executive-docs/id-register.{fmt}")
def id_register(project_id: int, fmt: str, body: HandoverBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    if fmt not in ("docx", "xlsx"):
        raise HTTPException(404, "Формат не поддерживается")
    if not body.object_name:
        raise HTTPException(422, "Укажите объект")
    rows = id_handover.register_rows(db, project_id, body.object_name)
    db.commit()
    try:
        if fmt == "docx":
            return _file(id_handover.render_register(body.header, rows), DOCX, "id-register.docx")
        return _file(id_handover.render_register_xlsx(body.header, rows), XLSX, "id-register.xlsx")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{project_id}/executive-docs/trial-act.docx")
def trial_act(project_id: int, body: HandoverBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    try:
        return _file(id_handover.render_trial_act(body.header, body.items), DOCX, "trial-act.docx")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{project_id}/executive-docs/commissioning-act.docx")
def commissioning_act(project_id: int, body: HandoverBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    return _file(id_handover.render_commissioning_act(body.header), DOCX, "commissioning-act.docx")
