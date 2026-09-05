import base64
import binascii
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import re

from fastapi import APIRouter, Depends, HTTPException, Response
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
from app.structured_import import parse_structured_rows
from app.schedule_import.mpp import MppImportUnavailable, read_mpp_bytes
from app.schedule_import.mspdi import build_mspdi

router = APIRouter(prefix="/execution", tags=["execution-finance"])


class BaselineCreate(BaseModel):
    project_id: int
    contract_id: int | None = None
    name: str = Field(min_length=2, max_length=500)
    note: str | None = Field(default=None, max_length=5000)


class BaselineClone(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=500)
    note: str | None = Field(default=None, max_length=5000)


class ScheduleItemCreate(BaseModel):
    baseline_id: int
    title: str = Field(min_length=2, max_length=500)
    sort_order: int | None = Field(default=None, ge=0)
    parent_id: int | None = None
    duration_days: int = Field(default=1, ge=0, le=10000)
    is_milestone: bool = False
    predecessor_ids: str | None = Field(default=None, max_length=2000)
    constraint_type: str | None = Field(default=None, pattern="^(asap|alap|mso|mfo|snet|snlt|fnet|fnlt)$")
    constraint_date: date | None = None
    planned_start: date | None = None
    planned_finish: date | None = None
    planned_progress: float = Field(default=0, ge=0, le=100)


class ScheduleProgress(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=500)
    sort_order: int | None = Field(default=None, ge=0)
    parent_id: int | None = None
    duration_days: int | None = Field(default=None, ge=0, le=10000)
    is_milestone: bool | None = None
    predecessor_ids: str | None = Field(default=None, max_length=2000)
    constraint_type: str | None = Field(default=None, pattern="^(asap|alap|mso|mfo|snet|snlt|fnet|fnlt)$")
    constraint_date: date | None = None
    planned_start: date | None = None
    planned_finish: date | None = None
    planned_progress: float | None = Field(default=None, ge=0, le=100)
    actual_progress: float | None = Field(default=None, ge=0, le=100)
    actual_start: date | None = None
    actual_finish: date | None = None


class ScheduleBulkUpdate(BaseModel):
    baseline_id: int
    item_ids: list[int] = Field(min_length=1, max_length=500)
    planned_progress: float | None = Field(default=None, ge=0, le=100)
    actual_progress: float | None = Field(default=None, ge=0, le=100)
    status: str | None = Field(default=None, pattern="^(planned|in_progress|completed|blocked|cancelled)$")
    delta_days: int | None = Field(default=None, ge=-36500, le=36500)


class MppImportRequest(BaseModel):
    project_id: int
    contract_id: int | None = None
    filename: str = Field(min_length=5, max_length=500)
    content_base64: str
    baseline_id: int | None = None


MAX_MPP_BYTES = 25 * 1024 * 1024


def _decode_mpp(payload: MppImportRequest) -> tuple[bytes, str]:
    if not payload.filename.casefold().endswith(".mpp"):
        raise HTTPException(422, "Выберите файл Microsoft Project с расширением .mpp")
    try:
        data = base64.b64decode(payload.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(422, "Некорректное содержимое MPP-файла") from exc
    if not data:
        raise HTTPException(422, "MPP-файл пуст")
    if len(data) > MAX_MPP_BYTES:
        raise HTTPException(413, "MPP-файл больше 25 МБ")
    return data, hashlib.sha256(data).hexdigest()


def _mpp_tasks(data: bytes):
    try:
        return read_mpp_bytes(data)
    except MppImportUnavailable as exc:
        raise HTTPException(503, "Импорт MPP временно недоступен: на сервере требуется MPXJ и Java 17") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _mpp_lag_suffix(value: object) -> str:
    """Translate MPXJ duration text into the schedule editor's day syntax."""
    match = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)d\s*", str(value or ""), re.IGNORECASE)
    if not match:
        return ""
    days = round(float(match.group(1)))
    return f"{days:+d}d" if days else ""


def _mpp_uid(item: ScheduleItem) -> str | None:
    match = re.fullmatch(r"MPP task UID (.+)", item.source_excerpt or "")
    return match.group(1) if match else None


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
    object_name: str | None = Field(default=None, max_length=300)
    category: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=5000)


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


class StructuredImportRequest(BaseModel):
    project_id: int
    contract_id: int | None = None
    kind: str = Field(pattern="^(schedule|budget|cash-flow)$")
    baseline_id: int | None = None
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


def _audit(db: Session, action: str, kind: str, entity_id: int, user_id: int, details: str):
    db.add(AuditLog(action=action, entity_type=kind, entity_id=entity_id, details=f"user={user_id}; {details}"))


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


def _schedule_predecessor_ids(value: str | None) -> list[int]:
    if not value:
        return []
    result: list[int] = []
    for token in re.split(r"[,;]+", value):
        match = re.match(r"\s*(\d+)(?:\s*(?:FS|SS|FF|SF))?(?:\s*[+-]\s*\d+\s*[dд])?\s*$", token, re.IGNORECASE)
        if not match:
            raise HTTPException(422, f"Не распознана связь: {token.strip()}")
        result.append(int(match.group(1)))
    return result


def _schedule_predecessors(value: str | None) -> list[tuple[int, str, int]]:
    """Return predecessor id, link type and lag in calendar days."""
    if not value:
        return []
    result: list[tuple[int, str, int]] = []
    for token in re.split(r"[,;]+", value):
        match = re.match(r"\s*(\d+)(?:\s*(FS|SS|FF|SF))?(?:\s*([+-])\s*(\d+)\s*[dд])?\s*$", token, re.IGNORECASE)
        if not match:
            raise HTTPException(422, f"Не распознана связь: {token.strip()}")
        lag = int(match.group(4) or 0) * (-1 if match.group(3) == "-" else 1)
        result.append((int(match.group(1)), (match.group(2) or "FS").upper(), lag))
    return result


def _remap_schedule_predecessors(value: str | None, item_id_map: dict[int, int]) -> str | None:
    """Replace task ids while preserving relation types, lags and formatting."""
    if not value:
        return value
    predecessor_ids = _schedule_predecessor_ids(value)
    missing = sorted(set(predecessor_ids).difference(item_id_map))
    if missing:
        raise ValueError(f"Cannot remap schedule predecessor ids: {missing}")

    def replace(match: re.Match[str]) -> str:
        return f"{match.group(1)}{match.group(2)}{item_id_map[int(match.group(3))]}"

    return re.sub(r"(^|[,;])(\s*)(\d+)", replace, value)


def _finish_from_start(start: date, duration_days: int) -> date:
    return start + timedelta(days=max(0, duration_days - 1))


def _start_from_finish(finish: date, duration_days: int) -> date:
    return finish - timedelta(days=max(0, duration_days - 1))


def _auto_schedule_baseline(db: Session, baseline_id: int) -> list[int]:
    """Forward-schedule dependent tasks after a plan edit.

    The current version deliberately uses calendar days. Project calendars and
    resource leveling are separate planner capabilities and must not be silently
    approximated here.
    """
    tasks = list(db.scalars(select(ScheduleItem).where(ScheduleItem.baseline_id == baseline_id)))
    by_id = {task.id: task for task in tasks}
    dependencies = {task.id: _schedule_predecessors(task.predecessor_ids) for task in tasks}
    pending = set(by_id)
    ordered: list[ScheduleItem] = []
    while pending:
        ready = sorted(
            (task_id for task_id in pending if all(pred_id not in pending for pred_id, _, _ in dependencies[task_id])),
            key=lambda task_id: (by_id[task_id].sort_order or 0, task_id),
        )
        if not ready:
            raise HTTPException(422, "Зависимости образуют цикл")
        for task_id in ready:
            pending.remove(task_id)
            ordered.append(by_id[task_id])

    changed: list[int] = []
    for task in ordered:
        duration = 0 if task.is_milestone else max(1, task.duration_days or 1)
        start_candidates: list[date] = []
        finish_candidates: list[date] = []
        for predecessor_id, link_type, lag in dependencies[task.id]:
            predecessor = by_id[predecessor_id]
            pred_start = predecessor.planned_start or predecessor.planned_finish
            pred_finish = predecessor.planned_finish or predecessor.planned_start
            if not pred_start or not pred_finish:
                continue
            if link_type == "FS":
                start_candidates.append(pred_finish + timedelta(days=1 + lag))
            elif link_type == "SS":
                start_candidates.append(pred_start + timedelta(days=lag))
            elif link_type == "FF":
                finish_candidates.append(pred_finish + timedelta(days=lag))
            else:  # SF
                finish_candidates.append(pred_start + timedelta(days=lag))

        start = max(start_candidates) if start_candidates else task.planned_start
        finish = max(finish_candidates) if finish_candidates else None
        if finish is not None:
            start_from_finish = _start_from_finish(finish, duration)
            start = max(start, start_from_finish) if start else start_from_finish

        constraint = task.constraint_type or "asap"
        constraint_date = task.constraint_date
        if constraint_date and constraint in {"mso", "snet"}:
            start = constraint_date if constraint == "mso" else max(start or constraint_date, constraint_date)
        if constraint_date and constraint in {"mfo", "fnet"}:
            constrained_start = _start_from_finish(constraint_date, duration)
            start = constrained_start if constraint == "mfo" else max(start or constrained_start, constrained_start)
        if start is None:
            continue
        calculated_finish = start if task.is_milestone else _finish_from_start(start, duration)
        if task.planned_start != start or task.planned_finish != calculated_finish:
            task.planned_start = start
            task.planned_finish = calculated_finish
            task.duration_days = duration
            changed.append(task.id)
    return changed


def _validate_schedule_predecessors(db: Session, baseline_id: int, item_id: int | None, value: str | None) -> None:
    predecessor_ids = _schedule_predecessor_ids(value)
    if len(predecessor_ids) != len(set(predecessor_ids)):
        raise HTTPException(422, "Предшественники не должны повторяться")
    tasks = list(db.scalars(select(ScheduleItem).where(ScheduleItem.baseline_id == baseline_id)))
    by_id = {task.id: task for task in tasks}
    if any(predecessor_id not in by_id for predecessor_id in predecessor_ids):
        raise HTTPException(422, "Все предшественники должны принадлежать этой версии ГПР")
    if item_id is None:
        return
    if item_id in predecessor_ids:
        raise HTTPException(422, "Задача не может зависеть от самой себя")
    for predecessor_id in predecessor_ids:
        stack = [predecessor_id]
        visited: set[int] = set()
        while stack:
            current = stack.pop()
            if current == item_id:
                raise HTTPException(422, "Зависимости образуют цикл")
            if current in visited:
                continue
            visited.add(current)
            if current not in by_id:
                raise HTTPException(422, "В существующих связях найден отсутствующий предшественник")
            stack.extend(_schedule_predecessor_ids(by_id[current].predecessor_ids))


@router.get("/overview")
def overview(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    baselines = list(db.scalars(select(ScheduleBaseline).where(ScheduleBaseline.project_id == project_id).order_by(ScheduleBaseline.version.desc())))
    schedule = list(db.scalars(select(ScheduleItem).where(ScheduleItem.project_id == project_id).order_by(ScheduleItem.baseline_id, ScheduleItem.sort_order, ScheduleItem.id)))
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
        "summary": {"budget_planned": planned, "budget_committed": sum((x.committed_amount for x in confirmed_budget), Decimal("0")),
                    "budget_actual": actual, "budget_forecast": forecast,
                    "budget_variance": forecast - planned, "cash_balance_forecast": balance,
                    "cash_gap": minimum, "cash_gap_date": gap_date, "delayed_schedule": len(delayed),
                    "late_procurement": len(late_procurement), "acts_pending": len([x for x in acts if x.status in {"proposed", "approved"}]),
                    "pending_payments": len([x for x in cash if x.direction == "outflow" and x.status == "approved"]),
                    "unlinked_invoices": len([x for x in cash if x.source_document_id and (not x.contract_id or not x.schedule_item_id or not x.budget_line_id)])},
        "baselines": [{"id": x.id, "contract_id": x.contract_id, "name": x.name, "version": x.version, "status": x.status, "note": x.note,
                       "source_format": x.source_format} for x in baselines],
        "schedule": [{"id": x.id, "baseline_id": x.baseline_id, "title": x.title, "sort_order": x.sort_order,
                      "parent_id": x.parent_id, "duration_days": x.duration_days, "is_milestone": x.is_milestone,
                      "predecessor_ids": x.predecessor_ids, "constraint_type": x.constraint_type, "constraint_date": x.constraint_date,
                      "planned_start": x.planned_start,
                      "planned_finish": x.planned_finish, "actual_start": x.actual_start, "actual_finish": x.actual_finish,
                      "planned_progress": x.planned_progress, "actual_progress": x.actual_progress, "status": x.status} for x in schedule],
        "budget": [{"id": x.id, "contract_id": x.contract_id, "category": x.category, "description": x.description,
                    "planned_amount": x.planned_amount, "committed_amount": x.committed_amount, "actual_amount": x.actual_amount,
                    "forecast_amount": x.forecast_amount, "currency": x.currency, "status": x.status} for x in budget],
        "cash_flow": [{"id": x.id, "contract_id": x.contract_id, "schedule_item_id": x.schedule_item_id,
                       "budget_line_id": x.budget_line_id, "source_document_id": x.source_document_id,
                       "direction": x.direction, "title": x.title,
                       "planned_date": x.planned_date, "actual_date": x.actual_date, "planned_amount": x.planned_amount,
                       "actual_amount": x.actual_amount, "counterparty": x.counterparty,
                       "object_name": x.object_name, "category": x.category, "note": x.note,
                       "status": x.status} for x in cash],
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


def _document_content(db: Session, project_id: int, document_id: int) -> tuple[Document, str]:
    document = db.scalar(select(Document).where(Document.id == document_id, Document.project_id == project_id))
    if document is None:
        raise HTTPException(404, "Документ не найден в выбранном проекте")
    version = db.scalar(select(DocumentVersion).where(
        DocumentVersion.document_id == document.id,
    ).order_by(DocumentVersion.version_number.desc()))
    content = version.content if version and version.content else ""
    if not content.strip():
        raise HTTPException(409, "У документа ещё нет извлечённого табличного текста")
    return document, content


def _import_date(value: str | None) -> date | None:
    """Convert the normalized parser value before assigning it to SQLAlchemy Date."""
    return date.fromisoformat(value) if value else None


@router.get("/documents/{document_id}/structured-preview")
def structured_preview(document_id: int, project_id: int, kind: str,
                       db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    if kind not in {"schedule", "budget", "cash-flow"}:
        raise HTTPException(422, "Поддерживаются ГПР, бюджет и ДДС")
    document, content = _document_content(db, project_id, document_id)
    preview = parse_structured_rows(content, kind, source_name=document.name)
    return {"document_id": document.id, "name": document.name, "kind": kind, **preview,
            "requires_confirmation": True, "originals_changed": False}


@router.post("/documents/{document_id}/structured-import")
def structured_import(document_id: int, payload: StructuredImportRequest,
                      db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    _check_contract(db, payload.project_id, payload.contract_id)
    document, content = _document_content(db, payload.project_id, document_id)
    preview = parse_structured_rows(content, payload.kind, source_name=document.name)
    if len(payload.source_rows) != len(set(payload.source_rows)):
        raise HTTPException(422, "Строки источника не должны повторяться")
    selected = {row["source_row"]: row for row in preview["rows"] if row["source_row"] in set(payload.source_rows)}
    if set(payload.source_rows) - set(selected):
        raise HTTPException(422, "Выбраны отсутствующие строки источника")
    if any(not row["importable"] for row in selected.values()):
        raise HTTPException(422, "Сначала исправьте строки с ошибками")

    baseline = None
    if payload.kind == "schedule":
        baseline = db.get(ScheduleBaseline, payload.baseline_id) if payload.baseline_id else None
        if baseline is None or baseline.project_id != payload.project_id:
            raise HTTPException(422, "Для импорта ГПР выберите черновик baseline проекта")
        if baseline.status != "draft":
            raise HTTPException(409, "Утверждённый baseline неизменяем")
        if payload.contract_id and baseline.contract_id not in {None, payload.contract_id}:
            raise HTTPException(422, "Baseline связан с другим договором")

    created = []
    for source_row in payload.source_rows:
        row = selected[source_row]
        source_name = f"{document.name}, {row['source_coordinate']}"
        if payload.kind == "schedule":
            item = ScheduleItem(
                project_id=payload.project_id, baseline_id=baseline.id, title=row["title"],
                sort_order=len(created),
                planned_start=_import_date(row["planned_start"]),
                planned_finish=_import_date(row["planned_finish"]),
                duration_days=max(0, ((_import_date(row["planned_finish"]) - _import_date(row["planned_start"])).days + 1) if row["planned_start"] and row["planned_finish"] else 1),
                planned_progress=min(100, max(0, row["progress"])),
                source_name=source_name, source_excerpt=row["excerpt"], status="planned",
            )
        elif payload.kind == "budget":
            amount = Decimal(row["amount"])
            item = BudgetLine(
                project_id=payload.project_id, contract_id=payload.contract_id,
                category=row["category"], description=row["title"], planned_amount=amount,
                forecast_amount=amount, status="proposed", source_name=source_name,
                source_excerpt=row["excerpt"],
            )
        else:
            item = CashFlowEntry(
                project_id=payload.project_id, contract_id=payload.contract_id,
                source_document_id=document.id, direction=row["direction"] or payload.direction,
                title=row["title"], planned_date=_import_date(row["planned_date"]),
                planned_amount=Decimal(row["amount"]), counterparty=row["counterparty"],
                object_name=row.get("object_name"), category=row.get("category"), note=row.get("note"),
                status="proposed", source_name=source_name, source_excerpt=row["excerpt"],
            )
        db.add(item)
        db.flush()
        created.append(item.id)
    db.add(AuditLog(
        action="structured_document_imported", entity_type="document", entity_id=document.id,
        details=(f"user={user.id}; kind={payload.kind}; contract={payload.contract_id}; "
                 f"rows={','.join(map(str, payload.source_rows))}; created={','.join(map(str, created))}; originals_changed=false"),
    ))
    db.commit()
    return {"document_id": document.id, "kind": payload.kind, "created_ids": created,
            "created": len(created), "status": "proposed", "originals_changed": False}


@router.post("/baselines")
def create_baseline(payload: BaselineCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "manager")
    _check_contract(db, payload.project_id, payload.contract_id)
    version = (db.scalar(select(func.max(ScheduleBaseline.version)).where(ScheduleBaseline.project_id == payload.project_id)) or 0) + 1
    item = ScheduleBaseline(project_id=payload.project_id, contract_id=payload.contract_id,
                            created_by_user_id=user.id, name=payload.name.strip(), version=version, note=payload.note)
    db.add(item); db.flush(); _audit(db, "baseline_created", "schedule_baseline", item.id, user.id, f"version={version}"); db.commit(); db.refresh(item)
    return {"id": item.id, "version": item.version, "status": item.status}


@router.post("/mpp/preview")
def preview_mpp(payload: MppImportRequest, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "viewer")
    _check_contract(db, payload.project_id, payload.contract_id)
    data, digest = _decode_mpp(payload)
    tasks = _mpp_tasks(data)
    dated = [row for row in tasks if row.planned_start or row.planned_finish]
    existing_by_uid: dict[str, ScheduleItem] = {}
    if payload.baseline_id:
        baseline = db.get(ScheduleBaseline, payload.baseline_id)
        if baseline is None or baseline.project_id != payload.project_id:
            raise HTTPException(404, "Версия ГПР не найдена")
        existing_rows = list(db.scalars(select(ScheduleItem).where(ScheduleItem.baseline_id == baseline.id)))
        existing_by_uid = {uid: item for item in existing_rows if (uid := _mpp_uid(item))}
    incoming_uids = {row.external_uid for row in tasks}
    changed = sum(
        1 for row in tasks if (old := existing_by_uid.get(row.external_uid)) and (
            old.title != row.title[:500] or old.planned_start != row.planned_start or old.planned_finish != row.planned_finish
        )
    )
    return {
        "filename": payload.filename, "sha256": digest, "task_count": len(tasks),
        "relation_count": sum(len(row.predecessors) for row in tasks),
        "milestone_count": sum(row.is_milestone for row in tasks),
        "summary_count": sum(row.is_summary for row in tasks),
        "critical_count": sum(row.is_critical for row in tasks),
        "planned_start": min((row.planned_start for row in dated if row.planned_start), default=None),
        "planned_finish": max((row.planned_finish for row in dated if row.planned_finish), default=None),
        "added_count": sum(row.external_uid not in existing_by_uid for row in tasks),
        "changed_count": changed,
        "removed_count": sum(uid not in incoming_uids for uid in existing_by_uid),
        "preserved_actual_count": sum(row.external_uid in existing_by_uid for row in tasks),
    }


@router.post("/mpp/import")
def import_mpp(payload: MppImportRequest, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    _check_contract(db, payload.project_id, payload.contract_id)
    data, digest = _decode_mpp(payload)
    existing = db.scalar(select(ScheduleBaseline).where(
        ScheduleBaseline.project_id == payload.project_id,
        ScheduleBaseline.contract_id == payload.contract_id,
        ScheduleBaseline.source_sha256 == digest,
    ))
    if existing:
        count = db.scalar(select(func.count(ScheduleItem.id)).where(ScheduleItem.baseline_id == existing.id)) or 0
        return {"baseline_id": existing.id, "version": existing.version, "created": count, "duplicate": True}

    tasks = _mpp_tasks(data)
    source_baseline = None
    previous_by_uid: dict[str, ScheduleItem] = {}
    if payload.baseline_id:
        source_baseline = db.get(ScheduleBaseline, payload.baseline_id)
        if source_baseline is None or source_baseline.project_id != payload.project_id:
            raise HTTPException(404, "Версия ГПР не найдена")
        if source_baseline.contract_id != payload.contract_id:
            raise HTTPException(409, "Договор выбранной версии ГПР не совпадает")
        previous_rows = list(db.scalars(select(ScheduleItem).where(ScheduleItem.baseline_id == source_baseline.id)))
        previous_by_uid = {uid: item for item in previous_rows if (uid := _mpp_uid(item))}
    version = (db.scalar(select(func.max(ScheduleBaseline.version)).where(ScheduleBaseline.project_id == payload.project_id)) or 0) + 1
    baseline = ScheduleBaseline(
        project_id=payload.project_id, contract_id=payload.contract_id, created_by_user_id=user.id,
        name=payload.filename, version=version, status="draft",
        note="Импортировано из Microsoft Project; требуется проверка и утверждение",
        source_format="mpp", source_sha256=digest,
    )
    db.add(baseline)
    db.flush()
    imported: dict[str, ScheduleItem] = {}
    imported_rows = []
    for order, row in enumerate(tasks):
        previous = previous_by_uid.get(row.external_uid)
        duration = max(0, (row.planned_finish - row.planned_start).days + 1) if row.planned_start and row.planned_finish else 0
        item = ScheduleItem(
            project_id=payload.project_id, baseline_id=baseline.id, title=row.title[:500], sort_order=order,
            duration_days=duration, is_milestone=row.is_milestone, planned_start=row.planned_start,
            planned_finish=row.planned_finish, planned_progress=row.progress,
            actual_progress=previous.actual_progress if previous else row.progress,
            actual_start=previous.actual_start if previous else None, actual_finish=previous.actual_finish if previous else None,
            status=previous.status if previous else "completed" if row.progress >= 100 else "in_progress" if row.progress > 0 else "planned",
            source_name=payload.filename, source_excerpt=f"MPP task UID {row.external_uid}",
        )
        db.add(item); imported[row.external_uid] = item; imported_rows.append((row, item))
    db.flush()
    for row, item in imported_rows:
        parent = imported.get(row.parent_external_uid or "")
        item.parent_id = parent.id if parent else None
        links = []
        for relation in row.predecessors:
            predecessor = imported.get(str(relation.get("external_uid") or ""))
            if predecessor:
                link_type = str(relation.get("type") or "FS")
                links.append(f"{predecessor.id}{link_type}{_mpp_lag_suffix(relation.get('lag'))}")
        item.predecessor_ids = ",".join(links) or None
    _audit(db, "mpp_schedule_imported", "schedule_baseline", baseline.id, user.id,
           f"tasks={len(tasks)}; sha256={digest[:12]}")
    db.commit()
    return {"baseline_id": baseline.id, "version": baseline.version, "created": len(tasks), "duplicate": False,
            "preserved_actual": sum(row.external_uid in previous_by_uid for row in tasks)}


@router.get("/mpp/export/{baseline_id}")
def export_mpp_xml(baseline_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    baseline = db.get(ScheduleBaseline, baseline_id)
    if baseline is None:
        raise HTTPException(404, "Версия ГПР не найдена")
    require_project_role(db, user, baseline.project_id, "viewer")
    rows = list(db.scalars(select(ScheduleItem).where(
        ScheduleItem.baseline_id == baseline.id,
    ).order_by(ScheduleItem.sort_order, ScheduleItem.id)))
    content = build_mspdi(baseline.name, [{
        "id": item.id, "parent_id": item.parent_id, "title": item.title, "is_milestone": item.is_milestone,
        "planned_start": item.planned_start, "planned_finish": item.planned_finish,
        "duration_days": item.duration_days, "actual_progress": item.actual_progress,
        "predecessor_ids": item.predecessor_ids,
    } for item in rows])
    return Response(content, media_type="application/xml", headers={
        "Content-Disposition": f'attachment; filename="schedule-v{baseline.version}.xml"',
    })


@router.post("/baselines/{baseline_id}/clone")
def clone_baseline(baseline_id: int, payload: BaselineClone, db: Session = Depends(get_db), user: User = Depends(require_user)):
    source = db.get(ScheduleBaseline, baseline_id)
    if source is None:
        raise HTTPException(404, "Baseline not found")
    require_project_role(db, user, source.project_id, "manager")
    version = (db.scalar(select(func.max(ScheduleBaseline.version)).where(
        ScheduleBaseline.project_id == source.project_id,
    )) or 0) + 1
    name = payload.name.strip() if payload.name is not None else f"{source.name} — версия {version}"
    if len(name) < 2:
        raise HTTPException(422, "Название версии должно содержать не менее двух символов")
    note = payload.note if "note" in payload.model_fields_set else source.note
    clone = ScheduleBaseline(
        project_id=source.project_id,
        contract_id=source.contract_id,
        created_by_user_id=user.id,
        name=name,
        version=version,
        status="draft",
        note=note,
    )
    db.add(clone)
    db.flush()

    source_items = list(db.scalars(select(ScheduleItem).where(
        ScheduleItem.baseline_id == source.id,
    ).order_by(ScheduleItem.sort_order, ScheduleItem.id)))
    cloned_pairs: list[tuple[ScheduleItem, ScheduleItem]] = []
    for source_item in source_items:
        cloned_item = ScheduleItem(
            project_id=source_item.project_id,
            baseline_id=clone.id,
            title=source_item.title,
            sort_order=source_item.sort_order,
            parent_id=None,
            duration_days=source_item.duration_days,
            is_milestone=source_item.is_milestone,
            predecessor_ids=None,
            constraint_type=source_item.constraint_type,
            constraint_date=source_item.constraint_date,
            planned_start=source_item.planned_start,
            planned_finish=source_item.planned_finish,
            actual_start=source_item.actual_start,
            actual_finish=source_item.actual_finish,
            planned_progress=source_item.planned_progress,
            actual_progress=source_item.actual_progress,
            status=source_item.status,
            source_name=source_item.source_name,
            source_excerpt=source_item.source_excerpt,
        )
        db.add(cloned_item)
        cloned_pairs.append((source_item, cloned_item))
    db.flush()

    item_id_map = {source_item.id: cloned_item.id for source_item, cloned_item in cloned_pairs}
    for source_item, cloned_item in cloned_pairs:
        if source_item.parent_id is not None:
            if source_item.parent_id not in item_id_map:
                raise HTTPException(422, "Родительская задача исходной версии отсутствует")
            cloned_item.parent_id = item_id_map[source_item.parent_id]
        try:
            cloned_item.predecessor_ids = _remap_schedule_predecessors(source_item.predecessor_ids, item_id_map)
        except ValueError as exc:
            raise HTTPException(422, "Предшественник исходной версии отсутствует") from exc

    _audit(
        db, "baseline_cloned", "schedule_baseline", clone.id, user.id,
        f"source_baseline={source.id}; version={version}; items={len(cloned_pairs)}",
    )
    db.commit()
    return {
        "id": clone.id,
        "source_baseline_id": source.id,
        "version": clone.version,
        "status": clone.status,
        "cloned_item_ids": [cloned_item.id for _, cloned_item in cloned_pairs],
        "item_id_map": item_id_map,
    }


@router.post("/schedule-items")
def create_schedule_item(payload: ScheduleItemCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    baseline = db.get(ScheduleBaseline, payload.baseline_id)
    if baseline is None: raise HTTPException(404, "Baseline not found")
    require_project_role(db, user, baseline.project_id, "editor")
    if baseline.status == "approved": raise HTTPException(409, "Утверждённый baseline неизменяем; создайте новую версию")
    data = payload.model_dump()
    if data["parent_id"] is not None:
        parent = db.get(ScheduleItem, data["parent_id"])
        if parent is None or parent.baseline_id != baseline.id:
            raise HTTPException(422, "Родительская задача должна принадлежать этой версии ГПР")
    _validate_schedule_predecessors(db, baseline.id, None, data["predecessor_ids"])
    if data["sort_order"] is None:
        data["sort_order"] = (db.scalar(select(func.max(ScheduleItem.sort_order)).where(ScheduleItem.baseline_id == baseline.id)) or 0) + 1
    if data["is_milestone"]:
        data["duration_days"] = 0
        data["planned_finish"] = data["planned_start"] or data["planned_finish"]
    item = ScheduleItem(project_id=baseline.project_id, **data)
    db.add(item); db.flush()
    changed = _auto_schedule_baseline(db, baseline.id)
    _audit(db, "schedule_item_created", "schedule_item", item.id, user.id, f"proposal; auto_scheduled={','.join(map(str, changed))}")
    db.commit(); db.refresh(item)
    return {"id": item.id, "status": item.status, "auto_scheduled_ids": changed}


@router.patch("/schedule-items/bulk")
def bulk_update_schedule(payload: ScheduleBulkUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    baseline = db.get(ScheduleBaseline, payload.baseline_id)
    if baseline is None:
        raise HTTPException(404, "Baseline not found")
    require_project_role(db, user, baseline.project_id, "editor")
    if len(payload.item_ids) != len(set(payload.item_ids)):
        raise HTTPException(422, "Задачи для массового изменения не должны повторяться")

    update_fields = payload.model_dump(
        include={"planned_progress", "actual_progress", "status"},
        exclude_unset=True,
        exclude_none=True,
    )
    delta_days = payload.delta_days if "delta_days" in payload.model_fields_set else None
    if delta_days is not None and update_fields:
        raise HTTPException(422, "Сдвиг дат и изменение состояния выполняются отдельными операциями")
    if delta_days == 0 or (delta_days is None and not update_fields):
        raise HTTPException(422, "Не задано массовое изменение")
    if baseline.status == "approved" and (delta_days is not None or "planned_progress" in update_fields):
        raise HTTPException(409, "Утверждённый baseline неизменяем; создайте новую версию")

    items = list(db.scalars(select(ScheduleItem).where(ScheduleItem.id.in_(payload.item_ids))))
    by_id = {item.id: item for item in items}
    missing_ids = sorted(set(payload.item_ids).difference(by_id))
    if missing_ids:
        raise HTTPException(404, f"Schedule items not found: {','.join(map(str, missing_ids))}")
    if any(item.baseline_id != baseline.id or item.project_id != baseline.project_id for item in items):
        raise HTTPException(422, "Все задачи должны принадлежать выбранной версии ГПР")

    if delta_days is not None:
        delta = timedelta(days=delta_days)
        for item in items:
            if item.planned_start is not None:
                item.planned_start += delta
            if item.planned_finish is not None:
                item.planned_finish += delta
            if item.constraint_date is not None:
                item.constraint_date += delta
        auto_scheduled_ids = _auto_schedule_baseline(db, baseline.id)
    else:
        for item in items:
            for name, value in update_fields.items():
                setattr(item, name, value)
            if "actual_progress" in update_fields and "status" not in update_fields:
                item.status = "completed" if item.actual_progress == 100 else "in_progress"
        auto_scheduled_ids = []

    updated_ids = sorted(payload.item_ids)
    _audit(
        db, "schedule_items_bulk_updated", "schedule_baseline", baseline.id, user.id,
        (f"items={','.join(map(str, updated_ids))}; fields={','.join(sorted(update_fields))}; "
         f"delta_days={delta_days}; auto_scheduled={','.join(map(str, auto_scheduled_ids))}"),
    )
    db.commit()
    return {
        "baseline_id": baseline.id,
        "updated_ids": updated_ids,
        "auto_scheduled_ids": auto_scheduled_ids,
        "status": "updated",
    }


@router.patch("/schedule-items/{item_id}")
def update_schedule(item_id: int, payload: ScheduleProgress, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.get(ScheduleItem, item_id)
    if item is None: raise HTTPException(404, "Schedule item not found")
    require_project_role(db, user, item.project_id, "editor")
    data = payload.model_dump(exclude_unset=True)
    plan_fields = {"title", "sort_order", "parent_id", "duration_days", "is_milestone", "predecessor_ids", "constraint_type", "constraint_date", "planned_start", "planned_finish", "planned_progress"}
    baseline = db.get(ScheduleBaseline, item.baseline_id)
    if baseline and baseline.status == "approved" and plan_fields.intersection(data):
        raise HTTPException(409, "Утверждённый baseline неизменяем; создайте новую версию")
    if "parent_id" in data and data["parent_id"] is not None:
        parent = db.get(ScheduleItem, data["parent_id"])
        if parent is None or parent.baseline_id != item.baseline_id or parent.id == item.id:
            raise HTTPException(422, "Недопустимая родительская задача")
        cursor = parent
        seen: set[int] = set()
        while cursor and cursor.id not in seen:
            if cursor.id == item.id:
                raise HTTPException(422, "Иерархия задач образует цикл")
            seen.add(cursor.id)
            cursor = db.get(ScheduleItem, cursor.parent_id) if cursor.parent_id else None
    if "predecessor_ids" in data:
        _validate_schedule_predecessors(db, item.baseline_id, item.id, data["predecessor_ids"])
    if data.get("is_milestone"):
        data["duration_days"] = 0
        data["planned_finish"] = data.get("planned_start") or data.get("planned_finish") or item.planned_start or item.planned_finish
    for name, value in data.items(): setattr(item, name, value)
    if "actual_progress" in data:
        item.status = "completed" if item.actual_progress == 100 else "in_progress"
    schedule_fields = {"duration_days", "is_milestone", "predecessor_ids", "constraint_type", "constraint_date", "planned_start", "planned_finish"}
    changed = _auto_schedule_baseline(db, item.baseline_id) if schedule_fields.intersection(data) else []
    action = "schedule_actual_updated" if "actual_progress" in data else "schedule_plan_updated"
    _audit(db, action, "schedule_item", item.id, user.id, f"fields={','.join(sorted(data))}; auto_scheduled={','.join(map(str, changed))}"); db.commit()
    return {"id": item.id, "status": item.status, "actual_progress": item.actual_progress,
            "auto_scheduled_ids": changed}


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
    if payload.direction != "outflow":
        raise HTTPException(422, "Счёт на оплату должен быть расходом ДДС")
    if payload.contract_id is None or payload.schedule_item_id is None or payload.budget_line_id is None:
        raise HTTPException(422, "Для счёта обязательны договор, этап ГПР и строка бюджета")
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
    _refresh_budget_from_cash_flow(db, item.budget_line_id)
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
    allowed = {"budget": {"approved", "active", "closed", "rejected"}, "cash-flow": {"approved", "cancelled"},
               "procurement": {"request", "ordered", "delivered", "accepted", "cancelled"}, "acts": {"approved", "signed", "paid", "rejected"},
               "baselines": {"approved", "superseded"}}[kind]
    if payload.status not in allowed: raise HTTPException(422, "Недопустимый статус")
    item.status = payload.status
    if hasattr(item, "approved_at") and payload.status == "approved": item.approved_at = datetime.now(timezone.utc)
    if payload.actual_amount is not None and hasattr(item, "actual_amount"): item.actual_amount = payload.actual_amount
    if payload.actual_date is not None:
        if hasattr(item, "actual_date"): item.actual_date = payload.actual_date
        if hasattr(item, "actual_delivery"): item.actual_delivery = payload.actual_date
    if kind == "cash-flow":
        _refresh_budget_from_cash_flow(db, item.budget_line_id)
    _audit(db, f"{kind}_status_updated", kind, item.id, user.id, f"status={payload.status}"); db.commit()
    return {"id": item.id, "status": item.status}
