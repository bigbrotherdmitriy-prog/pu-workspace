from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.governance import Decision, Risk
from app.models.task import Task
from app.models.user import User
from app.api.management import _locked_versioned, append_management_history

router = APIRouter(prefix="/governance", tags=["governance"])


class RiskUpdate(BaseModel):
    expected_record_version: int = Field(default=1, ge=1)
    status: str = Field(pattern="^(confirmed|mitigating|resolved|dismissed)$")
    action_note: str | None = Field(default=None, max_length=5000)


class DecisionUpdate(BaseModel):
    expected_record_version: int = Field(default=1, ge=1)
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
def risks(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user),
          status: str | None = None, cursor: int | None = None, limit: int = 100):
    require_project_role(db, user, project_id, "viewer")
    if not 1 <= limit <= 200: raise HTTPException(422, "limit must be between 1 and 200")
    query = select(Risk).where(Risk.project_id == project_id)
    if status: query = query.where(Risk.status == status)
    if cursor is not None: query = query.where(Risk.id < cursor)
    rows = list(db.scalars(query.order_by(Risk.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit; rows = rows[:limit]
    return {"risks": [{"id": x.id, "record_version": x.record_version, "kind": x.kind, "title": x.title,
                        "criticality": x.criticality, "status": x.status, "action_note": x.action_note,
                        "source_id": x.source_id, "source_name": x.source_name, "source_excerpt": x.source_excerpt,
                        "source_hash": x.source_hash, "confidence": x.confidence} for x in rows],
            "next_cursor": rows[-1].id if has_more and rows else None}


@router.patch("/risks/{risk_id}")
def update_risk(risk_id: int, payload: RiskUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = _locked_versioned(db, Risk, risk_id, payload.expected_record_version, "Risk")
    require_project_role(db, user, item.project_id, "manager")
    if payload.status in {"mitigating", "resolved"} and not (payload.action_note or item.action_note or "").strip():
        raise HTTPException(422, "Укажите действие или результат работы с риском")
    old = {"status": item.status, "action_note": item.action_note}
    item.status = payload.status
    if payload.action_note is not None: item.action_note = payload.action_note.strip() or None
    item.record_version += 1
    append_management_history(db, project_id=item.project_id, entity_type="risk", entity_id=item.id,
                              record_version=item.record_version, action="updated", actor_user_id=user.id,
                              old_values=old, new_values={"status": item.status, "action_note": item.action_note},
                              evidence={"source_id": item.source_id, "source_hash": item.source_hash},
                              reason=item.action_note)
    db.commit()
    return {"id": item.id, "record_version": item.record_version, "status": item.status}


@router.get("/decisions")
def decisions(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user),
              status: str | None = None, cursor: int | None = None, limit: int = 100):
    require_project_role(db, user, project_id, "viewer")
    if not 1 <= limit <= 200: raise HTTPException(422, "limit must be between 1 and 200")
    query = select(Decision).where(Decision.project_id == project_id)
    if status: query = query.where(Decision.status == status)
    if cursor is not None: query = query.where(Decision.id < cursor)
    rows = list(db.scalars(query.order_by(Decision.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit; rows = rows[:limit]
    return {"decisions": [{"id": x.id, "record_version": x.record_version, "question": x.question,
                            "status": x.status, "decision_text": x.decision_text, "reason": x.reason,
                            "source_id": x.source_id, "source_name": x.source_name,
                            "source_excerpt": x.source_excerpt, "source_hash": x.source_hash,
                            "confidence": x.confidence} for x in rows],
            "next_cursor": rows[-1].id if has_more and rows else None}


@router.patch("/decisions/{decision_id}")
def update_decision(decision_id: int, payload: DecisionUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = _locked_versioned(db, Decision, decision_id, payload.expected_record_version, "Decision")
    require_project_role(db, user, item.project_id, "manager")
    if payload.status in {"decided", "executed"} and not (payload.decision_text or item.decision_text or "").strip():
        raise HTTPException(422, "Зафиксируйте принятое решение")
    old = {"status": item.status, "decision_text": item.decision_text, "reason": item.reason}
    item.status = payload.status
    if payload.decision_text is not None: item.decision_text = payload.decision_text.strip() or None
    if payload.reason is not None: item.reason = payload.reason.strip() or None
    item.record_version += 1
    append_management_history(db, project_id=item.project_id, entity_type="decision", entity_id=item.id,
                              record_version=item.record_version, action="updated", actor_user_id=user.id,
                              old_values=old, new_values={"status": item.status,
                                                         "decision_text": item.decision_text, "reason": item.reason},
                              evidence={"source_id": item.source_id, "source_hash": item.source_hash},
                              reason=item.reason)
    db.commit()
    return {"id": item.id, "record_version": item.record_version, "status": item.status}
