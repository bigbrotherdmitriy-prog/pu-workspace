from __future__ import annotations

from email.utils import parseaddr
from hashlib import sha256
import json
import re
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.project_contact import ProjectContact, ProjectContactHistory
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.user import User

router = APIRouter(prefix="/project-contacts", tags=["project-contacts"])


class ContactCreate(BaseModel):
    project_id: int
    contract_id: int | None = None
    name: str = Field(min_length=1, max_length=300)
    company: str | None = Field(default=None, max_length=500)
    email: str = Field(min_length=3, max_length=500)
    phone: str | None = Field(default=None, max_length=100)


class ContactDraftCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)


class ContactUpdate(BaseModel):
    project_id: int | None = None
    contract_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=300)
    company: str | None = Field(default=None, max_length=500)
    company_activity: str | None = Field(default=None, max_length=4000)
    confirmed: bool | None = None
    active: bool | None = None


class ContactResolutionCommand(BaseModel):
    decision_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    expected_record_version: int = Field(ge=1)
    decision: Literal["confirm", "correct", "reject"]
    project_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=300)
    company: str | None = Field(default=None, max_length=500)
    email: str | None = Field(default=None, min_length=3, max_length=500)
    phone: str | None = Field(default=None, max_length=100)
    reason_code: str = Field(default="human_review", pattern=r"^[a-z0-9_]{3,50}$")


def normalize_email(value: str) -> str:
    email = parseaddr(value.strip())[1].strip()
    if not email or "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(422, "Введите корректный email клиента")
    local, domain = email.rsplit("@", 1)
    try:
        normalized_domain = domain.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise HTTPException(422, "Введите корректный email клиента") from exc
    if not normalized_domain or "." not in normalized_domain:
        raise HTTPException(422, "Введите корректный email клиента")
    return f"{local.casefold()}@{normalized_domain}"


def normalize_domain(email: str) -> str:
    return normalize_email(email).rsplit("@", 1)[1]


def normalize_phone(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    compact = re.sub(r"[^0-9+]", "", value.strip())
    if compact.startswith("8") and len(re.sub(r"\D", "", compact)) == 11:
        compact = "+7" + compact[1:]
    elif not compact.startswith("+"):
        compact = "+" + compact
    if not re.fullmatch(r"\+[1-9][0-9]{6,14}", compact):
        raise HTTPException(422, "Введите корректный телефон клиента")
    return compact


def payload(row: ProjectContact) -> dict:
    return {
        "id": row.id, "project_id": row.project_id, "contract_id": row.contract_id,
        "name": row.name, "company": row.company, "email": row.email,
        "phone": row.phone, "normalized_domain": row.normalized_domain,
        "mail_connection_id": row.mail_connection_id,
        "active": row.active, "confirmed": row.confirmed, "source": row.source,
        "record_version": row.record_version, "resolution_state": row.resolution_state,
        "resolution_reason_code": row.resolution_reason_code,
        "company_activity": row.company_activity, "created_at": row.created_at,
    }


def contact_for_sender(db: Session, fallback_project_id: int, sender: str, user: User,
                       *, mail_connection_id: str | None = None) -> ProjectContact | None:
    fallback = db.get(Project, fallback_project_id)
    if fallback is None:
        return None
    try:
        normalized = normalize_email(sender)
    except HTTPException:
        return None
    statement = select(ProjectContact).where(
        ProjectContact.organization_id == fallback.organization_id,
        ProjectContact.normalized_email == normalized,
        ProjectContact.mail_connection_id == mail_connection_id,
        ProjectContact.active.is_(True),
        ProjectContact.confirmed.is_(True),
    )
    candidates = list(db.scalars(statement.order_by(ProjectContact.id)))
    if not user.is_admin:
        candidates = [contact for contact in candidates if db.scalar(select(ProjectMember.id).where(
            ProjectMember.project_id == contact.project_id, ProjectMember.user_id == user.id,
        ))]
    # More than one project in the same mailbox is a conflict requiring review.
    return candidates[0] if len(candidates) == 1 else None


def discover_contact_from_message(db: Session, project_id: int, sender: str, content: str, user: User,
                                  *, mail_connection_id: str | None = None,
                                  source_message_id: int | None = None) -> ProjectContact | None:
    project = db.get(Project, project_id)
    if project is None:
        return None
    try:
        normalized = normalize_email(sender)
    except HTTPException:
        return None
    display_name = parseaddr(sender.strip())[0].strip() or normalized.split("@", 1)[0]
    domain = normalized.rsplit("@", 1)[1]
    personal_domains = {"gmail.com", "googlemail.com", "mail.ru", "yandex.ru", "ya.ru", "outlook.com", "hotmail.com", "icloud.com"}
    company = "Частный контакт" if domain in personal_domains else domain
    activity = " ".join(content.split())[:500] or None
    query = select(ProjectContact).where(
        ProjectContact.organization_id == project.organization_id,
        ProjectContact.normalized_email == normalized,
        ProjectContact.mail_connection_id == mail_connection_id,
    )
    if mail_connection_id is not None:
        query = query.where(ProjectContact.project_id == project.id)
    row = db.scalar(query.order_by(ProjectContact.id))
    if row is None:
        row = ProjectContact(
            organization_id=project.organization_id, project_id=project.id,
            created_by_user_id=user.id, name=display_name, company=company,
            email=normalized, normalized_email=normalized, source="gmail",
            normalized_domain=domain, mail_connection_id=mail_connection_id,
            source_message_id=source_message_id, company_activity=activity,
            confirmed=False, active=True, resolution_state="proposed",
            resolution_reason_code="gmail_sender_candidate",
        )
        db.add(row); db.flush()
        db.add(AuditLog(action="project_contact_discovered", entity_type="project_contact", entity_id=row.id,
                        details=f"project={project.id}; source=gmail; requires_confirmation=true"))
    elif not row.confirmed:
        # One email currently has one organization-wide row. Discovery must not
        # move it between projects or undo an operator's deactivation.
        if row.project_id == project.id and row.active:
            row.name = row.name or display_name
            row.company = row.company or company
            row.company_activity = activity or row.company_activity
    return row


def _snapshot_hash(values: dict) -> str:
    return sha256(json.dumps(values, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


@router.post("/{contact_id}/resolve")
def resolve_contact(contact_id: int, data: ContactResolutionCommand,
                    db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = db.get(ProjectContact, contact_id)
    if row is None:
        raise HTTPException(404, "Контакт не найден")
    require_project_role(db, user, row.project_id, "editor")
    command_hash = _snapshot_hash({"contact_id": contact_id, **data.model_dump(mode="json")})
    replay = db.scalar(select(ProjectContactHistory).where(
        ProjectContactHistory.organization_id == row.organization_id,
        ProjectContactHistory.decision_key == data.decision_key,
    ))
    if replay is not None:
        if replay.contact_id != row.id or replay.command_hash != command_hash:
            raise HTTPException(409, "Ключ решения уже использован с другим содержимым")
        result = payload(row)
        result["already_applied"] = True
        return result
    if row.record_version != data.expected_record_version:
        raise HTTPException(409, "Карточка контакта уже изменена; обновите данные")
    row_id = row.id
    organization_id = row.organization_id
    mail_connection_id = row.mail_connection_id
    from_state = row.resolution_state
    target_project_id = data.project_id or row.project_id
    target = db.get(Project, target_project_id)
    if target is None or target.organization_id != row.organization_id:
        raise HTTPException(422, "Проект не принадлежит организации контакта")
    require_project_role(db, user, target_project_id, "editor")

    email = normalize_email(data.email) if data.email is not None else row.normalized_email
    phone = normalize_phone(data.phone) if "phone" in data.model_fields_set else row.normalized_phone
    name = data.name.strip() if data.name is not None else row.name
    company = data.company.strip() or None if data.company is not None else row.company
    changed = sorted(name for name, changed_value in {
        "project_id": target_project_id != row.project_id,
        "name": name != row.name,
        "company": company != row.company,
        "email": email != row.normalized_email,
        "phone": phone != row.normalized_phone,
        "state": True,
    }.items() if changed_value)
    to_state = {"confirm": "confirmed", "correct": "corrected", "reject": "rejected"}[data.decision]
    confirmed = data.decision in {"confirm", "correct"}
    active = data.decision != "reject"

    if confirmed:
        conflicts = list(db.scalars(select(ProjectContact.id).where(
            ProjectContact.id != row.id,
            ProjectContact.organization_id == row.organization_id,
            ProjectContact.mail_connection_id == row.mail_connection_id,
            ProjectContact.normalized_email == email,
            ProjectContact.active.is_(True), ProjectContact.confirmed.is_(True),
        )))
        if conflicts:
            raise HTTPException(409, "Email имеет конфликтующие привязки; требуется отдельное решение")

    next_version = row.record_version + 1
    values = {
        "project_id": target_project_id, "name": name, "company": company,
        "email": email, "normalized_email": email, "normalized_domain": normalize_domain(email),
        "phone": phone, "normalized_phone": phone, "confirmed": confirmed, "active": active,
        "resolution_state": to_state, "resolution_reason_code": data.reason_code,
        "record_version": next_version,
    }
    updated = db.execute(update(ProjectContact).where(
        ProjectContact.id == row.id,
        ProjectContact.record_version == data.expected_record_version,
    ).values(**values))
    if updated.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "Карточка контакта уже изменена; обновите данные")
    sequence = int(db.scalar(select(func.count(ProjectContactHistory.id)).where(
        ProjectContactHistory.contact_id == row.id,
    )) or 0) + 1
    db.add(ProjectContactHistory(
        contact_id=row_id, organization_id=organization_id, project_id=target_project_id,
        mail_connection_id=mail_connection_id, sequence=sequence, event=data.decision,
        decision_key=data.decision_key, command_hash=command_hash,
        from_state=from_state, to_state=to_state, resulting_version=next_version,
        actor_user_id=user.id, reason_code=data.reason_code, changed_fields=changed,
        snapshot_hash=_snapshot_hash({
            "contact_id": row_id, "project_id": target_project_id,
            "mail_connection_id": mail_connection_id, "email_hash": sha256(email.encode()).hexdigest(),
            "phone_hash": sha256((phone or "").encode()).hexdigest(), "version": next_version,
        }),
    ))
    db.add(AuditLog(
        action="project_contact_resolved", entity_type="project_contact", entity_id=row_id,
        details=(f"project={target_project_id};mailbox_scoped={mail_connection_id is not None};"
                 f"decision={data.decision};reason={data.reason_code};version={next_version}"),
    ))
    db.commit()
    return payload(db.get(ProjectContact, row_id))


@router.get("")
def list_contacts(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    rows = list(db.scalars(select(ProjectContact).where(
        ProjectContact.project_id == project_id,
        ProjectContact.active.is_(True),
    ).order_by(ProjectContact.company, ProjectContact.name, ProjectContact.id)))
    return {"contacts": [payload(row) for row in rows]}


@router.post("")
def create_contact(data: ContactCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, data.project_id, "editor")
    project = db.get(Project, data.project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    if data.contract_id is not None:
        contract = db.get(Contract, data.contract_id)
        if contract is None or contract.project_id != data.project_id:
            raise HTTPException(422, "Договор не принадлежит выбранному проекту")
    normalized = normalize_email(data.email)
    row = ProjectContact(
        organization_id=project.organization_id, project_id=project.id,
        contract_id=data.contract_id, created_by_user_id=user.id,
        name=data.name.strip(), company=data.company.strip() if data.company else None,
        email=normalized, normalized_email=normalized,
        normalized_domain=normalize_domain(normalized), phone=normalize_phone(data.phone),
        normalized_phone=normalize_phone(data.phone), confirmed=True, source="manual",
        resolution_state="confirmed", resolution_reason_code="manual",
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Этот email уже закреплён за проектом организации")
    db.add(AuditLog(action="project_contact_created", entity_type="project_contact", entity_id=row.id,
                    details=f"project={row.project_id}; contract={row.contract_id}; confirmed=true"))
    db.commit(); db.refresh(row)
    return payload(row)


@router.patch("/{contact_id}")
def update_contact(contact_id: int, data: ContactUpdate,
                   db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = db.get(ProjectContact, contact_id)
    if row is None:
        raise HTTPException(404, "Контакт не найден")
    if data.confirmed is not None:
        raise HTTPException(409, "Подтверждение контакта требует версионного решения /resolve")
    require_project_role(db, user, row.project_id, "editor")
    target_project_id = data.project_id or row.project_id
    target = db.get(Project, target_project_id)
    if target is None or target.organization_id != row.organization_id:
        raise HTTPException(422, "Проект не принадлежит организации контакта")
    require_project_role(db, user, target_project_id, "editor")
    if data.contract_id is not None:
        contract = db.get(Contract, data.contract_id)
        if contract is None or contract.project_id != target_project_id:
            raise HTTPException(422, "Договор не принадлежит выбранному проекту")
    if row.project_id != target_project_id:
        row.contract_id = None
    row.project_id = target_project_id
    if "contract_id" in data.model_fields_set: row.contract_id = data.contract_id
    if data.name is not None: row.name = data.name.strip()
    if data.company is not None: row.company = data.company.strip() or None
    if data.company_activity is not None: row.company_activity = data.company_activity.strip() or None
    if data.active is not None: row.active = data.active
    db.add(AuditLog(action="project_contact_updated", entity_type="project_contact", entity_id=row.id,
                    details=f"project={row.project_id}; contract={row.contract_id}; confirmed={row.confirmed}"))
    db.commit(); db.refresh(row)
    return payload(row)


@router.post("/{contact_id}/draft")
def create_contact_draft(contact_id: int, data: ContactDraftCreate,
                         db: Session = Depends(get_db), user: User = Depends(require_user)):
    contact = db.get(ProjectContact, contact_id)
    if contact is None or not contact.active:
        raise HTTPException(404, "Контакт не найден")
    require_project_role(db, user, contact.project_id, "editor")
    source_id = f"contact:{contact.id}:{uuid4()}"
    excerpt = f"Получатель: {contact.name} <{contact.email}>"
    row = ResponseDraft(
        project_id=contact.project_id, reviewer_user_id=user.id,
        subject=data.subject.strip(), body=data.body.strip(), recipient_to=contact.email,
        status="draft", source_file_id=source_id, source_file_name=contact.name,
        source_excerpt=excerpt, source_excerpt_hash=sha256(f"{source_id}:{data.subject}:{data.body}".encode()).hexdigest(),
        confidence=1.0,
    )
    db.add(row); db.flush()
    db.add(AuditLog(action="project_contact_draft_created", entity_type="response_draft", entity_id=row.id,
                    details=f"contact={contact.id}; project={contact.project_id}; requires_approval=true"))
    db.commit()
    return {"draft_id": row.id, "recipient_to": contact.email, "status": row.status, "requires_approval": True}
