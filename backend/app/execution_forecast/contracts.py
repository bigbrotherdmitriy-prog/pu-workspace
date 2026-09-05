from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class EvidencePin:
    evidence_id: str
    source_version_id: str
    confidence: float | None
    verification: str
    page: int | None = None
    coordinates: tuple[float, ...] | None = None


@dataclass(frozen=True)
class EntitySource:
    entity_type: str
    entity_id: int
    fields: tuple[str, ...]
    state: str
    evidence: tuple[EvidencePin, ...] = ()


@dataclass(frozen=True)
class ScheduleFact:
    id: int
    title: str
    planned_start: date | None
    planned_finish: date | None
    actual_start: date | None
    actual_finish: date | None
    planned_progress: float
    actual_progress: float
    status: str
    source: EntitySource


@dataclass(frozen=True)
class BudgetFact:
    id: int
    description: str
    planned_amount: Decimal
    committed_amount: Decimal
    actual_amount: Decimal
    declared_forecast_amount: Decimal
    currency: str
    status: str
    source: EntitySource


@dataclass(frozen=True)
class CashFlowFact:
    id: int
    title: str
    direction: str
    planned_date: date
    actual_date: date | None
    planned_amount: Decimal
    actual_amount: Decimal
    status: str
    source: EntitySource


@dataclass(frozen=True)
class ContractFact:
    id: int
    number: str
    amount: Decimal | None
    signed_at: date | None
    status: str
    source: EntitySource


@dataclass(frozen=True)
class TaskFact:
    id: int
    title: str
    due_date: date | None
    status: str
    confidence: float
    needs_review: bool
    source: EntitySource


@dataclass(frozen=True)
class ForecastInput:
    project_id: int
    organization_id: int
    as_of: date
    schedule: tuple[ScheduleFact, ...] = field(default_factory=tuple)
    budget: tuple[BudgetFact, ...] = field(default_factory=tuple)
    cash_flow: tuple[CashFlowFact, ...] = field(default_factory=tuple)
    contracts: tuple[ContractFact, ...] = field(default_factory=tuple)
    tasks: tuple[TaskFact, ...] = field(default_factory=tuple)
