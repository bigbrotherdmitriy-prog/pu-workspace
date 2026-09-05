from datetime import date
from decimal import Decimal

from app.execution_forecast.api import router
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
from app.execution_forecast.engine import build_forecast
from app.execution_forecast.repository import _safe_locator, load_forecast_input
from app.models.execution_finance import BudgetLine, CashFlowEntry, ScheduleBaseline, ScheduleItem
from app.models.organization_contract import Contract, Organization
from app.models.project import Project


def source(kind: str, entity_id: int, *, verified: bool = False) -> EntitySource:
    evidence = ()
    if verified:
        evidence = (EvidencePin(
            evidence_id=f"evidence-{entity_id}",
            source_version_id=f"version-{entity_id}",
            confidence=0.94,
            verification="verified",
            page=2,
            coordinates=(10.0, 20.0, 110.0, 50.0),
        ),)
    return EntitySource(kind, entity_id, ("status",), "approved", evidence)


def sample_input(*, verified: bool = True) -> ForecastInput:
    return ForecastInput(
        project_id=7,
        organization_id=3,
        as_of=date(2026, 9, 10),
        schedule=(ScheduleFact(
            id=11,
            title="Synthetic installation",
            planned_start=date(2026, 9, 1),
            planned_finish=date(2026, 9, 12),
            actual_start=date(2026, 9, 1),
            actual_finish=None,
            planned_progress=80,
            actual_progress=50,
            status="in_progress",
            source=source("schedule_item", 11, verified=verified),
        ),),
        budget=(BudgetFact(
            id=21,
            description="Synthetic works",
            planned_amount=Decimal("100000"),
            committed_amount=Decimal("115000"),
            actual_amount=Decimal("60000"),
            declared_forecast_amount=Decimal("110000"),
            currency="RUB",
            status="approved",
            source=source("budget_line", 21, verified=verified),
        ),),
        cash_flow=(
            CashFlowFact(
                id=31, title="Synthetic advance", direction="inflow",
                planned_date=date(2026, 9, 8), actual_date=date(2026, 9, 8),
                planned_amount=Decimal("50000"), actual_amount=Decimal("50000"), status="received",
                source=source("cash_flow_entry", 31, verified=verified),
            ),
            CashFlowFact(
                id=32, title="Synthetic invoice", direction="outflow",
                planned_date=date(2026, 9, 11), actual_date=None,
                planned_amount=Decimal("80000"), actual_amount=Decimal("0"), status="approved",
                source=source("cash_flow_entry", 32, verified=verified),
            ),
        ),
        contracts=(ContractFact(
            id=41, number="SYN-41", amount=Decimal("200000"), signed_at=date(2026, 8, 20),
            status="active", source=source("contract", 41, verified=verified),
        ),),
        tasks=(TaskFact(
            id=51, title="Synthetic overdue task", due_date=date(2026, 9, 5), status="assigned",
            confidence=0.91, needs_review=False, source=source("task", 51, verified=verified),
        ),),
    )


def test_schedule_forecast_uses_explainable_linear_progress_formula():
    result = build_forecast(sample_input())
    stage = result["schedule"]["stages"][0]

    assert stage["formula"] == "linear_progress_extrapolation"
    assert stage["predicted_finish"] == "2026-09-20"
    assert "ceil(" in stage["formula_description"]
    assert stage["risks"] == ["predicted_delay"]


def test_cash_forecast_uses_actual_before_plan_and_exposes_gap():
    result = build_forecast(sample_input())

    assert result["cash_flow"]["formula"].startswith("running_balance")
    assert result["cash_flow"]["events"][0]["value_kind"] == "actual"
    assert result["cash_flow"]["closing_balance"] == "-30000.00"
    assert result["cash_flow"]["cash_gap_date"] == "2026-09-11"
    assert any(risk["code"] == "cash_gap" for risk in result["risks"])


def test_budget_forecast_never_hides_known_higher_commitment():
    result = build_forecast(sample_input())
    line = result["budget"]["lines"][0]

    assert line["formula"] == "max(plan, committed, actual, declared_forecast)"
    assert line["forecast_amount"] == "115000.00"
    assert result["budget"]["variance"] == "15000.00"
    assert any(risk["code"] == "budget_overrun" for risk in result["risks"])


def test_exact_evidence_pin_contains_page_and_coordinates_but_no_provider_locator():
    result = build_forecast(sample_input())
    evidence = result["cash_flow"]["events"][0]["sources"][0]["evidence"][0]

    assert evidence == {
        "evidence_id": "evidence-31",
        "source_version_id": "version-31",
        "confidence": 0.94,
        "verification": "verified",
        "page": 2,
        "coordinates": [10.0, 20.0, 110.0, 50.0],
    }


def test_missing_evidence_is_visible_and_lowers_confidence_without_fake_link():
    result = build_forecast(sample_input(verified=False))
    source_payload = result["budget"]["lines"][0]["sources"][0]

    assert source_payload["evidence"] == []
    assert source_payload["evidence_exact"] is False
    assert result["confidence"]["score"] < 0.70
    assert result["confidence"]["band"] == "low"


def test_forecast_is_deterministic_draft_and_cannot_trigger_actions():
    first = build_forecast(sample_input())
    second = build_forecast(sample_input())

    assert first["forecast_id"] == second["forecast_id"]
    assert first["publication_state"] == "draft"
    assert first["advisory_only"] is True
    assert first["can_trigger_actions"] is False
    assert first["requires_human_confirmation"] is True
    assert first["manual_confirmation"]["binding"] == first["forecast_id"]
    assert first["manual_confirmation"]["persistence_available"] is False


def test_overdue_task_is_a_portfolio_risk_not_an_invented_stage_link():
    result = build_forecast(sample_input())
    risk = next(row for row in result["risks"] if row["code"] == "overdue_task")

    assert risk["sources"][0]["entity_type"] == "task"
    assert risk["sources"][0]["entity_id"] == 51
    assert len(risk["sources"]) == 1


def test_safe_locator_allowlists_only_page_and_numeric_coordinates():
    assert _safe_locator({"page": 4, "bbox": [1, 2, 3, 4], "url": "https://forbidden.test"}) == (
        4, (1.0, 2.0, 3.0, 4.0),
    )
    assert _safe_locator({"page": -1, "coordinates": ["secret", 2, 3, 4], "path": "C:/secret"}) == (None, None)
    assert _safe_locator({"page_index": 0, "bbox": [1, 2, 3, 4]}) == (1, (1.0, 2.0, 3.0, 4.0))


def test_invalid_progress_fails_closed_instead_of_inventing_finish():
    original = sample_input()
    invalid_stage = ScheduleFact(**{**original.schedule[0].__dict__, "actual_progress": 125})
    result = build_forecast(ForecastInput(**{**original.__dict__, "schedule": (invalid_stage,)}))

    stage = result["schedule"]["stages"][0]
    assert stage["predicted_finish"] is None
    assert stage["formula"] == "invalid_actual_progress"
    assert stage["confidence"] <= 0.1


def test_router_is_read_only_and_has_no_confirmation_or_execution_route():
    methods = {(route.path, frozenset(route.methods or ())) for route in router.routes}
    assert methods == {("/execution/forecast/{project_id}", frozenset({"GET"}))}


def test_repository_uses_only_latest_approved_gpr_and_project_scoped_rows(db_session, user_factory):
    user = user_factory()
    organization = Organization(name="Synthetic forecast org")
    other_organization = Organization(name="Synthetic other org")
    db_session.add_all([organization, other_organization])
    db_session.flush()
    project = Project(name="Synthetic forecast project", organization_id=organization.id)
    other_project = Project(name="Synthetic other project", organization_id=other_organization.id)
    db_session.add_all([project, other_project])
    db_session.flush()
    old = ScheduleBaseline(
        project_id=project.id, created_by_user_id=user.id, name="Old", version=1, status="superseded",
    )
    current = ScheduleBaseline(
        project_id=project.id, created_by_user_id=user.id, name="Current", version=2, status="approved",
    )
    draft = ScheduleBaseline(
        project_id=project.id, created_by_user_id=user.id, name="Draft", version=3, status="draft",
    )
    db_session.add_all([old, current, draft])
    db_session.flush()
    db_session.add_all([
        ScheduleItem(project_id=project.id, baseline_id=old.id, title="Historical", planned_progress=100),
        ScheduleItem(project_id=project.id, baseline_id=current.id, title="Current", planned_progress=80),
        ScheduleItem(project_id=project.id, baseline_id=draft.id, title="Draft", planned_progress=10),
        BudgetLine(project_id=project.id, category="works", description="Included", planned_amount=10),
        BudgetLine(project_id=other_project.id, category="works", description="Excluded", planned_amount=999),
        CashFlowEntry(
            project_id=project.id, direction="outflow", title="Cancelled", planned_date=date(2026, 9, 2),
            planned_amount=20, status="cancelled",
        ),
        Contract(project_id=project.id, number="SYN-1", title="Synthetic contract", amount=100, status="active"),
    ])
    db_session.flush()

    loaded = load_forecast_input(db_session, project.id, date(2026, 9, 10))

    assert [row.title for row in loaded.schedule] == ["Current"]
    assert [row.description for row in loaded.budget] == ["Included"]
    assert loaded.cash_flow == ()
    assert [row.number for row in loaded.contracts] == ["SYN-1"]
