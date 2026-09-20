from __future__ import annotations

from email.utils import parseaddr
from hashlib import sha256
from datetime import datetime, timezone
import json
import re
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.ai_secretary import Message
from app.models.management import ManagementHistory
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.project_contact import ContactConflict, ProjectContact
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.user import User
from app.models.v54_pilot import ConnectionIdentity, MailConnection
from app.api.management import _locked_versioned, append_management_history

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
    expected_record_version: int = Field(default=1, ge=1)
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


class ContactConflictResolve(BaseModel):
    decision_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    expected_record_version: int = Field(default=1, ge=1)
    expected_contact_record_version: int = Field(default=1, ge=1)
    resolution: str = Field(pattern="^(keep_current|move_to_candidate|reject_candidate)$")
    reason: str = Field(min_length=2, max_length=2000)


def normalize_email(value: str) -> str:
    email = parseaddr(value.strip())[1].strip()
    if not email or "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(422, "Введите корректный email клиента")
    local, domain = email.rsplit("@", 1)
    try:
        normalized_domain = domain.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise HTTPException(422, "Введите корректный email клиента") from exc
    if not local or not normalized_domain or "." not in normalized_domain:
        raise HTTPException(422, "Введите корректный email клиента")
    return f"{local.casefold()}@{normalized_domain}"


def normalize_phone(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    compact = re.sub(r"[^0-9+]", "", value.strip())
    digits = re.sub(r"\D", "", compact)
    if compact.startswith("8") and len(digits) == 11:
        compact = "+7" + compact[1:]
    elif not compact.startswith("+"):
        compact = "+" + compact
    if not re.fullmatch(r"\+[1-9][0-9]{6,14}", compact):
        raise HTTPException(422, "Введите корректный телефон клиента")
    return compact


def _digest(value: str | None) -> str | None:
    return sha256(value.encode()).hexdigest() if value else None


def payload(row: ProjectContact) -> dict:
    return {
        "id": row.id, "record_version": row.record_version, "project_id": row.project_id, "contract_id": row.contract_id,
        "name": row.name, "company": row.company, "email": row.email, "phone": row.phone,
        "normalized_domain": row.normalized_domain,
        "active": row.active, "confirmed": row.confirmed, "source": row.source,
        "resolution_state": row.resolution_state,
        "resolution_reason_code": row.resolution_reason_code,
        "company_activity": row.company_activity, "created_at": row.created_at,
    }


def _history_payload(row: ProjectContact, *, normalized_email: str | None = None,
                     normalized_phone: str | None = None) -> dict:
    """PII-minimized state stored in generic ManagementHistory."""
    email = normalized_email if normalized_email is not None else row.normalized_email
    phone = normalized_phone if normalized_phone is not None else row.normalized_phone
    return {
        "project_id": row.project_id, "contract_id": row.contract_id,
        "mailbox_scoped": row.mail_connection_id is not None,
        "email_hash": _digest(email), "phone_hash": _digest(phone),
        "active": row.active, "confirmed": row.confirmed,
        "resolution_state": row.resolution_state,
        "resolution_reason_code": row.resolution_reason_code,
    }


def _command_hash(contact_id: int, data: BaseModel) -> str:
    encoded = json.dumps(
        {"contact_id": contact_id, **data.model_dump(mode="json")},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return sha256(encoded.encode()).hexdigest()


def _mailbox_is_active(db: Session, organization_id: int, mail_connection_id: str | None) -> bool:
    if mail_connection_id is None:
        return True
    return db.scalar(
        select(MailConnection.id)
        .join(ConnectionIdentity, ConnectionIdentity.id == MailConnection.identity_id)
        .where(
            MailConnection.id == mail_connection_id,
            MailConnection.organization_id == organization_id,
            MailConnection.state == "active",
            ConnectionIdentity.organization_id == organization_id,
            ConnectionIdentity.state == "verified",
        )
    ) is not None


def contact_for_sender(db: Session, fallback_project_id: int, sender: str, user: User,
                       *, mail_connection_id: str | None = None) -> ProjectContact | None:
    fallback = db.get(Project, fallback_project_id)
    if fallback is None:
        return None
    try:
        normalized = normalize_email(sender)
    except HTTPException:
        return None
    if not _mailbox_is_active(db, fallback.organization_id, mail_connection_id):
        return None
    contacts = list(db.scalars(select(ProjectContact).where(
        ProjectContact.organization_id == fallback.organization_id,
        ProjectContact.normalized_email == normalized,
        ProjectContact.mail_connection_id == mail_connection_id,
        ProjectContact.active.is_(True),
        ProjectContact.confirmed.is_(True),
    ).order_by(ProjectContact.id)))
    if not user.is_admin:
        contacts = [contact for contact in contacts if db.scalar(select(ProjectMember.id).where(
            ProjectMember.project_id == contact.project_id,
            ProjectMember.user_id == user.id,
        ))]
    # Multiple confirmed candidates under one mailbox are never guessed.
    return contacts[0] if len(contacts) == 1 else None


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
    if not _mailbox_is_active(db, project.organization_id, mail_connection_id):
        return None
    if source_message_id is not None:
        source_message = db.get(Message, source_message_id)
        if (source_message is None or source_message.organization_id != project.organization_id
                or source_message.mail_connection_id != mail_connection_id):
            return None
    display_name = parseaddr(sender.strip())[0].strip() or normalized.split("@", 1)[0]
    domain = normalized.rsplit("@", 1)[1]
    personal_domains = {"gmail.com", "googlemail.com", "mail.ru", "yandex.ru", "ya.ru", "outlook.com", "hotmail.com", "icloud.com"}
    company = "Частный контакт" if domain in personal_domains else domain
    activity = " ".join(content.split())[:500] or None
    row = db.scalar(select(ProjectContact).where(
        ProjectContact.organization_id == project.organization_id,
        ProjectContact.normalized_email == normalized,
        ProjectContact.mail_connection_id == mail_connection_id,
    ).with_for_update())
    if row is None:
        row = ProjectContact(
            organization_id=project.organization_id, project_id=project.id,
            created_by_user_id=user.id, name=display_name, company=company,
            email=normalized, normalized_email=normalized, source="gmail",
            normalized_domain=domain, mail_connection_id=mail_connection_id,
            source_message_id=source_message_id,
            company_activity=activity, confirmed=False, active=True,
            resolution_state="proposed", resolution_reason_code="gmail_sender_candidate",
        )
        db.add(row); db.flush()
        append_management_history(
            db, project_id=project.id, entity_type="project_contact", entity_id=row.id,
            record_version=row.record_version, action="discovered", actor_user_id=user.id,
            old_values={}, new_values=_history_payload(row),
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
            old = _history_payload(row)
            row.name = row.name or display_name
            row.company = row.company or company
            row.company_activity = activity or row.company_activity
            if _history_payload(row) != old:
                row.record_version += 1
                append_management_history(
                    db, project_id=project.id, entity_type="project_contact", entity_id=row.id,
                    record_version=row.record_version, action="discovery_refreshed", actor_user_id=user.id,
                    old_values=old, new_values=_history_payload(row),
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
        normalized_domain=normalized.rsplit("@", 1)[1],
        phone=normalize_phone(data.phone), normalized_phone=normalize_phone(data.phone),
        confirmed=True, source="manual", resolution_state="confirmed",
        resolution_reason_code="manual",
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
                              old_values={}, new_values=_history_payload(row),
                              evidence={"source": row.source}, reason="Контакт добавлен вручную")
    db.commit(); db.refresh(row)
    return payload(row)


@router.patch("/{contact_id}")
def update_contact(contact_id: int, data: ContactUpdate,
                   db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = _locked_versioned(db, ProjectContact, contact_id, data.expected_record_version, "Контакт")
    require_project_role(db, user, row.project_id, "editor")
    if data.confirmed is not None:
        raise HTTPException(409, "Подтверждение контакта требует версионного решения /resolve")
    old = _history_payload(row)
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
    row.record_version += 1
    append_management_history(db, project_id=row.project_id, entity_type="project_contact", entity_id=row.id,
                              record_version=row.record_version, action="updated", actor_user_id=user.id,
                              old_values=old, new_values=_history_payload(row), evidence={"source": row.source},
                              reason="Подтверждение или изменение привязки контакта")
    db.add(AuditLog(action="project_contact_updated", entity_type="project_contact", entity_id=row.id,
                    details=f"project={row.project_id}; contract={row.contract_id}; confirmed={row.confirmed}"))
    db.commit(); db.refresh(row)
    return payload(row)


def _resolution_replay(db: Session, *, organization_id: int, contact_id: int,
                       decision_key: str, command_hash: str) -> ManagementHistory | None:
    history = db.scalar(select(ManagementHistory).where(
        ManagementHistory.organization_id == organization_id,
        ManagementHistory.entity_type == "project_contact",
        ManagementHistory.idempotency_key == decision_key,
    ))
    if history is None:
        return None
    if history.entity_id != contact_id or history.command_hash != command_hash:
        raise HTTPException(409, "Ключ решения уже использован с другим содержимым")
    return history


@router.post("/{contact_id}/resolve")
def resolve_contact(contact_id: int, data: ContactResolutionCommand,
                    db: Session = Depends(get_db), user: User = Depends(require_user)):
    observed = db.get(ProjectContact, contact_id)
    if observed is None:
        raise HTTPException(404, "Контакт не найден")
    require_project_role(db, user, observed.project_id, "editor")
    command_hash = _command_hash(contact_id, data)
    replay = _resolution_replay(
        db, organization_id=observed.organization_id, contact_id=contact_id,
        decision_key=data.decision_key, command_hash=command_hash,
    )
    if replay is not None:
        # ``observed`` may have been loaded before the transaction that
        # recorded the replay committed its contact update.  Refresh it so an
        # exact concurrent replay returns the applied version, not the stale
        # identity-map snapshot that preceded the decision.
        db.refresh(observed)
        result = payload(observed); result["already_applied"] = True
        return result

    row = db.scalar(
        select(ProjectContact).where(ProjectContact.id == contact_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "Контакт не найден")
    # Re-check after acquiring the row lock so an exact concurrent replay is
    # successful rather than becoming a stale-version error.
    replay = _resolution_replay(
        db, organization_id=row.organization_id, contact_id=contact_id,
        decision_key=data.decision_key, command_hash=command_hash,
    )
    if replay is not None:
        result = payload(row); result["already_applied"] = True
        return result
    if row.record_version != data.expected_record_version:
        raise HTTPException(409, {"code": "record_version_conflict", "expected": data.expected_record_version,
                                  "actual": row.record_version})

    target_project_id = data.project_id or row.project_id
    target = db.get(Project, target_project_id)
    if target is None or target.organization_id != row.organization_id:
        raise HTTPException(422, "Проект не принадлежит организации контакта")
    require_project_role(db, user, target_project_id, "editor")
    normalized_email = normalize_email(data.email) if data.email is not None else row.normalized_email
    normalized_phone = normalize_phone(data.phone) if "phone" in data.model_fields_set else row.normalized_phone

    if data.decision in {"confirm", "correct"}:
        collision = db.scalar(select(ProjectContact.id).where(
            ProjectContact.id != row.id,
            ProjectContact.organization_id == row.organization_id,
            ProjectContact.mail_connection_id == row.mail_connection_id,
            ProjectContact.normalized_email == normalized_email,
            ProjectContact.active.is_(True), ProjectContact.confirmed.is_(True),
        ))
        if collision is not None:
            raise HTTPException(409, "Email имеет конфликтующие привязки; требуется отдельное решение")

    old = _history_payload(row)
    if row.project_id != target_project_id:
        row.contract_id = None
    row.project_id = target_project_id
    if data.name is not None: row.name = data.name.strip()
    if data.company is not None: row.company = data.company.strip() or None
    row.email = normalized_email
    row.normalized_email = normalized_email
    row.normalized_domain = normalized_email.rsplit("@", 1)[1]
    if "phone" in data.model_fields_set:
        row.phone = normalized_phone
        row.normalized_phone = normalized_phone
    row.confirmed = data.decision in {"confirm", "correct"}
    row.active = data.decision != "reject"
    row.resolution_state = {"confirm": "confirmed", "correct": "corrected", "reject": "rejected"}[data.decision]
    row.resolution_reason_code = data.reason_code
    row.record_version += 1
    append_management_history(
        db, project_id=row.project_id, entity_type="project_contact", entity_id=row.id,
        record_version=row.record_version, action=f"resolution_{data.decision}", actor_user_id=user.id,
        old_values=old, new_values=_history_payload(row),
        evidence={"mailbox_scoped": row.mail_connection_id is not None},
        reason=data.reason_code, idempotency_key=data.decision_key, command_hash=command_hash,
    )
    db.add(AuditLog(
        action="project_contact_resolved", entity_type="project_contact", entity_id=row.id,
        details=(f"project={row.project_id}; mailbox_scoped={row.mail_connection_id is not None};"
                 f"decision={data.decision}; reason={data.reason_code}; version={row.record_version}"),
    ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        replay = _resolution_replay(
            db, organization_id=observed.organization_id, contact_id=contact_id,
            decision_key=data.decision_key, command_hash=command_hash,
        )
        if replay is not None:
            current = db.get(ProjectContact, contact_id)
            result = payload(current); result["already_applied"] = True
            return result
        raise HTTPException(409, "Ключ решения уже использован с другим содержимым")
    db.refresh(row)
    return payload(row)


@router.post("/conflicts/{conflict_id}/resolve")
def resolve_contact_conflict(conflict_id: int, data: ContactConflictResolve,
                             db: Session = Depends(get_db), user: User = Depends(require_user)):
    observed = db.get(ContactConflict, conflict_id)
    if observed is None:
        raise HTTPException(404, "Contact conflict not found")
    command_hash = _command_hash(observed.contact_id, data)
    candidate = db.get(Project, observed.candidate_project_id)
    current = db.get(Project, observed.current_project_id)
    if current is None or candidate is None or current.organization_id != observed.organization_id or candidate.organization_id != observed.organization_id:
        raise HTTPException(409, "Contact conflict tenant binding is invalid")
    require_project_role(db, user, observed.current_project_id, "editor")
    require_project_role(db, user, observed.candidate_project_id, "editor")

    def replay() -> ManagementHistory | None:
        history = db.scalar(select(ManagementHistory).where(
            ManagementHistory.organization_id == observed.organization_id,
            ManagementHistory.entity_type == "contact_conflict",
            ManagementHistory.idempotency_key == data.decision_key,
        ))
        if history is not None and (history.entity_id != conflict_id or history.command_hash != command_hash):
            raise HTTPException(409, "Ключ решения уже использован с другим содержимым")
        return history

    if replay() is not None:
        return {"id": observed.id, "record_version": observed.record_version,
                "status": observed.status, "resolution": observed.resolution,
                "contact": payload(db.get(ProjectContact, observed.contact_id)), "already_applied": True}
    conflict = db.scalar(
        select(ContactConflict).where(ContactConflict.id == conflict_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if replay() is not None:
        return {"id": conflict.id, "record_version": conflict.record_version,
                "status": conflict.status, "resolution": conflict.resolution,
                "contact": payload(db.get(ProjectContact, conflict.contact_id)), "already_applied": True}
    if conflict.record_version != data.expected_record_version:
        raise HTTPException(409, {"code": "record_version_conflict", "expected": data.expected_record_version,
                                  "actual": conflict.record_version})
    if conflict.status != "pending":
        raise HTTPException(409, "Contact conflict is already resolved")
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
                              evidence={"contact_id": contact.id}, reason=conflict.reason,
                              idempotency_key=data.decision_key, command_hash=command_hash)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if replay() is not None:
            current_conflict = db.get(ContactConflict, conflict_id)
            return {"id": current_conflict.id, "record_version": current_conflict.record_version,
                    "status": current_conflict.status, "resolution": current_conflict.resolution,
                    "contact": payload(db.get(ProjectContact, current_conflict.contact_id)),
                    "already_applied": True}
        raise HTTPException(409, "Ключ решения уже использован с другим содержимым")
    db.refresh(conflict)
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
