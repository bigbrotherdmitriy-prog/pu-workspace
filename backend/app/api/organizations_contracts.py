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
from app.models.management import Obligation
from app.models.task import Task
from app.models.execution_finance import CashFlowEntry, ScheduleBaseline, ScheduleItem
from app.core.integration_types import StorageObject
from app.core.contract_roles import allowed_parent_kinds, cash_flow_direction, is_financial_contract
from app.governance_engine import create_governance_items
from app.task_engine import create_tasks_from_files
from sqlalchemy import func
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.audit_log import AuditLog

router = APIRouter(tags=["organizations", "contracts"])


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


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
    status: str = Field(default="active", pattern="^(draft|active|completed|terminated)$")
    source_document_id: int | None = None
    notes: str | None = Field(default=None, max_length=5000)


class ContractLinkUpdate(BaseModel):
    source_document_id: int | None = None
    parent_contract_id: int | None = None
    contract_kind: str | None = Field(
        default=None,
        pattern="^(prime_reference|customer|revenue_subcontract|downstream_subcontract|supply)$",
    )


def _normalized(value: str | None) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", " ", (value or "").casefold()).strip()


def _contract_document_score(row: Contract, document: Document, content: str) -> tuple[int, list[str]]:
    """Rank a possible contract source without mutating either record."""
    name = _normalized(document.name)
    body = _normalized(content[:120_000])
    haystack = f"{name} {body}"
    score = 0
    reasons: list[str] = []

    number = _normalized(row.number)
    compact_number = re.sub(r"\s+", "", number)
    compact_haystack = re.sub(r"\s+", "", haystack)
    if compact_number and len(compact_number) >= 4 and compact_number in compact_haystack:
        score += 60
        reasons.append("совпадает номер договора")

    counterparty = _normalized(row.counterparty)
    if counterparty and len(counterparty) >= 4 and counterparty in haystack:
        score += 25
        reasons.append("совпадает контрагент")

    title_tokens = {
        token for token in _normalized(row.title).split()
        if len(token) >= 5 and token not in {"выполнению", "работ", "системы", "области"}
    }
    matched = sorted(token for token in title_tokens if token in haystack)
    if matched:
        score += min(30, len(matched) * 5)
        reasons.append(f"совпадает предмет договора: {', '.join(matched[:3])}")

    contract_markers = ("договор", "контракт", "государственн контракт", "заказчик", "подрядчик")
    marker_count = sum(1 for marker in contract_markers if marker in haystack)
    if marker_count:
        score += min(15, marker_count * 4)
        reasons.append("обнаружены реквизиты договора")

    if any(marker in name for marker in ("договор", "контракт", "гк ", "гк-")):
        score += 15
        reasons.append("название похоже на договор")
    if any(marker in name for marker in ("приложение", "график", "акт", "письмо", "счет", "счёт")):
        score -= 12
        reasons.append("возможно приложение или связанный документ")
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
    db.commit(); db.refresh(row)
    return _contract(row, db)


def _contract_source_text(document: Document, db: Session | None = None) -> str:
    parts = [part.strip() for part in (document.summary, document.notes) if part and part.strip()]
    if db is not None:
        latest = db.scalar(select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
        ).order_by(DocumentVersion.version_number.desc()))
        if latest and latest.content and latest.content.strip():
            parts.append(latest.content.strip())
    return "\n".join(dict.fromkeys(parts))


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
    documents = db.execute(select(Document, DocumentVersion.content).outerjoin(
        DocumentVersion,
        and_(
            DocumentVersion.document_id == Document.id,
            DocumentVersion.version_number == Document.current_version,
        ),
    ).where(Document.project_id == project_id).order_by(Document.id.desc())).all()
    candidates = []
    for document, extracted_content in documents:
        content = "\n".join(dict.fromkeys(
            part.strip() for part in (document.summary, document.notes, extracted_content)
            if part and part.strip()
        ))
        score, reasons = _contract_document_score(row, document, content)
        candidates.append({
            "document_id": document.id,
            "name": document.name,
            "source": document.source,
            "mime_type": document.mime_type,
            "score": score,
            "reasons": reasons,
            "text_ready": bool(content),
        })
    candidates.sort(key=lambda item: (item["score"], item["text_ready"], item["document_id"]), reverse=True)
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
    if row.source_document_id is None:
        raise HTTPException(409, "Сначала выберите документ договора")
    document = db.scalar(select(Document).where(
        Document.id == row.source_document_id, Document.project_id == project_id,
    ))
    if document is None:
        raise HTTPException(404, "Contract source document not found")
    content = _contract_source_text(document, db)
    if not content:
        raise HTTPException(409, "Документ ещё не проанализирован. Сначала завершите анализ рабочей папки")
    source_id = document.external_id or f"document:{document.id}"
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
                 f"decisions_created={len(created_decisions)}; payment_proposals={len(payment_rows)}; originals_changed=false"),
    ))
    db.commit()
    result = _contract(row, db)
    result["created"] = {
        "tasks": len(created_tasks), "risks": len(created_risks), "decisions": len(created_decisions),
        "payment_schedule": len(payment_rows),
    }
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
        "contract_kind": row.contract_kind, "parent_contract_id": row.parent_contract_id,
        "amount": row.amount, "advance_amount": row.advance_amount,
        "retention_percent": row.retention_percent, "warranty_until": row.warranty_until,
        "signed_at": row.signed_at, "status": row.status,
        "source_document_id": row.source_document_id, "notes": row.notes,
    }
    if db is not None:
        document = db.get(Document, row.source_document_id) if row.source_document_id else None
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
