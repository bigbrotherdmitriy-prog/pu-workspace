"""Synthetic §27.1 forecasts and all-or-nothing DDS confirmation."""
import hashlib
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import execution_finance as api
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import BudgetLine, CashFlowEntry, PaymentEvent, ScheduleBaseline, ScheduleItem
from app.models.organization_contract import Contract, Organization
from app.models.project import Project


def build_world(db, user):
    year = date.today().year
    org = Organization(name="Synthetic confirmation organization")
    db.add(org); db.flush()
    project = Project(name="Synthetic confirmation project", organization_id=org.id, currency="RUB")
    db.add(project); db.flush()
    contract = Contract(project_id=project.id, number="TEST-CONFIRM-001", title="Synthetic contract")
    document = Document(project_id=project.id, name="matrix.xlsx", current_version=1, source="local_upload")
    db.add_all([contract, document]); db.flush()
    content = "Статья\tГод\tянварь\tфевраль\tмарт\nМатериалы\t3\t1\t2\t0\n"
    version = DocumentVersion(document_id=document.id, version_number=1, content=content)
    db.add(version); db.flush()
    digest = hashlib.sha256(content.encode()).hexdigest()
    budget = BudgetLine(project_id=project.id, contract_id=contract.id, category="Прямые", description="Материалы",
        line_kind="analytical_expense", budget_period=year, budget_revision=1, article_normalized_name="материалы",
        planned_amount=Decimal("3"), forecast_amount=Decimal("3"), currency="RUB", status="approved",
        source_document_id=document.id, source_document_version_id=version.id, source_document_sha256=digest)
    db.add(budget); db.flush()
    row = CashFlowEntry(project_id=project.id, contract_id=contract.id, budget_line_id=budget.id,
        direction="outflow", title="Материалы", planned_date=date(year, 1, 31), planned_amount=Decimal("1"),
        currency="RUB", status="proposed", entry_kind="plan_forecast", source_document_id=document.id,
        source_document_version_id=version.id, source_document_sha256=digest, source_name="matrix.xlsx, C2")
    db.add(row); db.commit()
    return SimpleNamespace(db=db, user=user, org=org, project=project, contract=contract, document=document,
                           version=version, budget=budget, row=row, year=year)


@pytest.fixture
def world(db_session, user_factory):
    return build_world(db_session, user_factory(is_admin=True))


def batch(w, rows):
    return api.CashFlowBatchConfirmation(project_id=w.project.id,
        items=[dict(id=r.id, expected_record_version=r.record_version) for r in rows])


def test_matrix_forecast_confirms_without_schedule_and_is_not_a_commitment(world):
    w = world
    before = (w.row.planned_amount, w.row.planned_date, w.row.actual_amount, w.row.actual_date)
    result = api.update_status("cash-flow", w.row.id, api.StatusUpdate(status="approved"), w.db, w.user)
    assert result["status"] == "approved"
    assert w.row.entry_kind == "plan_forecast" and w.row.schedule_item_id is None
    assert before == (w.row.planned_amount, w.row.planned_date, w.row.actual_amount, w.row.actual_date)
    assert w.budget.committed_amount == 0 and w.budget.actual_amount == 0
    summary = api.overview(w.project.id, w.db, w.user)["summary"]
    assert summary["pending_payments"] == summary["unlinked_invoices"] == 0


def test_forecast_cannot_become_invoice_without_schedule(world):
    w = world
    with pytest.raises(HTTPException) as error:
        api.convert_forecast_to_invoice(w.row.id,
            api.ForecastInvoiceConversion(expected_record_version=w.row.record_version), w.db, w.user)
    assert error.value.status_code == 422
    assert "CONTROL_CHAIN_REQUIRED" in str(error.value.detail)
    assert w.row.entry_kind == "plan_forecast" and w.row.status == "proposed"
    assert w.db.query(PaymentEvent).count() == 0


@pytest.mark.parametrize("direction", ["outflow", "inflow"])
def test_ordinary_invoice_without_schedule_is_still_rejected(world, direction):
    w = world
    w.row.entry_kind = "invoice_commitment"
    w.row.direction = direction
    w.row.source_document_id = None
    w.row.source_document_version_id = None
    w.row.source_document_sha256 = None
    w.db.commit()
    with pytest.raises(HTTPException) as error:
        api.update_status("cash-flow", w.row.id, api.StatusUpdate(status="approved"), w.db, w.user)
    assert error.value.status_code == 422
    assert w.db.get(CashFlowEntry, w.row.id).status == "proposed"


def test_batch_88_with_one_invalid_row_confirms_none_and_reports_every_row(world):
    w = world
    rows = [w.row]
    for index in range(87):
        r = CashFlowEntry(project_id=w.project.id, contract_id=w.contract.id, title=f"Synthetic inflow {index}",
            direction="inflow", planned_date=date(w.year, 2, 28), planned_amount=Decimal("1"), currency="RUB")
        w.db.add(r); rows.append(r)
    rows[-1].direction = "outflow"
    rows[-1].entry_kind = "invoice_commitment"
    w.db.commit()
    ids = [r.id for r in rows]
    with pytest.raises(HTTPException) as error:
        api.confirm_cash_flow_batch(batch(w, rows), w.db, w.user)
    assert error.value.status_code == 422
    detail = error.value.detail
    assert detail["code"] == "CASH_FLOW_BATCH_REJECTED" and detail["confirmed_count"] == 0
    assert len(detail["rows"]) == 88
    assert [r["id"] for r in detail["rows"] if r["code"] != "NOT_APPLIED"] == [ids[-1]]
    w.db.expire_all()
    assert all(w.db.get(CashFlowEntry, identifier).status == "proposed" for identifier in ids)
    assert all(w.db.get(CashFlowEntry, identifier).record_version == 1 for identifier in ids)
    assert w.db.query(AuditLog).count() == 0
    assert w.db.query(PaymentEvent).count() == 0 and w.budget.committed_amount == 0


def test_proven_legacy_matrix_is_classified_only_on_confirmation(world):
    w = world
    w.row.entry_kind = "legacy_unclassified"; w.db.commit()
    result = api.overview(w.project.id, w.db, w.user)
    candidate = next(r for r in result["cash_flow"] if r["id"] == w.row.id)
    assert candidate["confirmation_allowed"] is True and candidate["confirmation_kind"] == "plan_forecast"
    assert w.row.entry_kind == "legacy_unclassified" and w.row.status == "proposed"
    result = api.confirm_cash_flow_batch(batch(w, [w.row]), w.db, w.user)
    assert result["confirmed_count"] == 1
    assert w.row.entry_kind == "plan_forecast" and w.row.matrix_month == 1
    assert w.row.status == "approved" and w.budget.committed_amount == 0


@pytest.mark.parametrize("field,value", [("source_document_sha256", "0" * 64), ("source_name", "matrix.xlsx, Z99"),
                                         ("contract_id", None), ("budget_line_id", None)])
def test_forecast_exception_requires_provenance_contract_and_budget(world, field, value):
    w = world
    setattr(w.row, field, value); w.db.commit()
    with pytest.raises(HTTPException):
        api.update_status("cash-flow", w.row.id, api.StatusUpdate(status="approved"), w.db, w.user)
    assert w.db.get(CashFlowEntry, w.row.id).status == "proposed"


def test_batch_stale_version_rejects_without_changes(world):
    w = world
    request = batch(w, [w.row])
    w.row.record_version += 1; w.db.commit()
    with pytest.raises(HTTPException) as error:
        api.confirm_cash_flow_batch(request, w.db, w.user)
    assert error.value.status_code == 409
    assert error.value.detail["rows"][0]["code"] == "CASH_FLOW_VERSION_MISMATCH"
    assert w.row.status == "proposed" and w.db.query(AuditLog).count() == 0


def test_batch_success_keeps_amounts_dates_and_writes_one_audit_per_row(world):
    w = world
    income = CashFlowEntry(project_id=w.project.id, contract_id=w.contract.id, direction="inflow", title="Synthetic receipt",
        planned_date=date(w.year, 1, 31), planned_amount=Decimal("10"), currency="RUB")
    w.db.add(income); w.db.commit()
    result = api.confirm_cash_flow_batch(batch(w, [w.row, income]), w.db, w.user)
    assert result["confirmed_count"] == 2 and result["atomic"] is True
    assert all(r["status"] == "approved" for r in result["rows"])
    assert w.db.query(AuditLog).filter_by(action="cash-flow_status_updated").count() == 2
    assert w.db.query(PaymentEvent).count() == 0 and w.budget.committed_amount == 0


def test_ordinary_invoice_creation_requires_schedule(world):
    w = world
    with pytest.raises(HTTPException) as error:
        api.create_invoice_proposal(api.InvoiceProposalCreate(project_id=w.project.id, contract_id=w.contract.id,
            budget_line_id=w.budget.id, direction="outflow", title="Synthetic invoice", planned_date=w.row.planned_date,
            planned_amount=Decimal("1")), w.db, w.user)
    assert error.value.status_code == 422


def test_forecast_cannot_be_paid_directly(world):
    w = world
    with pytest.raises(HTTPException, match="FORECAST_CONVERSION_REQUIRED"):
        api.confirm_payment(w.row.id, api.PaymentConfirmation(actual_amount=Decimal("1")), w.db, w.user)
    assert w.db.query(PaymentEvent).count() == 0


def test_batch_write_fault_rolls_back_status_type_versions_and_audit(world, monkeypatch):
    w = world
    w.row.entry_kind = "legacy_unclassified"; w.db.commit()
    request = batch(w, [w.row])
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic-audit-fault")
    monkeypatch.setattr(api, "_audit", fail)
    with pytest.raises(HTTPException) as error:
        api.confirm_cash_flow_batch(request, w.db, w.user)
    assert error.value.status_code == 500 and error.value.detail["confirmed_count"] == 0
    w.db.expire_all()
    assert w.row.status == "proposed" and w.row.entry_kind == "legacy_unclassified"
    assert w.row.record_version == 1 and w.db.query(AuditLog).count() == 0


def test_batch_cross_project_id_rejects_every_row_without_exposing_other_project(world):
    w = world
    other = Project(name="Synthetic other project", organization_id=w.org.id, currency="RUB")
    w.db.add(other); w.db.flush()
    row = CashFlowEntry(project_id=other.id, title="Private synthetic row", direction="inflow",
        planned_date=date(w.year, 1, 31), planned_amount=Decimal("1"), currency="RUB")
    w.db.add(row); w.db.commit()
    with pytest.raises(HTTPException) as error:
        api.confirm_cash_flow_batch(batch(w, [w.row, row]), w.db, w.user)
    assert error.value.detail["rows"][1]["code"] == "CASH_FLOW_NOT_FOUND"
    assert "Private synthetic row" not in str(error.value.detail)
    assert w.row.status == row.status == "proposed"


def test_non_matrix_source_cannot_claim_forecast_exception(world):
    w = world
    w.version.content = "Synthetic invoice, not a monthly matrix"
    w.row.source_document_sha256 = hashlib.sha256(w.version.content.encode()).hexdigest()
    w.db.commit()
    with pytest.raises(HTTPException, match="MATRIX_PROVENANCE_REQUIRED"):
        api.update_status("cash-flow", w.row.id, api.StatusUpdate(status="approved"), w.db, w.user)
    assert w.row.status == "proposed"


def test_explicit_invoice_conversion_requires_full_chain_and_preserves_money(world):
    w = world
    api.update_status("cash-flow", w.row.id, api.StatusUpdate(status="approved"), w.db, w.user)
    baseline = ScheduleBaseline(project_id=w.project.id, contract_id=w.contract.id, created_by_user_id=w.user.id,
        name="Synthetic schedule", version=1)
    w.db.add(baseline); w.db.flush()
    schedule = ScheduleItem(project_id=w.project.id, baseline_id=baseline.id, title="Synthetic stage")
    w.db.add(schedule); w.db.commit()
    before = (w.row.planned_amount, w.row.planned_date, w.row.actual_amount, w.row.actual_date)
    result = api.convert_forecast_to_invoice(w.row.id, api.ForecastInvoiceConversion(
        expected_record_version=w.row.record_version, schedule_item_id=schedule.id), w.db, w.user)
    assert result["entry_kind"] == "invoice_commitment" and w.row.schedule_item_id == schedule.id
    assert before == (w.row.planned_amount, w.row.planned_date, w.row.actual_amount, w.row.actual_date)
    assert w.budget.committed_amount == Decimal("1")
    w.row.schedule_item_id = None; w.db.commit()
    with pytest.raises(HTTPException) as error:
        api.confirm_payment(w.row.id, api.PaymentConfirmation(actual_amount=Decimal("1")), w.db, w.user)
    assert error.value.status_code == 422 and w.db.query(PaymentEvent).count() == 0


def test_batch_endpoint_and_conversion_are_registered():
    paths = {r.path for r in api.router.routes}
    assert "/execution/cash-flow/confirm-batch" in paths
    assert "/execution/cash-flow/{item_id}/convert-to-invoice" in paths


def test_unclassified_matrix_cannot_skip_forecast_confirmation_and_be_paid(world):
    w = world
    w.row.entry_kind = "legacy_unclassified"; w.db.commit()
    with pytest.raises(HTTPException, match="FORECAST_CONVERSION_REQUIRED"):
        api.confirm_payment(w.row.id, api.PaymentConfirmation(actual_amount=Decimal("1")), w.db, w.user)
    assert w.row.status == "proposed" and w.db.query(PaymentEvent).count() == 0


def test_manual_unclassified_outflow_has_no_forecast_exception(world):
    w = world
    w.row.entry_kind = "legacy_unclassified"
    w.row.source_document_id = w.row.source_document_version_id = w.row.source_document_sha256 = None
    w.db.commit()
    with pytest.raises(HTTPException) as error:
        api.update_status("cash-flow", w.row.id, api.StatusUpdate(status="approved"), w.db, w.user)
    assert error.value.status_code == 422 and w.row.status == "proposed"
