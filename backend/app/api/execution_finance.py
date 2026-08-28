from datetime import date, datetime, timezone
from decimal import Decimal
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import AcceptanceAct, BudgetLine, CashFlowEntry, ProcurementItem, ScheduleBaseline, ScheduleItem
from app.models.organization_contract import Contract
from app.models.user import User

router = APIRouter(prefix="/execution", tags=["execution-finance"])


class BaselineCreate(BaseModel):
    project_id: int
    contract_id: int | None = None
    name: str = Field(min_length=2, max_length=500)
    note: str | None = Field(default=None, max_length=5000)


class ScheduleItemCreate(BaseModel):
    baseline_id: int
    title: str = Field(min_length=2, max_length=500)
    planned_start: date | None = None
    planned_finish: date | None = None
    planned_progress: float = Field(default=0, ge=0, le=100)


class ScheduleProgress(BaseModel):
    actual_progress: float = Field(ge=0, le=100)
    actual_start: date | None = None
    actual_finish: date | None = None


class BudgetCreate(BaseModel):
    project_id: int
    contract_id: int | None = None
    category: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=2, max_length=1000)
    planned_amount: Decimal = Field(ge=0)
    forecast_amount: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="RUB", pattern="^[A-Z]{3}$")


class CashFlowCreate(BaseModel):
    project_id: int
    contract_id: int | None = None
    direction: str = Field(pattern="^(inflow|outflow)$")
    title: str = Field(min_length=2, max_length=500)
    planned_date: date
    planned_amount: Decimal = Field(gt=0)
    counterparty: str | None = Field(default=None, max_length=500)


class InvoiceProposalCreate(CashFlowCreate):
    schedule_item_id: int | None = None
    budget_line_id: int | None = None
    source_document_id: int | None = None


class PaymentConfirmation(BaseModel):
    actual_amount: Decimal | None = Field(default=None, gt=0)
    actual_date: date | None = None


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
    actual_amount: Decimal | None = Field(default=None, ge=0)
    actual_date: date | None = None


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


def _audit(db: Session, action: str, kind: str, entity_id: int, user_id: int, details: str):
    db.add(AuditLog(action=action, entity_type=kind, entity_id=entity_id, details=f"user={user_id}; {details}"))


@router.get("/overview")
def overview(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    baselines = list(db.scalars(select(ScheduleBaseline).where(ScheduleBaseline.project_id == project_id).order_by(ScheduleBaseline.version.desc())))
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
    delayed = [x for x in schedule if x.planned_finish and x.planned_finish < today and x.actual_progress < 100]
    late_procurement = [x for x in procurement if x.planned_delivery and x.planned_delivery < today and x.stage not in {"delivered", "accepted", "cancelled"}]
    return {
        "summary": {"budget_planned": planned, "budget_actual": actual, "budget_forecast": forecast,
                    "budget_variance": forecast - planned, "cash_balance_forecast": balance,
                    "cash_gap": minimum, "cash_gap_date": gap_date, "delayed_schedule": len(delayed),
                    "late_procurement": len(late_procurement), "acts_pending": len([x for x in acts if x.status in {"proposed", "approved"}])},
        "baselines": [{"id": x.id, "contract_id": x.contract_id, "name": x.name, "version": x.version, "status": x.status, "note": x.note} for x in baselines],
        "schedule": [{"id": x.id, "baseline_id": x.baseline_id, "title": x.title, "planned_start": x.planned_start,
                      "planned_finish": x.planned_finish, "actual_start": x.actual_start, "actual_finish": x.actual_finish,
                      "planned_progress": x.planned_progress, "actual_progress": x.actual_progress, "status": x.status} for x in schedule],
        "budget": [{"id": x.id, "contract_id": x.contract_id, "category": x.category, "description": x.description,
                    "planned_amount": x.planned_amount, "committed_amount": x.committed_amount, "actual_amount": x.actual_amount,
                    "forecast_amount": x.forecast_amount, "currency": x.currency, "status": x.status} for x in budget],
        "cash_flow": [{"id": x.id, "contract_id": x.contract_id, "schedule_item_id": x.schedule_item_id,
                       "budget_line_id": x.budget_line_id, "source_document_id": x.source_document_id,
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


@router.post("/baselines")
def create_baseline(payload: BaselineCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "manager")
    _check_contract(db, payload.project_id, payload.contract_id)
    version = (db.scalar(select(func.max(ScheduleBaseline.version)).where(ScheduleBaseline.project_id == payload.project_id)) or 0) + 1
    item = ScheduleBaseline(project_id=payload.project_id, contract_id=payload.contract_id,
                            created_by_user_id=user.id, name=payload.name.strip(), version=version, note=payload.note)
    db.add(item); db.flush(); _audit(db, "baseline_created", "schedule_baseline", item.id, user.id, f"version={version}"); db.commit(); db.refresh(item)
    return {"id": item.id, "version": item.version, "status": item.status}


@router.post("/schedule-items")
def create_schedule_item(payload: ScheduleItemCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    baseline = db.get(ScheduleBaseline, payload.baseline_id)
    if baseline is None: raise HTTPException(404, "Baseline not found")
    require_project_role(db, user, baseline.project_id, "editor")
    if baseline.status == "approved": raise HTTPException(409, "Утверждённый baseline неизменяем; создайте новую версию")
    item = ScheduleItem(project_id=baseline.project_id, **payload.model_dump())
    db.add(item); db.flush(); _audit(db, "schedule_item_created", "schedule_item", item.id, user.id, "proposal"); db.commit(); db.refresh(item)
    return {"id": item.id, "status": item.status}


@router.patch("/schedule-items/{item_id}")
def update_schedule(item_id: int, payload: ScheduleProgress, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.get(ScheduleItem, item_id)
    if item is None: raise HTTPException(404, "Schedule item not found")
    require_project_role(db, user, item.project_id, "editor")
    for name, value in payload.model_dump(exclude_unset=True).items(): setattr(item, name, value)
    item.status = "completed" if item.actual_progress == 100 else "in_progress"
    _audit(db, "schedule_actual_updated", "schedule_item", item.id, user.id, f"progress={item.actual_progress}"); db.commit()
    return {"id": item.id, "status": item.status, "actual_progress": item.actual_progress}


@router.post("/budget")
def create_budget(payload: BudgetCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor"); _check_contract(db, payload.project_id, payload.contract_id)
    data = payload.model_dump(); data["forecast_amount"] = data["forecast_amount"] if data["forecast_amount"] is not None else data["planned_amount"]
    item = BudgetLine(**data); db.add(item); db.flush(); _audit(db, "budget_proposed", "budget_line", item.id, user.id, "status=proposed"); db.commit(); return {"id": item.id, "status": item.status}


@router.post("/cash-flow")
def create_cash_flow(payload: CashFlowCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor"); _check_contract(db, payload.project_id, payload.contract_id)
    item = CashFlowEntry(**payload.model_dump()); db.add(item); db.flush(); _audit(db, "cash_flow_proposed", "cash_flow", item.id, user.id, "status=proposed"); db.commit(); return {"id": item.id, "status": item.status}


@router.post("/invoice-proposals")
def create_invoice_proposal(payload: InvoiceProposalCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    _check_contract(db, payload.project_id, payload.contract_id)
    if payload.schedule_item_id is not None:
        stage = db.get(ScheduleItem, payload.schedule_item_id)
        if stage is None or stage.project_id != payload.project_id:
            raise HTTPException(422, "Этап ГПР не принадлежит выбранному проекту")
        baseline = db.get(ScheduleBaseline, stage.baseline_id)
        if payload.contract_id and baseline and baseline.contract_id not in {None, payload.contract_id}:
            raise HTTPException(422, "Этап ГПР связан с другим договором")
    if payload.budget_line_id is not None:
        budget = db.get(BudgetLine, payload.budget_line_id)
        if budget is None or budget.project_id != payload.project_id:
            raise HTTPException(422, "Строка бюджета не принадлежит выбранному проекту")
        if payload.contract_id and budget.contract_id not in {None, payload.contract_id}:
            raise HTTPException(422, "Строка бюджета связана с другим договором")
    if payload.source_document_id is not None:
        document = db.get(Document, payload.source_document_id)
        if document is None or document.project_id != payload.project_id:
            raise HTTPException(422, "Счёт не принадлежит выбранному проекту")
    item = CashFlowEntry(**payload.model_dump(), status="proposed")
    db.add(item); db.flush()
    _audit(db, "invoice_cash_flow_proposed", "cash_flow", item.id, user.id,
           f"contract={payload.contract_id}; schedule={payload.schedule_item_id}; budget={payload.budget_line_id}; document={payload.source_document_id}")
    db.commit()
    return {"id": item.id, "status": item.status, "requires_payment_confirmation": True}


@router.post("/cash-flow/{item_id}/confirm-payment")
def confirm_payment(item_id: int, payload: PaymentConfirmation, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.get(CashFlowEntry, item_id)
    if item is None:
        raise HTTPException(404, "Запись ДДС не найдена")
    require_project_role(db, user, item.project_id, "manager")
    paid_status = "received" if item.direction == "inflow" else "paid"
    if item.status in {"paid", "received"}:
        return {"id": item.id, "status": item.status, "already_confirmed": True}
    if item.status not in {"proposed", "approved"}:
        raise HTTPException(409, "Эту запись нельзя подтвердить как оплаченную")
    actual_amount = payload.actual_amount or item.planned_amount
    item.actual_amount = actual_amount
    item.actual_date = payload.actual_date or date.today()
    item.status = paid_status
    if item.budget_line_id:
        budget = db.get(BudgetLine, item.budget_line_id)
        if budget is not None:
            budget.actual_amount = (budget.actual_amount or Decimal("0")) + actual_amount
    _audit(db, "cash_flow_payment_confirmed", "cash_flow", item.id, user.id,
           f"status={paid_status}; amount={actual_amount}; date={item.actual_date}; budget={item.budget_line_id}")
    db.commit()
    return {"id": item.id, "status": item.status, "actual_amount": item.actual_amount,
            "actual_date": item.actual_date, "already_confirmed": False}


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
    item = db.get(model, item_id)
    if item is None: raise HTTPException(404, "Item not found")
    require_project_role(db, user, item.project_id, "manager")
    allowed = {"budget": {"approved", "active", "closed", "rejected"}, "cash-flow": {"approved", "paid", "received", "cancelled"},
               "procurement": {"request", "ordered", "delivered", "accepted", "cancelled"}, "acts": {"approved", "signed", "paid", "rejected"},
               "baselines": {"approved", "superseded"}}[kind]
    if payload.status not in allowed: raise HTTPException(422, "Недопустимый статус")
    item.status = payload.status
    if hasattr(item, "approved_at") and payload.status == "approved": item.approved_at = datetime.now(timezone.utc)
    if payload.actual_amount is not None and hasattr(item, "actual_amount"): item.actual_amount = payload.actual_amount
    if payload.actual_date is not None:
        if hasattr(item, "actual_date"): item.actual_date = payload.actual_date
        if hasattr(item, "actual_delivery"): item.actual_delivery = payload.actual_date
    _audit(db, f"{kind}_status_updated", kind, item.id, user.id, f"status={payload.status}"); db.commit()
    return {"id": item.id, "status": item.status}
