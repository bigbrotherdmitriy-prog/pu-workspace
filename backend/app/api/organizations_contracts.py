from datetime import date
from decimal import Decimal
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.auth import require_admin, require_project_role, require_user
from app.database import get_db
from app.models.organization_contract import Contract, Organization
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.governance import Decision, Risk
from app.models.management import Meeting, Obligation
from app.models.task import Task
from app.models.execution_finance import (
    AcceptanceAct, BudgetLine, CashFlowEntry, ProcurementItem, ScheduleBaseline, ScheduleItem,
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
from app.contract_evidence import extract_contract_evidence, persist_contract_evidence

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
    warranty_until: date | None = None
    signed_at: date | None = None
    status: str = Field(default="active", pattern="^(draft|active|completed|terminated|archived)$")
    source_document_id: int | None = None
    notes: str | None = Field(default=None, max_length=5000)


class ContractLinkUpdate(BaseModel):
    number: str | None = Field(default=None, min_length=1, max_length=255)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    counterparty: str | None = Field(default=None, max_length=500)
    amount: Decimal | None = Field(default=None, ge=0)
    advance_amount: Decimal | None = Field(default=None, ge=0)
    retention_percent: Decimal | None = Field(default=None, ge=0, le=100)
    signed_at: date | None = None
    status: str | None = Field(default=None, pattern="^(draft|active|completed|terminated|archived)$")
    notes: str | None = Field(default=None, max_length=5000)
    source_document_id: int | None = None
    parent_contract_id: int | None = None
    contract_kind: str | None = Field(
        default=None,
        pattern="^(prime_reference|customer|revenue_subcontract|downstream_subcontract|supply)$",
    )


def _normalized(value: str | None) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", " ", (value or "").casefold()).strip()


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
    row = Contract(project_id=project_id, **payload.model_dump())
    db.add(row); db.flush()
    if is_financial_contract(row.contract_kind):
        version = (db.scalar(select(func.max(ScheduleBaseline.version)).where(ScheduleBaseline.project_id == project_id)) or 0) + 1
        db.add(ScheduleBaseline(
            project_id=project_id, contract_id=row.id, created_by_user_id=user.id,
            name=f"ГПР по договору {row.number}", version=version,
            note="Автоматически создано при добавлении договора; заполните этапы и сроки.",
        ))
    db.add(AuditLog(action="contract_created", entity_type="contract", entity_id=row.id,
                    details=f"Contract: {row.number}; kind={row.contract_kind}"))
    db.commit(); db.refresh(row)
    return _contract(row, db)


@router.patch("/projects/{project_id}/contracts/{contract_id}")
def update_contract_links(project_id: int, contract_id: int, payload: ContractLinkUpdate,
                          db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    row = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if row is None:
        raise HTTPException(404, "Contract not found")
    if "source_document_id" in payload.model_fields_set and payload.source_document_id is not None and not db.scalar(select(Document.id).where(
        Document.id == payload.source_document_id, Document.project_id == project_id,
    )):
        raise HTTPException(422, "Документ не принадлежит выбранному проекту")
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
        row.contract_kind = target_kind
        row.parent_contract_id = parent.id if parent else None
        db.add(AuditLog(action="contract_parent_linked", entity_type="contract", entity_id=row.id,
                        details=f"parent={row.parent_contract_id or 'none'}"))
    if "source_document_id" in payload.model_fields_set:
        row.source_document_id = payload.source_document_id
        db.add(AuditLog(action="contract_source_linked", entity_type="contract", entity_id=row.id,
                        details=f"document={row.source_document_id or 'none'}"))
    editable_fields = {
        "number", "title", "counterparty", "amount", "advance_amount",
        "retention_percent", "signed_at", "status", "notes",
    }
    changed_fields = editable_fields & payload.model_fields_set
    for field in changed_fields:
        value = getattr(payload, field)
        setattr(row, field, value.strip() if isinstance(value, str) else value)
    if changed_fields:
        db.add(AuditLog(
            action="contract_updated", entity_type="contract", entity_id=row.id,
            details=f"fields={','.join(sorted(changed_fields))}; user={user.id}",
        ))
    db.commit(); db.refresh(row)
    return _contract(row, db)


class ContractDelete(BaseModel):
    confirmation: str


def _contract_dependencies(db: Session, project_id: int, contract_id: int) -> dict[str, int]:
    """Return links that would be destroyed or detached by a physical delete."""
    scoped = (
        ("child_contracts", Contract, Contract.parent_contract_id),
        ("documents", ContractDocumentLink, ContractDocumentLink.contract_id),
        ("schedule_baselines", ScheduleBaseline, ScheduleBaseline.contract_id),
        ("budget_lines", BudgetLine, BudgetLine.contract_id),
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
    for name, model, contract_column in scoped:
        project_column = getattr(model, "project_id", None)
        filters = [contract_column == contract_id]
        if project_column is not None:
            filters.append(project_column == project_id)
        count = int(db.scalar(select(func.count()).select_from(model).where(*filters)) or 0)
        if count:
            result[name] = count
    return result


@router.get("/projects/{project_id}/contracts/{contract_id}/deletion-preview")
def contract_deletion_preview(project_id: int, contract_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "owner")
    row = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if row is None:
        raise HTTPException(404, "Contract not found")
    dependencies = _contract_dependencies(db, project_id, contract_id)
    return {
        "contract_id": contract_id,
        "can_delete": not dependencies,
        "dependencies": dependencies,
        "recommended_action": "delete" if not dependencies else "archive",
        "source_documents_affected": False,
    }


@router.delete("/projects/{project_id}/contracts/{contract_id}")
def delete_contract(project_id: int, contract_id: int, payload: ContractDelete,
                    db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "owner")
    row = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if row is None:
        raise HTTPException(404, "Contract not found")
    if payload.confirmation.strip() != row.number.strip():
        raise HTTPException(422, "Введите точный номер договора для подтверждения")
    dependencies = _contract_dependencies(db, project_id, contract_id)
    if dependencies:
        raise HTTPException(409, {
            "code": "contract_has_dependencies",
            "message": "Договор связан с другими сущностями. Архивируйте его либо сначала снимите связи.",
            "dependencies": dependencies,
        })
    number = row.number
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


def _contract_financial_terms(content: str) -> dict:
    """Compatibility entrypoint for exact local contract-term extraction."""
    return extract_contract_evidence(content)


def _apply_contract_financial_terms(row: Contract, terms: dict, *, allow_auto_apply: bool = True) -> dict:
    applied: list[str] = []
    mismatches: list[dict] = []
    if terms.get("manual_review_required") or not allow_auto_apply:
        return {
            "applied": [],
            "mismatches": [],
            "terms": terms,
            "manual_review_required": True,
            "reason_codes": terms.get("reason_codes", []) or ["exact_evidence_required"],
        }
    fields = (
        ("amount", "Сумма договора", "amount_evidence", Decimal("1")),
        ("advance_amount", "Аванс", "advance_evidence", Decimal("1")),
        ("retention_percent", "Удержание", "retention_evidence", Decimal("0.01")),
    )
    # Preflight the complete proposal.  A single conflict makes the operation
    # all-or-nothing, so no unrelated empty field is silently populated.
    for field, label, evidence_field, tolerance in fields:
        extracted = terms.get(field)
        if extracted is None:
            continue
        current = getattr(row, field)
        if current is not None and abs(Decimal(current) - Decimal(extracted)) > tolerance:
            mismatches.append({
                "field": field, "label": label, "current": str(current),
                "extracted": str(extracted), "evidence": terms.get(evidence_field),
            })
    extracted_signed_at = terms.get("signed_at")
    if extracted_signed_at is not None and row.signed_at is not None and row.signed_at != extracted_signed_at:
        proof = terms.get("field_evidence", {}).get("signed_at", [])
        mismatches.append({
            "field": "signed_at", "label": "Дата договора",
            "current": row.signed_at.isoformat(), "extracted": extracted_signed_at.isoformat(),
            "evidence": proof[0]["excerpt"] if proof else None,
        })
    if mismatches:
        return {
            "applied": [],
            "mismatches": mismatches,
            "terms": terms,
            "manual_review_required": True,
            "reason_codes": ["existing_value_conflict"],
        }
    for field, _label, _evidence_field, _tolerance in fields:
        extracted = terms.get(field)
        if extracted is not None and getattr(row, field) is None:
            setattr(row, field, extracted)
            applied.append(field)
    if extracted_signed_at is not None and row.signed_at is None:
        row.signed_at = extracted_signed_at
        applied.append("signed_at")
    return {
        "applied": applied,
        "mismatches": mismatches,
        "terms": terms,
        "manual_review_required": bool(mismatches),
        "reason_codes": ["existing_value_conflict"] if mismatches else [],
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
    for line in (part.strip() for part in content.splitlines() if part.strip()):
        if not re.search(r"плат[её]ж|оплат|аванс", line, re.IGNORECASE):
            continue
        date_match = re.search(r"(?<!\d)([0-3]?\d)[.\-/]([01]?\d)[.\-/](20\d{2})(?!\d)", line)
        amount_matches = re.findall(r"(?<!\d)(\d[\d\s]{2,}(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|лей)?)", line, re.IGNORECASE)
        if not date_match or not amount_matches:
            continue
        try:
            planned_date = date(int(date_match.group(3)), int(date_match.group(2)), int(date_match.group(1)))
            amount = max(Decimal(value.replace(" ", "").replace(",", ".")) for value in amount_matches)
        except (ValueError, ArithmeticError):
            continue
        if amount <= 0:
            continue
        candidates.append({"planned_date": planned_date, "amount": amount, "excerpt": line[:1000]})
    unique: dict[tuple[date, Decimal], dict] = {}
    for item in candidates:
        unique[(item["planned_date"], item["amount"])] = item
    return list(unique.values())[:50]


def _create_payment_schedule_proposals(db: Session, row: Contract, document: Document, content: str) -> list[CashFlowEntry]:
    direction = cash_flow_direction(row.contract_kind)
    if direction is None:
        return []
    baseline = db.scalar(select(ScheduleBaseline).where(
        ScheduleBaseline.project_id == row.project_id,
        ScheduleBaseline.contract_id == row.id,
    ).order_by(ScheduleBaseline.version.desc()))
    stages = list(db.scalars(select(ScheduleItem).where(
        ScheduleItem.project_id == row.project_id,
        ScheduleItem.baseline_id == baseline.id if baseline else False,
    ).order_by(ScheduleItem.planned_finish, ScheduleItem.id)))
    created: list[CashFlowEntry] = []
    for candidate in _payment_schedule_candidates(content):
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
            source_document_id=document.id, direction=direction,
            title=f"Платёж по договору {row.number}", planned_date=candidate["planned_date"],
            planned_amount=candidate["amount"], actual_amount=Decimal("0"),
            counterparty=row.counterparty, status="proposed", source_name=document.name,
            source_excerpt=candidate["excerpt"],
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
    row = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if row is None:
        raise HTTPException(404, "Contract not found")
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
    document_version = db.scalar(select(DocumentVersion).where(
        DocumentVersion.document_id == document.id,
        DocumentVersion.version_number == document.current_version,
    ))
    if document_version is None or not document_version.content.strip():
        extracted_terms = {
            "status": "manual_review_required",
            "manual_review_required": True,
            "reason_codes": ["exact_document_version_unavailable"],
            "field_evidence": {},
        }
        evidence_result = {
            "status": "manual_review_required",
            "manual_review_required": True,
            "reason_codes": ["exact_document_version_unavailable"],
            "document_version_id": None,
            "source_id": None,
            "source_version_id": None,
            "evidence": [],
        }
    else:
        extracted_terms = _contract_financial_terms(document_version.content)
        project = db.get(Project, project_id)
        evidence_result = persist_contract_evidence(
            db,
            organization_id=project.organization_id,
            project_id=project_id,
            document_version=document_version,
            extraction=extracted_terms,
        )
    financial_check = _apply_contract_financial_terms(
        row,
        extracted_terms,
        allow_auto_apply=evidence_result["status"] == "ready",
    )
    financial_check["evidence"] = evidence_result
    financial_check["manual_review_required"] = bool(
        financial_check["manual_review_required"] or evidence_result["manual_review_required"]
    )
    financial_check["reason_codes"] = sorted(set(
        financial_check.get("reason_codes", []) + evidence_result.get("reason_codes", [])
    ))
    source = StorageObject(
        id=source_id, name=document.name, mime_type=document.mime_type or "application/octet-stream",
        parent_id=document.parent_external_id or "contracts", content_text=content,
        object_type="file", provider=document.source,
    )
    created_tasks = create_tasks_from_files(db, project_id, None, [source], source_type="contract_analysis")
    created_risks, created_decisions = create_governance_items(
        db, project_id, [source], source_type="contract_analysis",
    )
    payment_rows = _create_payment_schedule_proposals(db, row, document, content)
    remembered_organizations = remember_contract_organizations(db, row, content, document.id)
    task_ids = list(db.scalars(select(Task.id).where(
        Task.project_id == project_id, Task.source_file_id == source_id,
    )))
    linked_obligations = list(db.scalars(select(Obligation).where(
        Obligation.project_id == project_id, Obligation.task_id.in_(task_ids) if task_ids else False,
    )))
    for obligation in linked_obligations:
        obligation.contract_id = row.id
    db.add(AuditLog(
        action="contract_analyzed", entity_type="contract", entity_id=row.id,
        details=(f"document={document.id}; tasks_created={len(created_tasks)}; "
                 f"obligations_linked={len(linked_obligations)}; risks_created={len(created_risks)}; "
                 f"decisions_created={len(created_decisions)}; payment_proposals={len(payment_rows)}; "
                 f"organizations_remembered={len(remembered_organizations)}; automatically_linked={automatically_linked}; "
                 f"financial_fields_applied={','.join(financial_check['applied']) or 'none'}; "
                 f"financial_mismatches={len(financial_check['mismatches'])}; "
                 f"financial_manual_review={str(financial_check['manual_review_required']).lower()}; "
                 f"evidence_count={len(evidence_result['evidence'])}; originals_changed=false"),
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
        "title": row.title, "counterparty": row.counterparty,
        "counterparty_organization_id": row.counterparty_organization_id,
        "contract_kind": row.contract_kind, "parent_contract_id": row.parent_contract_id,
        "amount": row.amount, "advance_amount": row.advance_amount,
        "retention_percent": row.retention_percent, "warranty_until": row.warranty_until,
        "signed_at": row.signed_at, "status": row.status,
        "source_document_id": row.source_document_id, "notes": row.notes,
    }
    if db is not None:
        document = db.get(Document, row.source_document_id) if row.source_document_id else None
        linked_document_ids = {row.source_document_id} if row.source_document_id else set()
        linked_document_ids.update(db.scalars(select(CashFlowEntry.source_document_id).where(
            CashFlowEntry.project_id == row.project_id,
            CashFlowEntry.contract_id == row.id,
            CashFlowEntry.source_document_id.is_not(None),
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
