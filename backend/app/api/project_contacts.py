from __future__ import annotations

from email.utils import parseaddr
from hashlib import sha256
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.project_contact import ProjectContact
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


def normalize_email(value: str) -> str:
    email = parseaddr(value.strip())[1].strip().casefold()
    if not email or "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(422, "Введите корректный email клиента")
    return email


def payload(row: ProjectContact) -> dict:
    return {
        "id": row.id, "project_id": row.project_id, "contract_id": row.contract_id,
        "name": row.name, "company": row.company, "email": row.email,
        "active": row.active, "confirmed": row.confirmed, "source": row.source,
        "company_activity": row.company_activity, "created_at": row.created_at,
    }


def contact_for_sender(db: Session, fallback_project_id: int, sender: str, user: User) -> ProjectContact | None:
    fallback = db.get(Project, fallback_project_id)
    if fallback is None:
        return None
    try:
        normalized = normalize_email(sender)
    except HTTPException:
        return None
    contact = db.scalar(select(ProjectContact).where(
        ProjectContact.organization_id == fallback.organization_id,
        ProjectContact.normalized_email == normalized,
        ProjectContact.active.is_(True),
        ProjectContact.confirmed.is_(True),
    ))
    if contact is None or user.is_admin:
        return contact
    allowed = db.scalar(select(ProjectMember.id).where(
        ProjectMember.project_id == contact.project_id,
        ProjectMember.user_id == user.id,
    ))
    return contact if allowed else None


def discover_contact_from_message(db: Session, project_id: int, sender: str, content: str, user: User) -> ProjectContact | None:
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
    row = db.scalar(select(ProjectContact).where(
        ProjectContact.organization_id == project.organization_id,
        ProjectContact.normalized_email == normalized,
    ))
    if row is None:
        row = ProjectContact(
            organization_id=project.organization_id, project_id=project.id,
            created_by_user_id=user.id, name=display_name, company=company,
            email=normalized, normalized_email=normalized, source="gmail",
            company_activity=activity, confirmed=False, active=True,
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
        confirmed=True, source="manual",
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
    if data.confirmed is not None: row.confirmed = data.confirmed
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
