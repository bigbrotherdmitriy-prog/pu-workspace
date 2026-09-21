from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api.organizations_contracts import (
    ContractBudgetProposalUpdate,
    confirm_contract_budget_proposal,
    create_contract_budget_proposal,
    reject_contract_budget_proposal,
    update_contract_budget_proposal,
)
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import BudgetLine, ContractBudgetProposal, CostCategory
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


def _world(db, user_factory, *, role="manager", kind="supply", amount="1000.00", with_document=False):
    organization = Organization(name="Contract budget tenant")
    user = user_factory()
    db.add(organization); db.flush()
    project = Project(name="Contract budget project", organization_id=organization.id)
    db.add(project); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    document = None
    version = None
    if with_document:
        document = Document(project_id=project.id, name="Contract.pdf", source="local_upload", status="ready", current_version=1)
        db.add(document); db.flush()
        version = DocumentVersion(document_id=document.id, version_number=1, content="Contract amount 1000 RUB")
        db.add(version); db.flush()
    contract = Contract(
        project_id=project.id, number="C-1", title="Supply", contract_kind=kind,
        amount=Decimal(amount) if amount is not None else None,
        advance_amount=Decimal("200.00"), retention_percent=Decimal("5.00"),
        source_document_id=document.id if document else None, status="active",
    )
    category = CostCategory(
        organization_id=organization.id, name="Materials", normalized_name="materials", is_active=True,
    )
    db.add_all([contract, category]); db.flush()
    return user, project, contract, category, document, version


def _proposal(db, user, project, contract):
    return create_contract_budget_proposal(project.id, contract.id, db, user)


def _select_category(db, user, proposal_id, category_id, **overrides):
    return update_contract_budget_proposal(
        proposal_id,
        ContractBudgetProposalUpdate(selected_cost_category_id=category_id, **overrides),
        db, user,
    )


def test_01_contract_save_does_not_create_budget(db_session, user_factory):
    _user, _project, _contract, _category, _document, _version = _world(db_session, user_factory)
    assert db_session.query(BudgetLine).count() == 0
    assert db_session.query(ContractBudgetProposal).count() == 0


def test_02_proposal_is_one_total_amount_not_advance_or_retention(db_session, user_factory):
    user, project, contract, _category, _document, _version = _world(db_session, user_factory)
    result = _proposal(db_session, user, project, contract)
    assert result["amount"] == Decimal("1000.00")
    assert db_session.query(ContractBudgetProposal).count() == 1
    assert db_session.query(BudgetLine).count() == 0


def test_03_advance_and_retention_are_informational_snapshot(db_session, user_factory):
    user, project, contract, _category, _document, _version = _world(db_session, user_factory)
    result = _proposal(db_session, user, project, contract)
    assert result["advance_amount"] == Decimal("200.00")
    assert result["retention_percent"] == Decimal("5.00")


def test_04_contract_without_positive_amount_cannot_propose(db_session, user_factory):
    user, project, contract, _category, _document, _version = _world(db_session, user_factory, amount=None)
    with pytest.raises(HTTPException) as error:
        _proposal(db_session, user, project, contract)
    assert error.value.status_code == 422


def test_05_prime_reference_cannot_propose_budget(db_session, user_factory):
    user, project, contract, _category, _document, _version = _world(db_session, user_factory, kind="prime_reference")
    with pytest.raises(HTTPException) as error:
        _proposal(db_session, user, project, contract)
    assert error.value.status_code == 422


def test_06_manual_contract_proposal_has_no_document_pin(db_session, user_factory):
    user, project, contract, _category, _document, _version = _world(db_session, user_factory)
    result = _proposal(db_session, user, project, contract)
    assert result["source_document_id"] is None
    assert result["source_document_version_id"] is None
    assert result["source_document_sha256"] is None


def test_07_document_contract_proposal_pins_exact_version_and_hash(db_session, user_factory):
    user, project, contract, _category, document, version = _world(db_session, user_factory, with_document=True)
    result = _proposal(db_session, user, project, contract)
    assert result["source_document_id"] == document.id
    assert result["source_document_version_id"] == version.id
    assert len(result["source_document_sha256"]) == 64


def test_08_missing_document_version_returns_source_pin_missing(db_session, user_factory):
    user, project, contract, _category, document, version = _world(db_session, user_factory, with_document=True)
    db_session.delete(version); db_session.flush()
    with pytest.raises(HTTPException) as error:
        _proposal(db_session, user, project, contract)
    assert error.value.status_code == 409
    assert "SOURCE_PIN_MISSING" in error.value.detail


def test_09_changed_document_blocks_confirmation(db_session, user_factory):
    user, project, contract, category, document, _version = _world(db_session, user_factory, with_document=True)
    proposal = _proposal(db_session, user, project, contract)
    _select_category(db_session, user, proposal["id"], category.id)
    document.current_version = 2
    db_session.add(DocumentVersion(document_id=document.id, version_number=2, content="Changed terms")); db_session.flush()
    with pytest.raises(HTTPException) as error:
        confirm_contract_budget_proposal(proposal["id"], db_session, user)
    assert error.value.status_code == 409
    assert "SOURCE_VERSION_MISMATCH" in error.value.detail


def test_10_changed_contract_record_version_blocks_confirmation(db_session, user_factory):
    user, project, contract, category, _document, _version = _world(db_session, user_factory)
    proposal = _proposal(db_session, user, project, contract)
    _select_category(db_session, user, proposal["id"], category.id)
    contract.record_version += 1; db_session.flush()
    with pytest.raises(HTTPException) as error:
        confirm_contract_budget_proposal(proposal["id"], db_session, user)
    assert error.value.status_code == 409
    assert "CONTRACT_VERSION_MISMATCH" in error.value.detail


def test_11_editor_can_propose_but_cannot_confirm_or_reject(db_session, user_factory):
    editor, project, contract, category, _document, _version = _world(db_session, user_factory, role="editor")
    proposal = _proposal(db_session, editor, project, contract)
    for action in (confirm_contract_budget_proposal, reject_contract_budget_proposal):
        with pytest.raises(HTTPException) as error:
            action(proposal["id"], db_session, editor)
        assert error.value.status_code == 403


def test_12_tenant_isolation_hides_another_project_contract(db_session, user_factory):
    user, project, _contract, _category, _document, _version = _world(db_session, user_factory)
    _other_user, _other_project, other_contract, _other_category, _document, _version = _world(db_session, user_factory)
    with pytest.raises(HTTPException) as error:
        create_contract_budget_proposal(project.id, other_contract.id, db_session, user)
    assert error.value.status_code == 404


def test_13_manager_confirm_creates_exactly_one_proposed_budget_line(db_session, user_factory):
    user, project, contract, category, document, version = _world(db_session, user_factory, with_document=True)
    proposal = _proposal(db_session, user, project, contract)
    _select_category(db_session, user, proposal["id"], category.id, description="Contract total")
    result = confirm_contract_budget_proposal(proposal["id"], db_session, user)
    budget = db_session.get(BudgetLine, result["created_budget_line_id"])
    assert db_session.query(BudgetLine).count() == 1
    assert (budget.planned_amount, budget.forecast_amount, budget.status) == (Decimal("1000.00"), Decimal("1000.00"), "proposed")
    assert (budget.committed_amount, budget.actual_amount) == (Decimal("0.00"), Decimal("0.00"))
    assert (budget.source_document_id, budget.source_document_version_id) == (document.id, version.id)
    assert len(budget.source_document_sha256) == 64


def test_14_confirmation_replay_is_idempotent(db_session, user_factory):
    user, project, contract, category, _document, _version = _world(db_session, user_factory)
    proposal = _proposal(db_session, user, project, contract)
    _select_category(db_session, user, proposal["id"], category.id)
    first = confirm_contract_budget_proposal(proposal["id"], db_session, user)
    second = confirm_contract_budget_proposal(proposal["id"], db_session, user)
    assert first["created_budget_line_id"] == second["created_budget_line_id"]
    assert db_session.query(BudgetLine).count() == 1


def test_15_renegotiation_revises_line_without_losing_committed_or_actual(db_session, user_factory):
    user, project, contract, category, _document, _version = _world(db_session, user_factory)
    first = _proposal(db_session, user, project, contract)
    _select_category(db_session, user, first["id"], category.id)
    confirmed = confirm_contract_budget_proposal(first["id"], db_session, user)
    budget = db_session.get(BudgetLine, confirmed["created_budget_line_id"])
    budget.committed_amount = Decimal("300.00"); budget.actual_amount = Decimal("250.00")
    contract.amount = Decimal("1400.00"); contract.record_version += 1; db_session.flush()
    revised = _proposal(db_session, user, project, contract)
    assert revised["operation"] == "revise"
    _select_category(db_session, user, revised["id"], category.id)
    result = confirm_contract_budget_proposal(revised["id"], db_session, user)
    db_session.refresh(budget)
    assert result["created_budget_line_id"] == budget.id
    assert db_session.query(BudgetLine).count() == 1
    assert (budget.planned_amount, budget.forecast_amount) == (Decimal("1400.00"), Decimal("1400.00"))
    assert (budget.committed_amount, budget.actual_amount) == (Decimal("300.00"), Decimal("250.00"))


def test_16_rejection_creates_no_budget_line(db_session, user_factory):
    user, project, contract, _category, _document, _version = _world(db_session, user_factory)
    proposal = _proposal(db_session, user, project, contract)
    result = reject_contract_budget_proposal(proposal["id"], db_session, user)
    assert result["status"] == "rejected"
    assert db_session.query(BudgetLine).count() == 0


def test_17_edit_and_revision_audits_contain_before_after(db_session, user_factory):
    user, project, contract, category, _document, _version = _world(db_session, user_factory)
    first = _proposal(db_session, user, project, contract)
    _select_category(db_session, user, first["id"], category.id, amount=Decimal("1100.00"))
    confirm_contract_budget_proposal(first["id"], db_session, user)
    contract.amount = Decimal("1200.00"); contract.record_version += 1; db_session.flush()
    revised = _proposal(db_session, user, project, contract)
    _select_category(db_session, user, revised["id"], category.id)
    confirm_contract_budget_proposal(revised["id"], db_session, user)
    audit = db_session.query(AuditLog).filter(
        AuditLog.action == "contract_budget_confirmed", AuditLog.entity_id == revised["id"],
    ).one()
    assert "before=" in audit.details and "after=" in audit.details
