from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.audit_log import AuditLog
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
        "cash_flow": [{"id": x.id, "contract_id": x.contract_id, "direction": x.direction, "title": x.title,
                       "planned_date": x.planned_date, "actual_date": x.actual_date, "planned_amount": x.planned_amount,
                       "actual_amount": x.actual_amount, "counterparty": x.counterparty, "status": x.status} for x in cash],
        "procurement": [{"id": x.id, "contract_id": x.contract_id, "title": x.title, "supplier": x.supplier,
                         "stage": x.stage, "planned_delivery": x.planned_delivery, "actual_delivery": x.actual_delivery,
                         "planned_amount": x.planned_amount, "actual_amount": x.actual_amount} for x in procurement],
        "acts": [{"id": x.id, "contract_id": x.contract_id, "document_id": x.document_id, "number": x.number,
                  "title": x.title, "act_date": x.act_date, "amount": x.amount, "status": x.status} for x in acts],
    }


@router.post("/baselines")
def create_baseline(payload: BaselineCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "manager")
    _check_contract(db, payload.project_id, payload.contract_id)
    version = (db.scalar(select(func.max(ScheduleBaseline.version)).where(ScheduleBaseline.project_id == payload.project_id)) or 0) + 1
    item = ScheduleBaseline(project_id=payload.project_id, created_by_user_id=user.id, name=payload.name.strip(), version=version, note=payload.note)
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
