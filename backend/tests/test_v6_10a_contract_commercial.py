"""Synthetic behavior gates for the first commercial-fields increment."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api import organizations_contracts as api
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import BudgetLine, ContractBudgetProposal, CostCategory
from app.models.organization_contract import Contract, ContractVersion, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


def _world(db, user_factory):
    user = user_factory()
    organization = Organization(name="Synthetic commercial tenant")
    db.add(organization); db.flush()
    project = Project(name="Synthetic commercial project", organization_id=organization.id)
    db.add(project); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
    db.commit()
    return user, project


def _create(db, user, project, **values):
    return api.create_contract(project.id, api.ContractCreate(
        number="SYNTHETIC-1", title="Synthetic commercial contract", status="draft", **values,
    ), db, user)


@pytest.mark.parametrize("mode,rate", [("unspecified", None), ("none", None), ("rate", "0.00"), ("rate", "22.00")])
def test_create_preserves_three_vat_states_and_zero_rate(db_session, user_factory, mode, rate):
    user, project = _world(db_session, user_factory)
    result = _create(db_session, user, project, amount="0", vat_mode=mode, vat_rate=rate)
    assert result["vat_mode"] == mode
    assert result["vat_rate"] == rate
    assert result["amount"] == "0.00"
    assert result["version_history"][-1]["snapshot"]["vat_mode"] == mode
    assert result["version_history"][-1]["snapshot"]["vat_rate"] == rate


@pytest.mark.parametrize("values", [
    {"vat_mode": "rate"}, {"vat_mode": "none", "vat_rate": "0"},
    {"vat_mode": "unspecified", "vat_rate": "22"},
    {"amount": "0", "advance_amount": "1"},
    {"amount": "100", "advance_amount": "101"},
    {"performed_from": "2026-10-02", "performed_to": "2026-10-01"},
    {"signed_at": "2999-01-01"},
])
def test_invalid_create_has_no_record_or_history(db_session, user_factory, values):
    user, project = _world(db_session, user_factory)
    with pytest.raises((HTTPException, ValidationError)):
        _create(db_session, user, project, **values)
    assert db_session.query(Contract).count() == 0
    assert db_session.query(ContractVersion).count() == 0


@pytest.mark.parametrize("rate", ["-0.01", "100.01", "1.001", "NaN", "Infinity"])
def test_vat_rate_has_finite_two_decimal_precision_and_range(rate):
    with pytest.raises(ValidationError):
        api.ContractCreate(number="S-1", title="Synthetic", vat_mode="rate", vat_rate=rate)


def test_unknown_price_allows_preliminary_advance_with_warning(db_session, user_factory):
    user, project = _world(db_session, user_factory)
    result = _create(db_session, user, project, amount=None, advance_amount="200.00")
    assert result["amount"] is None
    assert result["advance_amount"] == "200.00"
    assert {"code": "CONTRACT_AMOUNT_UNSPECIFIED", "message": "сумма договора не задана"} in result["warnings"]


def test_patch_validates_final_state_without_advancing_version_or_history(db_session, user_factory):
    user, project = _world(db_session, user_factory)
    created = _create(db_session, user, project, amount="300", advance_amount="200")
    with pytest.raises(HTTPException) as error:
        api.update_contract_links(project.id, created["id"], api.ContractLinkUpdate(
            expected_record_version=created["record_version"], amount="100",
        ), db_session, user)
    assert error.value.status_code == 422
    row = db_session.get(Contract, created["id"])
    assert row.amount == Decimal("300.00")
    assert row.record_version == created["record_version"]
    assert db_session.query(ContractVersion).count() == 1


def test_patch_vat_dates_warranty_and_history_with_explicit_null(db_session, user_factory):
    user, project = _world(db_session, user_factory)
    created = _create(db_session, user, project, amount="300", vat_mode="rate", vat_rate="22")
    saved = api.update_contract_links(project.id, created["id"], api.ContractLinkUpdate(
        expected_record_version=created["record_version"], vat_mode="none", vat_rate=None,
        performed_from="2026-01-01", performed_to="2026-12-31", warranty_until="2027-12-31",
    ), db_session, user)
    assert (saved["vat_mode"], saved["vat_rate"]) == ("none", None)
    assert (saved["performed_from"], saved["performed_to"], saved["warranty_until"]) == (
        date(2026, 1, 1), date(2026, 12, 31), date(2027, 12, 31),
    )
    snapshot = saved["version_history"][-1]["snapshot"]
    assert snapshot["vat_mode"] == "none"
    assert snapshot["performed_to"] == "2026-12-31"
    assert snapshot["warranty_until"] == "2027-12-31"
    with pytest.raises(HTTPException):
        api.update_contract_links(project.id, created["id"], api.ContractLinkUpdate(
            expected_record_version=saved["record_version"], performed_to="2025-12-31",
        ), db_session, user)


def test_patch_vat_mode_cannot_keep_a_stale_rate(db_session, user_factory):
    user, project = _world(db_session, user_factory)
    created = _create(db_session, user, project, vat_mode="rate", vat_rate="22")
    with pytest.raises(HTTPException):
        api.update_contract_links(project.id, created["id"], api.ContractLinkUpdate(
            expected_record_version=created["record_version"], vat_mode="none",
        ), db_session, user)


def test_server_business_date_is_moscow_day_not_host_or_browser_day(db_session, user_factory, monkeypatch):
    class ServerClock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 10, 4, 21, 30, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(api, "datetime", ServerClock)
    user, project = _world(db_session, user_factory)
    _create(db_session, user, project, signed_at="2026-10-05")
    with pytest.raises((HTTPException, ValidationError)):
        _create(db_session, user, project, signed_at="2026-10-06")


def test_budget_proposal_and_new_line_inherit_exact_vat_without_recalculation(db_session, user_factory):
    user, project = _world(db_session, user_factory)
    created = _create(db_session, user, project, amount="1000.01", vat_mode="rate", vat_rate="22")
    category = CostCategory(organization_id=project.organization_id, name="Synthetic", normalized_name="synthetic")
    db_session.add(category); db_session.commit()
    proposal = api.create_contract_budget_proposal(project.id, created["id"], db_session, user)
    expected = {"schema_version": 1, "mode": "rate", "rate": "22.00",
                "source_contract_id": created["id"], "source_contract_record_version": created["record_version"]}
    assert proposal["vat_snapshot"] == expected
    api.update_contract_budget_proposal(proposal["id"], api.ContractBudgetProposalUpdate(
        selected_cost_category_id=category.id,
    ), db_session, user)
    confirmed = api.confirm_contract_budget_proposal(proposal["id"], db_session, user)
    budget = db_session.get(BudgetLine, confirmed["created_budget_line_id"])
    assert budget.vat_snapshot == expected
    assert budget.planned_amount == Decimal("1000.01")
    api.update_contract_links(project.id, created["id"], api.ContractLinkUpdate(
        expected_record_version=created["record_version"], vat_rate="0",
    ), db_session, user)
    assert budget.vat_snapshot == expected
    assert db_session.get(ContractBudgetProposal, proposal["id"]).vat_snapshot == expected


def test_legacy_financial_snapshot_is_sql_null(db_session, user_factory):
    _user, project = _world(db_session, user_factory)
    budget = BudgetLine(project_id=project.id, category="Synthetic", description="Legacy", planned_amount=1,
                        forecast_amount=1, vat_snapshot=None)
    db_session.add(budget); db_session.flush()
    assert db_session.scalar(text("SELECT vat_snapshot IS NULL FROM budget_lines WHERE id=:id"), {"id": budget.id}) == 1


@pytest.mark.parametrize("values", [{"vat_mode": "rate", "vat_rate": None}, {"vat_mode": "none", "vat_rate": 0},
                                    {"performed_from": date(2026, 2, 1), "performed_to": date(2026, 1, 1)}])
def test_database_constraints_prevent_invalid_vat_and_performed_state(db_session, user_factory, values):
    _user, project = _world(db_session, user_factory)
    with pytest.raises(IntegrityError):
        db_session.add(Contract(project_id=project.id, number="S-DB", title="Synthetic", **values))
        db_session.flush()


@pytest.mark.parametrize("values", [{"vat_mode": "none"}, {"vat_mode": "rate", "vat_rate": Decimal("0")},
                                    {"performed_from": date(2026, 1, 1)}])
def test_new_commercial_values_make_draft_nonempty(values):
    contract = Contract(project_id=1, number="S-DRAFT", title="Synthetic", status="draft", **values)
    assert api._is_empty_contract_draft(contract) is False


def test_explicit_budget_revision_updates_vat_with_old_new_audit(db_session, user_factory):
    user, project = _world(db_session, user_factory)
    created = _create(db_session, user, project, amount="1000.01", vat_mode="rate", vat_rate="22")
    category = CostCategory(organization_id=project.organization_id, name="Synthetic", normalized_name="synthetic")
    db_session.add(category); db_session.commit()
    first = api.create_contract_budget_proposal(project.id, created["id"], db_session, user)
    api.update_contract_budget_proposal(first["id"], api.ContractBudgetProposalUpdate(selected_cost_category_id=category.id), db_session, user)
    confirmed = api.confirm_contract_budget_proposal(first["id"], db_session, user)
    budget = db_session.get(BudgetLine, confirmed["created_budget_line_id"])
    before_vat = dict(budget.vat_snapshot)
    updated = api.update_contract_links(project.id, created["id"], api.ContractLinkUpdate(
        expected_record_version=created["record_version"], vat_mode="none", vat_rate=None,
    ), db_session, user)
    assert budget.vat_snapshot == before_vat
    second = api.create_contract_budget_proposal(project.id, created["id"], db_session, user)
    api.update_contract_budget_proposal(second["id"], api.ContractBudgetProposalUpdate(selected_cost_category_id=category.id), db_session, user)
    api.confirm_contract_budget_proposal(second["id"], db_session, user)
    assert budget.vat_snapshot == second["vat_snapshot"]
    assert budget.planned_amount == Decimal("1000.01")
    audit = db_session.query(AuditLog).filter_by(action="contract_budget_confirmed", entity_id=second["id"]).one()
    assert '"vat_snapshot"' in audit.details
    assert '"rate": "22.00"' in audit.details
    assert '"mode": "none"' in audit.details
    assert budget.vat_snapshot["source_contract_record_version"] == updated["record_version"]


def test_new_payment_schedule_proposal_inherits_vat_and_reanalysis_preserves_existing(db_session, user_factory):
    user, project = _world(db_session, user_factory)
    document = Document(project_id=project.id, name="Synthetic payment.pdf", source="local_upload", status="ready", current_version=1)
    db_session.add(document); db_session.flush()
    db_session.add(DocumentVersion(document_id=document.id, version_number=1,
                                   content="Авансовый платеж до 01.06.2026 — 200,00 руб."))
    db_session.commit()
    created = _create(db_session, user, project, amount="1000", vat_mode="rate", vat_rate="22", source_document_id=document.id)
    contract = db_session.get(Contract, created["id"])
    rows = api._create_payment_schedule_proposals(db_session, contract, document)
    db_session.commit()
    expected = {"schema_version": 1, "mode": "rate", "rate": "22.00", "source_contract_id": contract.id,
                "source_contract_record_version": created["record_version"]}
    assert len(rows) == 1
    assert rows[0].vat_snapshot == expected
    assert rows[0].planned_amount == Decimal("200.00")
    api.update_contract_links(project.id, contract.id, api.ContractLinkUpdate(
        expected_record_version=created["record_version"], vat_rate="0",
    ), db_session, user)
    assert api._create_payment_schedule_proposals(db_session, contract, document) == []
    assert rows[0].vat_snapshot == expected


@pytest.mark.parametrize("amount,advance", [(None, None), ("0.00", "0.00"),
                                          ("1234567890123456.78", "123456789012345.67")])
def test_http_contract_money_is_exact_decimal_string_for_manual_draft(amount, advance):
    # Transport gate uses exact Decimal values, like PostgreSQL NUMERIC; SQLite
    # storage itself uses float for huge money and cannot prove this boundary.
    row = Contract(id=901, project_id=902, number="SYN-DECIMAL", title="Synthetic decimal transport",
                   amount=Decimal(amount) if amount is not None else None,
                   advance_amount=Decimal(advance) if advance is not None else None,
                   record_version=1, vat_mode="unspecified")
    app = FastAPI()

    @app.get("/synthetic-contract")
    def synthetic_contract():
        return api._contract(row)

    with TestClient(app) as client:
        response = client.get("/synthetic-contract")
    assert response.status_code == 200
    loaded = response.json()
    assert (loaded["amount"], loaded["advance_amount"]) == (amount, advance)
    draft = api.ContractLinkUpdate(expected_record_version=loaded["record_version"],
                                   amount=loaded["amount"], advance_amount=loaded["advance_amount"],
                                   vat_mode="none", vat_rate=None, performed_to="2027-02-03")
    assert draft.amount == row.amount
    assert draft.advance_amount == row.advance_amount


@pytest.mark.parametrize("amount,advance", [("0.00", None),
                                          ("1234567890123456.78", "123456789012345.67")])
def test_encoded_budget_proposal_money_preserves_exact_decimal_values(amount, advance):
    proposal = ContractBudgetProposal(amount=Decimal(amount),
        advance_amount=Decimal(advance) if advance is not None else None)
    encoded = jsonable_encoder(api._contract_budget_proposal(proposal))
    assert (encoded["amount"], encoded["advance_amount"]) == (amount, advance)
