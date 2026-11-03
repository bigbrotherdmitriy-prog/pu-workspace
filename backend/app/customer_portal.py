"""Кабинет заказчика: ограниченный доступ к ходу работ и актам КС. Внутренние данные подрядчика недоступны."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app import estimates as est
from app.models.audit_log import AuditLog
from app.models.estimates import CustomerAccess, Estimate, KsAct
from app.models.project import Project
from app.models.user import User
from app.models.work_log import WorkLogEntry

VISIBLE_STATUSES = ("sent", "signed")


def invite(db: Session, project_id: int, email: str, invited_by: int | None) -> dict:
    mail = (email or "").strip().lower()
    if not mail:
        raise ValueError("Укажите электронную почту заказчика")
    user = db.scalar(select(User).where(User.email.ilike(mail)))
    if user is None:
        raise LookupError("Пользователь с такой почтой не найден: сначала создайте ему учётную запись")
    row = db.scalar(select(CustomerAccess).where(CustomerAccess.project_id == project_id, CustomerAccess.user_id == user.id))
    if row is None:
        row = CustomerAccess(project_id=project_id, user_id=user.id, invited_by_user_id=invited_by)
        db.add(row)
        db.flush()
    return {"id": row.id, "user_id": user.id, "email": user.email, "name": user.name}


def list_access(db: Session, project_id: int) -> list[dict]:
    rows = db.execute(
        select(CustomerAccess, User).join(User, User.id == CustomerAccess.user_id)
        .where(CustomerAccess.project_id == project_id).order_by(CustomerAccess.id)
    ).all()
    return [{"id": a.id, "user_id": u.id, "email": u.email, "name": u.name} for a, u in rows]


def revoke(db: Session, project_id: int, access_id: int) -> None:
    row = db.get(CustomerAccess, access_id)
    if row is None or row.project_id != project_id:
        raise LookupError("Доступ не найден")
    db.delete(row)
    db.flush()


def has_access(db: Session, user_id: int, project_id: int) -> bool:
    return db.scalar(select(CustomerAccess.id).where(CustomerAccess.project_id == project_id, CustomerAccess.user_id == user_id)) is not None


def my_projects(db: Session, user_id: int) -> list[dict]:
    rows = db.execute(
        select(Project.id, Project.name).join(CustomerAccess, CustomerAccess.project_id == Project.id)
        .where(CustomerAccess.user_id == user_id).order_by(Project.id)
    ).all()
    return [{"id": pid, "name": name} for pid, name in rows]


def summary(db: Session, project_id: int) -> dict:
    project = db.get(Project, project_id)
    estimates = []
    for e in db.execute(select(Estimate).where(Estimate.project_id == project_id, Estimate.status == "active").order_by(Estimate.id)).scalars().all():
        d = est.get_estimate(db, project_id, e.id)
        percent = None
        if d["total"] and float(d["total"]) > 0:
            percent = round(float(d["done_amount"]) / float(d["total"]) * 100, 1)
        estimates.append({"id": e.id, "name": e.name, "total": d["total"], "done_amount": d["done_amount"], "percent": percent})
    acts = db.execute(
        select(KsAct).where(KsAct.project_id == project_id, KsAct.status.in_(VISIBLE_STATUSES)).order_by(KsAct.id.desc())
    ).scalars().all()
    return {"project": {"id": project_id, "name": project.name if project else ""}, "estimates": estimates,
            "acts": [est._act_dict(a) for a in acts]}


def progress_log(db: Session, project_id: int, limit: int = 100) -> list[dict]:
    """Ход работ без внутренних данных: ни часов, ни людей, ни ставок."""
    rows = db.execute(
        select(WorkLogEntry).where(WorkLogEntry.project_id == project_id)
        .order_by(WorkLogEntry.work_date.desc(), WorkLogEntry.id.desc()).limit(limit)
    ).scalars().all()
    return [
        {"work_date": r.work_date.isoformat(), "work_name": r.work_name, "unit": r.unit,
         "qty": f"{r.qty:f}" if r.qty is not None else None, "location": r.location, "object_name": r.object_name}
        for r in rows
    ]


def visible_act(db: Session, project_id: int, act_id: int) -> KsAct:
    a = db.get(KsAct, act_id)
    if a is None or a.project_id != project_id or a.status not in VISIBLE_STATUSES:
        raise LookupError("Акт не найден")
    return a


def header_of(a: KsAct) -> dict:
    try:
        header = json.loads(a.header_json) if a.header_json else {}
    except (ValueError, TypeError, RecursionError):
        return {}
    return header if isinstance(header, dict) else {}


def decision_history(db: Session, project_id: int, act_id: int, *, limit: int = 100) -> dict:
    """Internal accessor; the authenticated API must enforce customer access first.

    Read at most limit+1 audit rows, newest first. Invalid legacy payloads are
    omitted; has_more describes the raw bounded audit window, not valid events.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Лимит истории должен быть от 1 до 100")
    with db.no_autoflush:
        visible = db.scalar(select(KsAct.id).where(
            KsAct.id == act_id, KsAct.project_id == project_id,
            KsAct.status.in_(VISIBLE_STATUSES),
        ))
        if visible is None:
            raise LookupError("Акт не найден")
        rows = db.execute(select(AuditLog.id, AuditLog.created_at, AuditLog.details).where(
            AuditLog.action == "customer_act_decision", AuditLog.entity_type == "ks_act",
            AuditLog.entity_id == act_id,
        ).order_by(AuditLog.id.desc()).limit(limit + 1)).all()
    events = []
    for row in rows[:limit]:
        try:
            payload = json.loads(row.details or "null")
        except (ValueError, TypeError, RecursionError):
            continue
        if not isinstance(payload, dict):
            continue
        if type(payload.get("project_id")) is not int or payload["project_id"] != project_id:
            continue
        if type(payload.get("user_id")) is not int:
            continue
        if not isinstance(payload.get("decision"), str) or payload["decision"] not in {"approved", "rejected"}:
            continue
        comment = payload.get("comment")
        if comment is not None and not isinstance(comment, str):
            continue
        events.append({
            "id": row.id, "created_at": row.created_at.isoformat(),
            "user_id": payload["user_id"], "decision": payload["decision"],
            "comment": comment,
        })
    return {"events": events, "has_more": len(rows) > limit}


def decide(db: Session, project_id: int, act_id: int, user_id: int, decision: str, comment: str | None) -> dict:
    if decision not in {"approved", "rejected"}:
        raise ValueError("Решение: approved или rejected")
    a = visible_act(db, project_id, act_id)
    if a.status == "signed":
        raise ValueError("Акт уже подписан")
    if a.customer_status in {"approved", "rejected"}:
        raise ValueError("Решение по акту уже принято")
    text = (comment or "").strip()
    if decision == "rejected" and not text:
        raise ValueError("Укажите причину отклонения")
    result = db.execute(
        update(KsAct).where(
            KsAct.id == act_id, KsAct.project_id == project_id,
            KsAct.status == "sent",
            or_(KsAct.customer_status.is_(None),
                KsAct.customer_status.not_in(("approved", "rejected"))),
        ).values(
            customer_status=decision, customer_comment=text or None,
            customer_decided_at=datetime.now(timezone.utc),
            customer_decided_by=user_id,
        ).execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        current = db.execute(select(KsAct.status, KsAct.customer_status).where(
            KsAct.id == act_id, KsAct.project_id == project_id,
        )).first()
        if current is None or current.status not in VISIBLE_STATUSES:
            raise LookupError("Акт не найден")
        if current.status == "signed":
            raise ValueError("Акт уже подписан")
        raise ValueError("Решение по акту уже принято")
    db.refresh(a)
    db.add(AuditLog(
        action="customer_act_decision", entity_type="ks_act", entity_id=act_id,
        details=json.dumps({
            "project_id": project_id, "user_id": user_id,
            "decision": decision, "comment": text or None,
        }, ensure_ascii=False),
    ))
    db.flush()
    return est._act_dict(a)
