from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.execution_forecast.contracts import (
    BudgetFact,
    CashFlowFact,
    ContractFact,
    EntitySource,
    EvidencePin,
    ForecastInput,
    ScheduleFact,
    TaskFact,
)
from app.models.document_version import DocumentVersion
from app.models.execution_finance import BudgetLine, CashFlowEntry, ScheduleBaseline, ScheduleItem
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.task import Task
from app.models.v54_pilot import Evidence, EvidenceAssessment, SourceReference, SourceVersion


MAX_FORECAST_ROWS = 500


def _decimal(value) -> Decimal:
    return Decimal(value or 0)


def _safe_locator(locator: object) -> tuple[int | None, tuple[float, ...] | None]:
    if not isinstance(locator, dict):
        return None, None
    if isinstance(locator.get("page"), int) and locator["page"] >= 1:
        page = locator["page"]
    elif isinstance(locator.get("page_number"), int) and locator["page_number"] >= 1:
        page = locator["page_number"]
    elif isinstance(locator.get("page_index"), int) and locator["page_index"] >= 0:
        page = locator["page_index"] + 1
    else:
        page = None
    raw_coordinates = locator.get("coordinates", locator.get("bbox"))
    coordinates = None
    if (
        isinstance(raw_coordinates, (list, tuple))
        and 4 <= len(raw_coordinates) <= 8
        and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in raw_coordinates)
    ):
        coordinates = tuple(float(value) for value in raw_coordinates)
    return page, coordinates


def _evidence_by_document(db: Session, project: Project, document_ids: set[int]) -> dict[int, tuple[EvidencePin, ...]]:
    if not document_ids:
        return {}
    rows = db.execute(
        select(DocumentVersion.document_id, Evidence, EvidenceAssessment)
        .join(SourceVersion, and_(
            SourceVersion.organization_id == Evidence.organization_id,
            SourceVersion.source_id == Evidence.source_id,
            SourceVersion.id == Evidence.source_version_id,
        ))
        .join(SourceReference, and_(
            SourceReference.organization_id == SourceVersion.organization_id,
            SourceReference.id == SourceVersion.source_id,
        ))
        .join(DocumentVersion, DocumentVersion.id == SourceVersion.legacy_document_version_id)
        .outerjoin(EvidenceAssessment, and_(
            EvidenceAssessment.organization_id == Evidence.organization_id,
            EvidenceAssessment.evidence_id == Evidence.id,
        ))
        .where(
            SourceReference.origin_project_id == project.id,
            SourceReference.organization_id == project.organization_id,
            DocumentVersion.document_id.in_(document_ids),
        )
        .order_by(DocumentVersion.document_id, Evidence.id)
    ).all()
    result: dict[int, list[EvidencePin]] = {}
    for document_id, evidence, assessment in rows:
        page, coordinates = _safe_locator(evidence.locator)
        result.setdefault(document_id, []).append(EvidencePin(
            evidence_id=str(evidence.id),
            source_version_id=str(evidence.source_version_id),
            confidence=evidence.confidence,
            verification=assessment.verification if assessment is not None else "unverified",
            page=page,
            coordinates=coordinates,
        ))
    return {document_id: tuple(pins) for document_id, pins in result.items()}


def _source(
    entity_type: str,
    entity_id: int,
    fields: tuple[str, ...],
    state: str,
    evidence_by_document: dict[int, tuple[EvidencePin, ...]],
    document_id: int | None = None,
) -> EntitySource:
    return EntitySource(
        entity_type=entity_type,
        entity_id=entity_id,
        fields=fields,
        state=state,
        evidence=evidence_by_document.get(document_id, ()) if document_id is not None else (),
    )


def _bounded(db: Session, statement, remaining: int, *, reason: str):
    if remaining < 0:
        raise LookupError(reason)
    rows = list(db.scalars(statement.limit(remaining + 1)))
    if len(rows) > remaining:
        raise LookupError(reason)
    return rows


def _wbs_scope(
    db: Session, project_id: int, schedule_item_id: int | None,
) -> tuple[str, set[int] | None, ScheduleItem | None]:
    if schedule_item_id is None:
        return "project", None, None
    selected = db.scalar(select(ScheduleItem).where(
        ScheduleItem.id == schedule_item_id,
        ScheduleItem.project_id == project_id,
    ))
    if selected is None:
        raise LookupError("schedule_item_not_found")
    baseline = db.scalar(select(ScheduleBaseline).where(
        ScheduleBaseline.id == selected.baseline_id,
        ScheduleBaseline.project_id == project_id,
        ScheduleBaseline.status == "approved",
    ))
    if baseline is None:
        raise LookupError("schedule_scope_not_approved")
    latest = db.scalar(select(ScheduleBaseline).where(
        ScheduleBaseline.project_id == project_id,
        ScheduleBaseline.contract_id == baseline.contract_id,
        ScheduleBaseline.status == "approved",
    ).order_by(ScheduleBaseline.version.desc(), ScheduleBaseline.id.desc()).limit(1))
    if latest is None or latest.id != baseline.id:
        raise LookupError("schedule_scope_not_current")
    if not selected.is_summary:
        child_exists = db.scalar(select(ScheduleItem.id).where(
            ScheduleItem.project_id == project_id,
            ScheduleItem.baseline_id == selected.baseline_id,
            ScheduleItem.wbs_parent_id == selected.id,
        ).limit(1))
        if child_exists is not None:
            raise LookupError("schedule_wbs_invalid")
        return "wbs_leaf", {selected.id}, selected

    rows = list(db.scalars(select(ScheduleItem).where(
        ScheduleItem.project_id == project_id,
        ScheduleItem.baseline_id == selected.baseline_id,
    ).order_by(ScheduleItem.id)))
    children: dict[int | None, list[ScheduleItem]] = {}
    for row in rows:
        children.setdefault(row.wbs_parent_id, []).append(row)
    leaf_ids: set[int] = set()
    stack = [selected.id]
    visited: set[int] = set()
    while stack:
        parent_id = stack.pop()
        if parent_id in visited:
            raise LookupError("schedule_wbs_cycle")
        visited.add(parent_id)
        for child in children.get(parent_id, []):
            if child.is_summary:
                stack.append(child.id)
            else:
                if children.get(child.id):
                    raise LookupError("schedule_wbs_invalid")
                leaf_ids.add(child.id)
    return "wbs_summary", leaf_ids, selected


def load_forecast_input(
    db: Session,
    project_id: int,
    as_of: date | None = None,
    *,
    contract_id: int | None = None,
    schedule_item_id: int | None = None,
    row_limit: int = 200,
) -> ForecastInput:
    if not 1 <= row_limit <= MAX_FORECAST_ROWS:
        raise ValueError("invalid_forecast_row_limit")
    project = db.get(Project, project_id)
    if project is None:
        raise LookupError("project_not_found")

    contract = None
    if contract_id is not None:
        contract = db.scalar(select(Contract).where(
            Contract.id == contract_id,
            Contract.project_id == project_id,
        ))
        if contract is None:
            raise LookupError("contract_not_found")

    scope_kind, schedule_ids, selected_schedule = _wbs_scope(db, project_id, schedule_item_id)
    if contract_id is not None and selected_schedule is not None:
        selected_baseline = db.get(ScheduleBaseline, selected_schedule.baseline_id)
        if selected_baseline is None or selected_baseline.contract_id != contract_id:
            raise LookupError("contract_schedule_scope_mismatch")
    if contract_id is not None:
        scope_kind = f"contract_{scope_kind}" if schedule_item_id is not None else "contract"

    baselines = list(db.scalars(
        select(ScheduleBaseline)
        .where(ScheduleBaseline.project_id == project_id, ScheduleBaseline.status == "approved")
        .order_by(ScheduleBaseline.version.desc(), ScheduleBaseline.id.desc())
    ))
    current_baselines: dict[int | None, ScheduleBaseline] = {}
    for baseline in baselines:
        current_baselines.setdefault(baseline.contract_id, baseline)
    baseline_ids = [row.id for row in current_baselines.values()]
    if contract_id is not None:
        baseline_ids = [row.id for row in current_baselines.values() if row.contract_id == contract_id]
    schedule_statement = select(ScheduleItem).where(
        ScheduleItem.project_id == project_id,
        ScheduleItem.baseline_id.in_(baseline_ids),
        ScheduleItem.is_summary.is_(False),
    )
    if schedule_ids is not None:
        schedule_statement = schedule_statement.where(ScheduleItem.id.in_(schedule_ids))
    schedule = _bounded(db, schedule_statement.order_by(ScheduleItem.id), row_limit,
                        reason="forecast_row_limit_exceeded") if baseline_ids else []

    budget_statement = select(BudgetLine).where(
        BudgetLine.project_id == project_id,
        BudgetLine.review_status == "confirmed",
        BudgetLine.status.notin_({"rejected"}),
    )
    cash_statement = select(CashFlowEntry).where(
        CashFlowEntry.project_id == project_id,
        CashFlowEntry.review_status == "confirmed",
        CashFlowEntry.status != "cancelled",
    )
    if contract_id is not None:
        budget_statement = budget_statement.where(BudgetLine.contract_id == contract_id)
        cash_statement = cash_statement.where(CashFlowEntry.contract_id == contract_id)
    if schedule_ids is not None:
        budget_statement = budget_statement.where(BudgetLine.schedule_item_id.in_(schedule_ids))
        cash_statement = cash_statement.where(CashFlowEntry.schedule_item_id.in_(schedule_ids))

    summary_ids = set(db.scalars(select(ScheduleItem.id).where(
        ScheduleItem.project_id == project_id,
        ScheduleItem.is_summary.is_(True),
    )))
    if summary_ids:
        direct_budget = db.scalar(select(BudgetLine.id).where(
            BudgetLine.project_id == project_id,
            BudgetLine.review_status == "confirmed",
            BudgetLine.schedule_item_id.in_(summary_ids),
        ).limit(1))
        direct_cash = db.scalar(select(CashFlowEntry.id).where(
            CashFlowEntry.project_id == project_id,
            CashFlowEntry.review_status == "confirmed",
            CashFlowEntry.schedule_item_id.in_(summary_ids),
        ).limit(1))
        if direct_budget is not None or direct_cash is not None:
            raise LookupError("schedule_summary_direct_finance_link")

    budget = _bounded(db, budget_statement.order_by(BudgetLine.id), row_limit - len(schedule),
                      reason="forecast_row_limit_exceeded")
    cash_flow = _bounded(db, cash_statement.order_by(CashFlowEntry.planned_date, CashFlowEntry.id),
                         row_limit - len(schedule) - len(budget), reason="forecast_row_limit_exceeded")

    contract_statement = select(Contract).where(Contract.project_id == project_id)
    if contract_id is not None:
        contract_statement = contract_statement.where(Contract.id == contract_id)
    contracts = _bounded(db, contract_statement.order_by(Contract.id),
                         row_limit - len(schedule) - len(budget) - len(cash_flow),
                         reason="forecast_row_limit_exceeded")

    task_statement = select(Task).where(
        Task.project_id == project_id,
        Task.status.notin_({"completed", "cancelled"}),
    )
    tasks = [] if contract_id is not None or schedule_ids is not None else _bounded(
        db, task_statement.order_by(Task.id),
        row_limit - len(schedule) - len(budget) - len(cash_flow) - len(contracts),
        reason="forecast_row_limit_exceeded",
    )
    document_ids = {
        value for value in (
            *(row.source_document_id for row in budget),
            *(row.source_document_id for row in cash_flow),
            *(row.source_document_id for row in contracts),
        ) if value is not None
    }
    evidence = _evidence_by_document(db, project, document_ids)

    return ForecastInput(
        project_id=project.id,
        organization_id=project.organization_id,
        as_of=as_of or date.today(),
        scope_kind=scope_kind,
        contract_id=contract_id,
        schedule_item_id=schedule_item_id,
        rows_included=len(schedule) + len(budget) + len(cash_flow) + len(contracts) + len(tasks),
        row_limit=row_limit,
        schedule=tuple(ScheduleFact(
            id=row.id,
            title=row.title,
            planned_start=row.planned_start,
            planned_finish=row.planned_finish,
            actual_start=row.actual_start,
            actual_finish=row.actual_finish,
            planned_progress=row.planned_progress,
            actual_progress=row.actual_progress,
            status=row.status,
            source=_source(
                "schedule_item", row.id,
                ("planned_finish", "actual_start", "actual_finish", "actual_progress"),
                row.status, evidence,
            ),
        ) for row in schedule),
        budget=tuple(BudgetFact(
            id=row.id,
            description=row.description,
            planned_amount=_decimal(row.planned_amount),
            committed_amount=_decimal(row.committed_amount),
            actual_amount=_decimal(row.actual_amount),
            declared_forecast_amount=_decimal(row.forecast_amount),
            currency=row.currency,
            status=row.status,
            source=_source(
                "budget_line", row.id,
                ("planned_amount", "committed_amount", "actual_amount", "forecast_amount"),
                row.status, evidence, row.source_document_id,
            ),
        ) for row in budget),
        cash_flow=tuple(CashFlowFact(
            id=row.id,
            title=row.title,
            direction=row.direction,
            planned_date=row.planned_date,
            actual_date=row.actual_date,
            planned_amount=_decimal(row.planned_amount),
            actual_amount=_decimal(row.actual_amount),
            status=row.status,
            source=_source(
                "cash_flow_entry", row.id,
                ("direction", "planned_date", "actual_date", "planned_amount", "actual_amount"),
                row.status, evidence, row.source_document_id,
            ),
        ) for row in cash_flow),
        contracts=tuple(ContractFact(
            id=row.id,
            number=row.number,
            amount=_decimal(row.amount) if row.amount is not None else None,
            signed_at=row.signed_at,
            status=row.status,
            source=_source(
                "contract", row.id, ("amount", "signed_at", "status"), row.status,
                evidence, row.source_document_id,
            ),
        ) for row in contracts),
        tasks=tuple(TaskFact(
            id=row.id,
            title=row.title,
            due_date=row.due_date,
            status=row.status,
            confidence=row.confidence,
            needs_review=row.needs_review,
            source=_source(
                "task", row.id, ("due_date", "status", "confidence", "needs_review"),
                row.status, evidence,
            ),
        ) for row in tasks),
    )
