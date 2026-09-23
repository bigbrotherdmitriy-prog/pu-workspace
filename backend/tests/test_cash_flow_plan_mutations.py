from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api.execution_finance import (
    CashFlowPlanMutationRequest,
    mutate_cash_flow_plan,
    undo_cash_flow_plan_mutation,
)
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.execution_finance import CashFlowEntry, CashFlowPlanMutation
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


def _row(db, user_factory, *, status="proposed", actual=False):
    user = user_factory(is_admin=False)
    organization = Organization(name="DDS organization")
    db.add(organization); db.flush()
    project = Project(name="DDS project", organization_id=organization.id)
    db.add(project); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="editor"))
    row = CashFlowEntry(
        project_id=project.id, direction="outflow", title="Planned invoice",
        planned_date=date(2026, 9, 25), planned_amount=Decimal("300.00"),
        actual_amount=Decimal("300.00") if actual else Decimal("0.00"),
        actual_date=date(2026, 9, 26) if actual else None,
        currency="RUB", status=status,
    )
    db.add(row); db.commit()
    return user, project, row


def _request(operation="move", *, version=1, key="mutation-0001", amount="300", day=30):
    return CashFlowPlanMutationRequest(
        operation=operation, planned_date=date(2026, 9, day),
        planned_amount=Decimal(amount), expected_record_version=version,
        idempotency_key=key,
    )


def test_planned_move_is_cas_idempotent_and_audited(db_session, user_factory):
    user, _project, row = _row(db_session, user_factory)

    first = mutate_cash_flow_plan(row.id, _request(), db_session, user)
    replay = mutate_cash_flow_plan(row.id, _request(), db_session, user)

    db_session.refresh(row)
    assert row.planned_date == date(2026, 9, 30)
    assert row.record_version == 2
    assert replay["mutation_id"] == first["mutation_id"]
    assert replay["replayed"] is True
    assert db_session.query(CashFlowPlanMutation).count() == 1
    assert db_session.query(AuditLog).filter_by(action="cash_flow_plan_mutated").count() == 1


def test_stale_plan_edit_is_rejected(db_session, user_factory):
    user, _project, row = _row(db_session, user_factory)
    mutate_cash_flow_plan(row.id, _request(), db_session, user)

    with pytest.raises(HTTPException) as stale:
        mutate_cash_flow_plan(
            row.id, _request("edit", version=1, key="mutation-0002", amount="350"),
            db_session, user,
        )

    assert stale.value.status_code == 409
    assert "VERSION_MISMATCH" in str(stale.value.detail)


@pytest.mark.parametrize("status,actual", [("approved", False), ("paid", True)])
def test_confirmed_or_actual_row_is_immutable(db_session, user_factory, status, actual):
    user, _project, row = _row(db_session, user_factory, status=status, actual=actual)

    with pytest.raises(HTTPException) as immutable:
        mutate_cash_flow_plan(row.id, _request(), db_session, user)

    assert immutable.value.status_code == 409
    assert "CONFIRMED_CASH_FLOW_IMMUTABLE" in str(immutable.value.detail)


def test_copy_and_undo_preserve_source_and_cancel_copy(db_session, user_factory):
    user, _project, row = _row(db_session, user_factory)
    result = mutate_cash_flow_plan(
        row.id, _request("copy", key="mutation-copy", amount="125"), db_session, user,
    )
    copy = db_session.get(CashFlowEntry, result["result_id"])

    assert copy.id != row.id
    assert copy.planned_amount == Decimal("125.00")
    assert row.planned_amount == Decimal("300.00")

    undone = undo_cash_flow_plan_mutation(result["mutation_id"], db_session, user)
    db_session.refresh(copy)
    assert undone["undone"] is True
    assert copy.status == "cancelled"


def test_source_pinned_invoice_row_cannot_be_copied(db_session, user_factory):
    user, project, row = _row(db_session, user_factory)
    source = Document(project_id=project.id, name="invoice.pdf", source="local_upload")
    db_session.add(source)
    db_session.flush()
    row.source_document_id = source.id
    db_session.commit()

    with pytest.raises(HTTPException) as forbidden:
        mutate_cash_flow_plan(
            row.id,
            _request("copy", key="invoice-copy-forbidden", amount="125"),
            db_session,
            user,
        )

    assert forbidden.value.status_code == 409
    assert "INVOICE_COPY_FORBIDDEN" in str(forbidden.value.detail)


def test_plan_mutation_is_project_scoped(db_session, user_factory):
    _owner, _project, row = _row(db_session, user_factory)
    outsider = user_factory(is_admin=False)

    with pytest.raises(HTTPException) as denied:
        mutate_cash_flow_plan(row.id, _request(), db_session, outsider)

    assert denied.value.status_code == 403
