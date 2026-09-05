from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.api.execution_finance as execution_finance
from app.api.execution_finance import (
    CashFlowCreate,
    PaymentConfirmation,
    PaymentCorrection,
    PaymentReversal,
    _check_task,
    confirm_payment,
    correct_payment,
    overview,
    reverse_payment,
)
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import BudgetLine, CashFlowEntry, PaymentEvent
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.task import Task


def _project(db_session, user_factory, name="MVP4"):
    organization = Organization(name=f"{name} organization")
    user = user_factory(is_admin=True)
    db_session.add(organization)
    db_session.flush()
    project = Project(name=name, organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    return user, project


def test_money_is_half_up_bounded_and_currency_is_strict():
    assert CashFlowCreate(
        project_id=1, direction="outflow", title="Счёт",
        planned_date="2026-09-04", planned_amount="1.005", currency="RUB",
    ).planned_amount == Decimal("1.01")
    with pytest.raises(ValidationError):
        CashFlowCreate(
            project_id=1, direction="outflow", title="Счёт",
            planned_date="2026-09-04", planned_amount="1", currency="rub",
        )
    with pytest.raises(ValidationError):
        CashFlowCreate(
            project_id=1, direction="outflow", title="Счёт",
            planned_date="2026-09-04", planned_amount="10000000000000000.00", currency="RUB",
        )


def test_mixed_currency_overview_never_returns_a_cross_currency_total(db_session, user_factory):
    user, project = _project(db_session, user_factory, "Mixed currency")
    db_session.add_all([
        BudgetLine(project_id=project.id, category="A", description="RUB", planned_amount=100,
                   forecast_amount=100, currency="RUB", status="approved"),
        BudgetLine(project_id=project.id, category="B", description="USD", planned_amount=2,
                   forecast_amount=2, currency="USD", status="approved"),
    ])
    db_session.flush()

    result = overview(project.id, db_session, user)

    assert result["summary"]["mixed_currency"] is True
    assert result["summary"]["budget_planned"] is None
    assert result["summary"]["by_currency"]["RUB"]["budget_planned"] == Decimal("100")
    assert result["summary"]["by_currency"]["USD"]["budget_planned"] == Decimal("2")


def test_payment_confirmation_correction_and_reversal_are_append_only(db_session, user_factory, monkeypatch):
    user, project = _project(db_session, user_factory, "Payment ledger")
    monkeypatch.setattr(execution_finance, "require_project_role", lambda *_args, **_kwargs: None)
    item = CashFlowEntry(
        project_id=project.id, direction="outflow", title="Оборудование",
        planned_date=date(2026, 9, 10), planned_amount=Decimal("100.00"),
        actual_amount=Decimal("0.00"), currency="RUB", status="approved",
    )
    db_session.add(item)
    db_session.flush()

    confirmation = confirm_payment(item.id, PaymentConfirmation(
        actual_amount="100.00", actual_date="2026-09-11", idempotency_key="confirm-0001",
    ), db_session, user)
    correction = correct_payment(item.id, PaymentCorrection(
        actual_amount="99.995", actual_date="2026-09-12", reason="Исправлен факт банка",
        supersedes_event_id=confirmation["payment_event_id"], idempotency_key="correct-0001",
    ), db_session, user)
    reversal = reverse_payment(item.id, PaymentReversal(
        reason="Платёж возвращён", supersedes_event_id=correction["payment_event_id"],
        idempotency_key="reverse-0001",
    ), db_session, user)
    replay = reverse_payment(item.id, PaymentReversal(
        reason="Платёж возвращён", supersedes_event_id=correction["payment_event_id"],
        idempotency_key="reverse-0001",
    ), db_session, user)

    events = list(db_session.query(PaymentEvent).filter_by(cash_flow_entry_id=item.id).order_by(PaymentEvent.id))
    assert [event.event_type for event in events] == ["confirmation", "correction", "reversal"]
    assert events[0].amount == Decimal("100.00")
    assert events[1].amount == Decimal("100.00")  # 99.995, HALF_UP
    assert events[1].supersedes_event_id == events[0].id
    assert events[2].supersedes_event_id == events[1].id
    assert reversal["status"] == "approved"
    assert replay["created"] is False and replay["payment_event_id"] == reversal["payment_event_id"]
    assert item.actual_amount == Decimal("0.00") and item.actual_date is None


def test_source_version_pin_rejects_stale_payment(db_session, user_factory, monkeypatch):
    user, project = _project(db_session, user_factory, "Evidence pin")
    monkeypatch.setattr(execution_finance, "require_project_role", lambda *_args, **_kwargs: None)
    document = Document(
        project_id=project.id, name="invoice.pdf", source="local_upload", status="analyzed",
        current_version=1, content_hash="a" * 64,
    )
    db_session.add(document)
    db_session.flush()
    version1 = DocumentVersion(document_id=document.id, version_number=1, content="invoice v1")
    db_session.add(version1)
    db_session.flush()
    item = CashFlowEntry(
        project_id=project.id, direction="outflow", title="Invoice", planned_date=date(2026, 9, 10),
        planned_amount=Decimal("10.00"), actual_amount=Decimal("0.00"), currency="RUB",
        source_document_id=document.id, source_document_version_id=version1.id,
        source_document_sha256="a" * 64,
    )
    db_session.add(item)
    db_session.flush()
    version2 = DocumentVersion(document_id=document.id, version_number=2, content="invoice v2")
    db_session.add(version2)
    db_session.flush()
    document.current_version = 2
    document.content_hash = "b" * 64
    db_session.flush()

    with pytest.raises(HTTPException) as error:
        confirm_payment(item.id, PaymentConfirmation(
            actual_amount="10.00", actual_date="2026-09-11",
            expected_document_version_id=version1.id,
            expected_document_sha256="a" * 64,
        ), db_session, user)
    assert error.value.status_code == 409
    assert "SOURCE_VERSION_MISMATCH" in error.value.detail
    assert db_session.query(PaymentEvent).filter_by(cash_flow_entry_id=item.id).count() == 0


def test_task_link_must_belong_to_same_project(db_session, user_factory):
    user, project = _project(db_session, user_factory, "Invoice project")
    _, other = _project(db_session, user_factory, "Other project")
    task = Task(
        project_id=other.id, assignee_user_id=user.id, created_by_user_id=user.id,
        title="Чужая задача", source_file_id="source", source_file_name="source.txt",
        source_excerpt="excerpt", source_excerpt_hash="c" * 64, confidence=1.0,
    )
    db_session.add(task)
    db_session.flush()

    with pytest.raises(HTTPException) as error:
        _check_task(db_session, project.id, task.id)
    assert error.value.status_code == 422
