from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import customer_portal as svc
from app import estimates as est
from app import ks_forms
from app.core.auth import require_project_role, require_user
from app.database import get_db

router = APIRouter(tags=["customer"])

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class InviteBody(BaseModel):
    email: str


class DecisionBody(BaseModel):
    decision: str
    comment: str | None = None


def _customer_or_admin(db: Session, user, project_id: int) -> None:
    if getattr(user, "is_admin", False):
        return
    if not svc.has_access(db, user.id, project_id):
        raise HTTPException(403, "Нет доступа к проекту")


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(404 if isinstance(exc, LookupError) else 422, str(exc))


# --- управление доступом (подрядчик) ---------------------------------------------------------------

@router.get("/projects/{project_id}/customer-access")
def list_customer_access(project_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    return {"access": svc.list_access(db, project_id)}


@router.post("/projects/{project_id}/customer-access")
def invite_customer(project_id: int, body: InviteBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    try:
        res = svc.invite(db, project_id, body.email, getattr(user, "id", None))
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return res


@router.delete("/projects/{project_id}/customer-access/{access_id}")
def revoke_customer(project_id: int, access_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    try:
        svc.revoke(db, project_id, access_id)
    except LookupError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return {"deleted": access_id}


# --- кабинет заказчика ------------------------------------------------------------------------------

@router.get("/customer/projects")
def customer_projects(db: Session = Depends(get_db), user=Depends(require_user)):
    return {"projects": svc.my_projects(db, user.id)}


@router.get("/customer/projects/{project_id}/summary")
def customer_summary(project_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    _customer_or_admin(db, user, project_id)
    return svc.summary(db, project_id)


@router.get("/customer/projects/{project_id}/progress")
def customer_progress(project_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    _customer_or_admin(db, user, project_id)
    return {"entries": svc.progress_log(db, project_id)}


def _form(db: Session, project_id: int, act_id: int, kind: str) -> Response:
    try:
        act = svc.visible_act(db, project_id, act_id)
        data = est.act_with_totals(db, project_id, act_id)
    except LookupError as exc:
        raise _fail(exc) from exc
    header = svc.header_of(act)
    if kind == "ks2":
        content, name = ks_forms.render_ks2_docx(header, data), "ks2.docx"
    else:
        content, name = ks_forms.render_ks3_docx(header, data), "ks3.docx"
    return Response(content=content, media_type=DOCX, headers={"Content-Disposition": f"attachment; filename=\"{name}\""})


@router.get("/customer/projects/{project_id}/ks-acts/{act_id}/ks2.docx")
def customer_ks2(project_id: int, act_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    _customer_or_admin(db, user, project_id)
    return _form(db, project_id, act_id, "ks2")


@router.get("/customer/projects/{project_id}/ks-acts/{act_id}/ks3.docx")
def customer_ks3(project_id: int, act_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    _customer_or_admin(db, user, project_id)
    return _form(db, project_id, act_id, "ks3")


@router.post("/customer/projects/{project_id}/ks-acts/{act_id}/decision")
def customer_decision(project_id: int, act_id: int, body: DecisionBody, db: Session = Depends(get_db), user=Depends(require_user)):
    _customer_or_admin(db, user, project_id)
    try:
        res = svc.decide(db, project_id, act_id, user.id, body.decision, body.comment)
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return res
