from datetime import date, datetime, timezone
from decimal import Decimal
from dataclasses import asdict
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Annotated, Literal
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import AcceptanceAct, BudgetLine, CashFlowEntry, CashFlowFactHistory, ProcurementItem, ScheduleBaseline, ScheduleItem
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.user import User
from app.models.v54_pilot import Evidence, EvidenceAssessment, SourceCurrent, SourceReference, SourceVersion
from app.mvp4.finance_guards import (
    IMPLICIT_LEDGER_CURRENCY,
    blocking_currency_detail,
    exact_decimal,
    finance_decision_requirements,
)
from app.structured_import import parse_structured_rows
from app.mvp4.schedule_planner import PlannerError, ScheduleTask, parse_dependencies, plan_schedule
from app.mvp4.cash_flow_views import project_cash_flow_views, valid_period

router = APIRouter(prefix="/execution", tags=["execution-finance"])
CASH_FLOW_VIEW_ROW_LIMIT = 5000


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
    # Planner intent cannot be acknowledged until its fields are persisted.
    model_config = ConfigDict(extra="forbid")

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


class ScheduleGraphItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(strict=True, ge=1)
    duration_days: int = Field(strict=True, ge=0, le=10000)
    is_milestone: bool = Field(strict=True)
    predecessor_ids: str | None = Field(default=None, max_length=2000)
    constraint_type: str = Field(pattern="^(asap|snet|fnet|snlt|fnlt|mso|mfo)$")
    constraint_date: date | None = None
    not_before_date: date | None = None


class ScheduleGraphPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_graph_revision: int = Field(strict=True, ge=1, le=9223372036854775806)
    project_start: date
    items: list[ScheduleGraphItem] = Field(max_length=500)


_ScheduleRowId = Annotated[int, Field(strict=True, ge=1)]
_ScheduleClientRef = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]


class ScheduleRowDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    predecessor_id: _ScheduleRowId | None = None
    predecessor_ref: _ScheduleClientRef | None = None
    link_type: Literal["FS", "SS", "FF", "SF"] = "FS"
    lag_days: int = Field(default=0, strict=True, ge=-10000, le=10000)

    @model_validator(mode="after")
    def one_reference(self):
        if (self.predecessor_id is None) == (self.predecessor_ref is None):
            raise ValueError("exactly_one_predecessor_reference_required")
        return self


class ScheduleGraphRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: _ScheduleRowId | None = None
    client_ref: _ScheduleClientRef | None = None
    title: str = Field(min_length=2, max_length=500)
    duration_days: int = Field(strict=True, ge=0, le=10000)
    is_milestone: bool = Field(strict=True)
    constraint_type: Literal["asap", "snet", "fnet", "snlt", "fnlt", "mso", "mfo"]
    constraint_date: date | None = None
    not_before_date: date | None = None
    dependencies: list[ScheduleRowDependency] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def one_identity(self):
        if (self.id is None) == (self.client_ref is None):
            raise ValueError("exactly_one_row_identity_required")
        if len(self.title.strip()) < 2:
            raise ValueError("schedule_title_required")
        return self


class ScheduleGraphRowsPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_graph_revision: int = Field(strict=True, ge=1, le=9223372036854775806)
    project_start: date
    deleted_ids: list[_ScheduleRowId] = Field(default_factory=list, max_length=500)
    items: list[ScheduleGraphRow] = Field(max_length=500)


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
    planned_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    forecast_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(default="RUB", pattern="^[A-Z]{3}$")

    @field_validator("planned_amount", "forecast_amount")
    @classmethod
    def exact_money(cls, value: Decimal | None):
        return exact_decimal(value, 2, reason="money_precision") if value is not None else None


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
    planned_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    counterparty: str | None = Field(default=None, max_length=500)

    @field_validator("planned_amount")
    @classmethod
    def exact_money(cls, value: Decimal):
        return exact_decimal(value, 2, reason="money_precision")


class InvoiceProposalCreate(CashFlowCreate):
    pass


class PaymentConfirmation(BaseModel):
    expected_record_version: int = Field(default=1, ge=1)
    actual_amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    actual_date: date | None = None

    @field_validator("actual_amount")
    @classmethod
    def exact_money(cls, value: Decimal | None):
        return exact_decimal(value, 2, reason="money_precision") if value is not None else None


class PaymentCorrection(BaseModel):
    expected_record_version: int = Field(default=1, ge=1)
    expected_actual_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    expected_actual_date: date
    actual_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    actual_date: date
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("expected_actual_amount", "actual_amount")
    @classmethod
    def exact_money(cls, value: Decimal):
        return exact_decimal(value, 2, reason="money_precision")


class ProcurementCreate(BaseModel):
    project_id: int
    contract_id: int | None = None
    title: str = Field(min_length=2, max_length=500)
    supplier: str | None = Field(default=None, max_length=500)
    planned_delivery: date | None = None
    planned_amount: Decimal = Field(default=0, ge=0, max_digits=18, decimal_places=2)

    @field_validator("planned_amount")
    @classmethod
    def exact_money(cls, value: Decimal):
        return exact_decimal(value, 2, reason="money_precision")


class ActCreate(BaseModel):
    project_id: int
    contract_id: int | None = None
    document_id: int | None = None
    number: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=2, max_length=500)
    act_date: date | None = None
    amount: Decimal = Field(default=0, ge=0, max_digits=18, decimal_places=2)

    @field_validator("amount")
    @classmethod
    def exact_money(cls, value: Decimal):
        return exact_decimal(value, 2, reason="money_precision")


class StatusUpdate(BaseModel):
    status: str = Field(min_length=2, max_length=30)
    expected_status: str | None = Field(default=None, min_length=2, max_length=30)
    expected_graph_revision: int | None = Field(default=None, strict=True, ge=1)
    actual_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    actual_date: date | None = None

    @field_validator("actual_amount")
    @classmethod
    def exact_money(cls, value: Decimal | None):
        return exact_decimal(value, 2, reason="money_precision") if value is not None else None


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
        if budget.currency != IMPLICIT_LEDGER_CURRENCY:
            raise HTTPException(409, blocking_currency_detail(
                budget.currency, linked_currency=IMPLICIT_LEDGER_CURRENCY,
            ))

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
        .execution_options(populate_existing=True)
    )


def _locked_graph_baseline(db: Session, baseline_id: int, user: User, role: str) -> ScheduleBaseline:
    # This endpoint owns its transaction: never flush uncommitted scope/graph
    # changes before authorization or overwrite them with populate_existing.
    guarded = (Project, ProjectMember, User, ScheduleBaseline, ScheduleItem)
    if any(isinstance(row, guarded) for row in (*db.new, *db.dirty, *db.deleted)):
        raise HTTPException(409, "schedule_pending_changes")
    with db.no_autoflush:
        project_id = db.scalar(select(ScheduleBaseline.project_id).where(ScheduleBaseline.id == baseline_id))
        if project_id is None:
            raise HTTPException(404, "Baseline not found")
        require_project_role(db, user, project_id, role)
        _lock_schedule_project(db, project_id)
        project = db.scalar(select(Project).where(Project.id == project_id).with_for_update()
                            .execution_options(populate_existing=True))
        actor = db.scalar(select(User).where(User.id == user.id).with_for_update(read=True)
                          .execution_options(populate_existing=True))
        if project is None or project.archived_at is not None or actor is None:
            raise HTTPException(403, "Insufficient project access")
        list(db.scalars(select(ProjectMember).where(ProjectMember.project_id == project_id,
                            ProjectMember.user_id == actor.id).with_for_update(read=True)
                            .execution_options(populate_existing=True)))
        require_project_role(db, actor, project_id, role)
        baseline = db.scalar(select(ScheduleBaseline).where(ScheduleBaseline.id == baseline_id)
                             .with_for_update().execution_options(populate_existing=True))
        if baseline is None or baseline.project_id != project_id:
            raise HTTPException(409, "schedule_scope_changed")
        return baseline


def _graph_rows(db: Session, baseline: ScheduleBaseline) -> list[ScheduleItem]:
    rows = list(db.scalars(select(ScheduleItem).where(ScheduleItem.baseline_id == baseline.id)
                          .order_by(ScheduleItem.id).with_for_update()
                          .execution_options(populate_existing=True)))
    if any(row.project_id != baseline.project_id for row in rows):
        raise HTTPException(409, "schedule_scope_changed")
    return rows


def _canonical_dependencies(dependencies) -> str | None:
    return "; ".join(f"{link.predecessor_id}{link.link_type}"
                     + (f"{link.lag_days:+d}d" if link.lag_days else "") for link in dependencies) or None


def _calculate_graph(items, project_start):
    try:
        tasks = tuple(ScheduleTask(
            task_id=item.id, duration_days=item.duration_days, is_milestone=item.is_milestone,
            dependencies=parse_dependencies(item.predecessor_ids), planned_start=item.not_before_date,
            constraint_type=item.constraint_type, constraint_date=item.constraint_date,
        ) for item in items)
        plan = plan_schedule(tasks, project_start=project_start)
        return plan, {task.task_id: _canonical_dependencies(task.dependencies) for task in tasks}
    except PlannerError as error:
        # The graph is scoped before calculation; no raw graph/text is returned.
        raise HTTPException(422, {"code": error.code, "task_ids": error.task_ids}) from None


def _graph_response(baseline, rows, plan=None):
    fields = ("id", "title", "duration_days", "is_milestone", "predecessor_ids",
              "constraint_type", "constraint_date", "not_before_date", "planned_start", "planned_finish")
    return {"baseline_id": baseline.id, "version": baseline.version, "status": baseline.status,
            "graph_revision": baseline.graph_revision, "planning_mode": baseline.planning_mode,
            "project_start": baseline.project_start,
            "items": [{name: getattr(row, name) for name in fields} for row in rows],
            "plan": asdict(plan) if plan is not None else None}


def _stored_graph_plan(baseline, rows):
    if baseline.planning_mode != "calendar_graph":
        if baseline.project_start is not None or any(
            getattr(row, name) is not None for row in rows for name in (
                "duration_days", "is_milestone", "predecessor_ids", "constraint_type", "constraint_date", "not_before_date")):
            raise HTTPException(409, "schedule_legacy_intent_requires_review")
        return None
    plan = _calculate_graph(rows, baseline.project_start)[0]
    dates = {item.task_id: item for item in plan.tasks}
    if any((row.planned_start, row.planned_finish) !=
           (dates[row.id].earliest_start, dates[row.id].earliest_finish) for row in rows):
        raise HTTPException(409, "schedule_stored_plan_inconsistent")
    return plan


@router.get("/baselines/{baseline_id}/graph")
def get_schedule_graph(baseline_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    baseline = _locked_graph_baseline(db, baseline_id, user, "viewer")
    rows = _graph_rows(db, baseline)
    plan = _stored_graph_plan(baseline, rows)
    return _graph_response(baseline, rows, plan)


@router.put("/baselines/{baseline_id}/graph")
def put_schedule_graph(baseline_id: int, payload: ScheduleGraphPut,
                       db: Session = Depends(get_db), user: User = Depends(require_user)):
    baseline = _locked_graph_baseline(db, baseline_id, user, "editor")
    if baseline.status != "draft":
        raise HTTPException(409, "schedule_draft_required")
    if baseline.graph_revision != payload.expected_graph_revision:
        raise HTTPException(409, "schedule_graph_revision_changed")
    rows = _graph_rows(db, baseline)
    ids = [item.id for item in payload.items]
    if len(ids) != len(set(ids)) or set(ids) != {row.id for row in rows}:
        raise HTTPException(422, "schedule_complete_graph_required")
    plan, canonical = _calculate_graph(payload.items, payload.project_start)
    if any(value is not None and len(value) > 2000 for value in canonical.values()):
        raise HTTPException(422, "schedule_dependency_limit")
    revised = payload.expected_graph_revision + 1
    result = db.execute(update(ScheduleBaseline).where(
        ScheduleBaseline.id == baseline.id, ScheduleBaseline.project_id == baseline.project_id,
        ScheduleBaseline.status == "draft", ScheduleBaseline.graph_revision == payload.expected_graph_revision,
    ).values(graph_revision=revised, planning_mode="calendar_graph", project_start=payload.project_start)
     .execution_options(synchronize_session="fetch"))
    if result.rowcount != 1:
        raise HTTPException(409, "schedule_graph_revision_changed")
    intent = {item.id: item for item in payload.items}
    dates = {item.task_id: item for item in plan.tasks}
    try:
        for row in rows:
            item = intent[row.id]
            for name in ("duration_days", "is_milestone", "constraint_type", "constraint_date", "not_before_date"):
                setattr(row, name, getattr(item, name))
            row.predecessor_ids = canonical[row.id]
            row.planned_start = dates[row.id].earliest_start
            row.planned_finish = dates[row.id].earliest_finish
        _audit(db, "schedule_graph_saved", "schedule_baseline", baseline.id, user.id,
               f"revision={revised}; count={len(rows)}")
        db.flush()
        response = _graph_response(baseline, rows, plan)
        db.commit()
        return response
    except Exception:
        db.rollback()
        raise


def _row_graph_intent(items, references):
    """Resolve request-local names, then use the existing planner unchanged."""
    result = []
    for item in items:
        identifier = item.id if item.id is not None else references[item.client_ref]
        parts = []
        for dependency in item.dependencies:
            predecessor = dependency.predecessor_id
            if predecessor is None:
                predecessor = references.get(dependency.predecessor_ref)
                if predecessor is None:
                    raise HTTPException(422, "schedule_unknown_client_reference")
            parts.append(f"{predecessor}{dependency.link_type}"
                         + (f"{dependency.lag_days:+d}d" if dependency.lag_days else ""))
        predecessors = "; ".join(parts) or None
        if predecessors is not None and len(predecessors) > 2000:
            raise HTTPException(422, "schedule_dependency_limit")
        result.append(ScheduleGraphItem(id=identifier, duration_days=item.duration_days,
            is_milestone=item.is_milestone, predecessor_ids=predecessors,
            constraint_type=item.constraint_type, constraint_date=item.constraint_date,
            not_before_date=item.not_before_date))
    return result


@router.put("/baselines/{baseline_id}/graph/rows")
def put_schedule_graph_rows(baseline_id: int, payload: ScheduleGraphRowsPut,
                            db: Session = Depends(get_db), user: User = Depends(require_user)):
    # Like graph PUT, this endpoint owns a clean transaction. In particular a
    # caller must not autoflush a pending financial unlink ahead of our guard.
    if db.new or db.dirty or db.deleted:
        raise HTTPException(409, "schedule_pending_changes")
    baseline = _locked_graph_baseline(db, baseline_id, user, "editor")
    if baseline.status != "draft":
        raise HTTPException(409, "schedule_draft_required")
    if baseline.graph_revision != payload.expected_graph_revision:
        raise HTTPException(409, "schedule_graph_revision_changed")
    rows = _graph_rows(db, baseline)
    existing = {row.id: row for row in rows}
    kept = [item.id for item in payload.items if item.id is not None]
    refs = [item.client_ref for item in payload.items if item.client_ref is not None]
    deleted = set(payload.deleted_ids)
    if (len(kept) != len(set(kept)) or len(refs) != len(set(refs))
            or len(deleted) != len(payload.deleted_ids) or deleted.intersection(kept)
            or set(kept).union(deleted) != set(existing)):
        raise HTTPException(422, "schedule_complete_graph_required")
    for identifier in deleted:
        row = existing[identifier]
        if (row.source_name is not None or row.source_excerpt is not None
                or row.actual_start is not None or row.actual_finish is not None
                or row.actual_progress != 0 or row.status != "planned"):
            raise HTTPException(409, "schedule_row_delete_protected")
    # Schedule rows are locked FOR UPDATE, so concurrent FK inserts cannot
    # slip between this guard and DELETE on PostgreSQL. Never SET NULL links.
    if deleted and any(db.scalar(select(model.id).where(model.schedule_item_id.in_(deleted)).limit(1))
                       is not None for model in (BudgetLine, CashFlowEntry)):
        raise HTTPException(409, "schedule_row_delete_protected")

    # Validate the *complete* prospective graph before allocating DB rows.
    # Temporary IDs are private to this calculation and never returned/stored.
    offset = max(existing, default=0) + 1
    temporary = {reference: offset + index for index, reference in enumerate(refs)}
    allowed = set(kept)
    if any(link.predecessor_id is not None and link.predecessor_id not in allowed
           for item in payload.items for link in item.dependencies):
        raise HTTPException(422, "schedule_unknown_predecessor")
    _calculate_graph(_row_graph_intent(payload.items, temporary), payload.project_start)
    try:
        mapping = {}
        for item in payload.items:
            if item.id is None:
                row = ScheduleItem(project_id=baseline.project_id, baseline_id=baseline.id, title=item.title)
                db.add(row)
                db.flush()
                mapping[item.client_ref] = row.id
                existing[row.id] = row
        intent = _row_graph_intent(payload.items, mapping)
        plan, canonical = _calculate_graph(intent, payload.project_start)
        revised = payload.expected_graph_revision + 1
        changed = db.execute(update(ScheduleBaseline).where(
            ScheduleBaseline.id == baseline.id, ScheduleBaseline.project_id == baseline.project_id,
            ScheduleBaseline.status == "draft", ScheduleBaseline.graph_revision == payload.expected_graph_revision,
        ).values(graph_revision=revised, planning_mode="calendar_graph", project_start=payload.project_start)
         .execution_options(synchronize_session="fetch"))
        if changed.rowcount != 1:
            raise HTTPException(409, "schedule_graph_revision_changed")
        dates = {task.task_id: task for task in plan.tasks}
        final_rows = []
        for item, resolved in zip(payload.items, intent):
            row = existing[resolved.id]
            row.title = item.title
            for field in ("duration_days", "is_milestone", "constraint_type", "constraint_date", "not_before_date"):
                setattr(row, field, getattr(item, field))
            row.predecessor_ids = canonical[row.id]
            row.planned_start = dates[row.id].earliest_start
            row.planned_finish = dates[row.id].earliest_finish
            final_rows.append(row)
        for identifier in deleted:
            db.delete(existing[identifier])
        _audit(db, "schedule_graph_rows_saved", "schedule_baseline", baseline.id, user.id,
               f"revision={revised}; count={len(final_rows)}; added={len(mapping)}; deleted={len(deleted)}")
        db.flush()
        response = _graph_response(baseline, sorted(final_rows, key=lambda row: row.id), plan)
        response["client_ref_map"] = mapping
        db.commit()
        return response
    except Exception:
        db.rollback()
        raise


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
    confirmed_budget = [x for x in budget if x.status in {"approved", "active", "closed"}
                        and x.currency == IMPLICIT_LEDGER_CURRENCY]
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
    decision_requirements = finance_decision_requirements(
        (row.currency for row in budget),
        has_implicit_currency_rows=bool(cash or procurement or acts),
        has_financial_rows=bool(budget or cash or procurement or acts),
    )
    return {
        "summary": {"budget_planned": planned, "budget_committed": sum((x.committed_amount for x in confirmed_budget), Decimal("0")),
                    "budget_actual": actual, "budget_forecast": forecast,
                    "budget_variance": forecast - planned, "cash_balance_forecast": balance,
                    "cash_gap": minimum, "cash_gap_date": gap_date, "delayed_schedule": len(delayed),
                    "late_procurement": len(late_procurement), "acts_pending": len([x for x in acts if x.status in {"proposed", "approved"}]),
                    "pending_payments": len([x for x in cash if x.direction == "outflow" and x.status == "approved"]),
                    "unlinked_invoices": len([x for x in cash if _cash_flow_missing_links(x)]),
                    "excluded_currency_rows": len([x for x in budget if x.currency != IMPLICIT_LEDGER_CURRENCY]),
                    "financial_totals_reliable": not any(
                        requirement["code"] in {"unknown_currency", "mixed_currency"}
                        for requirement in decision_requirements
                    )},
        "decision_requirements": decision_requirements,
        "external_effects": {"payment_created": False, "posting_created": False,
                             "automatic_conversion": False},
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


@router.get("/cash-flow/views")
def cash_flow_views(project_id: Annotated[int, Query(ge=1)], date_from: date, date_to: date,
                    contract_id: Annotated[int | None, Query(ge=1)] = None,
                    db: Session = Depends(get_db), user: User = Depends(require_user)):
    # Read-only means no accidental autoflush and no rewriting a caller's pending
    # data. Refresh retained identity before the existing project-role check.
    if db.new or db.dirty or db.deleted:
        raise HTTPException(409, "cash_flow_view_pending_changes")
    if not valid_period(date_from, date_to):
        raise HTTPException(422, "cash_flow_view_period_invalid")
    with db.no_autoflush:
        actor = db.scalar(select(User).where(User.id == user.id).execution_options(populate_existing=True))
        if actor is None:
            raise HTTPException(403, "Insufficient project access")
        require_project_role(db, actor, project_id, "viewer")
        project = db.scalar(select(Project).where(Project.id == project_id).execution_options(populate_existing=True))
        if project is None or project.archived_at is not None:
            raise HTTPException(403, "Insufficient project access")
        _check_contract(db, project_id, contract_id)
        fields = ("id", "project_id", "contract_id", "record_version", "schedule_item_id", "budget_line_id", "task_id",
                  "source_document_id", "source_document_version_id", "evidence_id", "evidence_revision",
                  "evidence_assessment_version", "confidence", "review_status", "direction", "title", "counterparty",
                  "planned_date", "actual_date", "planned_amount", "actual_amount", "status")
        statement = select(*(getattr(CashFlowEntry, name) for name in fields)).where(
            CashFlowEntry.project_id == project_id,
            or_(CashFlowEntry.planned_date.between(date_from, date_to),
                CashFlowEntry.actual_date.between(date_from, date_to)),
        )
        if contract_id is not None:
            statement = statement.where(CashFlowEntry.contract_id == contract_id)
        rows = list(db.execute(statement.order_by(CashFlowEntry.planned_date, CashFlowEntry.id)
                               .limit(CASH_FLOW_VIEW_ROW_LIMIT + 1)).mappings())
        if len(rows) > CASH_FLOW_VIEW_ROW_LIMIT:
            raise HTTPException(413, "cash_flow_view_row_limit")
        return project_cash_flow_views(rows, project_id=project_id, contract_id=contract_id,
                                       date_from=date_from, date_to=date_to)


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
        baseline = _locked_graph_baseline(db, payload.baseline_id, user, "editor") if payload.baseline_id else None
        if baseline is None or baseline.project_id != payload.project_id:
            raise HTTPException(422, "Для импорта ГПР выберите черновик baseline проекта")
        if baseline.status != "draft":
            raise HTTPException(409, "Утверждённый baseline неизменяем")
        if baseline.planning_mode == "calendar_graph":
            raise HTTPException(409, "schedule_graph_batch_insert_required")
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
    if baseline is not None and created:
        baseline.graph_revision += 1
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
    source = _locked_graph_baseline(db, baseline_id, user, "manager")
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
    source_items = _graph_rows(db, source)
    _stored_graph_plan(source, source_items)
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
        planning_mode=source.planning_mode,
        project_start=source.project_start,
    )
    db.add(draft)
    db.flush()
    copied = {}
    for source_item in source_items:
        clone_item = ScheduleItem(
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
            duration_days=source_item.duration_days,
            is_milestone=source_item.is_milestone,
            constraint_type=source_item.constraint_type,
            constraint_date=source_item.constraint_date,
            not_before_date=source_item.not_before_date,
        )
        db.add(clone_item)
        copied[source_item.id] = clone_item
    db.flush()
    for source_item in source_items:
        links = parse_dependencies(source_item.predecessor_ids)
        # Source was validated before cloning; no old-baseline IDs survive.
        remapped = "; ".join(
            f"{copied[link.predecessor_id].id}{link.link_type}" + (f"{link.lag_days:+d}d" if link.lag_days else "")
            for link in links) or None
        if remapped is not None and len(remapped) > 2000:
            # New IDs can be longer. Undo even already-flushed clone rows;
            # never rely on backend-specific VARCHAR truncation/rejection.
            db.rollback()
            raise HTTPException(422, "schedule_dependency_limit")
        copied[source_item.id].predecessor_ids = remapped
    _audit(db, "baseline_cloned", "schedule_baseline", draft.id, user.id,
           f"source_baseline={source.id}; version={version}; facts_copied=false")
    db.commit()
    db.refresh(draft)
    return {"id": draft.id, "version": draft.version, "status": draft.status, "already_created": False}


@router.post("/schedule-items")
def create_schedule_item(payload: ScheduleItemCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    baseline = _locked_graph_baseline(db, payload.baseline_id, user, "editor")
    if baseline.status != "draft": raise HTTPException(409, "Утверждённый или архивный baseline неизменяем; создайте новую версию")
    if baseline.planning_mode == "calendar_graph":
        raise HTTPException(409, "schedule_graph_batch_insert_required")
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
    baseline.graph_revision += 1
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
    currency_decisions = list(blocking_currency_detail(payload.currency)["decision_codes"])
    data["review_status"] = "required" if currency_decisions else review_status
    item = BudgetLine(**data)
    db.add(item)
    db.flush()
    _audit(db, "budget_proposed", "budget_line", item.id, user.id, "status=proposed")
    db.commit()
    return {"id": item.id, "status": item.status, "review_status": item.review_status,
            "decision_required": currency_decisions, "automatic_conversion": False}


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
    if kind == "baselines":
        item = _locked_graph_baseline(db, item_id, user, "manager")
    else:
        item = db.scalar(select(model).where(model.id == item_id).with_for_update())
        if item is None: raise HTTPException(404, "Item not found")
        require_project_role(db, user, item.project_id, "manager")
        item = db.scalar(select(model).where(model.id == item_id).with_for_update())
    allowed = {"budget": {"approved", "active", "closed", "rejected"}, "cash-flow": {"approved", "cancelled"},
               "procurement": {"request", "ordered", "delivered", "accepted", "cancelled"}, "acts": {"approved", "signed", "paid", "rejected"},
               "baselines": {"approved"}}[kind]
    if payload.status not in allowed: raise HTTPException(422, "Недопустимый статус")
    if (kind == "baselines" and item.planning_mode == "calendar_graph"
            and payload.expected_graph_revision != item.graph_revision):
        raise HTTPException(409, "schedule_graph_revision_changed")
    if kind in {"budget", "cash-flow"} and (
        payload.actual_amount is not None or payload.actual_date is not None
    ):
        raise HTTPException(
            422,
            "Факт бюджета/ДДС нельзя передать вместе со статусом; используйте отдельное подтверждение оплаты",
        )
    if item.status == payload.status and kind in {"budget", "cash-flow"}:
        return {"id": item.id, "status": item.status, "already_confirmed": True, "record_version": item.record_version}
    if item.status == payload.status:
        return {"id": item.id, "status": item.status, "already_applied": True}
    if payload.expected_status is not None and item.status != payload.expected_status:
        raise HTTPException(409, "Статус уже изменён; обновите данные перед повтором")
    if kind in {"budget", "cash-flow"} and payload.status in {"approved", "active", "closed"}:
        if kind == "budget" and item.currency != IMPLICIT_LEDGER_CURRENCY:
            raise HTTPException(409, blocking_currency_detail(item.currency))
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
    superseded_id = None
    if kind == "baselines":
        if payload.expected_status is None:
            raise HTTPException(422, "Для утверждения baseline укажите ожидаемый статус")
        if item.status != "draft":
            raise HTTPException(409, "Утвердить можно только черновик baseline")
        if item.planning_mode == "calendar_graph":
            _stored_graph_plan(item, _graph_rows(db, item))
        current = _current_approved_baseline(db, item)
        if current is not None and current.id != item.id:
            current.status = "superseded"
            current.graph_revision += 1
            superseded_id = current.id
            _audit(db, "baseline_superseded", "schedule_baseline", current.id, user.id,
                   f"replacement_baseline={item.id}; replacement_version={item.version}")
        item.graph_revision += 1
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
