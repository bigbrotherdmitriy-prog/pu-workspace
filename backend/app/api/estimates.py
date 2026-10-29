from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import estimates as svc
from app import ks_forms
from app.core.auth import require_project_role, require_user
from app.database import get_db

router = APIRouter(prefix="/projects", tags=["estimates"])

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_UPLOAD = 20 * 1024 * 1024


class LineBody(BaseModel):
    name: str | None = None
    unit: str | None = None
    code: str | None = None
    position: str | None = None
    section: str | None = None
    note: str | None = None
    qty: str | float | None = None
    unit_price: str | float | None = None
    total: str | float | None = None


class ActBody(BaseModel):
    period_from: date
    period_to: date
    number: str | None = None
    act_date: date | None = None


class StatusBody(BaseModel):
    status: str


class FormBody(BaseModel):
    header: dict[str, str | None] = {}


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(404 if isinstance(exc, LookupError) else 422, str(exc))


@router.get("/{project_id}/estimates")
def list_estimates(project_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    return {"estimates": svc.list_estimates(db, project_id)}


@router.post("/{project_id}/estimates/import")
async def import_estimate(
    project_id: int,
    request: Request,
    name: str = Query(..., min_length=1, max_length=500),
    contract_id: int | None = Query(default=None),
    vat_rate: str | None = Query(default=None),
    source_name: str | None = Query(default=None),
    preview: bool = Query(default=False),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """Загрузка сметы (.xlsx сырым телом). С preview=true только разбор без сохранения."""
    require_project_role(db, user, project_id, "editor")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD:
        raise HTTPException(413, "Файл слишком большой")
    data = await request.body()
    if not data:
        raise HTTPException(422, "Пустой файл")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Файл слишком большой")
    from app.estimate_import import parse_estimate_xlsx

    try:
        parsed = parse_estimate_xlsx(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - повреждённый файл не должен давать 500
        raise HTTPException(422, "Не удалось прочитать файл. Нужен Excel (.xlsx).") from exc
    if preview:
        return {"lines": parsed["lines"][:200], "lines_total": len(parsed["lines"]), "warnings": parsed["warnings"],
                "detected": parsed["detected"], "total": parsed.get("total")}
    try:
        est = svc.create_estimate(db, project_id, name, parsed, contract_id, source_name, vat_rate)
    except ValueError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    est["warnings"] = parsed["warnings"]
    return est


@router.get("/{project_id}/estimates/{estimate_id}")
def get_estimate(project_id: int, estimate_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    try:
        return svc.get_estimate(db, project_id, estimate_id)
    except LookupError as exc:
        raise _fail(exc) from exc


@router.delete("/{project_id}/estimates/{estimate_id}")
def delete_estimate(project_id: int, estimate_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    try:
        svc.delete_estimate(db, project_id, estimate_id)
    except LookupError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return {"deleted": estimate_id}


@router.patch("/{project_id}/estimate-lines/{line_id}")
def update_line(project_id: int, line_id: int, body: LineBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    try:
        res = svc.update_line(db, project_id, line_id, body.model_dump(exclude_unset=True))
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return res


@router.get("/{project_id}/estimates/{estimate_id}/ks-acts")
def list_acts(project_id: int, estimate_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    try:
        return {"acts": svc.list_acts(db, project_id, estimate_id)}
    except LookupError as exc:
        raise _fail(exc) from exc


@router.get("/{project_id}/estimates/{estimate_id}/ks-preview")
def preview_act(
    project_id: int, estimate_id: int, period_from: date = Query(...), period_to: date = Query(...),
    db: Session = Depends(get_db), user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    try:
        return svc.preview_act(db, project_id, estimate_id, period_from, period_to)
    except (ValueError, LookupError) as exc:
        raise _fail(exc) from exc


@router.post("/{project_id}/estimates/{estimate_id}/ks-acts")
def create_act(project_id: int, estimate_id: int, body: ActBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    try:
        act = svc.create_act(db, project_id, estimate_id, body.period_from, body.period_to, body.number, body.act_date)
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return act


@router.patch("/{project_id}/ks-acts/{act_id}")
def set_act_status(project_id: int, act_id: int, body: StatusBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    try:
        res = svc.set_act_status(db, project_id, act_id, body.status)
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return res


@router.delete("/{project_id}/ks-acts/{act_id}")
def delete_act(project_id: int, act_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    try:
        svc.delete_act(db, project_id, act_id)
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return {"deleted": act_id}


def _download(content: bytes, media: str, ascii_name: str) -> Response:
    return Response(content=content, media_type=media, headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\""})


@router.post("/{project_id}/ks-acts/{act_id}/ks2.docx")
def ks2_docx(project_id: int, act_id: int, body: FormBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    try:
        data = svc.act_with_totals(db, project_id, act_id)
    except LookupError as exc:
        raise _fail(exc) from exc
    return _download(ks_forms.render_ks2_docx(body.header, data), DOCX, "ks2.docx")


@router.post("/{project_id}/ks-acts/{act_id}/ks3.docx")
def ks3_docx(project_id: int, act_id: int, body: FormBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    try:
        data = svc.act_with_totals(db, project_id, act_id)
    except LookupError as exc:
        raise _fail(exc) from exc
    return _download(ks_forms.render_ks3_docx(body.header, data), DOCX, "ks3.docx")


@router.post("/{project_id}/ks-acts/{act_id}/ks.xlsx")
def ks_xlsx(project_id: int, act_id: int, body: FormBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    try:
        data = svc.act_with_totals(db, project_id, act_id)
    except LookupError as exc:
        raise _fail(exc) from exc
    return _download(ks_forms.render_xlsx(body.header, data), XLSX, "ks.xlsx")
