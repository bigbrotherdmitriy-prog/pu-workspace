import hashlib
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import BudgetLine, CashFlowEntry, CostCategory
from app.models.organization_contract import Contract, Organization
from app.models.project import Project


@pytest.fixture
def world(db_session, user_factory, monkeypatch):
    user = user_factory(is_admin=True)
    org = Organization(name="Matrix budget")
    db_session.add(org); db_session.flush()
    project = Project(name="Budget", organization_id=org.id, currency="RUB")
    db_session.add(project); db_session.flush()
    contract = Contract(project_id=project.id, number="25", title="Contract")
    category = CostCategory(organization_id=org.id, name="Материалы", normalized_name="материалы")
    db_session.add_all([contract, category]); db_session.flush()
    content = "Статья\tГодовой итог\tянварь\tфевраль\tмарт\nМатериалы (Городец)\t10\t1\t2\t3\nЭтапы Дубна\t20\t20\t0\t0\n"
    document = Document(project_id=project.id, name="ДДC.xlsx", source="local_upload", status="analyzed", current_version=1)
    db_session.add(document); db_session.flush()
    version = DocumentVersion(document_id=document.id, version_number=1, content=content)
    db_session.add(version); db_session.flush()
    return db_session, user, project, contract, category, document, version


def service():
    from app import dds_article_budget
    return dds_article_budget


def request(world, **overrides):
    db, user, project, contract, category, document, version = world
    values = dict(project_id=project.id, contract_id=contract.id, plan_year=2026,
                  budget_revision=1, mode="create_budget", category_by_article={2: category.id})
    values.update(overrides)
    return service().ArticleBudgetPreviewRequest(**values)


def preview(world, **overrides):
    db, user, _, _, _, document, _ = world
    return service().preview_article_budget(document.id, request(world, **overrides), db, user)


def apply(world, proposal=None, **overrides):
    db, user, _, _, _, document, _ = world
    base = request(world).model_dump()
    proposal = proposal or preview(world)
    base.update(preview_hash=proposal["preview_hash"], expected_document_version_id=proposal["document_version_id"],
                expected_document_sha256=proposal["document_sha256"], idempotency_key="matrix-operation-1",
                owner_confirmed=True)
    base.update(overrides)
    return service().apply_article_budget(document.id, service().ArticleBudgetApplyRequest(**base), db, user)


def test_preview_does_not_write_and_uses_months_not_annual(world):
    db = world[0]
    before = (db.query(BudgetLine).count(), db.query(CashFlowEntry).count())
    result = preview(world)
    assert result["expense_total"] == "6.00"
    assert len(result["articles"]) == 1
    assert result["articles"][0]["annual_difference"] == "4.00"
    assert result["warnings"] and result["conflicts"] == []
    assert before == (db.query(BudgetLine).count(), db.query(CashFlowEntry).count())


def test_creation_is_distinct_from_approval_and_never_creates_cash_in_budget_only_mode(world):
    db = world[0]
    result = apply(world)
    line = db.get(BudgetLine, result["created_budget_ids"][0])
    assert line.line_kind == "analytical_expense"
    assert line.budget_period == 2026 and line.budget_revision == 1
    assert line.planned_amount == Decimal("6.00") and line.status == "proposed"
    assert line.source_document_version_id == world[6].id
    assert result["created_cash_flow_ids"] == [] and db.query(CashFlowEntry).count() == 0


def test_new_forecasts_link_to_their_article_and_exclude_incomes(world):
    result = apply(world, preview(world, mode="import_forecast"), mode="import_forecast")
    db = world[0]
    rows = list(db.scalars(select(CashFlowEntry)))
    assert len(rows) == 3
    assert sum(row.planned_amount for row in rows) == Decimal("6.00")
    assert all(row.budget_line_id == result["created_budget_ids"][0] for row in rows)
    assert all(row.status == "proposed" and row.entry_kind == "plan_forecast" for row in rows)
    assert all(row.schedule_item_id is None and row.actual_amount == 0 for row in rows)
    assert all(row.matrix_operation_id == result["operation_id"] for row in rows)


def test_confirmation_is_required(world):
    with pytest.raises(HTTPException, match="OWNER_CONFIRMATION_REQUIRED"):
        apply(world, owner_confirmed=False)
    assert world[0].query(BudgetLine).count() == 0


def test_categories_require_explicit_active_project_choice(world):
    result = preview(world, category_by_article={})
    assert result["conflicts"][0]["code"] == "CATEGORY_CONFIRMATION_REQUIRED"
    world[4].is_active = False; world[0].flush()
    assert preview(world)["conflicts"][0]["code"] == "CATEGORY_UNAVAILABLE"


def test_same_request_replays_but_different_payload_conflicts(world):
    proposal = preview(world)
    first = apply(world, proposal)
    again = apply(world, proposal)
    assert again["operation_id"] == first["operation_id"] and again["replayed"] is True
    assert world[0].query(BudgetLine).count() == 1
    with pytest.raises(HTTPException, match="IDEMPOTENCY_CONFLICT"):
        apply(world, proposal, budget_revision=2)


def test_different_idempotency_key_does_not_duplicate_same_source_operation(world):
    proposal = preview(world)
    apply(world, proposal)
    with pytest.raises(HTTPException, match="SOURCE_ALREADY_APPLIED"):
        apply(world, proposal, idempotency_key="matrix-operation-2")
    assert world[0].query(BudgetLine).count() == 1


@pytest.mark.parametrize("field", ["category", "source", "cash"])
def test_stale_preview_fails_atomically(world, field):
    db, _, project, contract, category, document, version = world
    proposal = preview(world)
    if field == "category":
        category.name = "Changed"
    elif field == "source":
        version.content += "changed"
    else:
        db.add(CashFlowEntry(project_id=project.id, contract_id=contract.id, direction="outflow", title="Материалы (Городец)",
                             planned_date=date(2026, 1, 31), planned_amount=Decimal("1"), currency="RUB",
                             source_document_id=document.id, source_document_version_id=version.id,
                             source_document_sha256=hashlib.sha256(version.content.encode()).hexdigest()))
    db.flush()
    with pytest.raises(HTTPException, match="SOURCE_VERSION_MISMATCH|PREVIEW_STALE"):
        apply(world, proposal)
    assert db.query(BudgetLine).count() == 0


def add_budget(world, **overrides):
    db, _, project, contract, category, document, version = world
    values = dict(project_id=project.id, contract_id=contract.id, cost_category_id=category.id,
                  category=category.name, description="Материалы (Городец)", planned_amount=Decimal("6"),
                  forecast_amount=Decimal("6"), line_kind="analytical_expense", budget_period=2026,
                  budget_revision=1, article_normalized_name="материалы (городец)", currency="RUB",
                  source_document_id=document.id, source_document_version_id=version.id,
                  source_document_sha256=hashlib.sha256(version.content.encode()).hexdigest())
    values.update(overrides)
    row = BudgetLine(**values); db.add(row); db.flush()
    return row


def test_ambiguous_exact_names_block_instead_of_selecting_first(world):
    add_budget(world); add_budget(world)
    result = preview(world)
    assert any(item["code"] == "ARTICLE_AMBIGUOUS" for item in result["conflicts"])
    with pytest.raises(HTTPException, match="PREVIEW_CONFLICTS"):
        apply(world, result)


def test_normalization_preserves_punctuation_and_city(world):
    normalize = service().normalize_article
    assert normalize("  МАТЕРИАЛЫ\u00a0（Городец） ") == "материалы (городец)"
    assert normalize("Материалы (Городец)") != normalize("Материалы Городец")
    assert normalize("Материалы (Городец)") != normalize("Материалы (Дубна)")


@pytest.mark.parametrize("overrides", [{"budget_period": 2027}, {"budget_revision": 2}, {"currency": "USD"}])
def test_matching_never_crosses_period_revision_or_currency(world, overrides):
    add_budget(world, **overrides)
    result = preview(world)
    assert result["articles"][0]["budget_line_id"] is None


def test_existing_compatible_budget_is_reused_without_adding_money(world):
    line = add_budget(world)
    result = apply(world, preview(world, mode="import_forecast"), mode="import_forecast")
    assert result["created_budget_ids"] == [] and result["used_budget_ids"] == [line.id]
    assert line.planned_amount == Decimal("6")


def test_existing_cash_rows_are_preview_only_and_keep_manual_changes(world):
    db, _, project, contract, _, document, version = world
    line = add_budget(world)
    row = CashFlowEntry(project_id=project.id, contract_id=contract.id, direction="outflow", title="Материалы (Городец)",
                        planned_date=date(2026, 1, 31), planned_amount=Decimal("0.98"), currency="RUB",
                        source_document_id=document.id, source_document_version_id=version.id,
                        source_document_sha256=hashlib.sha256(version.content.encode()).hexdigest(),
                        record_version=2, source_name="ДДC.xlsx, C2")
    db.add(row); db.flush()
    result = preview(world)
    match = result["existing_rows"][0]
    assert match["id"] == row.id and match["proposed_budget_line_id"] == line.id
    assert match["amount"] == "0.98" and match["source_difference"] == "-0.02"
    assert match["record_version"] == 2 and match["apply_allowed"] is False
    apply(world, result)
    assert row.budget_line_id is None and row.planned_amount == Decimal("0.98") and row.status == "proposed"


def test_forecast_import_refuses_duplicate_existing_cash_rows(world):
    db, _, project, contract, _, document, version = world
    db.add(CashFlowEntry(project_id=project.id, contract_id=contract.id, direction="outflow", title="Материалы (Городец)",
                        planned_date=date(2026, 1, 31), planned_amount=Decimal("1"), currency="RUB",
                        source_document_id=document.id, source_document_version_id=version.id,
                        source_document_sha256=hashlib.sha256(version.content.encode()).hexdigest()))
    db.flush()
    result = preview(world, mode="import_forecast")
    assert any(item["code"] == "EXISTING_FORECAST_ROWS" for item in result["conflicts"])
    with pytest.raises(HTTPException, match="PREVIEW_CONFLICTS"):
        apply(world, result, mode="import_forecast")
    assert db.query(BudgetLine).count() == 0


def test_undo_rejects_only_own_created_proposals_preserving_history(world):
    db, user = world[:2]
    result = apply(world, preview(world, mode="import_forecast"), mode="import_forecast")
    undone = service().undo_article_budget(result["operation_id"], db, user)
    assert undone["undone"] is True
    assert db.get(BudgetLine, result["created_budget_ids"][0]).status == "rejected"
    assert all(row.status == "cancelled" for row in db.scalars(select(CashFlowEntry)))
    assert service().undo_article_budget(result["operation_id"], db, user)["replayed"] is True


def test_undo_does_not_reject_reused_budget_and_blocks_manual_change(world):
    db, user = world[:2]
    line = add_budget(world)
    result = apply(world, preview(world, mode="import_forecast"), mode="import_forecast")
    row = db.get(CashFlowEntry, result["created_cash_flow_ids"][0])
    row.planned_amount += Decimal("0.01"); db.flush()
    with pytest.raises(HTTPException, match="UNDO_DEPENDENCY_CONFLICT"):
        service().undo_article_budget(result["operation_id"], db, user)
    assert line.status == "proposed" and row.status == "proposed"


def test_unknown_budget_type_and_stale_source_block_matching(world):
    line = add_budget(world, line_kind="unknown")
    assert any(item["code"] == "UNKNOWN_BUDGET_TYPE" for item in preview(world)["conflicts"])
    line.line_kind = "analytical_expense"
    world[6].content += "changed"; world[0].flush()
    assert any(item["code"] == "BUDGET_SOURCE_STALE" for item in preview(world)["conflicts"])


def test_analytical_totals_exclude_contract_control_and_older_revisions(world):
    from app.api.execution_finance import overview
    add_budget(world, status="approved")
    add_budget(world, status="approved", budget_revision=2, planned_amount=Decimal("7"), forecast_amount=Decimal("7"))
    add_budget(world, line_kind="contract_control", status="approved", planned_amount=Decimal("100"), forecast_amount=Decimal("100"))
    result = overview(world[2].id, world[0], world[1])
    assert result["summary"]["budget_planned"] == Decimal("7.00")


def test_duplicate_confirmed_article_never_doubles_the_summary(world):
    from app.api.execution_finance import overview
    add_budget(world, status="approved")
    add_budget(world, status="approved")
    with pytest.raises(HTTPException, match="ARTICLE_AMBIGUOUS"):
        overview(world[2].id, world[0], world[1])


def test_closed_article_is_not_reused_for_new_forecasts(world):
    add_budget(world, status="closed")
    result = preview(world, mode="import_forecast")
    assert any(item["code"] == "BUDGET_STATUS_UNSUPPORTED" for item in result["conflicts"])


def test_proposed_forecasts_never_contribute_to_any_confirmed_snapshot_view(world):
    from app.cash_flow_snapshot import read_snapshot
    apply(world, preview(world, mode="import_forecast"), mode="import_forecast")
    result = read_snapshot(world[0], project_id=world[2].id, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
    assert result["details"] == []
    assert all(value["amount_minor"] == "0" for component in result["summary"]["totals"].values()
               for value in component.values())


def test_undo_keeps_shared_budget_unchanged(world):
    line = add_budget(world)
    result = apply(world, preview(world, mode="import_forecast"), mode="import_forecast")
    service().undo_article_budget(result["operation_id"], world[0], world[1])
    assert line.status == "proposed" and line.planned_amount == Decimal("6.00")


def test_26_article_creation_preserves_every_monthly_sum_and_annual_warning(world):
    db, _, _, _, category, _, version = world
    header = "Статья\tГодовой итог\tянварь\tфевраль\tмарт\n"
    version.content = header + "".join(f"Статья {index}\t5\t1.004\t2.004\t3.004\n" for index in range(26))
    categories = {index + 2: category.id for index in range(26)}
    db.flush()
    proposal = preview(world, category_by_article=categories, mode="import_forecast")
    assert len(proposal["articles"]) == 26 and len(proposal["warnings"]) == 26
    result = apply(world, proposal, category_by_article=categories, mode="import_forecast")
    assert len(result["created_budget_ids"]) == 26 and len(result["created_cash_flow_ids"]) == 78
    for line in db.scalars(select(BudgetLine)):
        rows = list(db.scalars(select(CashFlowEntry).where(CashFlowEntry.budget_line_id == line.id)))
        assert line.planned_amount == sum((row.planned_amount for row in rows), Decimal("0")) == Decimal("6.01")


def test_forecast_is_not_commitment_and_cannot_be_paid_as_an_invoice(world):
    from app.api.execution_finance import _linked_budget_committed, confirm_payment, PaymentConfirmation
    result = apply(world, preview(world, mode="import_forecast"), mode="import_forecast")
    row = world[0].get(CashFlowEntry, result["created_cash_flow_ids"][0])
    row.status = "approved"; world[0].flush()
    assert _linked_budget_committed([row]) == Decimal("0")
    with pytest.raises(HTTPException, match="FORECAST_CONVERSION_REQUIRED"):
        confirm_payment(row.id, PaymentConfirmation(actual_amount=Decimal("1"), actual_date=date(2026, 1, 31)), world[0], world[1])


def test_all_77_existing_rows_remain_byte_for_byte_semantically_unchanged(world):
    db, _, project, contract, _, document, version = world
    for identifier in range(1, 78):
        db.add(CashFlowEntry(id=identifier, project_id=project.id, contract_id=contract.id,
                            direction="outflow", title="Материалы (Городец)",
                            planned_date=date(2026, 1, 31), planned_amount=Decimal("0.98" if identifier == 29 else "1.00"),
                            source_document_id=document.id, source_document_version_id=version.id,
                            source_document_sha256=hashlib.sha256(version.content.encode()).hexdigest(),
                            record_version=2 if identifier == 29 else 1, source_name="ДДC.xlsx, C2"))
    db.flush()
    before = [service()._snapshot(row) for row in db.scalars(select(CashFlowEntry).order_by(CashFlowEntry.id))]
    proposal = preview(world)
    assert len(proposal["existing_rows"]) == 77
    result = apply(world, proposal)
    after = [service()._snapshot(row) for row in db.scalars(select(CashFlowEntry).order_by(CashFlowEntry.id))]
    assert before == after and result["existing_rows_changed"] == 0
    assert db.get(CashFlowEntry, 29).planned_amount == Decimal("0.98")


def test_all_four_snapshot_views_share_monthly_and_annual_forecast_totals(world):
    from app.cash_flow_snapshot import build_snapshot
    result = apply(world, preview(world, mode="import_forecast"), mode="import_forecast")
    rows = [service()._snapshot(world[0].get(CashFlowEntry, identifier)) for identifier in result["created_cash_flow_ids"]]
    # Hypothetical approved projection, in memory only; this does not bypass an approval API.
    for row in rows:
        row["planned_date"] = date.fromisoformat(row["planned_date"])
        row["status"] = "approved"
    snapshot = build_snapshot(project_id=world[2].id, currency="RUB", revision=1, rows=rows,
                              date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
    assert snapshot["summary"]["totals"]["plan"]["outflow"]["amount_minor"] == "600"
    assert sum(int(month["totals"]["plan"]["outflow"]["amount_minor"]) for month in snapshot["months"]) == 600
    assert sum(int(day["totals"]["plan"]["outflow"]["amount_minor"]) for day in snapshot["calendar"]) == 600
    assert len(snapshot["details"]) == 3


@pytest.mark.parametrize("overrides", [{}, {2003: {"direction": "inflow"}}])
def test_old_matrix_route_cannot_create_expenses_without_article_budget(world, overrides):
    from app.api.execution_finance import structured_import, StructuredImportRequest
    with pytest.raises(HTTPException, match="ARTICLE_BUDGET_PREVIEW_REQUIRED"):
        structured_import(world[5].id, StructuredImportRequest(project_id=world[2].id,
                          contract_id=world[3].id, kind="cash-flow", plan_year=2026,
                          source_rows=[2003], row_overrides=overrides), world[0], world[1])
    assert world[0].query(CashFlowEntry).count() == 0
