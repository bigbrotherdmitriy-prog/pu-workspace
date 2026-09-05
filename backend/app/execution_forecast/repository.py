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


def load_forecast_input(db: Session, project_id: int, as_of: date | None = None) -> ForecastInput:
    project = db.get(Project, project_id)
    if project is None:
        raise LookupError("project_not_found")

    baselines = list(db.scalars(
        select(ScheduleBaseline)
        .where(ScheduleBaseline.project_id == project_id, ScheduleBaseline.status == "approved")
        .order_by(ScheduleBaseline.version.desc(), ScheduleBaseline.id.desc())
    ))
    current_baselines: dict[int | None, ScheduleBaseline] = {}
    for baseline in baselines:
        current_baselines.setdefault(baseline.contract_id, baseline)
    baseline_ids = [row.id for row in current_baselines.values()]
    schedule = list(db.scalars(
        select(ScheduleItem)
        .where(ScheduleItem.project_id == project_id, ScheduleItem.baseline_id.in_(baseline_ids))
        .order_by(ScheduleItem.id)
    )) if baseline_ids else []
    budget = list(db.scalars(
        select(BudgetLine)
        .where(BudgetLine.project_id == project_id, BudgetLine.status.notin_({"rejected"}))
        .order_by(BudgetLine.id)
    ))
    cash_flow = list(db.scalars(
        select(CashFlowEntry)
        .where(CashFlowEntry.project_id == project_id, CashFlowEntry.status != "cancelled")
        .order_by(CashFlowEntry.planned_date, CashFlowEntry.id)
    ))
    contracts = list(db.scalars(
        select(Contract).where(Contract.project_id == project_id).order_by(Contract.id)
    ))
    tasks = list(db.scalars(
        select(Task)
        .where(Task.project_id == project_id, Task.status.notin_({"completed", "cancelled"}))
        .order_by(Task.id)
    ))
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
