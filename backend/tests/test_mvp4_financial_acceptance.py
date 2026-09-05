from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.execution_finance import (
    BudgetCreate,
    InvoiceProposalCreate,
    PaymentConfirmation,
    PaymentCorrection,
    ScheduleItemCreate,
    StatusUpdate,
    confirm_payment,
    correct_payment,
    create_budget,
    create_invoice_proposal,
    create_schedule_item,
    update_status,
)
from app.api.evidence import list_current_project_evidence
from app.contract_evidence import extract_contract_evidence, persist_contract_evidence
from app.execution_forecast.engine import build_forecast
from app.execution_forecast.repository import load_forecast_input
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
from app.models.job import BackgroundJob
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.v54_pilot import (
    ConnectionIdentity,
    Evidence,
    EvidenceAssessment,
    SourceCurrent,
    SourceReference,
    SourceVersion,
)
from app.mvp4.supply.contracts import (
    CreateDdsProposal,
    CreateSupplyRequest,
    EvidenceLink,
    PrepareOrder,
    ProposeAcceptanceAct,
    RecordDelivery,
    RecordOrder,
    ReviewSupplyRequest,
    VersionedCommand,
)
from app.mvp4.supply.router import _require_idempotency
from app.mvp4.supply.models import SupplyCase, SupplyCaseVersion
from app.mvp4.supply.service import SupplyConflict, SupplyDenied, SupplyService


NOW = datetime.now(timezone.utc)


def _uid(number: int) -> str:
    return f"10000000-0000-4000-8000-{number:012d}"


def _exact_document_evidence(
    db,
    user,
    *,
    project: Project,
    number: int = 1,
    confidence: float = 0.96,
    verified: bool = True,
):
    document = Document(
        project_id=project.id,
        name=f"synthetic-financial-{number}.txt",
        source="synthetic",
        status="ready",
    )
    db.add(document)
    db.flush()
    document_version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        content="synthetic fixture only; no client content",
    )
    db.add(document_version)
    db.flush()
    identity = ConnectionIdentity(
        id=_uid(number * 10 + 1),
        organization_id=project.organization_id,
        provider="synthetic",
        account_key=f"synthetic-finance-{number}",
        state="verified",
        credential_generation=1,
        verified_at=NOW,
    )
    db.add(identity)
    db.flush()
    source = SourceReference(
        id=_uid(number * 10 + 2),
        organization_id=project.organization_id,
        origin_project_id=project.id,
        identity_id=identity.id,
        namespace="synthetic-finance",
        external_id=f"synthetic-document-{number}",
        external_id_kind="stable_id",
        object_kind="file",
        canonical_locator={"kind": "opaque_id", "value": f"synthetic-{number}"},
        freshness="fresh",
        availability="available",
        last_checked_at=NOW,
        next_check_at=NOW + timedelta(hours=1),
        policy_pins={"retention": "synthetic-test", "residency": "local"},
    )
    db.add(source)
    db.flush()
    source_version = SourceVersion(
        id=_uid(number * 10 + 3),
        organization_id=project.organization_id,
        source_id=source.id,
        observation_key=f"synthetic-financial-v{number}",
        provider_revision=f"fixture-v{number}",
        consistency="revision_bound",
        locator_at_observation={"kind": "opaque_id", "value": f"synthetic-v{number}"},
        integrity=[],
        observed_at=NOW,
        legacy_document_version_id=document_version.id,
    )
    db.add(source_version)
    db.flush()
    db.add(SourceCurrent(
        source_id=source.id,
        organization_id=project.organization_id,
        version_id=source_version.id,
    ))
    evidence = Evidence(
        id=_uid(number * 10 + 4),
        organization_id=project.organization_id,
        source_id=source.id,
        source_version_id=source_version.id,
        locator={"kind": "page_region", "page": 2, "bbox": [10, 20, 110, 50]},
        extractor={"name": "synthetic", "version": "1"},
        confidence=confidence,
        confidence_kind="model",
        extracted_at=NOW,
        policy_pins=source.policy_pins,
    )
    db.add(evidence)
    db.flush()
    assessment = EvidenceAssessment(
        evidence_id=evidence.id,
        organization_id=project.organization_id,
        verification="verified" if verified else "unverified",
        freshness="fresh",
        availability="available",
        checked_at=NOW,
        valid_until=NOW + timedelta(hours=1),
        reviewed_by=user.id if verified else None,
        reviewed_at=NOW if verified else None,
    )
    db.add(assessment)
    db.flush()
    return document, document_version, source_version, evidence, assessment


@pytest.fixture()
def financial_world(db_session, user_factory):
    manager = user_factory(name="Synthetic finance manager")
    editor = user_factory(name="Synthetic finance editor")
    viewer = user_factory(name="Synthetic finance viewer")
    outsider = user_factory(name="Synthetic finance outsider")
    organization = Organization(name="Synthetic financial acceptance tenant")
    other_organization = Organization(name="Other synthetic tenant")
    db_session.add_all([organization, other_organization])
    db_session.flush()
    project = Project(name="Synthetic controlled project", organization_id=organization.id)
    other_project = Project(name="Other synthetic project", organization_id=other_organization.id)
    db_session.add_all([project, other_project])
    db_session.flush()
    db_session.add_all([
        ProjectMember(project_id=project.id, user_id=manager.id, role="manager"),
        ProjectMember(project_id=project.id, user_id=editor.id, role="editor"),
        ProjectMember(project_id=project.id, user_id=viewer.id, role="viewer"),
    ])
    document, document_version, source_version, evidence, assessment = _exact_document_evidence(
        db_session, manager, project=project, number=20
    )
    contract = Contract(
        project_id=project.id,
        number="SYN-M4-10",
        title="Synthetic evidenced contract",
        amount=Decimal("100000.00"),
        advance_amount=Decimal("20000.00"),
        retention_percent=Decimal("5.00"),
        signed_at=date(2026, 9, 1),
        status="active",
        source_document_id=document.id,
    )
    db_session.add(contract)
    db_session.flush()
    baseline = ScheduleBaseline(
        project_id=project.id,
        contract_id=contract.id,
        created_by_user_id=manager.id,
        name="Synthetic immutable GPR baseline",
        version=1,
        status="approved",
    )
    db_session.add(baseline)
    db_session.flush()
    stage = ScheduleItem(
        project_id=project.id,
        baseline_id=baseline.id,
        title="Synthetic delivery stage",
        planned_start=date(2026, 9, 1),
        planned_finish=date(2026, 9, 20),
        actual_start=date(2026, 9, 2),
        planned_progress=100,
        actual_progress=50,
        status="in_progress",
    )
    task = Task(
        project_id=project.id,
        assignee_user_id=editor.id,
        created_by_user_id=manager.id,
        title="Synthetic supply task",
        status="assigned",
        due_date=date(2026, 9, 10),
        source_type="synthetic",
        source_file_id="synthetic-finance-source",
        source_file_name="synthetic.txt",
        source_excerpt="synthetic only",
        source_excerpt_hash="b" * 64,
        confidence=1.0,
        needs_review=False,
    )
    db_session.add_all([stage, task])
    db_session.commit()
    return {
        "db": db_session,
        "organization": organization,
        "manager": manager,
        "editor": editor,
        "viewer": viewer,
        "outsider": outsider,
        "project": project,
        "other_project": other_project,
        "contract": contract,
        "baseline": baseline,
        "stage": stage,
        "task": task,
        "document": document,
        "document_version": document_version,
        "source_version": source_version,
        "evidence": evidence,
        "assessment": assessment,
    }


def _finance_pin(world) -> dict:
    return {
        "source_document_id": world["document"].id,
        "evidence_id": world["evidence"].id,
        "evidence_revision": 1,
        "evidence_assessment_version": world["assessment"].record_version,
        "confidence": Decimal("0.96"),
    }


def _supply_pin(world) -> EvidenceLink:
    return EvidenceLink(
        evidence_id=UUID(world["evidence"].id),
        evidence_revision=1,
        source_version_id=UUID(world["source_version"].id),
        document_version_id=world["document_version"].id,
    )


def _supply_request(world, *, key: str = "acceptance:request:0001") -> CreateSupplyRequest:
    return CreateSupplyRequest(
        command_key=key,
        organization_id=world["organization"].id,
        project_id=world["project"].id,
        contract_id=world["contract"].id,
        schedule_baseline_id=world["baseline"].id,
        schedule_baseline_version=world["baseline"].version,
        schedule_item_id=world["stage"].id,
        task_id=world["task"].id,
        evidence=_supply_pin(world),
        title="Synthetic equipment",
        supplier="Synthetic supplier",
        requested_quantity=Decimal("3.125"),
        unit="pcs",
        unit_price=Decimal("100.25"),
        currency="RUB",
    )


def test_forecast_preserves_exact_evidence_for_budget_source(db_session, user_factory):
    user = user_factory()
    organization = Organization(name="Synthetic forecast acceptance tenant")
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Synthetic forecast acceptance project", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    document, version, _source_version, evidence, _assessment = _exact_document_evidence(
        db_session, user, project=project
    )
    budget = BudgetLine(
        project_id=project.id,
        source_document_id=document.id,
        source_document_version_id=version.id,
        evidence_id=evidence.id,
        evidence_revision=1,
        evidence_assessment_version=1,
        confidence=0.96,
        review_status="confirmed",
        category="materials",
        description="Synthetic evidenced budget",
        planned_amount=Decimal("100.00"),
        forecast_amount=Decimal("100.00"),
        currency="RUB",
        status="approved",
    )
    db_session.add(budget)
    db_session.flush()

    loaded = load_forecast_input(db_session, project.id, date(2026, 9, 5))

    assert loaded.budget[0].source.evidence[0].evidence_id == evidence.id
    assert loaded.budget[0].source.evidence[0].page == 2
    assert loaded.budget[0].source.evidence[0].coordinates == (10.0, 20.0, 110.0, 50.0)


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (BudgetCreate, {
            "project_id": 1,
            "category": "materials",
            "description": "Synthetic precision check",
            "planned_amount": Decimal("1.001"),
        }),
        (PaymentConfirmation, {
            "expected_record_version": 1,
            "actual_amount": Decimal("1.001"),
        }),
    ],
)
def test_financial_commands_reject_sub_kopeck_precision(model, kwargs):
    with pytest.raises(ValidationError):
        model(**kwargs)


def test_contract_terms_have_exact_immutable_evidence_before_financial_use(financial_world):
    world = financial_world
    content = """\
Договор № SYN-M4-10 от 01.09.2026
Цена настоящего договора 100 000,00 руб.
Аванс 20 000,00 руб., 20 %.
Гарантийное удержание 5 %.
Заказчик: Синтетический заказчик
Подрядчик: Синтетический подрядчик
"""
    world["document_version"].content = content
    extraction = extract_contract_evidence(content)
    persisted = persist_contract_evidence(
        world["db"],
        organization_id=world["organization"].id,
        project_id=world["project"].id,
        document_version=world["document_version"],
        extraction=extraction,
    )

    assert extraction["status"] == "ready"
    assert extraction["amount"] == Decimal("100000.00")
    assert extraction["advance_amount"] == Decimal("20000.00")
    assert extraction["retention_percent"] == Decimal("5")
    assert persisted["status"] == "ready"
    assert persisted["source_version_id"] == world["source_version"].id
    assert persisted["document_version_id"] == world["document_version"].id
    assert {item["evidence_revision"] for item in persisted["evidence"]} == {1}
    assert all(item["locator"]["kind"] == "text_range" for item in persisted["evidence"])

    evidence = world["db"].get(Evidence, persisted["evidence"][0]["evidence_id"])
    evidence.confidence = 0.1
    with pytest.raises(ValueError, match="immutable"):
        world["db"].flush()
    world["db"].rollback()


def test_complete_financial_chain_requires_human_payment_and_preserves_corrections(financial_world):
    world = financial_world
    budget_result = create_budget(
        BudgetCreate(
            project_id=world["project"].id,
            contract_id=world["contract"].id,
            schedule_item_id=world["stage"].id,
            task_id=world["task"].id,
            category="materials",
            description="Synthetic controlled budget",
            planned_amount=Decimal("5000.00"),
            forecast_amount=Decimal("5000.00"),
            currency="RUB",
            **_finance_pin(world),
        ),
        world["db"],
        world["editor"],
    )
    budget = world["db"].get(BudgetLine, budget_result["id"])
    assert budget.review_status == "pending_confirmation"
    update_status("budget", budget.id, StatusUpdate(status="approved"), world["db"], world["manager"])

    invoice_result = create_invoice_proposal(
        InvoiceProposalCreate(
            project_id=world["project"].id,
            contract_id=world["contract"].id,
            schedule_item_id=world["stage"].id,
            task_id=world["task"].id,
            budget_line_id=budget.id,
            direction="outflow",
            title="Synthetic invoice",
            planned_date=date(2026, 9, 8),
            planned_amount=Decimal("1250.10"),
            counterparty="Synthetic supplier",
            **_finance_pin(world),
        ),
        world["db"],
        world["editor"],
    )
    cash = world["db"].get(CashFlowEntry, invoice_result["id"])
    assert invoice_result["requires_payment_confirmation"] is True
    assert (cash.status, cash.actual_amount, cash.actual_date) == ("proposed", Decimal("0"), None)

    update_status("cash-flow", cash.id, StatusUpdate(status="approved"), world["db"], world["manager"])
    assert (cash.status, cash.actual_amount, cash.actual_date) == ("approved", Decimal("0"), None)
    approved_version = cash.record_version
    confirmation = PaymentConfirmation(
        expected_record_version=approved_version,
        actual_amount=Decimal("1250.10"),
        actual_date=date(2026, 9, 9),
    )
    confirmed = confirm_payment(cash.id, confirmation, world["db"], world["manager"])
    replay = confirm_payment(cash.id, confirmation, world["db"], world["manager"])
    assert confirmed["already_confirmed"] is False
    assert replay["already_confirmed"] is True
    assert (cash.status, cash.actual_amount) == ("paid", Decimal("1250.10"))

    corrected = correct_payment(
        cash.id,
        PaymentCorrection(
            expected_record_version=cash.record_version,
            expected_actual_amount=Decimal("1250.10"),
            expected_actual_date=date(2026, 9, 9),
            actual_amount=Decimal("1200.05"),
            actual_date=date(2026, 9, 10),
            reason="Synthetic operator correction",
        ),
        world["db"],
        world["manager"],
    )
    assert corrected["corrected"] is True
    history = list(world["db"].scalars(
        select(CashFlowFactHistory)
        .where(CashFlowFactHistory.cash_flow_entry_id == cash.id)
        .order_by(CashFlowFactHistory.sequence)
    ))
    assert [item.event for item in history] == ["confirmed", "corrected"]
    assert history[1].previous_actual_amount == Decimal("1250.10")
    assert history[1].resulting_actual_amount == Decimal("1200.05")
    assert budget.actual_amount == Decimal("1200.05")

    loaded = load_forecast_input(world["db"], world["project"].id, date(2026, 9, 12))
    forecast = build_forecast(loaded)
    budget_source = forecast["budget"]["lines"][0]["sources"][0]
    cash_event = next(item for item in forecast["cash_flow"]["events"] if item["id"] == cash.id)
    assert budget_source["evidence_exact"] is True
    assert budget_source["evidence"][0]["evidence_id"] == world["evidence"].id
    assert cash_event["value_kind"] == "actual"
    assert cash_event["amount"] == "1200.05"
    assert forecast["advisory_only"] is True
    assert forecast["can_trigger_actions"] is False
    assert forecast["requires_human_confirmation"] is True


def test_complete_supply_chain_is_internal_idempotent_and_versioned(financial_world):
    world = financial_world
    service = SupplyService()
    command = _supply_request(world)
    created = service.create_request(world["db"], actor_user_id=world["editor"].id, command=command)
    replay = service.create_request(world["db"], actor_user_id=world["editor"].id, command=command)
    assert replay == created.model_copy(update={"already_applied": True})
    row = world["db"].get(SupplyCase, created.supply_case_id)
    common = {
        "organization_id": world["organization"].id,
        "project_id": world["project"].id,
        "supply_case_id": row.id,
    }
    service.approve_request(
        world["db"], **common, actor_user_id=world["manager"].id,
        command=VersionedCommand(command_key="acceptance:approve:request", expected_version=1),
    )
    service.prepare_order(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=PrepareOrder(
            command_key="acceptance:prepare:order",
            expected_version=2,
            ordered_quantity=Decimal("3.125"),
            order_reference="PO-SYN-M4-10",
        ),
    )
    service.approve_order(
        world["db"], **common, actor_user_id=world["manager"].id,
        command=VersionedCommand(command_key="acceptance:approve:order", expected_version=3),
    )
    service.record_order(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=RecordOrder(
            command_key="acceptance:record:order",
            expected_version=4,
            evidence=_supply_pin(world),
        ),
    )
    service.record_delivery(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=RecordDelivery(
            command_key="acceptance:record:delivery",
            expected_version=5,
            delivered_quantity=Decimal("3.125"),
            evidence=_supply_pin(world),
        ),
    )
    service.propose_acceptance_act(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=ProposeAcceptanceAct(
            command_key="acceptance:propose:act",
            expected_version=6,
            accepted_quantity=Decimal("3.125"),
            act_number="ACT-SYN-M4-10",
            evidence=_supply_pin(world),
        ),
    )
    final = service.approve_acceptance_act(
        world["db"], **common, actor_user_id=world["manager"].id,
        command=VersionedCommand(command_key="acceptance:approve:act", expected_version=7),
    )

    assert (final.status, final.record_version, final.external_action_created) == ("accepted", 8, False)
    assert row.external_action_status == "not_created"
    assert row.accepted_quantity == row.delivered_quantity == row.ordered_quantity == Decimal("3.125")
    versions = service.history(world["db"], **common)
    assert [version.sequence for version in versions] == list(range(1, 9))
    assert all(version.snapshot["external_action_status"] == "not_created" for version in versions)
    assert world["db"].scalar(select(func.count(BackgroundJob.id))) == 0

    versions[-1].event = "tampered"
    with pytest.raises(ValueError, match="append_only"):
        world["db"].flush()
    world["db"].rollback()


def test_rbac_scope_and_stale_versions_fail_closed(financial_world):
    world = financial_world
    budget_payload = BudgetCreate(
        project_id=world["project"].id,
        contract_id=world["contract"].id,
        schedule_item_id=world["stage"].id,
        task_id=world["task"].id,
        category="materials",
        description="Synthetic denied budget",
        planned_amount=Decimal("100.00"),
        currency="RUB",
        **_finance_pin(world),
    )
    with pytest.raises(HTTPException) as viewer_error:
        create_budget(budget_payload, world["db"], world["viewer"])
    assert viewer_error.value.status_code == 403

    cross_project = budget_payload.model_copy(update={"project_id": world["other_project"].id})
    with pytest.raises(HTTPException) as outsider_error:
        create_budget(cross_project, world["db"], world["outsider"])
    assert outsider_error.value.status_code == 403

    service = SupplyService()
    row_id = service.create_request(
        world["db"], actor_user_id=world["editor"].id, command=_supply_request(world)
    ).supply_case_id
    with pytest.raises(SupplyDenied, match="resource_unavailable"):
        service.approve_request(
            world["db"],
            organization_id=world["organization"].id,
            project_id=world["project"].id,
            supply_case_id=row_id,
            actor_user_id=world["editor"].id,
            command=VersionedCommand(command_key="acceptance:editor:approve", expected_version=1),
        )
    service.approve_request(
        world["db"],
        organization_id=world["organization"].id,
        project_id=world["project"].id,
        supply_case_id=row_id,
        actor_user_id=world["manager"].id,
        command=VersionedCommand(command_key="acceptance:manager:approve", expected_version=1),
    )
    with pytest.raises(SupplyConflict, match="record_version_conflict"):
        service.prepare_order(
            world["db"],
            organization_id=world["organization"].id,
            project_id=world["project"].id,
            supply_case_id=row_id,
            actor_user_id=world["editor"].id,
            command=PrepareOrder(
                command_key="acceptance:stale:prepare",
                expected_version=1,
                ordered_quantity=Decimal("1.000"),
                order_reference="PO-STALE",
            ),
        )

    with pytest.raises(HTTPException) as immutable_gpr:
        create_schedule_item(
            ScheduleItemCreate(
                baseline_id=world["baseline"].id,
                expected_baseline_version=world["baseline"].version,
                title="Forbidden approved-plan mutation",
            ),
            world["db"],
            world["manager"],
        )
    assert immutable_gpr.value.status_code == 409


def test_stale_financial_status_cas_does_not_mutate_pending_record(financial_world):
    world = financial_world
    result = create_budget(
        BudgetCreate(
            project_id=world["project"].id,
            contract_id=world["contract"].id,
            schedule_item_id=world["stage"].id,
            task_id=world["task"].id,
            category="materials",
            description="Synthetic stale CAS budget",
            planned_amount=Decimal("100.00"),
            currency="RUB",
            **_finance_pin(world),
        ),
        world["db"],
        world["editor"],
    )
    row = world["db"].get(BudgetLine, result["id"])

    with pytest.raises(HTTPException) as error:
        update_status(
            "budget",
            row.id,
            StatusUpdate(status="approved", expected_status="approved"),
            world["db"],
            world["manager"],
        )

    assert error.value.status_code == 409
    assert (row.status, row.review_status, row.record_version) == (
        "proposed", "pending_confirmation", 1,
    )


def test_low_confidence_stays_manual_and_never_creates_financial_or_external_effect(financial_world):
    world = financial_world
    _document, _version, source_version, evidence, assessment = _exact_document_evidence(
        world["db"],
        world["manager"],
        project=world["project"],
        number=30,
        confidence=0.40,
        verified=False,
    )
    low_pin = {
        "source_document_id": _document.id,
        "evidence_id": evidence.id,
        "evidence_revision": 1,
        "evidence_assessment_version": assessment.record_version,
        "confidence": Decimal("0.40"),
    }
    budget_result = create_budget(
        BudgetCreate(
            project_id=world["project"].id,
            contract_id=world["contract"].id,
            schedule_item_id=world["stage"].id,
            task_id=world["task"].id,
            category="materials",
            description="Synthetic low-confidence budget",
            planned_amount=Decimal("50.00"),
            currency="RUB",
            **low_pin,
        ),
        world["db"],
        world["editor"],
    )
    budget = world["db"].get(BudgetLine, budget_result["id"])
    assert (budget.status, budget.review_status) == ("proposed", "required")
    assert budget.actual_amount == Decimal("0")

    command = _supply_request(world, key="acceptance:low:request").model_copy(update={
        "evidence": EvidenceLink(
            evidence_id=UUID(evidence.id),
            evidence_revision=1,
            source_version_id=UUID(source_version.id),
            document_version_id=_version.id,
        )
    })
    service = SupplyService()
    result = service.create_request(world["db"], actor_user_id=world["editor"].id, command=command)
    row = world["db"].get(SupplyCase, result.supply_case_id)
    assert (row.status, row.review_state, row.external_action_status) == (
        "needs_review", "needs_review", "not_created",
    )
    with pytest.raises(SupplyConflict, match="invalid_supply_transition"):
        service.approve_request(
            world["db"],
            organization_id=row.organization_id,
            project_id=row.project_id,
            supply_case_id=row.id,
            actor_user_id=world["manager"].id,
            command=VersionedCommand(command_key="acceptance:low:approve", expected_version=1),
        )
    reviewed = service.review_request(
        world["db"],
        organization_id=row.organization_id,
        project_id=row.project_id,
        supply_case_id=row.id,
        actor_user_id=world["manager"].id,
        command=ReviewSupplyRequest(
            command_key="acceptance:low:review",
            expected_version=1,
            decision="confirm",
        ),
    )
    assert reviewed.status == "request_pending_approval"
    assert world["db"].scalar(select(func.count(BackgroundJob.id))) == 0


def test_financial_acceptance_logs_and_payloads_do_not_contain_document_content(financial_world):
    world = financial_world
    secret_marker = "SYNTHETIC_DOCUMENT_BODY_MUST_NOT_LEAK"
    world["document_version"].content = secret_marker
    world["db"].flush()
    service = SupplyService()
    service.create_request(world["db"], actor_user_id=world["editor"].id, command=_supply_request(world))
    world["db"].flush()

    audit_payload = "\n".join(row.details or "" for row in world["db"].scalars(select(AuditLog)))
    job_payload = "\n".join(str(row.payload) for row in world["db"].scalars(select(BackgroundJob)))
    assert secret_marker not in audit_payload
    assert secret_marker not in job_payload
    assert world["document"].name not in audit_payload


def test_supply_evidence_selector_returns_only_exact_current_project_pins(financial_world):
    world = financial_world

    result = list_current_project_evidence(
        project_id=world["project"].id,
        db=world["db"],
        user=world["viewer"],
    )

    assert result == {
        "projectId": world["project"].id,
        "items": [{
            "evidenceId": world["evidence"].id,
            "evidenceRevision": 1,
            "sourceVersionId": world["source_version"].id,
            "documentVersionId": world["document_version"].id,
            "assessmentVersion": 1,
            "verification": "verified",
            "confidence": 0.96,
            "locator": {"kind": "page", "page": 2},
        }],
        "total": 1,
    }


def test_supply_evidence_selector_enforces_project_role(financial_world):
    world = financial_world
    with pytest.raises(HTTPException) as denied:
        list_current_project_evidence(
            project_id=world["project"].id,
            db=world["db"],
            user=world["outsider"],
        )
    assert denied.value.status_code == 403


def test_supply_router_requires_one_matching_idempotency_key():
    _require_idempotency("supply-command-123", "supply-command-123")
    with pytest.raises(HTTPException) as conflict:
        _require_idempotency("supply-command-123", "different-command")
    assert conflict.value.status_code == 409
    assert conflict.value.detail == "idempotency_key_conflict"


def _recorded_supply_and_budget(world):
    service = SupplyService()
    created = service.create_request(
        world["db"], actor_user_id=world["editor"].id, command=_supply_request(world)
    )
    common = {
        "organization_id": world["organization"].id,
        "project_id": world["project"].id,
        "supply_case_id": created.supply_case_id,
    }
    service.approve_request(
        world["db"], **common, actor_user_id=world["manager"].id,
        command=VersionedCommand(command_key="dds:approve:request", expected_version=1),
    )
    service.prepare_order(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=PrepareOrder(command_key="dds:prepare:order", expected_version=2,
                             ordered_quantity=Decimal("3.125"), order_reference="PO-DDS-1"),
    )
    service.approve_order(
        world["db"], **common, actor_user_id=world["manager"].id,
        command=VersionedCommand(command_key="dds:approve:order", expected_version=3),
    )
    service.record_order(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=RecordOrder(command_key="dds:record:order", expected_version=4,
                            evidence=_supply_pin(world)),
    )
    budget_result = create_budget(
        BudgetCreate(
            project_id=world["project"].id, contract_id=world["contract"].id,
            schedule_item_id=world["stage"].id, task_id=world["task"].id,
            category="supply", description="Synthetic supply budget",
            planned_amount=Decimal("1000.00"), currency="RUB", **_finance_pin(world),
        ), world["db"], world["editor"],
    )
    budget = world["db"].get(BudgetLine, budget_result["id"])
    update_status("budget", budget.id, StatusUpdate(status="approved"),
                  world["db"], world["manager"])
    return service, common, world["db"].get(SupplyCase, created.supply_case_id), budget


def _dds_command(world, budget, *, key="dds:proposal:0001", expected_version=5):
    return CreateDdsProposal(
        command_key=key,
        expected_version=expected_version,
        contract_id=world["contract"].id,
        schedule_item_id=world["stage"].id,
        budget_line_id=budget.id,
        planned_date=date(2026, 9, 12),
        amount=Decimal("313.28"),
        currency="RUB",
        evidence_assessment_version=world["assessment"].record_version,
        evidence=_supply_pin(world),
    )


def test_supply_dds_is_only_exact_evidence_backed_proposal_until_human_confirms(financial_world):
    world = financial_world
    service, common, supply, budget = _recorded_supply_and_budget(world)
    command = _dds_command(world, budget)

    result = service.create_dds_proposal(
        world["db"], **common, actor_user_id=world["editor"].id, command=command,
    )
    replay = service.create_dds_proposal(
        world["db"], **common, actor_user_id=world["editor"].id, command=command,
    )
    cash = world["db"].get(CashFlowEntry, result.cash_flow_id)

    assert replay == result.model_copy(update={"already_applied": True})
    assert (cash.status, cash.actual_amount, cash.actual_date) == ("proposed", Decimal("0"), None)
    assert (cash.contract_id, cash.schedule_item_id, cash.budget_line_id) == (
        world["contract"].id, world["stage"].id, budget.id,
    )
    assert (cash.evidence_id, cash.evidence_revision, cash.source_document_version_id) == (
        world["evidence"].id, 1, world["document_version"].id,
    )
    assert result.requires_human_confirmation is True
    assert result.payment_created is False
    assert supply.record_version == 6
    assert world["db"].scalar(select(func.count(BackgroundJob.id))) == 0

    update_status("cash-flow", cash.id, StatusUpdate(status="approved", expected_status="proposed"),
                  world["db"], world["manager"])
    assert (cash.status, cash.actual_date, cash.actual_amount) == ("approved", None, Decimal("0"))


def test_supply_dds_fails_closed_for_stale_cas_scope_role_and_assessment(financial_world):
    world = financial_world
    service, common, _supply, budget = _recorded_supply_and_budget(world)
    command = _dds_command(world, budget)

    with pytest.raises(SupplyDenied, match="resource_unavailable"):
        service.create_dds_proposal(
            world["db"], **common, actor_user_id=world["viewer"].id, command=command,
        )
    with pytest.raises(SupplyDenied, match="resource_unavailable"):
        service.create_dds_proposal(
            world["db"], organization_id=world["organization"].id,
            project_id=world["other_project"].id, supply_case_id=common["supply_case_id"],
            actor_user_id=world["editor"].id, command=command,
        )
    with pytest.raises(SupplyConflict, match="record_version_conflict"):
        service.create_dds_proposal(
            world["db"], **common, actor_user_id=world["editor"].id,
            command=command.model_copy(update={"expected_version": 4, "command_key": "dds:stale:0001"}),
        )

    world["assessment"].freshness = "stale"
    world["db"].flush()
    with pytest.raises(SupplyDenied, match="manual_review_required"):
        service.create_dds_proposal(
            world["db"], **common, actor_user_id=world["editor"].id,
            command=command.model_copy(update={"command_key": "dds:stale:evidence"}),
        )
    assert world["db"].scalar(select(func.count(CashFlowEntry.id))) == 0
