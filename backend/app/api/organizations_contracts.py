from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_admin, require_project_role, require_user
from app.contract_terms_ai import extract_contract_terms_ai
from app.database import get_db
from app.contract_vat import contract_vat_snapshot
from app.finance_source_pins import resolve_current_document_pin
from app.finance_money import money as _money, project_currency, require_project_currency
from app.models.organization_contract import Contract, ContractVersion, Organization
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.governance import Decision, Risk
from app.models.management import Meeting, Obligation
from app.models.task import Task
from app.models.execution_finance import (
    AcceptanceAct, BudgetLine, CashFlowEntry, ContractBudgetProposal, CostCategory,
    ProcurementItem, ScheduleBaseline, ScheduleItem,
)
from app.models.ai_secretary import Message
from app.models.automation_rule import AutomationRule
from app.models.project_contact import ProjectContact
from app.core.integration_types import StorageObject
from app.core.contract_roles import allowed_parent_kinds, cash_flow_direction, is_financial_contract
from app.governance_engine import create_governance_items
from app.task_engine import create_tasks_from_files
from sqlalchemy import func
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.audit_log import AuditLog
from app.models.contract_document_link import ContractDocumentLink
from app.organization_requisites import remember_contract_organizations

router = APIRouter(tags=["organizations", "contracts"])


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    legal_name: str | None = Field(default=None, max_length=500)
    inn: str | None = Field(default=None, pattern=r"^(?:\d{10}|\d{12})$")
    kpp: str | None = Field(default=None, pattern=r"^\d{9}$")
    ogrn: str | None = Field(default=None, pattern=r"^(?:\d{13}|\d{15})$")
    okpo: str | None = Field(default=None, max_length=14)
    okato: str | None = Field(default=None, max_length=20)
    oktmo: str | None = Field(default=None, max_length=20)
    okogu: str | None = Field(default=None, max_length=20)
    okved: str | None = Field(default=None, max_length=500)
    legal_address: str | None = Field(default=None, max_length=2000)
    postal_address: str | None = Field(default=None, max_length=2000)
    phone: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    director_name: str | None = Field(default=None, max_length=255)
    chief_accountant: str | None = Field(default=None, max_length=255)
    registration_details: str | None = Field(default=None, max_length=2000)
    tax_office: str | None = Field(default=None, max_length=500)
    bank_name: str | None = Field(default=None, max_length=500)
    bank_address: str | None = Field(default=None, max_length=2000)
    settlement_account: str | None = Field(default=None, pattern=r"^\d{20}$")
    correspondent_account: str | None = Field(default=None, pattern=r"^\d{20}$")
    bik: str | None = Field(default=None, pattern=r"^\d{9}$")
    requisites_status: str | None = Field(default=None, pattern="^(draft|extracted|confirmed)$")


def _organization(row: Organization) -> dict:
    fields = (
        "id", "name", "legal_name", "inn", "kpp", "ogrn", "okpo", "okato", "oktmo",
        "okogu", "okved", "legal_address", "postal_address", "phone", "email",
        "director_name", "chief_accountant", "registration_details", "tax_office",
        "bank_name", "bank_address", "settlement_account", "correspondent_account", "bik",
        "requisites_status", "source_document_id",
    )
    return {field: getattr(row, field) for field in fields}


def _vat_rate(value) -> Decimal | None:
    if value is None:
        return None
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Некорректная ставка НДС") from exc
    if not rate.is_finite() or rate < 0 or rate > 100:
        raise ValueError("Ставка НДС должна быть конечным числом от 0 до 100")
    if rate.as_tuple().exponent < -2:
        raise ValueError("Ставка НДС должна содержать не более 2 знаков после запятой")
    return rate.quantize(Decimal("0.01"))


class ContractCreate(BaseModel):
    number: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    counterparty: str | None = Field(default=None, max_length=500)
    contract_kind: str = Field(
        default="customer",
        pattern="^(prime_reference|customer|revenue_subcontract|downstream_subcontract|supply)$",
    )
    parent_contract_id: int | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    advance_amount: Decimal | None = Field(default=None, ge=0)
    retention_percent: Decimal | None = Field(default=None, ge=0, le=100)
    vat_mode: str = Field(default="unspecified", pattern="^(unspecified|none|rate)$")
    vat_rate: Decimal | None = None
    performed_from: date | None = None
    performed_to: date | None = None
    warranty_until: date | None = None
    signed_at: date | None = None
    status: str = Field(default="active", pattern="^(draft|active|completed|terminated|archived)$")
    source_document_id: int | None = None
    notes: str | None = Field(default=None, max_length=5000)

    _amounts = field_validator("amount", "advance_amount", mode="before")(
        lambda value: None if value is None else _money(value)
    )
    _vat = field_validator("vat_rate", mode="before")(_vat_rate)


class ContractLinkUpdate(BaseModel):
    expected_record_version: int = Field(gt=0)
    number: str | None = Field(default=None, min_length=1, max_length=255)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    counterparty: str | None = Field(default=None, max_length=500)
    amount: Decimal | None = Field(default=None, ge=0)
    advance_amount: Decimal | None = Field(default=None, ge=0)
    retention_percent: Decimal | None = Field(default=None, ge=0, le=100)
    vat_mode: str | None = Field(default=None, pattern="^(unspecified|none|rate)$")
    vat_rate: Decimal | None = None
    performed_from: date | None = None
    performed_to: date | None = None
    warranty_until: date | None = None
    signed_at: date | None = None
    status: str | None = Field(default=None, pattern="^(draft|active|completed|terminated|archived)$")
    notes: str | None = Field(default=None, max_length=5000)
    source_document_id: int | None = None
    parent_contract_id: int | None = None
    contract_kind: str | None = Field(
        default=None,
        pattern="^(prime_reference|customer|revenue_subcontract|downstream_subcontract|supply)$",
    )

    _amounts = field_validator("amount", "advance_amount", mode="before")(
        lambda value: None if value is None else _money(value)
    )
    _vat = field_validator("vat_rate", mode="before")(_vat_rate)


_CONTRACT_COMMERCIAL_FIELDS = (
    "amount", "advance_amount", "signed_at", "performed_from", "performed_to", "vat_mode", "vat_rate",
)


def _validate_contract_commercial_state(values: dict) -> None:
    """Validate the final merged state before any version/history mutation."""
    errors = []

    def reject(field: str, message: str):
        errors.append({"loc": ["body", field], "msg": message, "type": "value_error"})

    amount, advance = values.get("amount"), values.get("advance_amount")
    if amount is not None and amount < 0:
        reject("amount", "Сумма договора не может быть отрицательной")
    if advance is not None and advance < 0:
        reject("advance_amount", "Аванс не может быть отрицательным")
    if amount is not None and advance is not None and advance > amount:
        reject("advance_amount", "Аванс не может превышать сумму договора")
    signed = values.get("signed_at")
    business_today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3))).date()
    if signed is not None and signed > business_today:
        reject("signed_at", "Дата подписания не может быть позже текущего дня")
    performed_from, performed_to = values.get("performed_from"), values.get("performed_to")
    if performed_from is not None and performed_to is not None and performed_from > performed_to:
        reject("performed_to", "Окончание исполнения не может быть раньше начала")
    mode, rate = values.get("vat_mode"), values.get("vat_rate")
    if mode not in {"unspecified", "none", "rate"}:
        reject("vat_mode", "Укажите состояние НДС")
    elif mode == "rate" and rate is None:
        reject("vat_rate", "Для режима «ставка» укажите ставку НДС, включая 0 %")
    elif mode != "rate" and rate is not None:
        reject("vat_rate", "Ставка допустима только в режиме «ставка»")
    if errors:
        raise HTTPException(422, errors)


def _contract_warnings(row: Contract) -> list[dict]:
    warnings = []
    if row.amount is None:
        warnings.append({"code": "CONTRACT_AMOUNT_UNSPECIFIED", "message": "сумма договора не задана"})
    if row.vat_mode == "unspecified":
        warnings.append({"code": "CONTRACT_VAT_UNSPECIFIED", "message": "НДС не указан"})
    return warnings


class ContractBudgetProposalUpdate(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    selected_cost_category_id: int | None = None

    _amount_money = field_validator("amount", mode="before")(
        lambda value: None if value is None else _money(value, allow_zero=False)
    )


def _normalized(value: str | None) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", " ", (value or "").casefold()).strip()


_CONTRACT_SNAPSHOT_FIELDS = (
    "number", "title", "counterparty", "counterparty_organization_id", "contract_kind",
    "parent_contract_id", "amount", "advance_amount", "retention_percent", "warranty_until",
    "vat_mode", "vat_rate", "performed_from", "performed_to",
    "signed_at", "status", "source_document_id", "notes",
)


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _contract_snapshot(row: Contract, db: Session) -> dict:
    linked_ids = list(db.scalars(select(ContractDocumentLink.document_id).where(
        ContractDocumentLink.project_id == row.project_id,
        ContractDocumentLink.contract_id == row.id,
    )))
    return {
        **{field: _json_value(getattr(row, field)) for field in _CONTRACT_SNAPSHOT_FIELDS},
        "linked_document_ids": sorted(set(linked_ids)),
    }


def _record_contract_version(db: Session, row: Contract, *, event: str,
                             changed_fields: set[str] | list[str], actor_user_id: int,
                             resulting_record_version: int | None = None,
                             snapshot_extra: dict | None = None) -> ContractVersion:
    version = resulting_record_version or row.record_version
    snapshot = _contract_snapshot(row, db)
    if snapshot_extra:
        snapshot.update(snapshot_extra)
    history = ContractVersion(
        contract_id=row.id, project_id=row.project_id, sequence=version,
        event=event, resulting_record_version=version, snapshot=snapshot,
        changed_fields=sorted(changed_fields), actor_user_id=actor_user_id,
    )
    db.add(history)
    return history


def _ensure_contract_baseline(db: Session, row: Contract, *, actor_user_id: int) -> None:
    exists = db.scalar(select(ContractVersion.id).where(
        ContractVersion.contract_id == row.id,
        ContractVersion.project_id == row.project_id,
    ).limit(1))
    if exists is None:
        _record_contract_version(db, row, event="baseline", changed_fields=[], actor_user_id=actor_user_id)


def _contract_version(row: ContractVersion) -> dict:
    return {
        "id": row.id, "sequence": row.sequence, "event": row.event,
        "record_version": row.resulting_record_version, "snapshot": row.snapshot,
        "changed_fields": row.changed_fields, "actor_user_id": row.actor_user_id,
        "occurred_at": row.occurred_at,
    }


_CONTRACT_TITLE_STOP_WORDS = {
    "выполнению", "выполнение", "работ", "работы", "системы", "система",
    "области", "область", "оказание", "услуг", "услуги", "договора",
}


def _identifier(value: str | None) -> str:
    """Normalize contract identifiers independently from human-readable text."""
    return re.sub(r"[^0-9a-zа-яё]+", "", (value or "").casefold())


def _contract_document_score(row: Contract, document: Document, content: str) -> tuple[int, list[str]]:
    """Rank a possible contract source without mutating either record."""
    name = _normalized(document.name)
    body = _normalized(content[:120_000])
    body_head = body[:5_000]
    score = 0
    reasons: list[str] = []

    number = _normalized(row.number)
    compact_number = _identifier(number)
    compact_name = _identifier(name)
    compact_body = _identifier(body)
    number_in_name = bool(compact_number and len(compact_number) >= 4 and compact_number in compact_name)
    number_in_body = bool(compact_number and len(compact_number) >= 4 and compact_number in compact_body)
    if number_in_name:
        # The filename is the strongest signal when OCR/text extraction is not ready.
        score += 100
        reasons.append("номер договора указан в имени файла")
    elif number_in_body:
        # References in acts, estimates and letters are useful context, but do not
        # make those documents the legal source of the contract.
        score += 35
        reasons.append("совпадает номер договора")

    counterparty = _normalized(row.counterparty)
    if counterparty and len(counterparty) >= 4 and counterparty in name:
        score += 15
        reasons.append("контрагент указан в имени файла")
    elif counterparty and len(counterparty) >= 4 and counterparty in body:
        score += 15
        reasons.append("контрагент упоминается в тексте")

    title_tokens = {
        token for token in _normalized(row.title).split()
        if len(token) >= 5 and token not in _CONTRACT_TITLE_STOP_WORDS
    }
    matched_name = sorted(token for token in title_tokens if token in name)
    matched_body = sorted(token for token in title_tokens if token in body and token not in matched_name)
    if matched_name:
        score += min(20, len(matched_name) * 5)
        reasons.append(f"предмет договора указан в имени: {', '.join(matched_name[:3])}")
    if matched_body:
        score += min(15, len(matched_body) * 5)
        reasons.append(f"предмет договора упоминается в тексте: {', '.join(matched_body[:3])}")

    contract_markers = ("договор", "контракт", "государственн контракт", "заказчик", "подрядчик")
    if any(marker in name for marker in ("договор", "контракт", "гк ", "гк-")):
        score += 20
        reasons.append("название похоже на договор")
    elif any(marker in body for marker in contract_markers):
        score += 10
        reasons.append("в тексте обнаружены реквизиты договора")

    # A real contract normally contains several legal-structure signals near the
    # beginning. This semantic evidence distinguishes it from a document that only
    # mentions the contract number in a footer or reference field.
    legal_structure_markers = (
        "стороны заключили", "именуем", "предмет договора", "предмет", "государственн контракт", "права и обязанности",
        "цена договора", "срок действия", "реквизиты сторон", "заказчик", "подрядчик",
    )
    legal_structure_count = sum(marker in body_head for marker in legal_structure_markers)
    if legal_structure_count >= 3:
        score += 25
        reasons.append("структура текста соответствует договору")
    elif legal_structure_count >= 2:
        score += 12
        reasons.append("найдены юридические разделы договора")

    excluded_markers = (
        "пропуск", "инструкц", "приложение", "график", "акт", "письмо", "счет",
        "счёт", "накладн", "кс 2", "кс 3", "ос 15", "справка о стоимости",
    )
    if not number_in_name and any(marker in name for marker in excluded_markers):
        score -= 60
        reasons.append("имя указывает на связанный документ, а не договор")
    return max(0, min(score, 100)), reasons


@router.get("/organizations")
def list_organizations(db: Session = Depends(get_db), user: User = Depends(require_user)):
    query = select(Organization).order_by(Organization.id)
    if not user.is_admin:
        query = query.join(Project, Project.organization_id == Organization.id).join(ProjectMember).where(ProjectMember.user_id == user.id).distinct()
    rows = db.scalars(query).all()
    return {"organizations": [{"id": row.id, "name": row.name} for row in rows]}


@router.post("/organizations")
def create_organization(payload: OrganizationCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    row = Organization(name=payload.name.strip())
    db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "name": row.name}


@router.get("/organizations/current/requisites")
def current_organization_requisites(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    project = db.scalar(select(Project).join(ProjectMember).where(ProjectMember.user_id == user.id).order_by(Project.id))
    if project is None:
        project = db.scalar(select(Project).order_by(Project.id))
    if project is None:
        raise HTTPException(404, "Organization not found")
    row = db.get(Organization, project.organization_id)
    if row is None:
        raise HTTPException(404, "Organization not found")
    return _organization(row)


@router.put("/organizations/{organization_id}")
def update_organization(organization_id: int, payload: OrganizationUpdate,
                        db: Session = Depends(get_db), _: User = Depends(require_admin)):
    row = db.get(Organization, organization_id)
    if row is None:
        raise HTTPException(404, "Organization not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value.strip() if isinstance(value, str) else value)
    db.add(AuditLog(action="organization_requisites_updated", entity_type="organization", entity_id=row.id,
                    details=f"status={row.requisites_status}; fields={','.join(payload.model_fields_set)}"))
    db.commit(); db.refresh(row)
    return _organization(row)


@router.get("/projects/{project_id}/contracts")
def list_contracts(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    rows = db.scalars(select(Contract).where(Contract.project_id == project_id).order_by(Contract.id.desc())).all()
    return {"contracts": [_contract(row, db) for row in rows]}


@router.get("/projects/{project_id}/contracts/{contract_id}/versions")
def list_contract_versions(project_id: int, contract_id: int,
                           db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    row = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if row is None:
        raise HTTPException(404, "Contract not found")
    versions = list(db.scalars(select(ContractVersion).where(
        ContractVersion.contract_id == contract_id,
        ContractVersion.project_id == project_id,
    ).order_by(ContractVersion.sequence)))
    return {"contract_id": contract_id, "record_version": row.record_version,
            "versions": [_contract_version(version) for version in versions]}


@router.post("/projects/{project_id}/contracts")
def create_contract(project_id: int, payload: ContractCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    if db.get(Project, project_id) is None:
        raise HTTPException(404, "Project not found")
    parent_kinds = allowed_parent_kinds(payload.contract_kind)
    parent = db.get(Contract, payload.parent_contract_id) if payload.parent_contract_id else None
    if parent_kinds and parent is None:
        raise HTTPException(422, "Для выбранной роли укажите связанный вышестоящий договор")
    if parent is not None and parent.project_id != project_id:
        raise HTTPException(422, "Связанный договор не принадлежит выбранному проекту")
    if parent is not None and parent.contract_kind not in parent_kinds:
        raise HTTPException(422, "Роль вышестоящего договора не соответствует выбранной цепочке")
    if not parent_kinds and payload.parent_contract_id is not None:
        raise HTTPException(422, "Для этой роли вышестоящий договор не используется")
    _validate_contract_commercial_state(payload.model_dump())
    row = Contract(project_id=project_id, **payload.model_dump())
    db.add(row); db.flush()
    if is_financial_contract(row.contract_kind) and row.status != "draft":
        version = (db.scalar(select(func.max(ScheduleBaseline.version)).where(ScheduleBaseline.project_id == project_id)) or 0) + 1
        db.add(ScheduleBaseline(
            project_id=project_id, contract_id=row.id, created_by_user_id=user.id,
            name=f"ГПР по договору {row.number}", version=version,
            note="Автоматически создано при добавлении договора; заполните этапы и сроки.",
        ))
    _record_contract_version(db, row, event="created", changed_fields=set(_CONTRACT_SNAPSHOT_FIELDS), actor_user_id=user.id)
    db.add(AuditLog(action="contract_created", entity_type="contract", entity_id=row.id,
                    details=f"Contract: {row.number}; kind={row.contract_kind}"))
    db.commit(); db.refresh(row)
    return _contract(row, db)


@router.patch("/projects/{project_id}/contracts/{contract_id}")
def update_contract_links(project_id: int, contract_id: int, payload: ContractLinkUpdate,
                          db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    row = db.scalar(select(Contract).where(
        Contract.id == contract_id, Contract.project_id == project_id,
    ).with_for_update())
    if row is None:
        raise HTTPException(404, "Contract not found")
    if row.record_version != payload.expected_record_version:
        raise HTTPException(409, "Договор уже изменён другим пользователем. Обновите карточку и повторите действие")
    if "source_document_id" in payload.model_fields_set and payload.source_document_id is not None and not db.scalar(select(Document.id).where(
        Document.id == payload.source_document_id, Document.project_id == project_id,
    )):
        raise HTTPException(422, "Документ не принадлежит выбранному проекту")
    changes: dict = {}
    structure_changed = bool({"parent_contract_id", "contract_kind"} & payload.model_fields_set)
    if structure_changed:
        target_kind = payload.contract_kind or row.contract_kind
        target_parent_id = payload.parent_contract_id if "parent_contract_id" in payload.model_fields_set else row.parent_contract_id
        parent_kinds = allowed_parent_kinds(target_kind)
        parent = db.get(Contract, target_parent_id) if target_parent_id else None
        if parent_kinds and parent is None:
            raise HTTPException(422, "Для выбранной роли укажите вышестоящий договор")
        if not parent_kinds and parent is not None:
            raise HTTPException(422, "Головной договор не может иметь вышестоящий договор")
        if parent is not None and (parent.project_id != project_id or parent.contract_kind not in parent_kinds):
            raise HTTPException(422, "Выбранный договор не может быть родителем в этой цепочке")
        if parent is not None and parent.id == row.id:
            raise HTTPException(422, "Договор нельзя связать с самим собой")
        cursor = parent
        seen: set[int] = set()
        while cursor is not None and cursor.id not in seen:
            if cursor.id == row.id:
                raise HTTPException(422, "Связь создаёт цикл в дереве договоров")
            seen.add(cursor.id)
            cursor = db.get(Contract, cursor.parent_contract_id) if cursor.parent_contract_id else None
        changes["contract_kind"] = target_kind
        changes["parent_contract_id"] = parent.id if parent else None
    if "source_document_id" in payload.model_fields_set:
        changes["source_document_id"] = payload.source_document_id
    editable_fields = {
        "number", "title", "counterparty", "amount", "advance_amount",
        "retention_percent", "signed_at", "status", "notes", "warranty_until",
        "vat_mode", "vat_rate", "performed_from", "performed_to",
    }
    changed_fields = editable_fields & payload.model_fields_set
    for field in changed_fields:
        value = getattr(payload, field)
        changes[field] = value.strip() if isinstance(value, str) else value
    _validate_contract_commercial_state({
        field: changes.get(field, getattr(row, field)) for field in _CONTRACT_COMMERCIAL_FIELDS
    })
    effective_changes = {field: value for field, value in changes.items() if getattr(row, field) != value}
    if not effective_changes:
        return _contract(row, db)
    _ensure_contract_baseline(db, row, actor_user_id=user.id)
    next_version = row.record_version + 1
    result = db.execute(update(Contract).where(
        Contract.id == contract_id,
        Contract.project_id == project_id,
        Contract.record_version == payload.expected_record_version,
    ).values(**effective_changes, record_version=next_version))
    if result.rowcount != 1:
        raise HTTPException(409, "Договор уже изменён другим пользователем. Обновите карточку и повторите действие")
    db.flush(); db.expire(row); db.refresh(row)
    event_name = "archived" if effective_changes.get("status") == "archived" else (
        "linked" if {"parent_contract_id", "contract_kind", "source_document_id"} & effective_changes.keys() else "updated"
    )
    _record_contract_version(db, row, event=event_name, changed_fields=set(effective_changes), actor_user_id=user.id)
    if {"parent_contract_id", "contract_kind"} & effective_changes.keys():
        db.add(AuditLog(action="contract_parent_linked", entity_type="contract", entity_id=row.id,
                        details=f"parent={row.parent_contract_id or 'none'}; version={next_version}"))
    if "source_document_id" in effective_changes:
        db.add(AuditLog(action="contract_source_linked", entity_type="contract", entity_id=row.id,
                        details=f"document={row.source_document_id or 'none'}; version={next_version}"))
    if changed_fields & effective_changes.keys():
        db.add(AuditLog(
            action="contract_updated", entity_type="contract", entity_id=row.id,
            details=f"fields={','.join(sorted(changed_fields & effective_changes.keys()))}; user={user.id}; version={next_version}",
        ))
    db.commit(); db.refresh(row)
    return _contract(row, db)


def _contract_budget_proposal(row: ContractBudgetProposal) -> dict:
    return {
        "id": row.id, "project_id": row.project_id, "contract_id": row.contract_id,
        "contract_record_version": row.contract_record_version,
        "vat_snapshot": row.vat_snapshot,
        "operation": row.operation, "amount": format(row.amount, ".2f") if row.amount is not None else None,
        "advance_amount": format(row.advance_amount, ".2f") if row.advance_amount is not None else None,
        "retention_percent": row.retention_percent,
        "currency": row.currency, "description": row.description,
        "selected_cost_category_id": row.selected_cost_category_id,
        "source_document_id": row.source_document_id,
        "source_document_version_id": row.source_document_version_id,
        "source_document_sha256": row.source_document_sha256,
        "source_name": row.source_name,
        "target_budget_line_id": row.target_budget_line_id,
        "created_budget_line_id": row.created_budget_line_id,
        "status": row.status, "confirmed_by_user_id": row.confirmed_by_user_id,
        "confirmed_at": row.confirmed_at,
        "requires_confirmation": row.status == "proposed",
    }


def _contract_budget_category(db: Session, project_id: int, category_id: int) -> CostCategory:
    project = db.get(Project, project_id)
    category = db.scalar(select(CostCategory).where(
        CostCategory.id == category_id,
        CostCategory.organization_id == project.organization_id,
        CostCategory.is_active.is_(True),
    )) if project is not None else None
    if category is None:
        raise HTTPException(422, "Категория затрат не принадлежит организации проекта или отключена")
    return category


def _latest_contract_budget_line(db: Session, contract_id: int) -> BudgetLine | None:
    proposal = db.scalar(select(ContractBudgetProposal).where(
        ContractBudgetProposal.contract_id == contract_id,
        ContractBudgetProposal.status == "confirmed",
        ContractBudgetProposal.created_budget_line_id.is_not(None),
    ).order_by(ContractBudgetProposal.contract_record_version.desc(), ContractBudgetProposal.id.desc()))
    return db.get(BudgetLine, proposal.created_budget_line_id) if proposal is not None else None


def _assert_contract_budget_source_current(db: Session, contract: Contract,
                                           proposal: ContractBudgetProposal) -> None:
    if contract.record_version != proposal.contract_record_version:
        raise HTTPException(409, "CONTRACT_VERSION_MISMATCH: условия договора изменились после создания предложения")
    if proposal.source_document_id is None:
        if contract.source_document_id is not None:
            raise HTTPException(409, "SOURCE_VERSION_MISMATCH: источник договора изменился")
        return
    if contract.source_document_id != proposal.source_document_id:
        raise HTTPException(409, "SOURCE_VERSION_MISMATCH: источник договора изменился")
    resolve_current_document_pin(
        db, contract.project_id, proposal.source_document_id,
        proposal.source_document_version_id, proposal.source_document_sha256,
    )


@router.post("/projects/{project_id}/contracts/{contract_id}/budget-proposals")
def create_contract_budget_proposal(project_id: int, contract_id: int,
                                    db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    contract = db.scalar(select(Contract).where(
        Contract.id == contract_id, Contract.project_id == project_id,
    ).with_for_update())
    if contract is None:
        raise HTTPException(404, "Contract not found")
    if not is_financial_contract(contract.contract_kind):
        raise HTTPException(422, "Для договора-контекста бюджет не формируется")
    if contract.amount is None or contract.amount <= 0:
        raise HTTPException(422, "Сначала укажите положительную сумму договора")
    if contract.advance_amount is not None and contract.advance_amount > contract.amount:
        raise HTTPException(422, "Аванс не может превышать сумму договора")
    existing = db.scalar(select(ContractBudgetProposal).where(
        ContractBudgetProposal.contract_id == contract.id,
        ContractBudgetProposal.contract_record_version == contract.record_version,
    ))
    if existing is not None:
        return _contract_budget_proposal(existing)

    source = {"document_id": None, "version_id": None, "sha256": None, "name": None}
    if contract.source_document_id is not None:
        try:
            pin = resolve_current_document_pin(db, project_id, contract.source_document_id)
        except HTTPException as exc:
            if exc.status_code == 409:
                raise HTTPException(
                    409,
                    "SOURCE_PIN_MISSING: договор связан с документом, но текущая версия источника недоступна",
                ) from exc
            raise
        source = {
            "document_id": pin.document.id, "version_id": pin.version.id,
            "sha256": pin.sha256, "name": pin.document.name,
        }
    target = _latest_contract_budget_line(db, contract.id)
    for older in db.scalars(select(ContractBudgetProposal).where(
        ContractBudgetProposal.contract_id == contract.id,
        ContractBudgetProposal.status == "proposed",
    ).with_for_update()):
        older.status = "superseded"
        db.add(AuditLog(
            action="contract_budget_proposal_superseded", entity_type="contract_budget_proposal",
            entity_id=older.id, details=f"user={user.id}; superseded_by_contract_version={contract.record_version}",
        ))
    proposal = ContractBudgetProposal(
        project_id=project_id, contract_id=contract.id,
        contract_record_version=contract.record_version,
        operation="revise" if target is not None else "create",
        amount=contract.amount, advance_amount=contract.advance_amount,
        retention_percent=contract.retention_percent, currency=project_currency(db, project_id),
        vat_snapshot=contract_vat_snapshot(contract),
        description=f"Договор {contract.number}: {contract.title}",
        source_document_id=source["document_id"],
        source_document_version_id=source["version_id"],
        source_document_sha256=source["sha256"], source_name=source["name"],
        target_budget_line_id=target.id if target is not None else None,
    )
    try:
        with db.begin_nested():
            db.add(proposal)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(ContractBudgetProposal).where(
            ContractBudgetProposal.contract_id == contract.id,
            ContractBudgetProposal.contract_record_version == contract.record_version,
        ))
        if existing is None:
            raise
        return _contract_budget_proposal(existing)
    db.add(AuditLog(
        action="contract_budget_proposed", entity_type="contract_budget_proposal",
        entity_id=proposal.id,
        details=(f"user={user.id}; contract={contract.id}; contract_version={contract.record_version}; "
                 f"operation={proposal.operation}; amount={proposal.amount}; human_confirmation_required=true"),
    ))
    db.commit()
    return _contract_budget_proposal(proposal)


@router.patch("/contract-budget-proposals/{proposal_id}")
def update_contract_budget_proposal(proposal_id: int, payload: ContractBudgetProposalUpdate,
                                    db: Session = Depends(get_db), user: User = Depends(require_user)):
    proposal = db.scalar(select(ContractBudgetProposal).where(
        ContractBudgetProposal.id == proposal_id,
    ).with_for_update())
    if proposal is None:
        raise HTTPException(404, "Предложение бюджета не найдено")
    require_project_role(db, user, proposal.project_id, "manager")
    if proposal.status != "proposed":
        raise HTTPException(409, "Подтверждённое, отклонённое или устаревшее предложение неизменяемо")
    before = _contract_budget_proposal(proposal)
    data = payload.model_dump(exclude_unset=True)
    if "amount" in data and data["amount"] is None:
        raise HTTPException(422, "Сумма предложения обязательна")
    if "description" in data and data["description"] is None:
        raise HTTPException(422, "Описание предложения обязательно")
    if "selected_cost_category_id" in data and data["selected_cost_category_id"] is not None:
        data["selected_cost_category_id"] = _contract_budget_category(
            db, proposal.project_id, data["selected_cost_category_id"],
        ).id
    for name, value in data.items():
        setattr(proposal, name, value.strip() if isinstance(value, str) else value)
    db.add(AuditLog(
        action="contract_budget_proposal_edited", entity_type="contract_budget_proposal",
        entity_id=proposal.id,
        details=f"user={user.id}; before={json.dumps(before, default=str, ensure_ascii=False)}; after={json.dumps(_contract_budget_proposal(proposal), default=str, ensure_ascii=False)}",
    ))
    db.commit()
    return _contract_budget_proposal(proposal)


@router.post("/contract-budget-proposals/{proposal_id}/confirm")
def confirm_contract_budget_proposal(proposal_id: int, db: Session = Depends(get_db),
                                     user: User = Depends(require_user)):
    proposal = db.scalar(select(ContractBudgetProposal).where(
        ContractBudgetProposal.id == proposal_id,
    ).with_for_update())
    if proposal is None:
        raise HTTPException(404, "Предложение бюджета не найдено")
    require_project_role(db, user, proposal.project_id, "manager")
    if proposal.status == "confirmed":
        return _contract_budget_proposal(proposal)
    if proposal.status != "proposed":
        raise HTTPException(409, "Отклонённое или устаревшее предложение нельзя подтвердить")
    contract = db.scalar(select(Contract).where(
        Contract.id == proposal.contract_id, Contract.project_id == proposal.project_id,
    ).with_for_update())
    if contract is None:
        raise HTTPException(409, "Договор больше не существует")
    _assert_contract_budget_source_current(db, contract, proposal)
    try:
        require_project_currency(db, proposal.project_id, proposal.currency)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if proposal.selected_cost_category_id is None:
        raise HTTPException(422, "Менеджер должен выбрать категорию затрат")
    category = _contract_budget_category(db, proposal.project_id, proposal.selected_cost_category_id)
    before = None
    if proposal.operation == "create":
        budget = BudgetLine(
            project_id=proposal.project_id, contract_id=proposal.contract_id,
            line_kind="contract_control", direction=cash_flow_direction(contract.contract_kind),
            cost_category_id=category.id, category=category.name,
            description=proposal.description, planned_amount=proposal.amount,
            committed_amount=Decimal("0"), actual_amount=Decimal("0"),
            forecast_amount=proposal.amount, currency=proposal.currency, status="proposed",
            source_document_id=proposal.source_document_id,
            source_document_version_id=proposal.source_document_version_id,
            source_document_sha256=proposal.source_document_sha256,
            source_name=proposal.source_name,
            vat_snapshot=proposal.vat_snapshot,
        )
        db.add(budget); db.flush()
    else:
        budget = db.scalar(select(BudgetLine).where(
            BudgetLine.id == proposal.target_budget_line_id,
            BudgetLine.project_id == proposal.project_id,
            BudgetLine.contract_id == proposal.contract_id,
        ).with_for_update())
        if budget is None:
            raise HTTPException(409, "TARGET_BUDGET_LINE_MISSING: исходная строка бюджета больше не существует")
        before = {
            "id": budget.id, "planned_amount": str(budget.planned_amount),
            "forecast_amount": str(budget.forecast_amount), "committed_amount": str(budget.committed_amount),
            "actual_amount": str(budget.actual_amount), "category": budget.category,
            "description": budget.description,
            "vat_snapshot": budget.vat_snapshot,
        }
        budget.cost_category_id = category.id
        budget.category = category.name
        budget.description = proposal.description
        budget.planned_amount = proposal.amount
        budget.forecast_amount = proposal.amount
        budget.currency = proposal.currency
        budget.source_document_id = proposal.source_document_id
        budget.source_document_version_id = proposal.source_document_version_id
        budget.source_document_sha256 = proposal.source_document_sha256
        budget.source_name = proposal.source_name
        budget.vat_snapshot = proposal.vat_snapshot
        budget.status = "proposed"
    proposal.created_budget_line_id = budget.id
    proposal.status = "confirmed"
    proposal.confirmed_by_user_id = user.id
    proposal.confirmed_at = datetime.now(timezone.utc)
    after = {
        "id": budget.id, "planned_amount": str(budget.planned_amount),
        "forecast_amount": str(budget.forecast_amount), "committed_amount": str(budget.committed_amount),
        "actual_amount": str(budget.actual_amount), "category": budget.category,
        "description": budget.description,
        "vat_snapshot": budget.vat_snapshot,
    }
    db.add(AuditLog(
        action="contract_budget_confirmed", entity_type="contract_budget_proposal",
        entity_id=proposal.id,
        details=(f"user={user.id}; operation={proposal.operation}; budget_line={budget.id}; "
                 f"before={json.dumps(before, ensure_ascii=False)}; after={json.dumps(after, ensure_ascii=False)}"),
    ))
    db.commit()
    return _contract_budget_proposal(proposal)


@router.post("/contract-budget-proposals/{proposal_id}/reject")
def reject_contract_budget_proposal(proposal_id: int, db: Session = Depends(get_db),
                                    user: User = Depends(require_user)):
    proposal = db.scalar(select(ContractBudgetProposal).where(
        ContractBudgetProposal.id == proposal_id,
    ).with_for_update())
    if proposal is None:
        raise HTTPException(404, "Предложение бюджета не найдено")
    require_project_role(db, user, proposal.project_id, "manager")
    if proposal.status == "confirmed":
        raise HTTPException(409, "Подтверждённое предложение нельзя отклонить")
    if proposal.status == "superseded":
        raise HTTPException(409, "Устаревшее предложение уже заменено новой версией")
    proposal.status = "rejected"
    db.add(AuditLog(
        action="contract_budget_rejected", entity_type="contract_budget_proposal",
        entity_id=proposal.id, details=f"user={user.id}; human_confirmation=true",
    ))
    db.commit()
    return _contract_budget_proposal(proposal)


class ContractDelete(BaseModel):
    confirmation: str
    expected_record_version: int = Field(gt=0)


def _contract_dependencies(db: Session, project_id: int, contract_id: int) -> dict[str, int]:
    """Return links that would be destroyed or detached by a physical delete."""
    scoped = (
        ("child_contracts", Contract, Contract.parent_contract_id),
        ("documents", ContractDocumentLink, ContractDocumentLink.contract_id),
        ("schedule_baselines", ScheduleBaseline, ScheduleBaseline.contract_id),
        ("budget_lines", BudgetLine, BudgetLine.contract_id),
        ("budget_proposals", ContractBudgetProposal, ContractBudgetProposal.contract_id),
        ("cash_flow_entries", CashFlowEntry, CashFlowEntry.contract_id),
        ("procurement_items", ProcurementItem, ProcurementItem.contract_id),
        ("acceptance_acts", AcceptanceAct, AcceptanceAct.contract_id),
        ("obligations", Obligation, Obligation.contract_id),
        ("meetings", Meeting, Meeting.contract_id),
        ("messages", Message, Message.contract_id),
        ("contacts", ProjectContact, ProjectContact.contract_id),
        ("automation_rules", AutomationRule, AutomationRule.contract_id),
    )
    result: dict[str, int] = {}
    contract = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if contract is not None and contract.source_document_id is not None:
        result["source_document"] = 1
    for name, model, contract_column in scoped:
        project_column = getattr(model, "project_id", None)
        filters = [contract_column == contract_id]
        if project_column is not None:
            filters.append(project_column == project_id)
        count = int(db.scalar(select(func.count()).select_from(model).where(*filters)) or 0)
        if count:
            result[name] = count
    return result


def _is_empty_contract_draft(row: Contract) -> bool:
    return row.status == "draft" and row.vat_mode in {None, "unspecified"} and all(getattr(row, field) is None for field in (
        "counterparty", "counterparty_organization_id", "parent_contract_id", "amount",
        "advance_amount", "retention_percent", "warranty_until", "signed_at",
        "source_document_id", "notes", "vat_rate", "performed_from", "performed_to",
    ))


@router.get("/projects/{project_id}/contracts/{contract_id}/deletion-preview")
def contract_deletion_preview(project_id: int, contract_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "owner")
    row = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if row is None:
        raise HTTPException(404, "Contract not found")
    dependencies = _contract_dependencies(db, project_id, contract_id)
    can_delete = _is_empty_contract_draft(row) and not dependencies
    return {
        "contract_id": contract_id,
        "can_delete": can_delete,
        "dependencies": dependencies,
        "recommended_action": "delete" if can_delete else "archive",
        "reason": None if can_delete else "Только ошибочный пустой черновик можно удалить физически",
        "source_documents_affected": False,
    }


@router.delete("/projects/{project_id}/contracts/{contract_id}")
def delete_contract(project_id: int, contract_id: int, payload: ContractDelete,
                    db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "owner")
    row = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if row is None:
        raise HTTPException(404, "Contract not found")
    if row.record_version != payload.expected_record_version:
        raise HTTPException(409, "Договор уже изменён другим пользователем. Обновите карточку и повторите действие")
    if payload.confirmation.strip() != row.number.strip():
        raise HTTPException(422, "Введите точный номер договора для подтверждения")
    dependencies = _contract_dependencies(db, project_id, contract_id)
    if dependencies or not _is_empty_contract_draft(row):
        raise HTTPException(409, {
            "code": "contract_has_dependencies",
            "message": "Физически удалить можно только ошибочный пустой черновик. Архивируйте договор, чтобы сохранить историю.",
            "dependencies": dependencies,
        })
    number = row.number
    _ensure_contract_baseline(db, row, actor_user_id=user.id)
    deleted_version = row.record_version + 1
    _record_contract_version(
        db, row, event="deleted", changed_fields={"deleted"}, actor_user_id=user.id,
        resulting_record_version=deleted_version, snapshot_extra={"deleted": True},
    )
    db.flush()
    db.delete(row)
    db.flush()
    db.add(AuditLog(
        action="contract_deleted", entity_type="project", entity_id=project_id,
        details=f"contract_id={contract_id}; number={number}; user={user.id}; source_documents_affected=false",
    ))
    db.commit()
    return {"deleted": contract_id, "number": number, "source_documents_affected": False}


def _contract_source_text(document: Document, db: Session | None = None) -> str:
    parts = [part.strip() for part in (document.summary, document.notes) if part and part.strip()]
    if db is not None:
        latest = db.scalar(select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
        ).order_by(DocumentVersion.version_number.desc()))
        if latest and latest.content and latest.content.strip():
            parts.append(latest.content.strip())
    return "\n".join(dict.fromkeys(parts))


def _decimal_value(raw: str) -> Decimal:
    # «300000-00» / «180327-87» is rubles-kopecks written with a dash.
    return Decimal(re.sub(r"-(\d{2})$", r".\1", re.sub(r"\s+", "", raw)).replace(",", "."))


_DATE_PATTERN = r"(\d{1,2})[./](\d{1,2})[./](20\d{2})"

_EVIDENCE_RADIUS = 160


def _evidence_window(line: str, match: re.Match | None, radius: int = _EVIDENCE_RADIUS) -> str:
    """Return a slice of `line` around `match` widened to word boundaries.

    Long DOCX/PDF extracts are often a single physical line, so slicing from
    the beginning shows the preamble instead of the clause with the value.
    Falls back to `line[:500]` when there is no match to center on."""
    if match is None:
        return line[:500]
    length = len(line)
    start = max(0, match.start() - radius)
    end = min(length, match.end() + radius)
    if start > 0:
        boundary = line.find(" ", start)
        if boundary != -1 and boundary < match.start():
            start = boundary + 1
    if end < length:
        boundary = line.rfind(" ", match.end(), end)
        if boundary != -1:
            end = boundary
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < length else ""
    return f"{prefix}{line[start:end].strip()}{suffix}"


def _evidence_for_keyword(line: str, keyword: str, radius: int = _EVIDENCE_RADIUS) -> str:
    match = re.search(keyword, line, re.IGNORECASE)
    return _evidence_window(line, match, radius)


def _date_value(day: str, month: str, year: str) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


_RU_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}


def _spaced_word(word: str) -> str:
    # OCR splits words with stray spaces («Ок ончание»): allow \s* between letters.
    return r"\s*".join(re.escape(letter) for letter in word)


_WORDY_DATE = r"«?\s*(\d{1,2})\s*»?\s+([а-яё]+)\s+(20\s*\d\s*\d)"


def _wordy_date_value(day: str, month_word: str, year: str) -> date | None:
    month_word = month_word.casefold()
    month = next((number for stem, number in sorted(_RU_MONTHS.items(), key=lambda item: -len(item[0]))
                  if month_word.startswith(stem)), None)
    if month is None:
        return None
    return _date_value(day, str(month), re.sub(r"\s+", "", year))


def _work_boundary_date(text: str, word: str) -> date | None:
    """«Начало Работ: «27» марта 2026 года» / «Окончание выполнения работ: 31.12.2026»."""
    pattern = rf"{_spaced_word(word)}\s*(?:{_spaced_word('выполнения')}\s*)?{_spaced_word('работ')}\s*[:\-–]?\s*"
    match = re.search(pattern + _WORDY_DATE, text, re.IGNORECASE)
    if match:
        return _wordy_date_value(*match.groups())
    match = re.search(pattern + _DATE_PATTERN, text, re.IGNORECASE)
    return _date_value(*match.groups()) if match else None


def _contract_financial_terms(content: str, source_name: str = "") -> dict:
    """Extract explicit contract price, advance, retention, VAT state, dates
    and warranty with source evidence (ADR V6-10, step 8б: "предложение суммы,
    дат и НДС ... со ссылкой на источник"). Regex over explicit phrasing only --
    this is not NLP; an absent match leaves the field None, never a guess."""
    result: dict[str, Decimal | str | date | None] = {
        "amount": None, "advance_amount": None, "advance_percent": None,
        "retention_percent": None, "amount_evidence": None,
        "advance_evidence": None, "retention_evidence": None,
        "signed_at": None, "signed_at_evidence": None,
        "vat_mode": None, "vat_rate": None, "vat_evidence": None,
        "performed_from": None, "performed_to": None, "performed_evidence": None,
        "warranty_until": None, "warranty_evidence": None,
    }
    money_pattern = (
        r"(?<!\d)(\d[\d\s]{2,}(?:[.,]\d{1,2}|-\d{2}(?!\d))?)"
        r"(?:\s*=?\s*\([^)]{0,200}\))?\s*"
        r"(?:руб(?:\.|лей|ля)?|₽)"
    )
    percent_pattern = r"(?<!\d)(\d{1,3}(?:[.,]\d{1,2})?)\s*%"
    amount_keywords = re.compile(
        r"цена\s+(?:настоящего\s+)?договора|цена\s+работ|общая\s+цена|"
        r"стоимость\s+(?:работ|услуг|договора)|общая\s+стоимость|составляет|стоимость\s+всех|в\s+сумме",
        re.IGNORECASE,
    )
    application_reference = re.compile(
        r"спецификаци|приложени",
        re.IGNORECASE,
    )
    application_total_markers = re.compile(
        r"итого\s+с\s+ндс|итого|всего\s+к\s+оплате|всего|общая\s+стоимость",
        re.IGNORECASE,
    )
    vat_exempt = re.compile(
        r"без\s+ндс|ндс\s+не\s+облагается|не\s+является\s+плательщиком\s+ндс|"
        r"упрощ|\bусн\b|346\.11|145\s+нк|самозанят|налог\s+на\s+профессиональный\s+доход|\bнпд\b",
        re.IGNORECASE,
    )
    vat_unknown_rate_pattern = re.compile(
        r"(?:в\s*т\.?\s*ч\.?|в\s+том\s+числе|включая\s+ндс)\s*[:\-]?\s*ндс\s*[:\-]?\s*"
        r"(\d[\d\s]{2,}(?:[.,]\d{1,2})?)\s*(?:руб(?:\.|лей|ля)?|₽)?",
        re.IGNORECASE,
    )
    vat_keywords = re.compile(
        r"(?:в\s*т\.?\s*ч\.?|в\s+том\s+числе|включая\s+ндс|кроме\s+ндс|плюс\s+ндс|"
        r"ндс\s+составляет|ндс\s+в\s+размере)",
        re.IGNORECASE,
    )
    vat_percent_after_keyword = re.compile(
        r"(?:в\s*т\.?\s*ч\.?|в\s+том\s+числе|включая\s+ндс|кроме\s+ндс|плюс\s+ндс|"
        r"ндс\s+составляет|ндс\s+в\s+размере)\s*[:\-]?\s*НДС\s*(\d{1,3}(?:[.,]\d{1,2})?)\s*%",
        re.IGNORECASE,
    )
    vat_percent_generic = re.compile(
        r"НДС\s*(\d{1,3}(?:[.,]\d{1,2})?)\s*%",
        re.IGNORECASE,
    )
    vat_none = re.compile(
        r"(?:не\s+облагается|без\s+ндс|ндс\s+не\s+предусмотрен|"
        r"не\s+признаётся\s+плательщиком\s+ндс)",
        re.IGNORECASE,
    )
    retention_marker = re.compile(r"удержан|удержива", re.IGNORECASE)
    retention_generic = re.compile(r"гарантийн[а-яё]*\s+удерж", re.IGNORECASE)
    date_pattern = re.compile(_DATE_PATTERN)
    period_pattern = re.compile(
        rf"(?:срок|период)\s+(?:выполнения|исполнения|оказания\s+услуг|действия)[^.\n]*?"
        rf"с\s+{_DATE_PATTERN}\s+(?:по|до)\s+{_DATE_PATTERN}",
        re.IGNORECASE,
    )
    deadline_pattern = re.compile(
        rf"(?:срок[а-яё]*\s+(?:выполнения|исполнения|оказания\s+услуг|поставки|работ)|"
        rf"не\s+позднее|до)\s*[«\"]?\s*{_DATE_PATTERN}"
        rf"|(?:срок[а-яё]*\s+(?:выполнения|исполнения|оказания\s+услуг|поставки|работ))\s*[^.\n]*?"
        rf"не\s+позднее\s*[«\"]?\s*{_DATE_PATTERN}",
        re.IGNORECASE,
    )
    month_names = {
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
        "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    }
    text_deadline_pattern = re.compile(
        rf"(?:не\s+позднее|до)\s*[«\"]?\s*(\d{{1,2}})[»\"]?\s+"
        rf"({'|'.join(month_names)})\s+(\d{{4}})",
        re.IGNORECASE,
    )
    current_year = date.today().year
    min_signed_year = current_year - 5
    max_signed_year = current_year + 2
    lines = [re.sub(r"\s+", " ", line).strip() for line in content.splitlines() if line.strip()]
    appendix_marker = "=== приложение:"
    appendix_indices: set[int] = set()
    for index, line in enumerate(lines):
        if line.casefold().startswith(appendix_marker):
            appendix_indices.add(index)
    for line in lines:
        # Scans/DOCX split numbers with stray spaces («НДС 2 2 %», «20 2 6»); digits
        # separated only by spaces belong to one number.
        line = re.sub(r"(?<=\d)\s+(?=\d)", "", line)
        lowered = line.casefold()
        money_matches = list(re.finditer(money_pattern, line, re.IGNORECASE))
        # The contract price is the largest amount on its line (VAT and advances are smaller).
        money = max(money_matches, key=lambda item: _decimal_value(item.group(1)) or 0, default=None)
        percent = re.search(percent_pattern, line)
        if result["amount"] is None and money and amount_keywords.search(lowered):
            result["amount"] = _decimal_value(money.group(1))
            result["amount_evidence"] = _evidence_window(line, money)
        if "аванс" in lowered:
            # Only figures that follow the word «аванс» closely belong to the advance;
            # the contract price and VAT often share the same paragraph.
            window = " ".join(match.group(0) for match in re.finditer(r"аванс\w*[^.;]{0,160}", line, re.IGNORECASE))
            advance_money = re.search(money_pattern, window, re.IGNORECASE)
            advance_percent = re.search(percent_pattern, window)
            if advance_percent and re.search(r"ндс\s*$", window[:advance_percent.start()].casefold()):
                advance_percent = None
            if result["advance_amount"] is None and advance_money:
                result["advance_amount"] = _decimal_value(advance_money.group(1))
            if result["advance_percent"] is None and advance_percent:
                result["advance_percent"] = _decimal_value(advance_percent.group(1))
            if advance_money or advance_percent:
                anchor = advance_money or advance_percent
                result["advance_evidence"] = _evidence_window(line, anchor)
            elif result["advance_evidence"] is None:
                result["advance_evidence"] = _evidence_for_keyword(line, r"аванс\w*")
        if result["retention_percent"] is None:
            marker = retention_marker.search(line) or retention_generic.search(line)
            if marker:
                tail = line[marker.start():marker.start() + 60 + len(marker.group(0))]
                vat_near = re.search(r"НДС", tail, re.IGNORECASE)
                percent_in_tail = re.search(percent_pattern, tail)
                if percent_in_tail and not vat_near:
                    result["retention_percent"] = _decimal_value(percent_in_tail.group(1))
                    result["retention_evidence"] = _evidence_window(line, marker)
                elif result["retention_evidence"] is None:
                    result["retention_evidence"] = _evidence_window(line, marker)
        if result["signed_at"] is None and re.search(r"подписан|дата\s+подписания", lowered):
            match = date_pattern.search(line)
            if match and (value := _date_value(*match.groups())) is not None:
                if min_signed_year <= value.year <= max_signed_year:
                    result["signed_at"] = value
                    result["signed_at_evidence"] = _evidence_window(line, match)
        if result["vat_mode"] is None and "ндс" in lowered:
            if vat_none.search(line):
                result["vat_mode"] = "none"
                result["vat_evidence"] = _evidence_window(line, vat_none.search(line))
            else:
                vat_rate_match = vat_percent_after_keyword.search(line)
                if vat_rate_match is None and vat_keywords.search(line):
                    vat_rate_match = vat_percent_generic.search(line)
                if vat_rate_match is None:
                    vat_rate_match = vat_percent_generic.search(line)
                if vat_rate_match is not None:
                    result["vat_mode"] = "rate"
                    result["vat_rate"] = _decimal_value(vat_rate_match.group(1))
                    result["vat_evidence"] = _evidence_window(line, vat_rate_match)
                elif vat_unknown_rate_pattern.search(line):
                    vat_unknown = vat_unknown_rate_pattern.search(line)
                    result["vat_mode"] = "rate"
                    result["vat_rate"] = None
                    result["vat_evidence"] = _evidence_window(line, vat_unknown)
        if result["performed_from"] is None or result["performed_to"] is None:
            match = period_pattern.search(line)
            if match:
                groups = match.groups()
                start, end = _date_value(*groups[0:3]), _date_value(*groups[3:6])
                if start is not None and end is not None:
                    result["performed_from"], result["performed_to"] = start, end
                    result["performed_evidence"] = _evidence_window(line, match)
        if result["performed_to"] is None and re.search(
            r"срок[а-яё]*\s+(?:выполнения|исполнения|оказания\s+услуг|поставки|работ)"
            r"|не\s+позднее",
            lowered,
        ):
            numeric = deadline_pattern.search(line)
            value: date | None = None
            if numeric is not None:
                groups = numeric.groups()
                if all(groups[:3]):
                    value = _date_value(*groups[0:3])
            if value is None:
                textual = text_deadline_pattern.search(line)
                if textual:
                    day, month_name, year = textual.groups()
                    month_number = month_names.get(month_name.casefold())
                    if month_number is not None:
                        try:
                            value = date(int(year), month_number, int(day))
                        except ValueError:
                            value = None
            if value is not None:
                result["performed_to"] = value
                if result["performed_evidence"] is None:
                    anchor = numeric or textual
                    result["performed_evidence"] = _evidence_window(line, anchor)
        if result["warranty_until"] is None and re.search(r"гарантийн.*срок|гарант(?:ия|ийные\s+обязательства)", lowered):
            match = re.search(rf"(?:до|по)\s+{_DATE_PATTERN}", line, re.IGNORECASE)
            if match and (value := _date_value(*match.groups())) is not None:
                result["warranty_until"] = value
                result["warranty_evidence"] = _evidence_window(line, match)
            elif result["warranty_evidence"] is None:
                result["warranty_evidence"] = line[:500]
    flat = re.sub(r"\s+", " ", content)
    if result["performed_from"] is None and (start := _work_boundary_date(flat, "начало")) is not None:
        result["performed_from"] = start
        result["performed_evidence"] = result["performed_evidence"] or "Начало работ: " + start.isoformat()
    if result["performed_to"] is None and (finish := _work_boundary_date(flat, "окончание")) is not None:
        result["performed_to"] = finish
        result["performed_evidence"] = result["performed_evidence"] or "Окончание работ: " + finish.isoformat()

    # Fallback: the contract references an appendix/specification for the price.
    needs_appendix_amount = result["amount"] is None or (
        result["amount"] < Decimal("1000")
        and bool(appendix_indices)
    )
    if needs_appendix_amount:
        appendix_totals: list[Decimal] = []
        appendix_evidence: str | None = None
        for index in sorted(appendix_indices):
            following = sorted(i for i in appendix_indices if i > index)
            section_end = following[0] if following else len(lines)
            for offset in range(index + 1, section_end):
                appendix_line = re.sub(r"(?<=\d)\s+(?=\d)", "", lines[offset])
                # Specification totals are table cells («Итого: 373 620,00») without
                # «руб.»; VAT lines are skipped so the VAT sum is never the price.
                if not application_total_markers.search(appendix_line) or "ндс" in appendix_line.casefold().split(":")[0]:
                    continue
                for amount_match in re.finditer(r"(\d+(?:[.,]\d{1,2})?)", appendix_line):
                    try:
                        value = _decimal_value(amount_match.group(1))
                    except (InvalidOperation, AttributeError):
                        continue
                    if value is None:
                        continue
                    appendix_totals.append(value)
                    if appendix_evidence is None or value >= max(appendix_totals):
                        appendix_evidence = _evidence_window(appendix_line, amount_match)
        if appendix_totals:
            best_total = max(appendix_totals)
            if result["amount"] is None or best_total > result["amount"]:
                result["amount"] = best_total
                result["amount_evidence"] = appendix_evidence

    # A specification's explicit total («Итого: 373 620,00», «Всего к оплате»)
    # beats any amount found by scanning table rows: DOCX tables often arrive
    # as one line and merging spaced digits glues neighbouring cells together
    # (owner's «Сириус» spec gave 21 280 620 instead of 373 620,00).
    appendix_at = content.casefold().find("=== приложение:")
    spec_text = content[appendix_at:] if appendix_at >= 0 else (
        content if re.search(r"спецификаци", content[:400], re.IGNORECASE) else "")
    if spec_text:
        explicit_totals = []
        for total_match in re.finditer(
            r"(всего\s+к\s+оплате|итого\s+с\s+ндс|итого|на\s+сумму)\s*[:\-]?\s*"
            r"(\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?!\d)",
            spec_text, re.IGNORECASE,
        ):
            try:
                value = _decimal_value(total_match.group(2))
            except (InvalidOperation, ValueError):
                continue
            if value and value >= Decimal("1000"):
                explicit_totals.append((value, total_match))
        if explicit_totals:
            best_value, best_match = max(explicit_totals, key=lambda item: item[0])
            if result["amount"] != best_value:
                result["amount"] = best_value
                result["amount_evidence"] = _evidence_window(spec_text, best_match)
                if result["advance_percent"] is not None:
                    result["advance_amount"] = (best_value * result["advance_percent"] / Decimal("100")).quantize(Decimal("0.01"))

    # An advance schedule in an appendix («ГРАФИК АВАНСИРОВАНИЯ»: «Первый аванс
    # 2000000 …», «Второй аванс …») gives the total advance as the sum of rows.
    if appendix_at >= 0:
        schedule = [
            item for item in re.finditer(
                r"(?:перв|втор|трет|четв[её]рт|пят|шест|седьм|восьм|девят|десят)\w*\s+аванс\w*\s*[:\-]?\s*"
                r"(\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?!\d)",
                content[appendix_at:], re.IGNORECASE,
            )
        ]
        # One row per ordinal («Первый аванс» …): an appendix text can appear twice
        # (document summary + full text), which doubled the total to 11.9M.
        unique_rows: dict[str, re.Match] = {}
        for item in schedule:
            unique_rows.setdefault(re.match(r"\w+", item.group(0)).group(0).casefold(), item)
        schedule = list(unique_rows.values())
        if len(schedule) >= 2:
            total_advance = sum((_decimal_value(item.group(1)) for item in schedule), Decimal("0"))
            if total_advance > 0:
                result["advance_amount"] = total_advance.quantize(Decimal("0.01"))
                result["advance_evidence"] = _evidence_window(content[appendix_at:], schedule[0])

    # VAT rate derivation from explicit total/VAT sums when the rate itself is
    # not printed, followed by the RF default of 22%.
    vat_exempt_in_content = vat_exempt.search(flat)
    if result["vat_mode"] is None or (result["vat_mode"] == "rate" and result["vat_rate"] is None):
        result["vat_mode"] = None
        if vat_exempt_in_content:
            result["vat_mode"] = "none"
            result["vat_rate"] = Decimal("0")
            result["vat_evidence"] = _evidence_window(flat, vat_exempt_in_content)
        else:
            vat_unknown = vat_unknown_rate_pattern.search(flat)
            derived_rate: Decimal | None = None
            if vat_unknown is not None:
                vat_sum = _decimal_value(vat_unknown.group(1))
                if vat_sum is not None and result["amount"] is not None and result["amount"] > vat_sum:
                    base = result["amount"] - vat_sum
                    if base > 0:
                        raw_rate = vat_sum / base * Decimal("100")
                        for candidate in (Decimal("22"), Decimal("20"), Decimal("10"),
                                          Decimal("7"), Decimal("5")):
                            if abs(raw_rate - candidate) <= Decimal("0.5"):
                                derived_rate = candidate
                                break
            if derived_rate is not None:
                result["vat_mode"] = "rate"
                result["vat_rate"] = derived_rate
                result["vat_evidence"] = _evidence_window(flat, vat_unknown)
            else:
                # Applied by analyze_contract only after every document was read.
                result["vat_mode"] = None
                result["vat_rate"] = None
                result["vat_default_22"] = True

    # Signed date fallback from the header ("… № 52 от 2 февраля 2026 г."),
    # from an appendix ("к договору № 52 от 2 февраля 2026 г.") and finally
    # from the source document name.
    if result["signed_at"] is None:
        header_markers = re.compile(
            rf"(?:договор[а-яё]*\s*№\s*\S+|к\s+договору\s*№\s*\S+|"
            rf"«?\d{{1,2}}»?\s+[а-яё]+\s+20\s*\d\s*\d)",
            re.IGNORECASE,
        )
        wordy_bound = re.search(
            rf"«?\s*(\d{{1,2}})\s*»?\s+([а-яё]+)\s+(20\s*\d\s*\d)",
            flat,
            re.IGNORECASE,
        )
        numeric_bound = re.search(_DATE_PATTERN, flat)
        candidate_date: date | None = None
        candidate_match: re.Match | None = None
        if header_markers.search(flat):
            for wordy in re.finditer(
                rf"«?\s*(\d{{1,2}})\s*»?\s+([а-яё]+)\s+(20\s*\d\s*\d)",
                flat,
                re.IGNORECASE,
            ):
                value = _wordy_date_value(*wordy.groups())
                if value is not None and min_signed_year <= value.year <= max_signed_year and value <= date.today():
                    candidate_date = value
                    candidate_match = wordy
                    break
            if candidate_date is None:
                for numeric in re.finditer(_DATE_PATTERN, flat):
                    value = _date_value(*numeric.groups())
                    if value is not None and min_signed_year <= value.year <= max_signed_year and value <= date.today():
                        candidate_date = value
                        candidate_match = numeric
                        break
        elif wordy_bound is not None:
            value = _wordy_date_value(*wordy_bound.groups())
            if value is not None and min_signed_year <= value.year <= max_signed_year and value <= date.today():
                candidate_date = value
                candidate_match = wordy_bound
        elif numeric_bound is not None:
            value = _date_value(*numeric_bound.groups())
            if value is not None and min_signed_year <= value.year <= max_signed_year and value <= date.today():
                candidate_date = value
                candidate_match = numeric_bound
        if candidate_date is not None:
            result["signed_at"] = candidate_date
            result["signed_at_evidence"] = _evidence_window(flat, candidate_match)

    if result["signed_at"] is None and source_name:
        filename_match = re.search(
            rf"от\s*(\d{{1,2}})[.\-](\d{{1,2}})[.\-](20\d{{2}})"
            rf"|от\s*(\d{{1,2}})[.\-](\d{{1,2}})[.\-](\d{{2}})(?!\d)"
            rf"|(\d{{1,2}})[.\-](\d{{1,2}})[.\-](20\d{{2}})(?!\d)",
            source_name,
            re.IGNORECASE,
        )
        if filename_match is not None:
            groups = filename_match.groups()
            day = month = year = None
            if groups[0]:
                day, month, year = groups[0], groups[1], groups[2]
            elif groups[3]:
                day, month, year = groups[3], groups[4], "20" + groups[5]
            elif groups[6]:
                day, month, year = groups[6], groups[7], groups[8]
            if day and month and year:
                value = _date_value(day, month, year)
                if value is not None and min_signed_year <= value.year <= max_signed_year and value <= date.today():
                    result["signed_at"] = value
                    result["signed_at_evidence"] = f"Дата из имени файла: {source_name}"

    if result["advance_amount"] is None and result["advance_percent"] is not None and result["amount"] is not None:
        result["advance_amount"] = (result["amount"] * result["advance_percent"] / Decimal("100")).quantize(Decimal("0.01"))
    return result


_CONTRACT_TERM_FIELDS = (
    "amount", "advance_amount", "advance_percent", "retention_percent",
    "signed_at", "performed_from", "performed_to", "warranty_until",
    "vat_mode", "vat_rate",
)

_CONTRACT_TERM_EVIDENCE_FIELDS = (
    "amount_evidence", "advance_evidence", "retention_evidence",
    "signed_at_evidence", "performed_evidence", "vat_evidence",
    "warranty_evidence",
)
_CONTRACT_TERM_EVIDENCE_FIELDS = tuple(_CONTRACT_TERM_EVIDENCE_FIELDS) + (
    "advance_amount_evidence", "advance_percent_evidence",
    "performed_from_evidence", "performed_to_evidence", "warranty_until_evidence",
)


def _contract_term_values_differ(field: str, left, right) -> bool:
    if left is None or right is None:
        return False
    if field in {"amount", "advance_amount"}:
        try:
            return abs(Decimal(left) - Decimal(right)) > Decimal("1")
        except (InvalidOperation, TypeError, ValueError):
            return str(left) != str(right)
    if field == "vat_rate":
        try:
            return Decimal(left) != Decimal(right)
        except (InvalidOperation, TypeError, ValueError):
            return str(left) != str(right)
    if field == "retention_percent":
        try:
            return Decimal(left) != Decimal(right)
        except (InvalidOperation, TypeError, ValueError):
            return str(left) != str(right)
    return left != right


def _merge_contract_terms_with_ai(content: str, base: dict) -> dict:
    """AI is the primary source for contract terms; regex is the fallback.

    The AI is always called when content is available. For every field where
    the AI produced a value (extract_contract_terms_ai already verifies that
    each value has a verbatim evidence quote) the AI value and its *_evidence
    replace the regex result; otherwise the regex value is kept. If both sides
    produced a value and they disagree (Decimal diff > 1 for money, plain
    inequality for dates/strings), the conflict is reported in
    "extraction_conflicts" with the field name and both representations as
    strings. Errors of the AI call are never propagated: the regex result is
    returned unchanged. Extraction callers iterating over the result must skip
    the auxiliary "extraction_conflicts" key.
    """
    if not content:
        return dict(base)
    try:
        ai_terms = extract_contract_terms_ai(content)
    except Exception:
        return dict(base)
    if not ai_terms:
        return dict(base)
    merged = dict(base)
    conflicts: list[dict] = []
    for field in _CONTRACT_TERM_FIELDS:
        ai_value = ai_terms.get(field)
        if ai_value is None:
            continue
        regex_value = merged.get(field)
        if regex_value is not None and _contract_term_values_differ(field, ai_value, regex_value):
            conflicts.append({
                "field": field,
                "ai": str(ai_value),
                "regex": str(regex_value),
            })
        merged[field] = ai_value
        for evidence_field in _CONTRACT_TERM_EVIDENCE_FIELDS:
            if not evidence_field.startswith(field):
                continue
            evidence = ai_terms.get(evidence_field)
            if evidence is None:
                continue
            merged[evidence_field] = evidence
    # Advance evidence may arrive under the "advance_*" prefix alongside the
    # generic "advance_evidence" key used by regex extraction.
    for source_field, target_field in (
        ("advance_amount_evidence", "advance_evidence"),
        ("advance_percent_evidence", "advance_evidence"),
    ):
        evidence = ai_terms.get(source_field)
        if evidence is not None:
            merged[target_field] = evidence
    evidence = ai_terms.get("performed_from_evidence") or ai_terms.get("performed_to_evidence")
    if evidence is not None:
        merged["performed_evidence"] = evidence
    evidence = ai_terms.get("warranty_until_evidence")
    if evidence is not None:
        merged["warranty_evidence"] = evidence
    if conflicts:
        merged["extraction_conflicts"] = conflicts
    else:
        merged["extraction_conflicts"] = []
    return merged


def _contract_terms_content(db: Session, project_id: int, row: Contract,
                            document: Document, content: str) -> str:
    """Main contract text plus every attached appendix/specification.

    Prices, advances and VAT are often given only in «Приложение № 1» or a
    specification; reading the main file alone produced a random 500 ₽."""
    parts = [content[:60_000]]
    total = len(parts[0])
    links = db.scalars(select(ContractDocumentLink).where(
        ContractDocumentLink.contract_id == row.id,
        ContractDocumentLink.project_id == project_id,
        ContractDocumentLink.document_id != document.id,
    ).order_by(ContractDocumentLink.created_at, ContractDocumentLink.id))
    for link in links:
        linked = db.scalar(select(Document).where(
            Document.id == link.document_id, Document.project_id == project_id,
        ))
        text = _contract_source_text(linked, db) if linked is not None else ""
        if not text:
            continue
        chunk = f"\n\n=== Приложение: {linked.name} ===\n" + text[:30_000]
        if total + len(chunk) > 150_000:
            break
        parts.append(chunk)
        total += len(chunk)
    return "".join(parts)


def _fill_contract_identity(db: Session, project_id: int, row: Contract, document: Document,
                            content: str, financial_check: dict) -> None:
    """Fill the counterparty and replace a file-name number from the contract text.

    Owner rule: the system fills the card itself; a number copied from the file
    name («11-26_правки_v2_03_02_2026») is replaced by the heading number,
    hand-edited values are never touched."""
    from pathlib import Path

    from app.api.contract_discovery import discover_contract_fields
    from app.models.project import Project

    manual = _manually_edited_contract_fields(db, row.id)
    stem = Path(document.name or "").stem.strip()
    compact_number = (row.number or "").replace(" ", "")
    number_from_file = len(compact_number) >= 8 and compact_number in stem.replace(" ", "")
    project = db.get(Project, project_id)
    organization = db.get(Organization, project.organization_id) if project is not None else None
    own = tuple(filter(None, (organization.name, organization.legal_name))) if organization else ()
    try:
        found = discover_contract_fields(document.name or "", content, own)
    except Exception:
        return
    counterparty = (found.get("counterparty") or "").strip()
    # A counterparty the system stored earlier in the genitive/OCR-broken form
    # («Индивидуального предпринимателя … Юри я …») is corrected too.
    garbled = bool(row.counterparty) and bool(re.match(
        r"(индивидуального\s+предпринимателя|общества\s+с\s+ограниченной|акционерного\s+общества)",
        row.counterparty.strip(), re.IGNORECASE,
    ))
    if counterparty and (not row.counterparty or garbled) and counterparty != row.counterparty and "counterparty" not in manual:
        row.counterparty = counterparty[:500]
        financial_check["applied"].append("counterparty")
    number = (found.get("number") or "").strip()
    # The heading number corrects a number the system took from the file name or
    # glued together earlier («5202» from «№ 52 02 февраля»).
    glued = bool(number) and bool(row.number) and row.number.replace(" ", "").startswith(number.replace(" ", "")) and row.number != number
    # A stored number that does not occur in the document at all (taken from an
    # old file name, «859» for contract «№ 976») is replaced by the heading number.
    absent = bool(row.number) and re.search(
        rf"(?<![\w/-]){re.escape(row.number.strip())}(?![\w/-])", content or "",
    ) is None
    if number and (number_from_file or glued or absent) and number != row.number and "number" not in manual:
        taken = db.scalar(select(Contract.id).where(
            Contract.project_id == project_id, Contract.number == number, Contract.id != row.id,
        ))
        if taken is None:
            row.number = number[:255]
            financial_check["applied"].append("number")


def _manually_edited_contract_fields(db: Session | None, contract_id: int | None) -> set[str]:
    """Fields a person changed by hand (contract_updated audit «fields=a,b»)."""
    if db is None or contract_id is None:
        return set()
    edited: set[str] = set()
    for details in db.scalars(select(AuditLog.details).where(
        AuditLog.action == "contract_updated", AuditLog.entity_type == "contract",
        AuditLog.entity_id == contract_id,
    )):
        match = re.search(r"fields=([^;]*)", details or "")
        if match:
            edited.update(item.strip() for item in match.group(1).split(",") if item.strip())
    return edited


def _apply_contract_financial_terms(row: Contract, terms: dict, db: Session | None = None) -> dict:
    """Fill only currently-empty fields; never silently overwrite a value a
    human already entered. A date/VAT extraction that would make the merged
    state invalid (e.g. performed_from > performed_to) is not applied -- it is
    reported separately so a human decides, per the plan's "подтверждение
    выхода за [период] с записью в аудит". Auxiliary keys such as
    "extraction_conflicts" produced by the AI/regex merge are ignored here."""
    applied: list[str] = []
    mismatches: list[dict] = []
    rejected: list[dict] = []
    replaced: list[dict] = []
    # Owner rule 15.10.2026: values the system itself put into the card (wizard,
    # previous analysis) are corrected by the system; only hand-entered values
    # are protected and reported as mismatches. Zero money counts as empty.
    manual = _manually_edited_contract_fields(db, getattr(row, "id", None))
    extraction_conflicts = terms.get("extraction_conflicts")
    for field, label, evidence_field, tolerance in (
        ("amount", "Сумма договора", "amount_evidence", Decimal("1")),
        ("advance_amount", "Аванс", "advance_evidence", Decimal("1")),
        ("retention_percent", "Удержание", "retention_evidence", Decimal("0.01")),
        ("signed_at", "Дата подписания", "signed_at_evidence", None),
        ("warranty_until", "Гарантия до", "warranty_evidence", None),
    ):
        extracted = terms.get(field)
        if extracted is None:
            continue
        current = getattr(row, field)
        zero_money = (field in {"amount", "advance_amount", "retention_percent"}
                      and current is not None and Decimal(current) == 0
                      and Decimal(extracted) > 0)
        if current is None or zero_money:
            setattr(row, field, extracted)
            applied.append(field)
        elif (tolerance is not None and abs(Decimal(current) - Decimal(extracted)) > tolerance) or (
            tolerance is None and current != extracted
        ):
            if db is not None and field not in manual:
                setattr(row, field, extracted)
                applied.append(field)
                replaced.append({
                    "field": field, "label": label, "previous": str(current),
                    "extracted": str(extracted), "evidence": terms.get(evidence_field),
                })
                continue
            mismatches.append({
                "field": field, "label": label, "current": str(current),
                "extracted": str(extracted), "evidence": terms.get(evidence_field),
            })
    vat_mode, vat_rate = terms.get("vat_mode"), terms.get("vat_rate")
    if vat_mode is not None:
        if row.vat_mode in (None, "unspecified"):
            row.vat_mode, row.vat_rate = vat_mode, vat_rate
            applied.append("vat_mode")
        elif (row.vat_mode != vat_mode or (vat_mode == "rate" and row.vat_rate != vat_rate)) and (
            db is not None and not ({"vat_mode", "vat_rate"} & manual)
            and not str(terms.get("vat_evidence") or "").startswith("Ставка не указана")
        ):
            replaced.append({
                "field": "vat_mode", "label": "НДС", "previous": f"{row.vat_mode} {row.vat_rate or ''}".strip(),
                "extracted": f"{vat_mode} {vat_rate or ''}".strip(), "evidence": terms.get("vat_evidence"),
            })
            row.vat_mode, row.vat_rate = vat_mode, vat_rate
            applied.append("vat_mode")
        elif row.vat_mode != vat_mode or (vat_mode == "rate" and row.vat_rate != vat_rate):
            mismatches.append({
                "field": "vat_mode", "label": "НДС", "current": row.vat_mode,
                "extracted": vat_mode, "evidence": terms.get("vat_evidence"),
            })
    performed_from, performed_to = terms.get("performed_from"), terms.get("performed_to")
    if performed_from is not None and performed_to is not None:
        if row.performed_from is None and row.performed_to is None:
            if performed_from <= performed_to:
                row.performed_from, row.performed_to = performed_from, performed_to
                applied.append("performed_from")
                applied.append("performed_to")
            else:
                # Extraction itself is self-contradictory (from > to) -- a
                # human must look at the source, not have it written silently.
                rejected.append({
                    "field": "performed_from", "label": "Период исполнения",
                    "extracted": f"{performed_from} - {performed_to}",
                    "evidence": terms.get("performed_evidence"),
                    "reason": "Начало периода позже окончания в тексте источника",
                })
        elif row.performed_from != performed_from or row.performed_to != performed_to:
            mismatches.append({
                "field": "performed_from", "label": "Период исполнения",
                "current": f"{row.performed_from} - {row.performed_to}",
                "extracted": f"{performed_from} - {performed_to}",
                "evidence": terms.get("performed_evidence"),
            })
    return {
        "applied": applied,
        "mismatches": mismatches,
        "rejected": rejected,
        "terms": terms,
        "extraction_conflicts": extraction_conflicts or [],
        "replaced": replaced,
    }


def _rank_contract_documents(db: Session, project_id: int, row: Contract) -> list[dict]:
    documents = db.execute(select(Document, DocumentVersion.content).outerjoin(
        DocumentVersion,
        and_(DocumentVersion.document_id == Document.id, DocumentVersion.version_number == Document.current_version),
    ).where(Document.project_id == project_id).order_by(Document.id.desc())).all()
    candidates = []
    for document, extracted_content in documents:
        content = "\n".join(dict.fromkeys(
            part.strip() for part in (document.summary, document.notes, extracted_content) if part and part.strip()
        ))
        score, reasons = _contract_document_score(row, document, content)
        candidates.append({
            "document_id": document.id, "name": document.name, "source": document.source,
            "mime_type": document.mime_type, "score": score, "reasons": reasons,
            "text_ready": bool(content),
        })
    candidates.sort(key=lambda item: (item["score"], item["text_ready"], item["document_id"]), reverse=True)
    return candidates


def _payment_schedule_candidates(content: str) -> list[dict]:
    """Extract conservative payment proposals; never marks anything as paid."""
    candidates: list[dict] = []
    # PDF extractors sometimes collapse the whole document into one physical
    # line.  Treat punctuation as a semantic boundary too: otherwise a payment
    # word in one clause can be paired with a building-code date and a price
    # from a remote specification table.
    fragments = re.split(r"(?:[;!?]|(?<!\d)\.(?=\s+[^\d])|[\r\n]+)", content)
    for fragment in (part.strip() for part in fragments if part.strip()):
        if not re.search(r"плат[её]ж|оплат|аванс", fragment, re.IGNORECASE):
            continue
        date_match = re.search(r"(?<!\d)([0-3]?\d)[.\-/]([01]?\d)[.\-/](20\d{2})(?!\d)", fragment)
        amount_matches = re.findall(r"(?<!\d)(\d[\d\s]{2,}(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|лей)?)", fragment, re.IGNORECASE)
        if not date_match or not amount_matches:
            continue
        reference_prefix = fragment[max(0, date_match.start() - 24):date_match.start()]
        if re.search(r"(?:СНиП|ГОСТ|СП)\s*$", reference_prefix, re.IGNORECASE):
            continue
        try:
            planned_date = date(int(date_match.group(3)), int(date_match.group(2)), int(date_match.group(1)))
            amount = max(Decimal(value.replace(" ", "").replace(",", ".")) for value in amount_matches)
        except (ValueError, ArithmeticError):
            continue
        if amount <= 0:
            continue
        candidates.append({"planned_date": planned_date, "amount": amount, "excerpt": fragment[:1000]})
    unique: dict[tuple[date, Decimal], dict] = {}
    for item in candidates:
        unique[(item["planned_date"], item["amount"])] = item
    return list(unique.values())[:50]


def _create_payment_schedule_proposals(db: Session, row: Contract, document: Document) -> list[CashFlowEntry]:
    direction = cash_flow_direction(row.contract_kind)
    if direction is None:
        return []
    # Some legacy/synthetic documents have only summary text and no immutable
    # DocumentVersion. They may still participate in non-financial analysis,
    # but must never produce an unpinned financial proposal.
    current_version_exists = db.scalar(select(DocumentVersion.id).where(
        DocumentVersion.document_id == document.id,
        DocumentVersion.version_number == document.current_version,
    ))
    if current_version_exists is None:
        return []
    pin = resolve_current_document_pin(db, row.project_id, document.id)
    baseline = db.scalar(select(ScheduleBaseline).where(
        ScheduleBaseline.project_id == row.project_id,
        ScheduleBaseline.contract_id == row.id,
    ).order_by(ScheduleBaseline.version.desc()))
    stages = list(db.scalars(select(ScheduleItem).where(
        ScheduleItem.project_id == row.project_id,
        ScheduleItem.baseline_id == baseline.id if baseline else False,
    ).order_by(ScheduleItem.planned_finish, ScheduleItem.id)))
    created: list[CashFlowEntry] = []
    for candidate in _payment_schedule_candidates(pin.version.content or ""):
        existing = db.scalar(select(CashFlowEntry.id).where(
            CashFlowEntry.project_id == row.project_id,
            CashFlowEntry.contract_id == row.id,
            CashFlowEntry.planned_date == candidate["planned_date"],
            CashFlowEntry.planned_amount == candidate["amount"],
            CashFlowEntry.source_document_id == document.id,
        ))
        if existing:
            continue
        eligible = [stage for stage in stages if stage.planned_finish and stage.planned_finish <= candidate["planned_date"]]
        stage = eligible[-1] if eligible else (stages[0] if len(stages) == 1 else None)
        item = CashFlowEntry(
            project_id=row.project_id, contract_id=row.id,
            schedule_item_id=stage.id if stage else None,
            source_document_id=document.id,
            source_document_version_id=pin.version.id,
            source_document_sha256=pin.sha256,
            direction=direction,
            title=f"Платёж по договору {row.number}", planned_date=candidate["planned_date"],
            planned_amount=candidate["amount"], actual_amount=Decimal("0"),
            counterparty=row.counterparty, status="proposed", source_name=document.name,
            source_excerpt=candidate["excerpt"],
            vat_snapshot=contract_vat_snapshot(row),
        )
        db.add(item)
        created.append(item)
    return created


@router.get("/projects/{project_id}/contracts/{contract_id}/source-candidates")
def contract_source_candidates(project_id: int, contract_id: int,
                               db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Return explainable, read-only source suggestions for a contract."""
    require_project_role(db, user, project_id, "viewer")
    row = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if row is None:
        raise HTTPException(404, "Contract not found")
    candidates = _rank_contract_documents(db, project_id, row)
    return {
        "contract_id": row.id,
        "recommended_document_id": candidates[0]["document_id"] if candidates and candidates[0]["score"] >= 50 else None,
        "candidates": candidates[:100],
    }


@router.post("/projects/{project_id}/contracts/{contract_id}/analyze")
def analyze_contract(project_id: int, contract_id: int,
                     db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Extract proposed controls from the linked contract without changing its source document."""
    require_project_role(db, user, project_id, "editor")
    row = db.scalar(select(Contract).where(
        Contract.id == contract_id, Contract.project_id == project_id,
    ).with_for_update())
    if row is None:
        raise HTTPException(404, "Contract not found")
    before_analysis = _contract_snapshot(row, db)
    _ensure_contract_baseline(db, row, actor_user_id=user.id)
    automatically_linked = False
    if row.source_document_id is None:
        candidates = _rank_contract_documents(db, project_id, row)
        best = candidates[0] if candidates else None
        runner_up_score = candidates[1]["score"] if len(candidates) > 1 else 0
        if not best or not best["text_ready"] or best["score"] < 85 or best["score"] - runner_up_score < 10:
            raise HTTPException(409, "Однозначный документ договора не найден. Проверьте предложенные файлы вручную")
        row.source_document_id = best["document_id"]
        automatically_linked = True
    document = db.scalar(select(Document).where(
        Document.id == row.source_document_id, Document.project_id == project_id,
    ))
    if document is None:
        raise HTTPException(404, "Contract source document not found")
    content = _contract_source_text(document, db)
    if not content:
        raise HTTPException(409, "Документ ещё не проанализирован. Сначала завершите анализ рабочей папки")
    source_id = document.external_id or f"document:{document.id}"
    terms_content = _contract_terms_content(db, project_id, row, document, content)
    merged_terms = _merge_contract_terms_with_ai(
        terms_content, _contract_financial_terms(terms_content, source_name=document.name or ""),
    )
    if merged_terms.get("vat_mode") is None and merged_terms.get("vat_default_22") and row.vat_mode in (None, "unspecified"):
        # Owner rule 15.10.2026: in Russia VAT is 22% unless a document says otherwise.
        merged_terms["vat_mode"], merged_terms["vat_rate"] = "rate", Decimal("22")
        merged_terms["vat_evidence"] = "Ставка не указана в документах — применена ставка НДС 22% по умолчанию (РФ)"
    financial_check = _apply_contract_financial_terms(row, merged_terms, db)
    _fill_contract_identity(db, project_id, row, document, content, financial_check)
    _validate_contract_commercial_state({field: getattr(row, field) for field in _CONTRACT_COMMERCIAL_FIELDS})
    source = StorageObject(
        id=source_id, name=document.name, mime_type=document.mime_type or "application/octet-stream",
        parent_id=document.parent_external_id or "contracts", content_text=content,
        object_type="file", provider=document.source,
    )
    created_tasks = create_tasks_from_files(db, project_id, None, [source], source_type="contract_analysis")
    created_risks, created_decisions = create_governance_items(
        db, project_id, [source], source_type="contract_analysis",
    )
    payment_rows = _create_payment_schedule_proposals(db, row, document)
    remembered_organizations = remember_contract_organizations(db, row, content, document.id)
    task_ids = list(db.scalars(select(Task.id).where(
        Task.project_id == project_id, Task.source_file_id == source_id,
    )))
    linked_obligations = list(db.scalars(select(Obligation).where(
        Obligation.project_id == project_id, Obligation.task_id.in_(task_ids) if task_ids else False,
    )))
    for obligation in linked_obligations:
        obligation.contract_id = row.id
    after_analysis = _contract_snapshot(row, db)
    analysis_changed = {
        field for field in _CONTRACT_SNAPSHOT_FIELDS
        if before_analysis.get(field) != after_analysis.get(field)
    }
    if analysis_changed:
        row.record_version += 1
        db.flush()
        _record_contract_version(
            db, row, event="analyzed", changed_fields=analysis_changed, actor_user_id=user.id,
        )
    db.add(AuditLog(
        action="contract_analyzed", entity_type="contract", entity_id=row.id,
        details=(f"document={document.id}; tasks_created={len(created_tasks)}; "
                 f"obligations_linked={len(linked_obligations)}; risks_created={len(created_risks)}; "
                 f"decisions_created={len(created_decisions)}; payment_proposals={len(payment_rows)}; "
                 f"organizations_remembered={len(remembered_organizations)}; automatically_linked={automatically_linked}; "
                 f"financial_fields_applied={','.join(financial_check['applied']) or 'none'}; "
                 f"financial_mismatches={len(financial_check['mismatches'])}; "
                 f"financial_rejected={len(financial_check.get('rejected', []))}; originals_changed=false"),
    ))
    db.commit()
    result = _contract(row, db)
    result["created"] = {
        "tasks": len(created_tasks), "risks": len(created_risks), "decisions": len(created_decisions),
        "payment_schedule": len(payment_rows),
        "organizations": len(remembered_organizations),
    }
    result["source"] = {"automatically_linked": automatically_linked, "document_id": document.id, "name": document.name}
    result["financial_check"] = financial_check
    return result


@router.post("/projects/{project_id}/contracts/{contract_id}/initialize-control")
def initialize_contract_control(project_id: int, contract_id: int,
                                db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Create the missing GPR anchor after an explicit user action; safe to repeat."""
    require_project_role(db, user, project_id, "editor")
    row = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if row is None:
        raise HTTPException(404, "Contract not found")
    if not is_financial_contract(row.contract_kind):
        raise HTTPException(422, "Генподрядный договор хранится как контекст; выберите наш доходный договор для ГПР, бюджета и ДДС")
    baseline = db.scalar(select(ScheduleBaseline).where(
        ScheduleBaseline.project_id == project_id,
        ScheduleBaseline.contract_id == contract_id,
    ).order_by(ScheduleBaseline.version.desc()))
    created = baseline is None
    if created:
        version = (db.scalar(select(func.max(ScheduleBaseline.version)).where(
            ScheduleBaseline.project_id == project_id,
        )) or 0) + 1
        baseline = ScheduleBaseline(
            project_id=project_id,
            contract_id=contract_id,
            created_by_user_id=user.id,
            name=f"ГПР по договору {row.number}",
            version=version,
            note="Создано по команде пользователя; заполните этапы и сроки.",
        )
        db.add(baseline)
        db.flush()
        db.add(AuditLog(
            action="contract_control_initialized",
            entity_type="contract",
            entity_id=row.id,
            details=f"baseline={baseline.id}",
        ))
        db.commit()
        db.refresh(baseline)
    return {"created": created, "baseline_id": baseline.id, "contract_id": contract_id}


def _contract(row: Contract, db: Session | None = None) -> dict:
    result = {
        "id": row.id, "project_id": row.project_id, "number": row.number,
        "record_version": row.record_version,
        "title": row.title, "counterparty": row.counterparty,
        "counterparty_organization_id": row.counterparty_organization_id,
        "contract_kind": row.contract_kind, "parent_contract_id": row.parent_contract_id,
        "amount": format(row.amount, ".2f") if row.amount is not None else None,
        "advance_amount": format(row.advance_amount, ".2f") if row.advance_amount is not None else None,
        "retention_percent": row.retention_percent, "warranty_until": row.warranty_until,
        "vat_mode": row.vat_mode, "vat_rate": format(row.vat_rate, ".2f") if row.vat_rate is not None else None,
        "performed_from": row.performed_from, "performed_to": row.performed_to,
        "signed_at": row.signed_at, "status": row.status,
        "source_document_id": row.source_document_id, "notes": row.notes,
        "warnings": _contract_warnings(row),
    }
    if db is not None:
        proposals = list(db.scalars(select(ContractBudgetProposal).where(
            ContractBudgetProposal.contract_id == row.id,
            ContractBudgetProposal.project_id == row.project_id,
        ).order_by(ContractBudgetProposal.contract_record_version.desc(), ContractBudgetProposal.id.desc())))
        result["budget_proposals"] = [_contract_budget_proposal(proposal) for proposal in proposals]
        versions = list(db.scalars(select(ContractVersion).where(
            ContractVersion.contract_id == row.id,
            ContractVersion.project_id == row.project_id,
        ).order_by(ContractVersion.sequence)))
        result["version_history"] = [_contract_version(version) for version in versions]
        document = db.get(Document, row.source_document_id) if row.source_document_id else None
        linked_document_ids = {row.source_document_id} if row.source_document_id else set()
        # Plan files (the uploaded DDS) belong to the contract; supplier invoices booked
        # against its DDS lines do not (owner 21.10.2026: keep invoices apart from the contract).
        linked_document_ids.update(db.scalars(select(CashFlowEntry.source_document_id).where(
            CashFlowEntry.project_id == row.project_id,
            CashFlowEntry.contract_id == row.id,
            CashFlowEntry.source_document_id.is_not(None),
            func.coalesce(CashFlowEntry.invoiced_amount, 0) == 0,
            CashFlowEntry.plan_entry_id.is_(None),
            CashFlowEntry.planned_amount > 0,
        )))
        linked_document_ids.update(db.scalars(select(ContractDocumentLink.document_id).where(
            ContractDocumentLink.project_id == row.project_id,
            ContractDocumentLink.contract_id == row.id,
        )))
        linked_documents = db.scalars(select(Document).where(
            Document.project_id == row.project_id,
            Document.id.in_(linked_document_ids),
        ).order_by(Document.name)).all() if linked_document_ids else []
        result["linked_documents"] = [{
            "id": linked.id,
            "name": linked.name,
            "source": linked.source,
            "source_url": linked.source_url if hasattr(linked, "source_url") else None,
        } for linked in linked_documents]
        source_id = (document.external_id or f"document:{document.id}") if document else None
        task_ids = list(db.scalars(select(Task.id).where(
            Task.project_id == row.project_id, Task.source_file_id == source_id,
        ))) if source_id else []
        result["analysis"] = {
            "source_ready": bool(document and _contract_source_text(document, db)),
            "tasks": len(task_ids),
            "obligations": db.scalar(select(func.count(Obligation.id)).where(
                Obligation.project_id == row.project_id, Obligation.contract_id == row.id,
            )) or 0,
            "risks": db.scalar(select(func.count(Risk.id)).where(
                Risk.project_id == row.project_id, Risk.source_id == source_id,
            )) or 0 if source_id else 0,
            "decisions": db.scalar(select(func.count(Decision.id)).where(
                Decision.project_id == row.project_id, Decision.source_id == source_id,
            )) or 0 if source_id else 0,
        }
    return result
