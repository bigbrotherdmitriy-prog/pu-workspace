from datetime import date, datetime, timezone
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import HTTPException
from sqlalchemy import func, select

import app.api.execution_finance as finance_api
from app.api.execution_finance import (
    CashFlowCreate,
    InvoiceProposalCreate,
    PaymentConfirmation,
    PaymentCorrection,
    StatusUpdate,
    confirm_payment,
    correct_payment,
    create_cash_flow,
    create_invoice_proposal,
    update_status,
)
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import (
    BudgetLine,
    CashFlowEntry,
    CashFlowFactHistory,
    ScheduleBaseline,
    ScheduleItem,
)
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.task import Task
from app.models.v54_pilot import (
    ConnectionIdentity,
    Evidence,
    EvidenceAssessment,
    SourceCurrent,
    SourceReference,
    SourceVersion,
)
from app.schema import CURRENT_SCHEMA_REVISION


BACKEND = Path(__file__).resolve().parents[1]


def _migration_config(output=None):
    value = Config(str(BACKEND / "alembic.ini"), output_buffer=output)
    value.set_main_option("script_location", str(BACKEND / "migrations"))
    return value


def _chain(db, user, *, project_name="Synthetic project"):
    organization = Organization(name=f"{project_name} organization")
    db.add(organization)
    db.flush()
    project = Project(name=project_name, organization_id=organization.id)
    db.add(project)
    db.flush()
    contract = Contract(project_id=project.id, number="SYN-1", title="Synthetic contract")
    db.add(contract)
    db.flush()
    baseline = ScheduleBaseline(
        project_id=project.id,
        contract_id=contract.id,
        created_by_user_id=user.id,
        name="Synthetic approved baseline",
        version=1,
        status="approved",
    )
    db.add(baseline)
    db.flush()
    stage = ScheduleItem(project_id=project.id, baseline_id=baseline.id, title="Synthetic stage")
    db.add(stage)
    document = Document(project_id=project.id, name="synthetic-invoice.pdf", source="local_upload")
    db.add(document)
    db.flush()
    document_version = DocumentVersion(document_id=document.id, version_number=1, content="synthetic fixture")
    db.add(document_version)
    task = Task(
        project_id=project.id,
        assignee_user_id=user.id,
        created_by_user_id=user.id,
        title="Synthetic stage task",
        source_file_id="synthetic-task-source",
        source_file_name="synthetic.txt",
        source_excerpt="synthetic",
        source_excerpt_hash="a" * 64,
        confidence=1.0,
    )
    db.add(task)
    db.flush()
    budget = BudgetLine(
        project_id=project.id,
        contract_id=contract.id,
        schedule_item_id=stage.id,
        task_id=task.id,
        source_document_id=document.id,
        source_document_version_id=document_version.id,
        category="materials",
        description="Synthetic materials budget",
        planned_amount=Decimal("100000"),
        forecast_amount=Decimal("100000"),
        status="approved",
        review_status="confirmed",
    )
    db.add(budget)
    db.flush()
    return project, contract, stage, task, budget, document, document_version


def _proposal(db, user, monkeypatch, *, direction="outflow"):
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    project, contract, stage, task, budget, document, document_version = _chain(db, user)
    result = create_cash_flow(
        CashFlowCreate(
            project_id=project.id,
            contract_id=contract.id,
            schedule_item_id=stage.id,
            task_id=task.id,
            budget_line_id=budget.id,
            source_document_id=document.id,
            direction=direction,
            title="Synthetic customer receipt" if direction == "inflow" else "Synthetic supplier payment",
            planned_date="2026-09-10",
            planned_amount="75000",
            confidence="0.62",
        ),
        db,
        user,
    )
    row = db.get(CashFlowEntry, result["id"])
    return row, (project, contract, stage, task, budget, document, document_version)


def test_cash_flow_request_supports_full_control_chain():
    payload = CashFlowCreate(
        project_id=1,
        contract_id=2,
        schedule_item_id=3,
        task_id=4,
        budget_line_id=5,
        source_document_id=6,
        evidence_id="11111111-1111-1111-1111-111111111111",
        evidence_revision=1,
        evidence_assessment_version=2,
        confidence="0.91",
        direction="inflow",
        title="Customer payment",
        planned_date="2026-09-10",
        planned_amount="50000",
    )

    assert payload.schedule_item_id == 3
    assert payload.task_id == 4
    assert payload.budget_line_id == 5
    assert payload.source_document_id == 6
    assert payload.evidence_revision == 1


def test_low_confidence_cash_flow_remains_manual_review_proposal(db_session, user_factory, monkeypatch):
    user = user_factory()
    row, chain = _proposal(db_session, user, monkeypatch)

    assert row.status == "proposed"
    assert row.review_status == "required"
    assert row.source_document_version_id == chain[-1].id
    assert row.record_version == 1


def test_payment_cannot_confirm_unapproved_proposal(db_session, user_factory, monkeypatch):
    user = user_factory()
    row, _ = _proposal(db_session, user, monkeypatch)

    with pytest.raises(HTTPException) as error:
        confirm_payment(
            row.id,
            PaymentConfirmation(expected_record_version=1, actual_amount="75000", actual_date="2026-09-11"),
            db_session,
            user,
        )

    assert error.value.status_code == 409
    assert "сначала" in str(error.value.detail).casefold()


def test_invoice_proposal_requires_primary_document_before_creation(db_session, user_factory, monkeypatch):
    user = user_factory()
    project, contract, stage, task, budget, _document, _version = _chain(db_session, user)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as error:
        create_invoice_proposal(
            InvoiceProposalCreate(
                project_id=project.id,
                contract_id=contract.id,
                schedule_item_id=stage.id,
                task_id=task.id,
                budget_line_id=budget.id,
                direction="outflow",
                title="Synthetic invoice without source",
                planned_date="2026-09-10",
                planned_amount="100",
            ),
            db_session,
            user,
        )

    assert error.value.status_code == 422
    assert "первич" in str(error.value.detail).casefold()


def test_human_approval_confirms_low_confidence_review_but_not_payment(db_session, user_factory, monkeypatch):
    user = user_factory()
    row, _ = _proposal(db_session, user, monkeypatch)

    result = update_status(
        "cash-flow",
        row.id,
        StatusUpdate(status="approved"),
        db_session,
        user,
    )

    assert result["status"] == "approved"
    assert row.review_status == "confirmed"
    assert row.actual_amount == Decimal("0")
    assert row.actual_date is None


def test_status_transition_cannot_smuggle_cash_flow_fact(db_session, user_factory, monkeypatch):
    user = user_factory()
    row, _ = _proposal(db_session, user, monkeypatch)

    with pytest.raises(HTTPException) as error:
        update_status(
            "cash-flow",
            row.id,
            StatusUpdate(status="approved", actual_amount="75000", actual_date="2026-09-11"),
            db_session,
            user,
        )

    assert error.value.status_code == 422
    assert row.status == "proposed"
    assert row.actual_amount == Decimal("0")
    assert row.actual_date is None


def test_confirmed_receipt_updates_fact_and_creates_one_history_event(db_session, user_factory, monkeypatch):
    user = user_factory()
    row, _ = _proposal(db_session, user, monkeypatch, direction="inflow")
    update_status("cash-flow", row.id, StatusUpdate(status="approved"), db_session, user)
    version = row.record_version

    first = confirm_payment(
        row.id,
        PaymentConfirmation(expected_record_version=version, actual_amount="74250", actual_date="2026-09-11"),
        db_session,
        user,
    )
    second = confirm_payment(
        row.id,
        PaymentConfirmation(expected_record_version=version, actual_amount="74250", actual_date="2026-09-11"),
        db_session,
        user,
    )

    assert first["status"] == "received"
    assert first["already_confirmed"] is False
    assert second["already_confirmed"] is True
    assert db_session.scalar(select(func.count(CashFlowFactHistory.id))) == 1


def test_payment_confirmation_fails_closed_without_complete_links(db_session, user_factory, monkeypatch):
    user = user_factory()
    organization = Organization(name="Synthetic incomplete organization")
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Synthetic incomplete project", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    row = CashFlowEntry(
        project_id=project.id,
        direction="outflow",
        title="Incomplete payment",
        planned_date=date(2026, 9, 10),
        planned_amount=Decimal("100"),
        status="approved",
        review_status="confirmed",
    )
    db_session.add(row)
    db_session.flush()
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as error:
        confirm_payment(
            row.id,
            PaymentConfirmation(expected_record_version=1, actual_amount="100", actual_date="2026-09-11"),
            db_session,
            user,
        )

    assert error.value.status_code == 409
    assert "связ" in str(error.value.detail).casefold()


def test_cross_project_stage_is_rejected(db_session, user_factory, monkeypatch):
    user = user_factory()
    project, contract, _stage, task, budget, document, _version = _chain(db_session, user, project_name="First")
    other_project, _other_contract, other_stage, _other_task, _other_budget, _other_doc, _ = _chain(
        db_session, user, project_name="Second"
    )
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as error:
        create_cash_flow(
            CashFlowCreate(
                project_id=project.id,
                contract_id=contract.id,
                schedule_item_id=other_stage.id,
                task_id=task.id,
                budget_line_id=budget.id,
                source_document_id=document.id,
                direction="outflow",
                title="Cross-project attempt",
                planned_date="2026-09-10",
                planned_amount="100",
            ),
            db_session,
            user,
        )

    assert error.value.status_code == 422
    assert other_project.id != project.id


def test_unavailable_evidence_pin_is_rejected_fail_closed(db_session, user_factory, monkeypatch):
    user = user_factory()
    project, contract, stage, task, budget, document, _version = _chain(db_session, user)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as error:
        create_cash_flow(
            CashFlowCreate(
                project_id=project.id,
                contract_id=contract.id,
                schedule_item_id=stage.id,
                task_id=task.id,
                budget_line_id=budget.id,
                source_document_id=document.id,
                evidence_id="11111111-1111-1111-1111-111111111111",
                evidence_revision=1,
                evidence_assessment_version=1,
                direction="outflow",
                title="Unavailable evidence",
                planned_date="2026-09-10",
                planned_amount="100",
            ),
            db_session,
            user,
        )

    assert error.value.status_code == 409
    assert "evidence" in str(error.value.detail).casefold()


def test_verified_current_evidence_is_pinned_to_cash_flow(db_session, user_factory, monkeypatch):
    user = user_factory()
    project, contract, stage, task, budget, document, document_version = _chain(db_session, user)
    now = datetime.now(timezone.utc)
    identity = ConnectionIdentity(
        organization_id=project.organization_id,
        provider="synthetic",
        account_key="synthetic-finance-account",
        state="verified",
    )
    db_session.add(identity)
    db_session.flush()
    source = SourceReference(
        organization_id=project.organization_id,
        origin_project_id=project.id,
        identity_id=identity.id,
        namespace="synthetic-finance",
        external_id="synthetic-finance-document",
        external_id_kind="opaque",
        object_kind="file",
        canonical_locator={"kind": "synthetic"},
        freshness="fresh",
        sync_state="indexed",
        availability="available",
    )
    db_session.add(source)
    db_session.flush()
    source_version = SourceVersion(
        organization_id=project.organization_id,
        source_id=source.id,
        observation_key="synthetic-finance-v1",
        consistency="revision_bound",
        locator_at_observation={"kind": "synthetic"},
        integrity=[{"algorithm": "sha256", "digest": "b" * 64}],
        observed_at=now,
        legacy_document_version_id=document_version.id,
    )
    db_session.add(source_version)
    db_session.flush()
    db_session.add(SourceCurrent(
        source_id=source.id,
        organization_id=project.organization_id,
        version_id=source_version.id,
    ))
    evidence = Evidence(
        organization_id=project.organization_id,
        source_id=source.id,
        source_version_id=source_version.id,
        locator={"page": 1, "bbox": [10, 20, 200, 40]},
        extractor={"kind": "synthetic"},
        confidence=0.97,
        confidence_kind="synthetic",
        extracted_at=now,
    )
    db_session.add(evidence)
    db_session.flush()
    db_session.add(EvidenceAssessment(
        evidence_id=evidence.id,
        organization_id=project.organization_id,
        record_version=1,
        verification="verified",
        freshness="fresh",
        availability="available",
        checked_at=now,
        reviewed_by=user.id,
        reviewed_at=now,
    ))
    db_session.flush()
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)

    result = create_cash_flow(
        CashFlowCreate(
            project_id=project.id,
            contract_id=contract.id,
            schedule_item_id=stage.id,
            task_id=task.id,
            budget_line_id=budget.id,
            source_document_id=document.id,
            evidence_id=evidence.id,
            evidence_revision=1,
            evidence_assessment_version=1,
            direction="inflow",
            title="Evidence-backed customer receipt",
            planned_date="2026-09-10",
            planned_amount="100",
        ),
        db_session,
        user,
    )

    row = db_session.get(CashFlowEntry, result["id"])
    assert row.evidence_id == evidence.id
    assert row.evidence_revision == 1
    assert row.evidence_assessment_version == 1
    assert row.source_document_version_id == document_version.id
    assert row.confidence == pytest.approx(0.97)
    assert row.review_status == "pending_confirmation"


def test_payment_correction_uses_record_version_cas_and_immutable_history(db_session, user_factory, monkeypatch):
    user = user_factory()
    row, _ = _proposal(db_session, user, monkeypatch)
    update_status("cash-flow", row.id, StatusUpdate(status="approved"), db_session, user)
    confirm_payment(
        row.id,
        PaymentConfirmation(expected_record_version=row.record_version, actual_amount="74250", actual_date="2026-09-11"),
        db_session,
        user,
    )
    confirmed_version = row.record_version

    result = correct_payment(
        row.id,
        PaymentCorrection(
            expected_record_version=confirmed_version,
            expected_actual_amount="74250",
            expected_actual_date="2026-09-11",
            actual_amount="74000",
            actual_date="2026-09-12",
            reason="Synthetic correction",
        ),
        db_session,
        user,
    )

    assert result["record_version"] == confirmed_version + 1
    history = list(db_session.scalars(select(CashFlowFactHistory).order_by(CashFlowFactHistory.sequence)))
    assert [item.event for item in history] == ["confirmed", "corrected"]
    assert history[0].resulting_actual_amount == Decimal("74250")
    assert history[1].previous_actual_amount == Decimal("74250")
    assert history[1].resulting_actual_amount == Decimal("74000")

    with pytest.raises(HTTPException) as error:
        correct_payment(
            row.id,
            PaymentCorrection(
                expected_record_version=confirmed_version,
                expected_actual_amount="74250",
                expected_actual_date="2026-09-11",
                actual_amount="73000",
                actual_date="2026-09-13",
                reason="Stale correction",
            ),
            db_session,
            user,
        )
    assert error.value.status_code == 409


def test_audit_does_not_store_freeform_correction_reason(db_session, user_factory, monkeypatch):
    user = user_factory()
    row, _ = _proposal(db_session, user, monkeypatch)
    update_status("cash-flow", row.id, StatusUpdate(status="approved"), db_session, user)
    confirm_payment(
        row.id,
        PaymentConfirmation(expected_record_version=row.record_version, actual_amount="74250", actual_date="2026-09-11"),
        db_session,
        user,
    )
    secret_marker = "SYNTHETIC_PRIVATE_REASON_MARKER"
    correct_payment(
        row.id,
        PaymentCorrection(
            expected_record_version=row.record_version,
            expected_actual_amount="74250",
            expected_actual_date="2026-09-11",
            actual_amount="74000",
            actual_date="2026-09-12",
            reason=secret_marker,
        ),
        db_session,
        user,
    )

    details = "\n".join(value or "" for value in db_session.scalars(select(AuditLog.details)))
    assert secret_marker not in details


def test_cash_flow_fact_history_is_immutable(db_session, user_factory, monkeypatch):
    user = user_factory()
    row, _ = _proposal(db_session, user, monkeypatch)
    update_status("cash-flow", row.id, StatusUpdate(status="approved"), db_session, user)
    confirm_payment(
        row.id,
        PaymentConfirmation(expected_record_version=row.record_version, actual_amount="74250", actual_date="2026-09-11"),
        db_session,
        user,
    )
    history = db_session.scalar(select(CashFlowFactHistory))
    history.resulting_actual_amount = Decimal("1")

    with pytest.raises(ValueError, match="immutable_cash_flow_fact_history"):
        db_session.flush()


def test_budget_dds_migration_is_single_sequential_head():
    script = ScriptDirectory.from_config(_migration_config())

    assert script.get_heads() == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a17"]
    assert script.get_revision("a54f001c0a14").down_revision == "a54f001c0a13"


def test_budget_dds_offline_migration_contains_controls_and_history(monkeypatch):
    output = StringIO()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_finance_offline")

    command.upgrade(_migration_config(output), "a54f001c0a13:a54f001c0a14", sql=True)
    sql = output.getvalue().lower()

    for token in (
        "record_version",
        "source_document_version_id",
        "evidence_assessment_version",
        "review_status",
        "cash_flow_fact_history",
        "resulting_record_version",
        "changed_by_user_id",
    ):
        assert token in sql
