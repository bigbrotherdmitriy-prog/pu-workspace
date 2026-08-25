from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.governance import Decision, Risk
from app.models.task import Task
from app.models.user import User

router = APIRouter(prefix="/governance", tags=["governance"])


class RiskUpdate(BaseModel):
    status: str = Field(pattern="^(confirmed|mitigating|resolved|dismissed)$")
    action_note: str | None = Field(default=None, max_length=5000)


class DecisionUpdate(BaseModel):
    status: str = Field(pattern="^(confirmed|decided|executed|dismissed)$")
    decision_text: str | None = Field(default=None, max_length=5000)
    reason: str | None = Field(default=None, max_length=5000)


@router.get("/open-issues")
def open_issues(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    tasks = db.scalars(select(Task).where(Task.project_id == project_id, Task.status.in_(["assigned", "in_progress"]))).all()
    risks = db.scalars(select(Risk).where(Risk.project_id == project_id, Risk.status.in_(["needs_confirmation", "confirmed", "mitigating"]))).all()
    decisions = db.scalars(select(Decision).where(Decision.project_id == project_id, Decision.status.in_(["needs_confirmation", "confirmed", "decided"]))).all()
    items = ([{"type": "task", "id": x.id, "title": x.title, "status": x.status, "criticality": x.priority, "source": x.source_file_name} for x in tasks]
        + [{"type": "risk", "id": x.id, "title": x.title, "status": x.status, "criticality": x.criticality, "source": x.source_name} for x in risks]
        + [{"type": "decision", "id": x.id, "title": x.question, "status": x.status, "criticality": "attention", "source": x.source_name} for x in decisions])
    return {"items": items, "count": len(items), "tasks": len(tasks), "risks": len(risks), "decisions": len(decisions)}


@router.get("/risks")
def risks(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    rows = db.scalars(select(Risk).where(Risk.project_id == project_id).order_by(Risk.created_at.desc())).all()
    return {"risks": [{"id": x.id, "kind": x.kind, "title": x.title, "criticality": x.criticality, "status": x.status, "action_note": x.action_note, "source_name": x.source_name, "confidence": x.confidence} for x in rows]}


@router.patch("/risks/{risk_id}")
def update_risk(risk_id: int, payload: RiskUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.get(Risk, risk_id)
    if not item: raise HTTPException(404, "Risk not found")
    require_project_role(db, user, item.project_id, "manager")
    if payload.status in {"mitigating", "resolved"} and not (payload.action_note or item.action_note or "").strip():
        raise HTTPException(422, "Укажите действие или результат работы с риском")
    item.status = payload.status
    if payload.action_note is not None: item.action_note = payload.action_note.strip() or None
    db.commit()
    return {"id": item.id, "status": item.status}


@router.get("/decisions")
def decisions(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    rows = db.scalars(select(Decision).where(Decision.project_id == project_id).order_by(Decision.created_at.desc())).all()
    return {"decisions": [{"id": x.id, "question": x.question, "status": x.status, "decision_text": x.decision_text, "reason": x.reason, "source_name": x.source_name, "confidence": x.confidence} for x in rows]}


@router.patch("/decisions/{decision_id}")
def update_decision(decision_id: int, payload: DecisionUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.get(Decision, decision_id)
    if not item: raise HTTPException(404, "Decision not found")
    require_project_role(db, user, item.project_id, "manager")
    if payload.status in {"decided", "executed"} and not (payload.decision_text or item.decision_text or "").strip():
        raise HTTPException(422, "Зафиксируйте принятое решение")
    item.status = payload.status
    if payload.decision_text is not None: item.decision_text = payload.decision_text.strip() or None
    if payload.reason is not None: item.reason = payload.reason.strip() or None
    db.commit()
    return {"id": item.id, "status": item.status}
