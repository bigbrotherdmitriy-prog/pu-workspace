from __future__ import annotations

from email.utils import parseaddr
from hashlib import sha256
from datetime import datetime, timezone
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
from app.models.project_contact import ContactConflict, ProjectContact
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.user import User
from app.api.management import _locked_versioned, append_management_history

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
    expected_record_version: int = Field(default=1, ge=1)
    project_id: int | None = None
    contract_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=300)
    company: str | None = Field(default=None, max_length=500)
    company_activity: str | None = Field(default=None, max_length=4000)
    confirmed: bool | None = None
    active: bool | None = None


class ContactConflictResolve(BaseModel):
    expected_record_version: int = Field(default=1, ge=1)
    expected_contact_record_version: int = Field(default=1, ge=1)
    resolution: str = Field(pattern="^(keep_current|move_to_candidate|reject_candidate)$")
    reason: str = Field(min_length=2, max_length=2000)


def normalize_email(value: str) -> str:
    email = parseaddr(value.strip())[1].strip().casefold()
    if not email or "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(422, "Введите корректный email клиента")
    return email


def payload(row: ProjectContact) -> dict:
    return {
        "id": row.id, "record_version": row.record_version, "project_id": row.project_id, "contract_id": row.contract_id,
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
    ).with_for_update())
    if row is None:
        row = ProjectContact(
            organization_id=project.organization_id, project_id=project.id,
            created_by_user_id=user.id, name=display_name, company=company,
            email=normalized, normalized_email=normalized, source="gmail",
            company_activity=activity, confirmed=False, active=True,
        )
        db.add(row); db.flush()
        append_management_history(
            db, project_id=project.id, entity_type="project_contact", entity_id=row.id,
            record_version=row.record_version, action="discovered", actor_user_id=user.id,
            old_values={}, new_values=payload(row),
            evidence={"source": "gmail", "normalized_email_hash": sha256(normalized.encode()).hexdigest()},
            reason="Контакт обнаружен во входящем сообщении и требует подтверждения",
        )
        db.add(AuditLog(action="project_contact_discovered", entity_type="project_contact", entity_id=row.id,
                        details=f"project={project.id}; source=gmail; requires_confirmation=true"))
    elif row.project_id != project.id:
        conflict = db.scalar(select(ContactConflict).where(
            ContactConflict.organization_id == project.organization_id,
            ContactConflict.contact_id == row.id,
            ContactConflict.candidate_project_id == project.id,
            ContactConflict.status == "pending",
        ))
        if conflict is None:
            conflict = ContactConflict(
                organization_id=project.organization_id, contact_id=row.id,
                current_project_id=row.project_id, candidate_project_id=project.id,
                normalized_email=normalized, status="pending",
            )
            db.add(conflict); db.flush()
            append_management_history(
                db, project_id=project.id, entity_type="contact_conflict", entity_id=conflict.id,
                record_version=conflict.record_version, action="detected", actor_user_id=user.id,
                old_values={"current_project_id": row.project_id},
                new_values={"candidate_project_id": project.id, "status": "pending"},
                evidence={"normalized_email_hash": sha256(normalized.encode()).hexdigest()},
                reason="Один email обнаружен в другом проекте организации",
            )
        # An unresolved conflict must never silently route mail to either project.
        return None
    elif not row.confirmed:
        # One email currently has one organization-wide row. Discovery must not
        # move it between projects or undo an operator's deactivation.
        if row.project_id == project.id and row.active:
            old = payload(row)
            row.name = row.name or display_name
            row.company = row.company or company
            row.company_activity = activity or row.company_activity
            if payload(row) != old:
                row.record_version += 1
                append_management_history(
                    db, project_id=project.id, entity_type="project_contact", entity_id=row.id,
                    record_version=row.record_version, action="discovery_refreshed", actor_user_id=user.id,
                    old_values=old, new_values=payload(row),
                    evidence={"source": "gmail", "normalized_email_hash": sha256(normalized.encode()).hexdigest()},
                    reason="Контекст неподтверждённого контакта дополнен новым сообщением",
                )
    return row


@router.get("")
def list_contacts(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user),
                  confirmed: bool | None = None, cursor: int | None = None, limit: int = 100):
    require_project_role(db, user, project_id, "viewer")
    if not 1 <= limit <= 200: raise HTTPException(422, "limit must be between 1 and 200")
    query = select(ProjectContact).where(ProjectContact.project_id == project_id, ProjectContact.active.is_(True))
    if confirmed is not None: query = query.where(ProjectContact.confirmed.is_(confirmed))
    if cursor is not None: query = query.where(ProjectContact.id < cursor)
    rows = list(db.scalars(query.order_by(ProjectContact.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit; rows = rows[:limit]
    return {"contacts": [payload(row) for row in rows],
            "next_cursor": rows[-1].id if has_more and rows else None}


@router.get("/conflicts")
def list_contact_conflicts(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user),
                           status: str = "pending", cursor: int | None = None, limit: int = 100):
    require_project_role(db, user, project_id, "viewer")
    if not 1 <= limit <= 200: raise HTTPException(422, "limit must be between 1 and 200")
    query = (
        select(ContactConflict, ProjectContact)
        .join(ProjectContact, ProjectContact.id == ContactConflict.contact_id)
        .where(ContactConflict.candidate_project_id == project_id,
               ContactConflict.status == status)
    )
    if cursor is not None: query = query.where(ContactConflict.id < cursor)
    rows = db.execute(query.order_by(ContactConflict.id.desc()).limit(limit + 1)).all()
    has_more = len(rows) > limit; rows = rows[:limit]
    return {"conflicts": [{"id": conflict.id, "record_version": conflict.record_version,
                            "contact_id": conflict.contact_id,
                            "contact_record_version": contact.record_version,
                            "contact_name": contact.name, "contact_email": contact.email,
                            "current_project_id": conflict.current_project_id,
                            "candidate_project_id": conflict.candidate_project_id,
                            "status": conflict.status, "resolution": conflict.resolution,
                            "reason": conflict.reason} for conflict, contact in rows],
            "next_cursor": rows[-1][0].id if has_more and rows else None}


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
    append_management_history(db, project_id=row.project_id, entity_type="project_contact", entity_id=row.id,
                              record_version=row.record_version, action="created", actor_user_id=user.id,
                              old_values={}, new_values=payload(row),
                              evidence={"source": row.source}, reason="Контакт добавлен вручную")
    db.commit(); db.refresh(row)
    return payload(row)


@router.patch("/{contact_id}")
def update_contact(contact_id: int, data: ContactUpdate,
                   db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = _locked_versioned(db, ProjectContact, contact_id, data.expected_record_version, "Контакт")
    require_project_role(db, user, row.project_id, "editor")
    old = payload(row)
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
    row.record_version += 1
    append_management_history(db, project_id=row.project_id, entity_type="project_contact", entity_id=row.id,
                              record_version=row.record_version, action="updated", actor_user_id=user.id,
                              old_values=old, new_values=payload(row), evidence={"source": row.source},
                              reason="Подтверждение или изменение привязки контакта")
    db.add(AuditLog(action="project_contact_updated", entity_type="project_contact", entity_id=row.id,
                    details=f"project={row.project_id}; contract={row.contract_id}; confirmed={row.confirmed}"))
    db.commit(); db.refresh(row)
    return payload(row)


@router.post("/conflicts/{conflict_id}/resolve")
def resolve_contact_conflict(conflict_id: int, data: ContactConflictResolve,
                             db: Session = Depends(get_db), user: User = Depends(require_user)):
    conflict = _locked_versioned(db, ContactConflict, conflict_id, data.expected_record_version, "Contact conflict")
    if conflict.status != "pending":
        raise HTTPException(409, "Contact conflict is already resolved")
    current = db.get(Project, conflict.current_project_id)
    candidate = db.get(Project, conflict.candidate_project_id)
    if current is None or candidate is None or current.organization_id != conflict.organization_id or candidate.organization_id != conflict.organization_id:
        raise HTTPException(409, "Contact conflict tenant binding is invalid")
    require_project_role(db, user, conflict.current_project_id, "editor")
    require_project_role(db, user, conflict.candidate_project_id, "editor")
    contact = _locked_versioned(db, ProjectContact, conflict.contact_id,
                                data.expected_contact_record_version, "Контакт")
    if contact.organization_id != conflict.organization_id or contact.project_id != conflict.current_project_id:
        raise HTTPException(409, "Contact conflict is stale")
    old_contact = payload(contact)
    if data.resolution == "move_to_candidate":
        contact.project_id = conflict.candidate_project_id
        contact.contract_id = None
        contact.confirmed = True
        contact.record_version += 1
        append_management_history(db, project_id=contact.project_id, entity_type="project_contact", entity_id=contact.id,
                                  record_version=contact.record_version, action="conflict_resolved", actor_user_id=user.id,
                                  old_values=old_contact, new_values=payload(contact),
                                  evidence={"contact_conflict_id": conflict.id}, reason=data.reason)
    conflict.status = "resolved"
    conflict.resolution = data.resolution
    conflict.reason = data.reason.strip()
    conflict.resolved_by_user_id = user.id
    conflict.resolved_at = datetime.now(timezone.utc)
    conflict.record_version += 1
    append_management_history(db, project_id=conflict.candidate_project_id, entity_type="contact_conflict",
                              entity_id=conflict.id, record_version=conflict.record_version,
                              action="resolved", actor_user_id=user.id,
                              old_values={"status": "pending"},
                              new_values={"status": conflict.status, "resolution": conflict.resolution},
                              evidence={"contact_id": contact.id}, reason=conflict.reason)
    db.commit(); db.refresh(conflict)
    return {"id": conflict.id, "record_version": conflict.record_version,
            "status": conflict.status, "resolution": conflict.resolution,
            "contact": payload(contact)}


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
        contract_id=contact.contract_id,
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
