from datetime import date, datetime, timezone
from decimal import Decimal
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import AcceptanceAct, BudgetLine, CashFlowEntry, CashFlowFactHistory, ProcurementItem, ScheduleBaseline, ScheduleItem
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.task import Task
from app.models.user import User
from app.models.v54_pilot import Evidence, EvidenceAssessment, SourceCurrent, SourceReference, SourceVersion
from app.structured_import import parse_structured_rows

router = APIRouter(prefix="/execution", tags=["execution-finance"])


class BaselineCreate(BaseModel):
    project_id: int
    contract_id: int | None = None
    name: str = Field(min_length=2, max_length=500)
    note: str | None = Field(default=None, max_length=5000)


class BaselineClone(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=2, max_length=500)
    note: str | None = Field(default=None, max_length=5000)


class ScheduleItemCreate(BaseModel):
    baseline_id: int
    expected_baseline_version: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=2, max_length=500)
    planned_start: date | None = None
    planned_finish: date | None = None
    planned_progress: float = Field(default=0, ge=0, le=100)


class ScheduleProgress(BaseModel):
    actual_progress: float = Field(ge=0, le=100)
    actual_start: date | None = None
    actual_finish: date | None = None
    expected_actual_progress: float | None = Field(default=None, ge=0, le=100)
    evidence_ref: str | None = Field(
        default=None,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$",
    )


class BudgetCreate(BaseModel):
    project_id: int
    contract_id: int | None = None
    schedule_item_id: int | None = None
    task_id: int | None = None
    source_document_id: int | None = None
    evidence_id: str | None = Field(default=None, pattern="^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    evidence_revision: int | None = Field(default=None, ge=1)
    evidence_assessment_version: int | None = Field(default=None, ge=1)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    category: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=2, max_length=1000)
    planned_amount: Decimal = Field(ge=0)
    forecast_amount: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="RUB", pattern="^[A-Z]{3}$")


class CashFlowCreate(BaseModel):
    project_id: int
    contract_id: int | None = None
    schedule_item_id: int | None = None
    task_id: int | None = None
    budget_line_id: int | None = None
    source_document_id: int | None = None
    evidence_id: str | None = Field(default=None, pattern="^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    evidence_revision: int | None = Field(default=None, ge=1)
    evidence_assessment_version: int | None = Field(default=None, ge=1)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    direction: str = Field(pattern="^(inflow|outflow)$")
    title: str = Field(min_length=2, max_length=500)
    planned_date: date
    planned_amount: Decimal = Field(gt=0)
    counterparty: str | None = Field(default=None, max_length=500)


class InvoiceProposalCreate(CashFlowCreate):
    pass


class PaymentConfirmation(BaseModel):
    expected_record_version: int = Field(default=1, ge=1)
    actual_amount: Decimal | None = Field(default=None, gt=0)
    actual_date: date | None = None


class PaymentCorrection(BaseModel):
    expected_record_version: int = Field(default=1, ge=1)
    expected_actual_amount: Decimal = Field(gt=0)
    expected_actual_date: date
    actual_amount: Decimal = Field(gt=0)
    actual_date: date
    reason: str = Field(min_length=3, max_length=1000)


class ProcurementCreate(BaseModel):
    project_id: int
    contract_id: int | None = None
    title: str = Field(min_length=2, max_length=500)
    supplier: str | None = Field(default=None, max_length=500)
    planned_delivery: date | None = None
    planned_amount: Decimal = Field(default=0, ge=0)


class ActCreate(BaseModel):
    project_id: int
    contract_id: int | None = None
    document_id: int | None = None
    number: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=2, max_length=500)
    act_date: date | None = None
    amount: Decimal = Field(default=0, ge=0)


class StatusUpdate(BaseModel):
    status: str = Field(min_length=2, max_length=30)
    expected_status: str | None = Field(default=None, min_length=2, max_length=30)
    actual_amount: Decimal | None = Field(default=None, ge=0)
    actual_date: date | None = None


class StructuredImportRequest(BaseModel):
    project_id: int
    contract_id: int | None = None
    kind: str = Field(pattern="^(schedule|budget|cash-flow)$")
    baseline_id: int | None = None
    expected_baseline_version: int | None = Field(default=None, ge=1)
    schedule_item_id: int | None = None
    task_id: int | None = None
    budget_line_id: int | None = None
    direction: str = Field(default="outflow", pattern="^(inflow|outflow)$")
    source_rows: list[int] = Field(min_length=1, max_length=500)


_DOCUMENT_KIND_MARKERS = {
    "schedule": (("гпр", 45), ("график производства работ", 50), ("календарный план", 40), ("график", 35), ("срок выполнения", 15)),
    "budget": (("бюджет", 45), ("смета", 45), ("стоимость работ", 25), ("ведомость объем", 20)),
    "invoice": (("счет на оплату", 55), ("счёт на оплату", 55), ("итого к оплате", 35), ("платеж", 15)),
    "cash-flow": (("ддс", 55), ("движение денежных средств", 55), ("платежный календарь", 40), ("платёжный календарь", 40)),
    "act": (("акт выполненных работ", 55), ("акт приемки", 50), ("акт приёмки", 50), ("кс-2", 45), ("кс 2", 40)),
}


def _finance_document_score(name: str, content: str, kind: str) -> tuple[int, list[str]]:
    """Explainably classify an extracted project document without changing it."""
    normalized_name = re.sub(r"\s+", " ", name.casefold().replace("_", " "))
    normalized_text = re.sub(r"\s+", " ", content[:120_000].casefold())
    score = 0
    reasons: list[str] = []
    for marker, weight in _DOCUMENT_KIND_MARKERS[kind]:
        if marker in normalized_name:
            score += weight
            reasons.append(f"«{marker}» найдено в названии")
        elif marker in normalized_text:
            score += max(8, weight // 2)
            reasons.append(f"«{marker}» найдено в тексте")
    if kind == "invoice" and any(word in normalized_name for word in ("акт", "договор", "приложение")):
        score -= 20
    if kind == "act" and "счет" in normalized_name:
        score -= 20
    return max(0, min(score, 100)), reasons[:4]


def _finance_document_hints(name: str, content: str) -> dict:
    text = f"{name}\n{content[:120_000]}"
    amount_matches = re.findall(r"(?<!\d)(\d[\d\s]{2,}(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|лей)?)", text, re.IGNORECASE)
    amount = None
    if amount_matches:
        try:
            amount = str(max(Decimal(value.replace(" ", "").replace(",", ".")) for value in amount_matches))
        except Exception:
            amount = None
    date_match = re.search(r"(?<!\d)([0-3]?\d)[.\-/]([01]?\d)[.\-/](20\d{2})(?!\d)", text)
    suggested_date = None
    if date_match:
        try:
            suggested_date = date(int(date_match.group(3)), int(date_match.group(2)), int(date_match.group(1))).isoformat()
        except ValueError:
            pass
    number_match = re.search(r"(?:№|номер|сч[её]т(?:\s+на\s+оплату)?|акт)\s*[:№-]?\s*([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9./_-]{1,40})", text, re.IGNORECASE)
    return {"amount": amount, "date": suggested_date, "number": number_match.group(1) if number_match else None}


def _check_contract(db: Session, project_id: int, contract_id: int | None):
    if contract_id is not None and not db.scalar(select(Contract.id).where(Contract.id == contract_id, Contract.project_id == project_id)):
        raise HTTPException(422, "Договор не принадлежит выбранному проекту")


def _validate_control_links(
    db: Session,
    *,
    project_id: int,
    contract_id: int | None,
    schedule_item_id: int | None,
    task_id: int | None,
    budget_line_id: int | None,
    source_document_id: int | None,
    evidence_id: str | None,
    evidence_revision: int | None,
    evidence_assessment_version: int | None,
    confidence: Decimal | None,
) -> tuple[int | None, float | None, str]:
    """Validate scoped links and return exact legacy version plus review state."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Проект не найден")
    _check_contract(db, project_id, contract_id)

    if schedule_item_id is not None:
        stage = db.get(ScheduleItem, schedule_item_id)
        baseline = db.get(ScheduleBaseline, stage.baseline_id) if stage else None
        if stage is None or stage.project_id != project_id or baseline is None:
            raise HTTPException(422, "Этап ГПР не принадлежит выбранному проекту")
        if contract_id is not None and baseline.contract_id not in {None, contract_id}:
            raise HTTPException(422, "Этап ГПР связан с другим договором")
    if task_id is not None:
        task = db.get(Task, task_id)
        if task is None or task.project_id != project_id:
            raise HTTPException(422, "Задача не принадлежит выбранному проекту")
    if budget_line_id is not None:
        budget = db.get(BudgetLine, budget_line_id)
        if budget is None or budget.project_id != project_id:
            raise HTTPException(422, "Строка бюджета не принадлежит выбранному проекту")
        if contract_id is not None and budget.contract_id not in {None, contract_id}:
            raise HTTPException(422, "Строка бюджета связана с другим договором")

    document_version = None
    if source_document_id is not None:
        document = db.get(Document, source_document_id)
        if document is None or document.project_id != project_id:
            raise HTTPException(422, "Первичный документ не принадлежит выбранному проекту")
        document_version = db.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version_number.desc(), DocumentVersion.id.desc())
        )
        if document_version is None:
            raise HTTPException(409, "У первичного документа нет зафиксированной версии")

    review_status = "required" if confidence is not None and confidence < Decimal("0.90") else "pending_confirmation"
    resolved_confidence = float(confidence) if confidence is not None else None
    pins = (evidence_id, evidence_revision, evidence_assessment_version)
    if any(value is not None for value in pins):
        if not all(value is not None for value in pins) or document_version is None:
            raise HTTPException(422, "Evidence требует точного первичного документа, revision и assessment version")
        evidence = db.scalar(select(Evidence).where(
            Evidence.id == evidence_id,
            Evidence.organization_id == project.organization_id,
            Evidence.revision == evidence_revision,
        ))
        assessment = db.scalar(select(EvidenceAssessment).where(
            EvidenceAssessment.evidence_id == evidence_id,
            EvidenceAssessment.organization_id == project.organization_id,
        ))
        version = db.get(SourceVersion, evidence.source_version_id) if evidence else None
        source = db.get(SourceReference, evidence.source_id) if evidence else None
        current = db.get(SourceCurrent, evidence.source_id) if evidence else None
        if (
            evidence is None
            or assessment is None
            or version is None
            or source is None
            or current is None
            or assessment.record_version != evidence_assessment_version
            or version.legacy_document_version_id != document_version.id
            or source.origin_project_id != project_id
            or current.organization_id != project.organization_id
            or current.version_id != version.id
            or source.availability != "available"
        ):
            raise HTTPException(409, "Evidence не подтверждает текущую версию первичного документа")
        resolved_confidence = evidence.confidence
        if (
            evidence.confidence is None
            or evidence.confidence < 0.90
            or assessment.verification != "verified"
            or assessment.availability != "available"
            or assessment.freshness == "stale"
        ):
            review_status = "required"
    return document_version.id if document_version else None, resolved_confidence, review_status


def _cash_flow_missing_links(item: CashFlowEntry) -> list[str]:
    missing = []
    if item.contract_id is None:
        missing.append("contract")
    if item.schedule_item_id is None and item.task_id is None:
        missing.append("gpr_stage_or_task")
    if item.budget_line_id is None:
        missing.append("budget")
    if item.source_document_id is None or item.source_document_version_id is None:
        missing.append("primary_document")
    return missing


def _next_fact_sequence(db: Session, item_id: int) -> int:
    return int(db.scalar(select(func.max(CashFlowFactHistory.sequence)).where(
        CashFlowFactHistory.cash_flow_entry_id == item_id,
    )) or 0) + 1


def _append_fact_history(
    db: Session,
    *,
    item: CashFlowEntry,
    event: str,
    user_id: int,
    previous_amount: Decimal | None,
    previous_date: date | None,
) -> None:
    db.add(CashFlowFactHistory(
        cash_flow_entry_id=item.id,
        project_id=item.project_id,
        sequence=_next_fact_sequence(db, item.id),
        event=event,
        previous_actual_amount=previous_amount,
        previous_actual_date=previous_date,
        resulting_actual_amount=item.actual_amount,
        resulting_actual_date=item.actual_date,
        resulting_record_version=item.record_version,
        changed_by_user_id=user_id,
    ))


def _audit(db: Session, action: str, kind: str, entity_id: int, user_id: int, details: str):
    db.add(AuditLog(action=action, entity_type=kind, entity_id=entity_id, details=f"user={user_id}; {details}"))


def _schedule_scope(model, contract_id: int | None):
    return model.contract_id.is_(None) if contract_id is None else model.contract_id == contract_id


def _lock_schedule_project(db: Session, project_id: int) -> None:
    """Serialize baseline version allocation in PostgreSQL; SQLite tests remain portable."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:namespace, :project_id)"), {
            "namespace": 0x475052,  # ASCII GPR
            "project_id": project_id,
        })


def _current_approved_baseline(db: Session, baseline: ScheduleBaseline) -> ScheduleBaseline | None:
    return db.scalar(
        select(ScheduleBaseline)
        .where(
            ScheduleBaseline.project_id == baseline.project_id,
            _schedule_scope(ScheduleBaseline, baseline.contract_id),
            ScheduleBaseline.status == "approved",
        )
        .order_by(ScheduleBaseline.version.desc(), ScheduleBaseline.id.desc())
        .with_for_update()
    )


def _linked_budget_totals(rows: list[CashFlowEntry]) -> tuple[Decimal, Decimal]:
    committed = sum((row.planned_amount for row in rows if row.direction == "outflow" and row.status in {"approved", "paid"}), Decimal("0"))
    actual = sum((row.actual_amount for row in rows if row.direction == "outflow" and row.status == "paid"), Decimal("0"))
    return committed, actual


def _refresh_budget_from_cash_flow(db: Session, budget_line_id: int | None) -> None:
    if not budget_line_id:
        return
    budget = db.get(BudgetLine, budget_line_id)
    if budget is None:
        return
    rows = list(db.scalars(select(CashFlowEntry).where(CashFlowEntry.budget_line_id == budget.id)))
    budget.committed_amount, budget.actual_amount = _linked_budget_totals(rows)


@router.get("/overview")
def overview(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    baselines = list(db.scalars(select(ScheduleBaseline).where(ScheduleBaseline.project_id == project_id).order_by(ScheduleBaseline.version.desc())))
    current_by_contract: dict[int | None, int] = {}
    for row in baselines:
        if row.status == "approved" and row.contract_id not in current_by_contract:
            current_by_contract[row.contract_id] = row.id
    current_baseline_ids = set(current_by_contract.values())
    schedule = list(db.scalars(select(ScheduleItem).where(ScheduleItem.project_id == project_id).order_by(ScheduleItem.planned_finish, ScheduleItem.id)))
    budget = list(db.scalars(select(BudgetLine).where(BudgetLine.project_id == project_id).order_by(BudgetLine.id.desc())))
    cash = list(db.scalars(select(CashFlowEntry).where(CashFlowEntry.project_id == project_id).order_by(CashFlowEntry.planned_date, CashFlowEntry.id)))
    procurement = list(db.scalars(select(ProcurementItem).where(ProcurementItem.project_id == project_id).order_by(ProcurementItem.planned_delivery, ProcurementItem.id)))
    acts = list(db.scalars(select(AcceptanceAct).where(AcceptanceAct.project_id == project_id).order_by(AcceptanceAct.act_date.desc(), AcceptanceAct.id.desc())))
    confirmed_budget = [x for x in budget if x.status in {"approved", "active", "closed"}]
    planned = sum((x.planned_amount for x in confirmed_budget), Decimal("0"))
    actual = sum((x.actual_amount for x in confirmed_budget), Decimal("0"))
    forecast = sum(((x.forecast_amount or x.planned_amount) for x in confirmed_budget), Decimal("0"))
    balance = Decimal("0"); minimum = Decimal("0"); gap_date = None
    for row in [x for x in cash if x.status in {"approved", "paid", "received"}]:
        value = row.actual_amount if row.actual_date else row.planned_amount
        balance += value if row.direction == "inflow" else -value
        if balance < minimum:
            minimum, gap_date = balance, row.actual_date or row.planned_date
    today = date.today()
    delayed = [x for x in schedule if x.baseline_id in current_baseline_ids and x.planned_finish and x.planned_finish < today and x.actual_progress < 100]
    late_procurement = [x for x in procurement if x.planned_delivery and x.planned_delivery < today and x.stage not in {"delivered", "accepted", "cancelled"}]
    return {
        "summary": {"budget_planned": planned, "budget_committed": sum((x.committed_amount for x in confirmed_budget), Decimal("0")),
                    "budget_actual": actual, "budget_forecast": forecast,
                    "budget_variance": forecast - planned, "cash_balance_forecast": balance,
                    "cash_gap": minimum, "cash_gap_date": gap_date, "delayed_schedule": len(delayed),
                    "late_procurement": len(late_procurement), "acts_pending": len([x for x in acts if x.status in {"proposed", "approved"}]),
                    "pending_payments": len([x for x in cash if x.direction == "outflow" and x.status == "approved"]),
                    "unlinked_invoices": len([x for x in cash if _cash_flow_missing_links(x)])},
        "baselines": [{"id": x.id, "contract_id": x.contract_id, "name": x.name, "version": x.version,
                       "status": x.status, "note": x.note,
                       "is_current": current_by_contract.get(x.contract_id) == x.id} for x in baselines],
        "schedule": [{"id": x.id, "baseline_id": x.baseline_id, "title": x.title, "planned_start": x.planned_start,
                      "planned_finish": x.planned_finish, "actual_start": x.actual_start, "actual_finish": x.actual_finish,
                      "planned_progress": x.planned_progress, "actual_progress": x.actual_progress, "status": x.status} for x in schedule],
        "budget": [{"id": x.id, "contract_id": x.contract_id, "category": x.category, "description": x.description,
                    "record_version": x.record_version, "schedule_item_id": x.schedule_item_id, "task_id": x.task_id,
                    "source_document_id": x.source_document_id, "source_document_version_id": x.source_document_version_id,
                    "evidence_id": x.evidence_id, "evidence_revision": x.evidence_revision,
                    "evidence_assessment_version": x.evidence_assessment_version, "confidence": x.confidence,
                    "review_status": x.review_status,
                    "planned_amount": x.planned_amount, "committed_amount": x.committed_amount, "actual_amount": x.actual_amount,
                    "forecast_amount": x.forecast_amount, "currency": x.currency, "status": x.status} for x in budget],
        "cash_flow": [{"id": x.id, "contract_id": x.contract_id, "schedule_item_id": x.schedule_item_id,
                       "budget_line_id": x.budget_line_id, "task_id": x.task_id,
                       "source_document_id": x.source_document_id, "source_document_version_id": x.source_document_version_id,
                       "evidence_id": x.evidence_id, "evidence_revision": x.evidence_revision,
                       "evidence_assessment_version": x.evidence_assessment_version, "confidence": x.confidence,
                       "review_status": x.review_status, "record_version": x.record_version,
                       "direction": x.direction, "title": x.title,
                       "planned_date": x.planned_date, "actual_date": x.actual_date, "planned_amount": x.planned_amount,
                       "actual_amount": x.actual_amount, "counterparty": x.counterparty, "status": x.status} for x in cash],
        "procurement": [{"id": x.id, "contract_id": x.contract_id, "title": x.title, "supplier": x.supplier,
                         "stage": x.stage, "planned_delivery": x.planned_delivery, "actual_delivery": x.actual_delivery,
                         "planned_amount": x.planned_amount, "actual_amount": x.actual_amount} for x in procurement],
        "acts": [{"id": x.id, "contract_id": x.contract_id, "document_id": x.document_id, "number": x.number,
                  "title": x.title, "act_date": x.act_date, "amount": x.amount, "status": x.status} for x in acts],
    }


@router.get("/document-candidates")
def document_candidates(project_id: int, contract_id: int | None = None,
                        db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Suggest finance/control roles for analyzed documents; never mutates a source."""
    require_project_role(db, user, project_id, "viewer")
    _check_contract(db, project_id, contract_id)
    linked_document_ids = set(db.scalars(select(CashFlowEntry.source_document_id).where(
        CashFlowEntry.project_id == project_id,
        CashFlowEntry.source_document_id.is_not(None),
    ))) | set(db.scalars(select(AcceptanceAct.document_id).where(
        AcceptanceAct.project_id == project_id,
        AcceptanceAct.document_id.is_not(None),
    )))
    rows = db.execute(select(Document, DocumentVersion.content).outerjoin(
        DocumentVersion,
        (DocumentVersion.document_id == Document.id) &
        (DocumentVersion.version_number == Document.current_version),
    ).where(Document.project_id == project_id).order_by(Document.id.desc())).all()
    candidates = []
    for document, extracted_content in rows:
        content = "\n".join(part.strip() for part in (document.summary, document.notes, extracted_content)
                            if part and part.strip())
        ranked = []
        for kind in _DOCUMENT_KIND_MARKERS:
            score, reasons = _finance_document_score(document.name, content, kind)
            if score:
                ranked.append((score, kind, reasons))
        if not ranked:
            continue
        ranked.sort(reverse=True)
        score, kind, reasons = ranked[0]
        if score < 20:
            continue
        candidates.append({
            "document_id": document.id,
            "name": document.name,
            "source": document.source,
            "kind": kind,
            "score": score,
            "reasons": reasons,
            "hints": _finance_document_hints(document.name, content),
            "already_linked": document.id in linked_document_ids,
            "originals_changed": False,
        })
    candidates.sort(key=lambda item: (item["already_linked"], -item["score"], item["name"].casefold()))
    return {"project_id": project_id, "contract_id": contract_id, "candidates": candidates[:100],
            "requires_confirmation": True, "originals_changed": False}


def _document_content(db: Session, project_id: int, document_id: int) -> tuple[Document, DocumentVersion, str]:
    document = db.scalar(select(Document).where(Document.id == document_id, Document.project_id == project_id))
    if document is None:
        raise HTTPException(404, "Документ не найден в выбранном проекте")
    version = db.scalar(select(DocumentVersion).where(
        DocumentVersion.document_id == document.id,
    ).order_by(DocumentVersion.version_number.desc()))
    content = version.content if version and version.content else ""
    if not content.strip():
        raise HTTPException(409, "У документа ещё нет извлечённого табличного текста")
    return document, version, content


def _import_date(value: str | None) -> date | None:
    """Convert the normalized parser value before assigning it to SQLAlchemy Date."""
    return date.fromisoformat(value) if value else None


@router.get("/documents/{document_id}/structured-preview")
def structured_preview(document_id: int, project_id: int, kind: str,
                       db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    if kind not in {"schedule", "budget", "cash-flow"}:
        raise HTTPException(422, "Поддерживаются ГПР, бюджет и ДДС")
    document, _version, content = _document_content(db, project_id, document_id)
    preview = parse_structured_rows(content, kind)
    return {"document_id": document.id, "name": document.name, "kind": kind, **preview,
            "requires_confirmation": True, "originals_changed": False}


@router.post("/documents/{document_id}/structured-import")
def structured_import(document_id: int, payload: StructuredImportRequest,
                      db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    _check_contract(db, payload.project_id, payload.contract_id)
    document, document_version, content = _document_content(db, payload.project_id, document_id)
    preview = parse_structured_rows(content, payload.kind)
    if len(payload.source_rows) != len(set(payload.source_rows)):
        raise HTTPException(422, "Строки источника не должны повторяться")
    selected = {row["source_row"]: row for row in preview["rows"] if row["source_row"] in set(payload.source_rows)}
    if set(payload.source_rows) - set(selected):
        raise HTTPException(422, "Выбраны отсутствующие строки источника")
    if any(not row["importable"] for row in selected.values()):
        raise HTTPException(422, "Сначала исправьте строки с ошибками")

    baseline = None
    if payload.kind == "schedule":
        _lock_schedule_project(db, payload.project_id)
        baseline = db.scalar(select(ScheduleBaseline).where(
            ScheduleBaseline.id == payload.baseline_id,
        ).with_for_update()) if payload.baseline_id else None
        if baseline is None or baseline.project_id != payload.project_id:
            raise HTTPException(422, "Для импорта ГПР выберите черновик baseline проекта")
        if baseline.status != "draft":
            raise HTTPException(409, "Утверждённый baseline неизменяем")
        if payload.expected_baseline_version is None:
            raise HTTPException(422, "Для импорта ГПР укажите ожидаемую версию baseline")
        if baseline.version != payload.expected_baseline_version:
            raise HTTPException(409, "Версия baseline изменилась; обновите ГПР перед импортом")
        if payload.contract_id and baseline.contract_id not in {None, payload.contract_id}:
            raise HTTPException(422, "Baseline связан с другим договором")

    created = []
    resolved = []
    replayed = []
    for source_row in payload.source_rows:
        row = selected[source_row]
        source_name = f"{document.name}, строка {source_row}"
        if payload.kind == "schedule":
            existing = db.scalar(select(ScheduleItem).where(
                ScheduleItem.baseline_id == baseline.id,
                ScheduleItem.source_name == source_name,
            ).order_by(ScheduleItem.id))
            if existing is not None:
                resolved.append(existing.id)
                replayed.append(existing.id)
                continue
            item = ScheduleItem(
                project_id=payload.project_id, baseline_id=baseline.id, title=row["title"],
                planned_start=_import_date(row["planned_start"]),
                planned_finish=_import_date(row["planned_finish"]),
                planned_progress=min(100, max(0, row["progress"])),
                source_name=source_name, source_excerpt=row["excerpt"], status="planned",
            )
        elif payload.kind == "budget":
            _validate_control_links(
                db,
                project_id=payload.project_id,
                contract_id=payload.contract_id,
                schedule_item_id=payload.schedule_item_id,
                task_id=payload.task_id,
                budget_line_id=None,
                source_document_id=document.id,
                evidence_id=None,
                evidence_revision=None,
                evidence_assessment_version=None,
                confidence=None,
            )
            amount = Decimal(row["amount"])
            item = BudgetLine(
                project_id=payload.project_id, contract_id=payload.contract_id,
                schedule_item_id=payload.schedule_item_id, task_id=payload.task_id,
                source_document_id=document.id, source_document_version_id=document_version.id,
                category=row["category"], description=row["title"], planned_amount=amount,
                forecast_amount=amount, status="proposed", source_name=source_name,
                source_excerpt=row["excerpt"], review_status="required",
            )
        else:
            _validate_control_links(
                db,
                project_id=payload.project_id,
                contract_id=payload.contract_id,
                schedule_item_id=payload.schedule_item_id,
                task_id=payload.task_id,
                budget_line_id=payload.budget_line_id,
                source_document_id=document.id,
                evidence_id=None,
                evidence_revision=None,
                evidence_assessment_version=None,
                confidence=None,
            )
            item = CashFlowEntry(
                project_id=payload.project_id, contract_id=payload.contract_id,
                schedule_item_id=payload.schedule_item_id, task_id=payload.task_id,
                budget_line_id=payload.budget_line_id,
                source_document_id=document.id, source_document_version_id=document_version.id,
                direction=row["direction"] or payload.direction,
                title=row["title"], planned_date=_import_date(row["planned_date"]),
                planned_amount=Decimal(row["amount"]), counterparty=row["counterparty"],
                status="proposed", source_name=source_name, source_excerpt=row["excerpt"],
                review_status="required",
            )
        db.add(item)
        db.flush()
        created.append(item.id)
        resolved.append(item.id)
    db.add(AuditLog(
        action="structured_document_imported", entity_type="document", entity_id=document.id,
        details=(f"user={user.id}; kind={payload.kind}; contract={payload.contract_id}; "
                 f"rows={','.join(map(str, payload.source_rows))}; created={','.join(map(str, created))}; "
                 f"replayed={','.join(map(str, replayed))}; originals_changed=false"),
    ))
    db.commit()
    return {"document_id": document.id, "kind": payload.kind, "created_ids": resolved,
            "created": len(created), "already_existing": len(replayed),
            "status": "proposed", "originals_changed": False}


@router.post("/baselines")
def create_baseline(payload: BaselineCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "manager")
    _check_contract(db, payload.project_id, payload.contract_id)
    _lock_schedule_project(db, payload.project_id)
    existing_draft = db.scalar(select(ScheduleBaseline).where(
        ScheduleBaseline.project_id == payload.project_id,
        _schedule_scope(ScheduleBaseline, payload.contract_id),
        ScheduleBaseline.status == "draft",
    ).order_by(ScheduleBaseline.version.desc()).with_for_update())
    if existing_draft is not None:
        raise HTTPException(409, "Для договора уже существует черновик ГПР; завершите или удалите его перед новой версией")
    version = (db.scalar(select(func.max(ScheduleBaseline.version)).where(ScheduleBaseline.project_id == payload.project_id)) or 0) + 1
    item = ScheduleBaseline(project_id=payload.project_id, contract_id=payload.contract_id,
                            created_by_user_id=user.id, name=payload.name.strip(), version=version, note=payload.note)
    db.add(item); db.flush(); _audit(db, "baseline_created", "schedule_baseline", item.id, user.id, f"version={version}"); db.commit(); db.refresh(item)
    return {"id": item.id, "version": item.version, "status": item.status}


@router.post("/baselines/{baseline_id}/clone")
def clone_baseline(baseline_id: int, payload: BaselineClone,
                   db: Session = Depends(get_db), user: User = Depends(require_user)):
    source = db.get(ScheduleBaseline, baseline_id)
    if source is None:
        raise HTTPException(404, "Baseline not found")
    require_project_role(db, user, source.project_id, "manager")
    _lock_schedule_project(db, source.project_id)
    source = db.scalar(select(ScheduleBaseline).where(ScheduleBaseline.id == baseline_id).with_for_update())
    if source.version != payload.expected_version:
        raise HTTPException(409, "Версия исходного baseline изменилась; обновите ГПР")
    if source.status != "approved":
        raise HTTPException(409, "Новую редакцию можно создать только из текущего утверждённого baseline")
    current = _current_approved_baseline(db, source)
    if current is None or current.id != source.id:
        raise HTTPException(409, "Исходный baseline больше не является текущим")
    draft = db.scalar(select(ScheduleBaseline).where(
        ScheduleBaseline.project_id == source.project_id,
        _schedule_scope(ScheduleBaseline, source.contract_id),
        ScheduleBaseline.status == "draft",
    ).order_by(ScheduleBaseline.version.desc()).with_for_update())
    if draft is not None:
        return {"id": draft.id, "version": draft.version, "status": draft.status, "already_created": True}
    version = (db.scalar(select(func.max(ScheduleBaseline.version)).where(
        ScheduleBaseline.project_id == source.project_id,
    )) or 0) + 1
    draft = ScheduleBaseline(
        project_id=source.project_id,
        contract_id=source.contract_id,
        created_by_user_id=user.id,
        name=(payload.name or source.name).strip(),
        version=version,
        status="draft",
        note=payload.note if payload.note is not None else source.note,
    )
    db.add(draft)
    db.flush()
    source_items = list(db.scalars(select(ScheduleItem).where(
        ScheduleItem.baseline_id == source.id,
    ).order_by(ScheduleItem.id)))
    for source_item in source_items:
        db.add(ScheduleItem(
            project_id=source.project_id,
            baseline_id=draft.id,
            title=source_item.title,
            planned_start=source_item.planned_start,
            planned_finish=source_item.planned_finish,
            planned_progress=source_item.planned_progress,
            actual_start=None,
            actual_finish=None,
            actual_progress=0,
            status="planned",
            source_name=source_item.source_name,
            source_excerpt=source_item.source_excerpt,
        ))
    _audit(db, "baseline_cloned", "schedule_baseline", draft.id, user.id,
           f"source_baseline={source.id}; version={version}; facts_copied=false")
    db.commit()
    db.refresh(draft)
    return {"id": draft.id, "version": draft.version, "status": draft.status, "already_created": False}


@router.post("/schedule-items")
def create_schedule_item(payload: ScheduleItemCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    baseline = db.get(ScheduleBaseline, payload.baseline_id)
    if baseline is None: raise HTTPException(404, "Baseline not found")
    require_project_role(db, user, baseline.project_id, "editor")
    _lock_schedule_project(db, baseline.project_id)
    baseline = db.scalar(select(ScheduleBaseline).where(
        ScheduleBaseline.id == payload.baseline_id,
    ).with_for_update())
    if baseline.status != "draft": raise HTTPException(409, "Утверждённый или архивный baseline неизменяем; создайте новую версию")
    if payload.expected_baseline_version is None:
        raise HTTPException(422, "Укажите ожидаемую версию baseline")
    if payload.expected_baseline_version != baseline.version:
        raise HTTPException(409, "Версия baseline изменилась; обновите ГПР")
    item_data = payload.model_dump(exclude={"expected_baseline_version"})
    duplicate = db.scalar(select(ScheduleItem).where(
        ScheduleItem.baseline_id == baseline.id,
        ScheduleItem.title == payload.title,
        ScheduleItem.planned_start == payload.planned_start,
        ScheduleItem.planned_finish == payload.planned_finish,
        ScheduleItem.planned_progress == payload.planned_progress,
    ).order_by(ScheduleItem.id))
    if duplicate is not None:
        return {"id": duplicate.id, "status": duplicate.status, "already_created": True}
    item = ScheduleItem(project_id=baseline.project_id, **item_data)
    db.add(item); db.flush(); _audit(db, "schedule_item_created", "schedule_item", item.id, user.id, "proposal"); db.commit(); db.refresh(item)
    return {"id": item.id, "status": item.status, "already_created": False}


@router.patch("/schedule-items/{item_id}")
def update_schedule(item_id: int, payload: ScheduleProgress, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.get(ScheduleItem, item_id)
    if item is None: raise HTTPException(404, "Schedule item not found")
    require_project_role(db, user, item.project_id, "editor")
    _lock_schedule_project(db, item.project_id)
    item = db.scalar(select(ScheduleItem).where(ScheduleItem.id == item_id).with_for_update())
    baseline = db.scalar(select(ScheduleBaseline).where(
        ScheduleBaseline.id == item.baseline_id,
    ).with_for_update())
    if baseline is None or baseline.status != "approved":
        raise HTTPException(409, "Факт можно вносить только в текущий утверждённый baseline")
    current = _current_approved_baseline(db, baseline)
    if current is None or current.id != baseline.id:
        raise HTTPException(409, "Факт нельзя вносить в историческую версию ГПР")
    values = payload.model_dump(exclude_unset=True, exclude={"expected_actual_progress", "evidence_ref"})
    if all(getattr(item, name) == value for name, value in values.items()):
        return {"id": item.id, "status": item.status, "actual_progress": item.actual_progress,
                "already_applied": True}
    if payload.expected_actual_progress is None:
        raise HTTPException(422, "Укажите ожидаемое текущее значение факта")
    if item.actual_progress != payload.expected_actual_progress:
        raise HTTPException(409, "Факт ГПР уже изменён; обновите данные перед повтором")
    for name, value in values.items(): setattr(item, name, value)
    item.status = "completed" if item.actual_progress == 100 else "in_progress"
    evidence = payload.evidence_ref or "none"
    _audit(db, "schedule_actual_updated", "schedule_item", item.id, user.id,
           f"progress={item.actual_progress}; evidence_ref={evidence}"); db.commit()
    return {"id": item.id, "status": item.status, "actual_progress": item.actual_progress,
            "already_applied": False}


@router.post("/budget")
def create_budget(payload: BudgetCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    source_version_id, confidence, review_status = _validate_control_links(
        db,
        project_id=payload.project_id,
        contract_id=payload.contract_id,
        schedule_item_id=payload.schedule_item_id,
        task_id=payload.task_id,
        budget_line_id=None,
        source_document_id=payload.source_document_id,
        evidence_id=payload.evidence_id,
        evidence_revision=payload.evidence_revision,
        evidence_assessment_version=payload.evidence_assessment_version,
        confidence=payload.confidence,
    )
    data = payload.model_dump()
    data["forecast_amount"] = data["forecast_amount"] if data["forecast_amount"] is not None else data["planned_amount"]
    data["source_document_version_id"] = source_version_id
    data["confidence"] = confidence
    data["review_status"] = review_status
    item = BudgetLine(**data); db.add(item); db.flush(); _audit(db, "budget_proposed", "budget_line", item.id, user.id, "status=proposed"); db.commit(); return {"id": item.id, "status": item.status}


@router.post("/cash-flow")
def create_cash_flow(payload: CashFlowCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    source_version_id, confidence, review_status = _validate_control_links(
        db,
        project_id=payload.project_id,
        contract_id=payload.contract_id,
        schedule_item_id=payload.schedule_item_id,
        task_id=payload.task_id,
        budget_line_id=payload.budget_line_id,
        source_document_id=payload.source_document_id,
        evidence_id=payload.evidence_id,
        evidence_revision=payload.evidence_revision,
        evidence_assessment_version=payload.evidence_assessment_version,
        confidence=payload.confidence,
    )
    data = payload.model_dump()
    data["source_document_version_id"] = source_version_id
    data["confidence"] = confidence
    data["review_status"] = review_status
    item = CashFlowEntry(**data)
    db.add(item); db.flush(); _audit(db, "cash_flow_proposed", "cash_flow", item.id, user.id, f"status=proposed; review={review_status}"); db.commit()
    return {"id": item.id, "status": item.status, "review_status": item.review_status}


@router.post("/invoice-proposals")
def create_invoice_proposal(payload: InvoiceProposalCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    _check_contract(db, payload.project_id, payload.contract_id)
    if payload.direction != "outflow":
        raise HTTPException(422, "Счёт на оплату должен быть расходом ДДС")
    if (
        payload.contract_id is None
        or (payload.schedule_item_id is None and payload.task_id is None)
        or payload.budget_line_id is None
        or payload.source_document_id is None
    ):
        raise HTTPException(
            422,
            "Для счёта обязательны договор, ГПР/задача, строка бюджета и первичный документ",
        )
    source_version_id, confidence, review_status = _validate_control_links(
        db,
        project_id=payload.project_id,
        contract_id=payload.contract_id,
        schedule_item_id=payload.schedule_item_id,
        task_id=payload.task_id,
        budget_line_id=payload.budget_line_id,
        source_document_id=payload.source_document_id,
        evidence_id=payload.evidence_id,
        evidence_revision=payload.evidence_revision,
        evidence_assessment_version=payload.evidence_assessment_version,
        confidence=payload.confidence,
    )
    data = payload.model_dump()
    data["source_document_version_id"] = source_version_id
    data["confidence"] = confidence
    data["review_status"] = review_status
    item = CashFlowEntry(**data, status="proposed")
    db.add(item); db.flush()
    _audit(db, "invoice_cash_flow_proposed", "cash_flow", item.id, user.id,
           f"contract={payload.contract_id}; schedule={payload.schedule_item_id}; budget={payload.budget_line_id}; document={payload.source_document_id}")
    db.commit()
    return {"id": item.id, "status": item.status, "requires_payment_confirmation": True}


@router.post("/cash-flow/{item_id}/confirm-payment")
def confirm_payment(item_id: int, payload: PaymentConfirmation, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.scalar(select(CashFlowEntry).where(CashFlowEntry.id == item_id).with_for_update())
    if item is None:
        raise HTTPException(404, "Запись ДДС не найдена")
    require_project_role(db, user, item.project_id, "manager")
    paid_status = "received" if item.direction == "inflow" else "paid"
    if item.status in {"paid", "received"}:
        amount_conflicts = (
            payload.actual_amount is not None
            and item.actual_amount != payload.actual_amount
        )
        date_conflicts = (
            payload.actual_date is not None
            and item.actual_date != payload.actual_date
        )
        if amount_conflicts or date_conflicts:
            raise HTTPException(
                409,
                "Оплата уже подтверждена с другими значениями; создайте отдельную корректировку",
            )
        return {"id": item.id, "status": item.status, "already_confirmed": True}
    if item.status == "proposed":
        raise HTTPException(409, "Сначала подтвердите плановую запись ДДС")
    if item.status != "approved":
        raise HTTPException(409, "Эту запись нельзя подтвердить как оплаченную")
    if item.record_version != payload.expected_record_version:
        raise HTTPException(409, "Запись ДДС уже изменена; обновите данные и повторите")
    missing_links = _cash_flow_missing_links(item)
    if missing_links:
        raise HTTPException(409, "Перед подтверждением оплаты завершите связи ДДС")
    if item.review_status != "confirmed":
        raise HTTPException(409, "Запись ДДС требует ручной проверки")
    actual_amount = payload.actual_amount or item.planned_amount
    item.actual_amount = actual_amount
    item.actual_date = payload.actual_date or date.today()
    item.status = paid_status
    item.record_version += 1
    item.confirmed_by_user_id = user.id
    item.confirmed_at = datetime.now(timezone.utc)
    _refresh_budget_from_cash_flow(db, item.budget_line_id)
    _append_fact_history(db, item=item, event="confirmed", user_id=user.id, previous_amount=None, previous_date=None)
    _audit(db, "cash_flow_payment_confirmed", "cash_flow", item.id, user.id,
           f"status={paid_status}; amount={actual_amount}; date={item.actual_date}; budget={item.budget_line_id}")
    db.commit()
    return {"id": item.id, "status": item.status, "actual_amount": item.actual_amount,
            "actual_date": item.actual_date, "record_version": item.record_version, "already_confirmed": False}


@router.post("/cash-flow/{item_id}/correct-payment")
def correct_payment(item_id: int, payload: PaymentCorrection, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.scalar(select(CashFlowEntry).where(CashFlowEntry.id == item_id).with_for_update())
    if item is None:
        raise HTTPException(404, "Запись ДДС не найдена")
    require_project_role(db, user, item.project_id, "manager")
    if item.status not in {"paid", "received"} or item.actual_amount is None or item.actual_date is None:
        raise HTTPException(409, "Корректировать можно только подтверждённую оплату")
    if item.record_version != payload.expected_record_version:
        raise HTTPException(409, "Подтверждённая оплата уже изменена; обновите данные и повторите")
    if item.actual_amount != payload.expected_actual_amount or item.actual_date != payload.expected_actual_date:
        raise HTTPException(409, "Подтверждённая оплата уже изменена; обновите данные и повторите")
    if item.actual_amount == payload.actual_amount and item.actual_date == payload.actual_date:
        raise HTTPException(422, "Корректировка не изменяет сумму или дату оплаты")

    old_amount = item.actual_amount
    old_date = item.actual_date
    item.actual_amount = payload.actual_amount
    item.actual_date = payload.actual_date
    item.record_version += 1
    _refresh_budget_from_cash_flow(db, item.budget_line_id)
    _append_fact_history(
        db,
        item=item,
        event="corrected",
        user_id=user.id,
        previous_amount=old_amount,
        previous_date=old_date,
    )
    _audit(
        db,
        "cash_flow_payment_corrected",
        "cash_flow",
        item.id,
        user.id,
        (
            f"old_amount={old_amount}; old_date={old_date}; "
            f"new_amount={payload.actual_amount}; new_date={payload.actual_date}; "
            "reason_supplied=true"
        ),
    )
    db.commit()
    return {
        "id": item.id,
        "status": item.status,
        "actual_amount": item.actual_amount,
        "actual_date": item.actual_date,
        "record_version": item.record_version,
        "corrected": True,
    }


@router.post("/procurement")
def create_procurement(payload: ProcurementCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor"); _check_contract(db, payload.project_id, payload.contract_id)
    item = ProcurementItem(**payload.model_dump()); db.add(item); db.flush(); _audit(db, "procurement_created", "procurement", item.id, user.id, "stage=request"); db.commit(); return {"id": item.id, "stage": item.stage}


@router.post("/acts")
def create_act(payload: ActCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor"); _check_contract(db, payload.project_id, payload.contract_id)
    item = AcceptanceAct(**payload.model_dump()); db.add(item); db.flush(); _audit(db, "act_proposed", "acceptance_act", item.id, user.id, "status=proposed"); db.commit(); return {"id": item.id, "status": item.status}


@router.patch("/{kind}/{item_id}/status")
def update_status(kind: str, item_id: int, payload: StatusUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    models = {"budget": BudgetLine, "cash-flow": CashFlowEntry, "procurement": ProcurementItem, "acts": AcceptanceAct, "baselines": ScheduleBaseline}
    model = models.get(kind)
    if model is None: raise HTTPException(404, "Unsupported register")
    item = db.scalar(select(model).where(model.id == item_id).with_for_update())
    if item is None: raise HTTPException(404, "Item not found")
    require_project_role(db, user, item.project_id, "manager")
    if kind == "baselines":
        _lock_schedule_project(db, item.project_id)
    item = db.scalar(select(model).where(model.id == item_id).with_for_update())
    allowed = {"budget": {"approved", "active", "closed", "rejected"}, "cash-flow": {"approved", "cancelled"},
               "procurement": {"request", "ordered", "delivered", "accepted", "cancelled"}, "acts": {"approved", "signed", "paid", "rejected"},
               "baselines": {"approved"}}[kind]
    if payload.status not in allowed: raise HTTPException(422, "Недопустимый статус")
    if kind in {"budget", "cash-flow"} and (
        payload.actual_amount is not None or payload.actual_date is not None
    ):
        raise HTTPException(
            422,
            "Факт бюджета/ДДС нельзя передать вместе со статусом; используйте отдельное подтверждение оплаты",
        )
    if item.status == payload.status and kind in {"budget", "cash-flow"}:
        return {"id": item.id, "status": item.status, "already_confirmed": True, "record_version": item.record_version}
    if kind in {"budget", "cash-flow"} and payload.status in {"approved", "active", "closed"}:
        if item.contract_id is None or (item.schedule_item_id is None and item.task_id is None) or item.source_document_version_id is None:
            raise HTTPException(409, "Перед подтверждением завершите связи с договором, ГПР/задачей и первичным документом")
        if kind == "cash-flow" and item.budget_line_id is None:
            raise HTTPException(409, "Перед подтверждением ДДС свяжите строку бюджета")
        item.review_status = "confirmed"
        item.confirmed_by_user_id = user.id
        item.confirmed_at = datetime.now(timezone.utc)
        item.record_version += 1
    elif kind in {"budget", "cash-flow"} and payload.status in {"rejected", "cancelled"}:
        item.review_status = "rejected"
        item.record_version += 1
    elif item.status == payload.status:
        return {"id": item.id, "status": item.status, "already_applied": True}
    if payload.expected_status is not None and item.status != payload.expected_status:
        raise HTTPException(409, "Статус уже изменён; обновите данные перед повтором")
    superseded_id = None
    if kind == "baselines":
        if payload.expected_status is None:
            raise HTTPException(422, "Для утверждения baseline укажите ожидаемый статус")
        if item.status != "draft":
            raise HTTPException(409, "Утвердить можно только черновик baseline")
        current = _current_approved_baseline(db, item)
        if current is not None and current.id != item.id:
            current.status = "superseded"
            superseded_id = current.id
            _audit(db, "baseline_superseded", "schedule_baseline", current.id, user.id,
                   f"replacement_baseline={item.id}; replacement_version={item.version}")
    item.status = payload.status
    if hasattr(item, "approved_at") and payload.status == "approved": item.approved_at = datetime.now(timezone.utc)
    if payload.actual_amount is not None and hasattr(item, "actual_amount"): item.actual_amount = payload.actual_amount
    if payload.actual_date is not None:
        if hasattr(item, "actual_date"): item.actual_date = payload.actual_date
        if hasattr(item, "actual_delivery"): item.actual_delivery = payload.actual_date
    if kind == "cash-flow":
        _refresh_budget_from_cash_flow(db, item.budget_line_id)
    _audit(db, f"{kind}_status_updated", kind, item.id, user.id, f"status={payload.status}"); db.commit()
    result = {
        "id": item.id,
        "status": item.status,
        "already_applied": False,
        "record_version": getattr(item, "record_version", None),
    }
    if kind == "baselines":
        result["superseded_id"] = superseded_id
    return result
