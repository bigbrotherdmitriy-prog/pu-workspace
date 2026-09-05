from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - register the existing schema for isolated create_all
from app.database import Base
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import ScheduleBaseline, ScheduleItem
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.user import User
from app.models.v54_pilot import (
    ConnectionIdentity,
    Evidence,
    EvidenceAssessment,
    SourceCurrent,
    SourceReference,
    SourceVersion,
)
from app.mvp4.supply.contracts import (
    CreateSupplyRequest,
    EvidenceLink,
    PrepareOrder,
    ProposeAcceptanceAct,
    RecordDelivery,
    RecordOrder,
    ResolveDiscrepancy,
    ReviewSupplyRequest,
    VersionedCommand,
)
from app.mvp4.supply.models import SupplyCase, SupplyCaseVersion, SupplyCommandReceipt
from app.mvp4.supply.router import router
from app.mvp4.supply.service import SupplyConflict, SupplyDenied, SupplyService


NOW = datetime.now(timezone.utc)


def uid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


@pytest.fixture()
def world():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        organization = Organization(name="Synthetic supply tenant")
        manager = User(name="Synthetic manager", email="manager@supply.invalid", is_admin=False)
        editor = User(name="Synthetic editor", email="editor@supply.invalid", is_admin=False)
        db.add_all([organization, manager, editor])
        db.flush()
        project = Project(name="Synthetic project", organization_id=organization.id)
        other_project = Project(name="Other synthetic project", organization_id=organization.id)
        db.add_all([project, other_project])
        db.flush()
        db.add_all([
            ProjectMember(project_id=project.id, user_id=manager.id, role="manager"),
            ProjectMember(project_id=project.id, user_id=editor.id, role="editor"),
        ])
        db.flush()
        contract = Contract(
            project_id=project.id,
            number="SYN-SUPPLY-1",
            title="Synthetic supply contract",
            status="active",
        )
        db.add(contract)
        db.flush()
        baseline = ScheduleBaseline(
            project_id=project.id,
            contract_id=contract.id,
            created_by_user_id=manager.id,
            name="Synthetic GPR",
            version=3,
            status="approved",
        )
        db.add(baseline)
        db.flush()
        stage = ScheduleItem(
            project_id=project.id,
            baseline_id=baseline.id,
            title="Synthetic installation",
            planned_finish=date(2026, 10, 1),
            planned_progress=100,
            actual_progress=0,
            status="planned",
        )
        task = Task(
            project_id=project.id,
            assignee_user_id=editor.id,
            created_by_user_id=manager.id,
            title="Synthetic purchase task",
            status="assigned",
            source_type="synthetic",
            source_file_id="synthetic-source",
            source_file_name="synthetic.txt",
            source_excerpt="synthetic only",
            source_excerpt_hash="a" * 64,
            confidence=1.0,
            needs_review=False,
        )
        document = Document(
            project_id=project.id,
            name="synthetic-invoice.txt",
            source="synthetic",
            status="ready",
        )
        db.add_all([stage, task, document])
        db.flush()
        document_version = DocumentVersion(
            document_id=document.id,
            version_number=1,
            content="synthetic fixture only",
        )
        db.add(document_version)
        db.flush()
        identity = ConnectionIdentity(
            id=uid(1),
            organization_id=organization.id,
            provider="synthetic",
            account_key="synthetic-supply-account",
            state="verified",
            credential_generation=1,
            verified_at=NOW,
        )
        db.add(identity)
        db.flush()
        source = SourceReference(
            id=uid(2),
            organization_id=organization.id,
            origin_project_id=project.id,
            identity_id=identity.id,
            namespace="synthetic-supply",
            external_id="synthetic-document",
            external_id_kind="stable_id",
            object_kind="file",
            canonical_locator={"kind": "opaque_id", "value": "synthetic-document"},
        )
        db.add(source)
        db.flush()
        source_version = SourceVersion(
            id=uid(3),
            organization_id=organization.id,
            source_id=source.id,
            observation_key="synthetic-supply-v1",
            provider_revision="fixture-v1",
            consistency="revision_bound",
            locator_at_observation={"kind": "opaque_id", "value": "synthetic-document-v1"},
            integrity=[],
            observed_at=NOW,
            legacy_document_version_id=document_version.id,
        )
        db.add(source_version)
        db.flush()
        db.add(SourceCurrent(
            organization_id=organization.id,
            source_id=source.id,
            version_id=source_version.id,
        ))
        db.flush()
        evidence = Evidence(
            id=uid(4),
            organization_id=organization.id,
            source_id=source.id,
            source_version_id=source_version.id,
            locator={"kind": "text_range", "start": 10, "end": 40},
            extractor={"name": "synthetic", "version": "1"},
            confidence=0.96,
            confidence_kind="model",
            extracted_at=NOW,
        )
        db.add(evidence)
        db.flush()
        assessment = EvidenceAssessment(
            evidence_id=evidence.id,
            organization_id=organization.id,
            verification="verified",
            freshness="fresh",
            availability="available",
            checked_at=NOW,
            valid_until=NOW + timedelta(hours=1),
            reviewed_by=manager.id,
            reviewed_at=NOW,
        )
        db.add(assessment)
        db.commit()
        yield {
            "db": db,
            "organization": organization,
            "manager": manager,
            "editor": editor,
            "project": project,
            "other_project": other_project,
            "contract": contract,
            "baseline": baseline,
            "stage": stage,
            "task": task,
            "document": document,
            "document_version": document_version,
            "source": source,
            "source_version": source_version,
            "evidence": evidence,
            "assessment": assessment,
        }
    engine.dispose()


def evidence_link(world) -> EvidenceLink:
    return EvidenceLink(
        evidence_id=UUID(world["evidence"].id),
        evidence_revision=1,
        source_version_id=UUID(world["source_version"].id),
        document_version_id=world["document_version"].id,
    )


def create_command(world, *, key="request:synthetic:0001", evidence: EvidenceLink | None = None) -> CreateSupplyRequest:
    return CreateSupplyRequest(
        command_key=key,
        organization_id=world["organization"].id,
        project_id=world["project"].id,
        contract_id=world["contract"].id,
        schedule_baseline_id=world["baseline"].id,
        schedule_baseline_version=world["baseline"].version,
        schedule_item_id=world["stage"].id,
        task_id=world["task"].id,
        evidence=evidence or evidence_link(world),
        title="Synthetic equipment",
        supplier="Synthetic supplier",
        requested_quantity=Decimal("10"),
        unit="pcs",
        unit_price=Decimal("1250.00"),
        currency="RUB",
    )


def create_case(world, *, key="request:synthetic:0001",
                evidence: EvidenceLink | None = None) -> tuple[SupplyService, SupplyCase]:
    service = SupplyService()
    result = service.create_request(
        world["db"], actor_user_id=world["editor"].id,
        command=create_command(world, key=key, evidence=evidence),
    )
    world["db"].flush()
    return service, world["db"].get(SupplyCase, result.supply_case_id)


def alternate_evidence(world, *, number: int, confidence: float, verified: bool = True) -> EvidenceLink:
    evidence = Evidence(
        id=uid(number),
        organization_id=world["organization"].id,
        source_id=world["source"].id,
        source_version_id=world["source_version"].id,
        locator={"kind": "text_range", "start": number, "end": number + 10},
        extractor={"name": "synthetic", "version": "1"},
        confidence=confidence,
        confidence_kind="model",
        extracted_at=NOW,
    )
    world["db"].add(evidence)
    world["db"].flush()
    world["db"].add(EvidenceAssessment(
        evidence_id=evidence.id,
        organization_id=world["organization"].id,
        verification="verified" if verified else "unverified",
        freshness="fresh",
        availability="available",
        checked_at=NOW,
        valid_until=NOW + timedelta(hours=1),
        reviewed_by=world["manager"].id if verified else None,
        reviewed_at=NOW if verified else None,
    ))
    world["db"].flush()
    return EvidenceLink(
        evidence_id=UUID(evidence.id),
        evidence_revision=1,
        source_version_id=UUID(world["source_version"].id),
        document_version_id=world["document_version"].id,
    )


def advance_to_recorded_order(world) -> tuple[SupplyService, SupplyCase]:
    service, row = create_case(world)
    common = {
        "organization_id": row.organization_id,
        "project_id": row.project_id,
        "supply_case_id": row.id,
    }
    service.approve_request(
        world["db"], **common, actor_user_id=world["manager"].id,
        command=VersionedCommand(command_key="approve:request:0001", expected_version=1),
    )
    service.prepare_order(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=PrepareOrder(
            command_key="prepare:order:0001", expected_version=2,
            ordered_quantity=Decimal("10"), order_reference="PO-SYN-1",
        ),
    )
    service.approve_order(
        world["db"], **common, actor_user_id=world["manager"].id,
        command=VersionedCommand(command_key="approve:order:0001", expected_version=3),
    )
    service.record_order(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=RecordOrder(
            command_key="record:order:0001", expected_version=4, evidence=evidence_link(world),
        ),
    )
    world["db"].flush()
    return service, row


def test_router_exposes_complete_internal_supply_chain_without_mounting_main():
    paths = {route.path for route in router.routes}
    assert paths == {
        "/api/mvp4/supply",
        "/api/mvp4/supply/requests",
        "/api/mvp4/supply/{supply_case_id}/review",
        "/api/mvp4/supply/{supply_case_id}/approve-request",
        "/api/mvp4/supply/{supply_case_id}/order",
        "/api/mvp4/supply/{supply_case_id}/approve-order",
        "/api/mvp4/supply/{supply_case_id}/record-order",
        "/api/mvp4/supply/{supply_case_id}/deliveries",
        "/api/mvp4/supply/{supply_case_id}/resolve-discrepancy",
        "/api/mvp4/supply/{supply_case_id}/acceptance-acts",
        "/api/mvp4/supply/{supply_case_id}/approve-acceptance-act",
        "/api/mvp4/supply/{supply_case_id}/dds-proposals",
        "/api/mvp4/supply/{supply_case_id}/history",
    }


def test_verified_request_preserves_all_exact_control_links(world):
    service, row = create_case(world)

    assert row.status == "request_pending_approval"
    assert row.review_state == "verified"
    assert (row.project_id, row.contract_id, row.schedule_baseline_id, row.schedule_baseline_version) == (
        world["project"].id,
        world["contract"].id,
        world["baseline"].id,
        world["baseline"].version,
    )
    assert (row.schedule_item_id, row.task_id, row.document_version_id) == (
        world["stage"].id,
        world["task"].id,
        world["document_version"].id,
    )
    assert (row.evidence_id, row.evidence_revision, row.source_version_id) == (
        world["evidence"].id,
        1,
        world["source_version"].id,
    )
    history = service.history(
        world["db"], organization_id=row.organization_id, project_id=row.project_id, supply_case_id=row.id
    )
    assert history[0].evidence_pin == evidence_link(world).model_dump(mode="json")
    assert history[0].snapshot["external_action_status"] == "not_created"


def test_full_partial_delivery_and_acceptance_sequence_is_versioned(world):
    service, row = advance_to_recorded_order(world)
    common = {"organization_id": row.organization_id, "project_id": row.project_id, "supply_case_id": row.id}

    first_delivery = service.record_delivery(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=RecordDelivery(
            command_key="delivery:partial:0001", expected_version=5,
            delivered_quantity=Decimal("4"), evidence=evidence_link(world),
        ),
    )
    assert first_delivery.status == "partially_delivered"
    proposed = service.propose_acceptance_act(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=ProposeAcceptanceAct(
            command_key="act:partial:0001", expected_version=6,
            accepted_quantity=Decimal("4"), act_number="ACT-SYN-1", evidence=evidence_link(world),
        ),
    )
    assert proposed.status == "act_pending_approval"
    approved = service.approve_acceptance_act(
        world["db"], **common, actor_user_id=world["manager"].id,
        command=VersionedCommand(command_key="act:approve:0001", expected_version=7),
    )
    assert approved.status == "partially_accepted"
    service.record_delivery(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=RecordDelivery(
            command_key="delivery:final:0001", expected_version=8,
            delivered_quantity=Decimal("6"), evidence=evidence_link(world),
        ),
    )
    service.propose_acceptance_act(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=ProposeAcceptanceAct(
            command_key="act:final:0001", expected_version=9,
            accepted_quantity=Decimal("6"), act_number="ACT-SYN-2", evidence=evidence_link(world),
        ),
    )
    final = service.approve_acceptance_act(
        world["db"], **common, actor_user_id=world["manager"].id,
        command=VersionedCommand(command_key="act:approve:0002", expected_version=10),
    )

    assert final.status == "accepted"
    assert row.delivered_quantity == row.accepted_quantity == row.ordered_quantity == Decimal("10")
    events = [entry.event for entry in service.history(world["db"], **common)]
    assert events == [
        "request_created",
        "request_approved",
        "order_prepared",
        "order_approved_internal",
        "order_recorded",
        "delivery_recorded",
        "acceptance_act_proposed",
        "acceptance_act_approved_internal",
        "delivery_recorded",
        "acceptance_act_proposed",
        "acceptance_act_approved_internal",
    ]
    assert row.external_action_status == "not_created"


def test_low_confidence_requires_human_review_before_manager_approval(world):
    low = alternate_evidence(world, number=40, confidence=0.4)
    service, row = create_case(world, evidence=low)
    assert (row.status, row.review_state) == ("needs_review", "needs_review")

    with pytest.raises(SupplyConflict, match="invalid_supply_transition"):
        service.approve_request(
            world["db"], organization_id=row.organization_id, project_id=row.project_id,
            supply_case_id=row.id, actor_user_id=world["manager"].id,
            command=VersionedCommand(command_key="approve:blocked:0001", expected_version=1),
        )

    reviewed = service.review_request(
        world["db"], organization_id=row.organization_id, project_id=row.project_id,
        supply_case_id=row.id, actor_user_id=world["manager"].id,
        command=ReviewSupplyRequest(
            command_key="review:human:0001", expected_version=1, decision="confirm",
            corrected_quantity=Decimal("8"), corrected_unit_price=Decimal("1200"),
        ),
    )
    assert reviewed.status == "request_pending_approval"
    assert row.reviewed_by_user_id == world["manager"].id
    assert row.requested_quantity == Decimal("8")


def test_unverified_transition_evidence_is_fail_closed(world):
    service, row = advance_to_recorded_order(world)
    world["assessment"].verification = "unverified"
    world["assessment"].reviewed_by = None
    world["assessment"].reviewed_at = None
    world["db"].flush()

    with pytest.raises(SupplyDenied, match="manual_review_required"):
        service.record_delivery(
            world["db"], organization_id=row.organization_id, project_id=row.project_id,
            supply_case_id=row.id, actor_user_id=world["editor"].id,
            command=RecordDelivery(
                command_key="delivery:unverified:0001", expected_version=5,
                delivered_quantity=Decimal("1"), evidence=evidence_link(world),
            ),
        )
    assert row.delivered_quantity == 0


@pytest.mark.parametrize("broken", ["project", "contract", "baseline", "stage", "task"])
def test_cross_scope_or_stale_control_link_is_rejected(world, broken):
    command = create_command(world).model_copy(deep=True)
    changes = {
        "project": {"project_id": world["other_project"].id},
        "contract": {"contract_id": 99999},
        "baseline": {"schedule_baseline_version": world["baseline"].version + 1},
        "stage": {"schedule_item_id": 99999},
        "task": {"task_id": 99999},
    }[broken]
    command = command.model_copy(update=changes)

    with pytest.raises(SupplyDenied, match="resource_unavailable"):
        SupplyService().create_request(world["db"], actor_user_id=world["editor"].id, command=command)
    assert world["db"].scalar(select(SupplyCase.id)) is None


def test_document_version_and_source_version_must_be_the_same_observation(world):
    other_document = Document(project_id=world["project"].id, name="other.txt", source="synthetic", status="ready")
    world["db"].add(other_document)
    world["db"].flush()
    other_version = DocumentVersion(document_id=other_document.id, version_number=1, content="synthetic")
    world["db"].add(other_version)
    world["db"].flush()
    command = create_command(world).model_copy(update={
        "evidence": evidence_link(world).model_copy(update={"document_version_id": other_version.id})
    })

    with pytest.raises(SupplyDenied, match="resource_unavailable"):
        SupplyService().create_request(world["db"], actor_user_id=world["editor"].id, command=command)


def test_create_and_transition_replays_are_idempotent_but_collisions_fail(world):
    service = SupplyService()
    command = create_command(world)
    first = service.create_request(world["db"], actor_user_id=world["editor"].id, command=command)
    replay = service.create_request(world["db"], actor_user_id=world["editor"].id, command=command)
    assert replay == first.model_copy(update={"already_applied": True})
    with pytest.raises(SupplyConflict, match="idempotency_key_conflict"):
        service.create_request(
            world["db"], actor_user_id=world["editor"].id,
            command=command.model_copy(update={"supplier": "Different synthetic supplier"}),
        )

    row = world["db"].get(SupplyCase, first.supply_case_id)
    approval = VersionedCommand(command_key="approve:idempotent:0001", expected_version=1)
    first_approval = service.approve_request(
        world["db"], organization_id=row.organization_id, project_id=row.project_id,
        supply_case_id=row.id, actor_user_id=world["manager"].id, command=approval,
    )
    approval_replay = service.approve_request(
        world["db"], organization_id=row.organization_id, project_id=row.project_id,
        supply_case_id=row.id, actor_user_id=world["manager"].id, command=approval,
    )
    assert approval_replay == first_approval.model_copy(update={"already_applied": True})
    assert world["db"].scalar(select(SupplyCommandReceipt).where(
        SupplyCommandReceipt.command_key == approval.command_key
    )) is not None


def test_stale_cas_and_preapproval_order_are_rejected(world):
    service, row = create_case(world)
    with pytest.raises(SupplyConflict, match="invalid_supply_transition"):
        service.prepare_order(
            world["db"], organization_id=row.organization_id, project_id=row.project_id,
            supply_case_id=row.id, actor_user_id=world["editor"].id,
            command=PrepareOrder(
                command_key="order:before:approval", expected_version=1,
                ordered_quantity=Decimal("5"), order_reference="PO-BLOCKED",
            ),
        )
    service.approve_request(
        world["db"], organization_id=row.organization_id, project_id=row.project_id,
        supply_case_id=row.id, actor_user_id=world["manager"].id,
        command=VersionedCommand(command_key="approve:for:cas:01", expected_version=1),
    )
    with pytest.raises(SupplyConflict, match="record_version_conflict"):
        service.prepare_order(
            world["db"], organization_id=row.organization_id, project_id=row.project_id,
            supply_case_id=row.id, actor_user_id=world["editor"].id,
            command=PrepareOrder(
                command_key="order:stale:version", expected_version=1,
                ordered_quantity=Decimal("5"), order_reference="PO-STALE",
            ),
        )


def test_editor_cannot_approve_monetary_or_legal_records_even_via_service(world):
    service, row = create_case(world)
    with pytest.raises(SupplyDenied, match="resource_unavailable"):
        service.approve_request(
            world["db"], organization_id=row.organization_id, project_id=row.project_id,
            supply_case_id=row.id, actor_user_id=world["editor"].id,
            command=VersionedCommand(command_key="editor:cannot:approve", expected_version=1),
        )
    assert row.status == "request_pending_approval"


def test_manager_approval_rechecks_evidence_freshness_immediately_before_commitment(world):
    service, row = create_case(world)
    world["assessment"].valid_until = NOW - timedelta(minutes=1)
    world["db"].flush()
    with pytest.raises(SupplyDenied, match="manual_review_required"):
        service.approve_request(
            world["db"], organization_id=row.organization_id, project_id=row.project_id,
            supply_case_id=row.id, actor_user_id=world["manager"].id,
            command=VersionedCommand(command_key="approve:stale:evidence", expected_version=1),
        )
    assert row.status == "request_pending_approval"


def test_order_cannot_exceed_approved_request(world):
    service, row = create_case(world)
    common = {"organization_id": row.organization_id, "project_id": row.project_id, "supply_case_id": row.id}
    service.approve_request(
        world["db"], **common, actor_user_id=world["manager"].id,
        command=VersionedCommand(command_key="approve:quantity:01", expected_version=1),
    )
    with pytest.raises(SupplyConflict, match="invalid_supply_transition"):
        service.prepare_order(
            world["db"], **common, actor_user_id=world["editor"].id,
            command=PrepareOrder(
                command_key="order:too:large:01", expected_version=2,
                ordered_quantity=Decimal("11"), order_reference="PO-LARGE",
            ),
        )


def test_delivery_discrepancy_blocks_acceptance_and_overdelivery_needs_order_correction(world):
    service, row = advance_to_recorded_order(world)
    common = {"organization_id": row.organization_id, "project_id": row.project_id, "supply_case_id": row.id}
    result = service.record_delivery(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=RecordDelivery(
            command_key="delivery:over:0001", expected_version=5,
            delivered_quantity=Decimal("11"), evidence=evidence_link(world),
            discrepancy_code="quantity", discrepancy_note="Synthetic overdelivery",
        ),
    )
    assert result.status == "delivery_discrepancy"
    with pytest.raises(SupplyConflict, match="invalid_supply_transition"):
        service.propose_acceptance_act(
            world["db"], **common, actor_user_id=world["editor"].id,
            command=ProposeAcceptanceAct(
                command_key="act:blocked:0001", expected_version=6,
                accepted_quantity=Decimal("1"), act_number="ACT-BLOCKED", evidence=evidence_link(world),
            ),
        )
    with pytest.raises(SupplyConflict, match="order_correction_required"):
        service.resolve_discrepancy(
            world["db"], **common, actor_user_id=world["manager"].id,
            command=ResolveDiscrepancy(
                command_key="resolve:over:0001", expected_version=6,
                decision="accept_recorded_quantity",
            ),
        )


def test_acceptance_cannot_exceed_verified_delivery(world):
    service, row = advance_to_recorded_order(world)
    common = {"organization_id": row.organization_id, "project_id": row.project_id, "supply_case_id": row.id}
    service.record_delivery(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=RecordDelivery(
            command_key="delivery:only:two:01", expected_version=5,
            delivered_quantity=Decimal("2"), evidence=evidence_link(world),
        ),
    )
    with pytest.raises(SupplyConflict, match="acceptance_exceeds_delivery"):
        service.propose_acceptance_act(
            world["db"], **common, actor_user_id=world["editor"].id,
            command=ProposeAcceptanceAct(
                command_key="act:too:large:0001", expected_version=6,
                accepted_quantity=Decimal("3"), act_number="ACT-LARGE", evidence=evidence_link(world),
            ),
        )


def test_act_approval_rechecks_the_exact_act_evidence(world):
    service, row = advance_to_recorded_order(world)
    common = {"organization_id": row.organization_id, "project_id": row.project_id, "supply_case_id": row.id}
    service.record_delivery(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=RecordDelivery(
            command_key="delivery:act:recheck", expected_version=5,
            delivered_quantity=Decimal("10"), evidence=evidence_link(world),
        ),
    )
    service.propose_acceptance_act(
        world["db"], **common, actor_user_id=world["editor"].id,
        command=ProposeAcceptanceAct(
            command_key="act:recheck:proposal", expected_version=6,
            accepted_quantity=Decimal("10"), act_number="ACT-RECHECK", evidence=evidence_link(world),
        ),
    )
    world["assessment"].freshness = "stale"
    world["db"].flush()
    with pytest.raises(SupplyDenied, match="manual_review_required"):
        service.approve_acceptance_act(
            world["db"], **common, actor_user_id=world["manager"].id,
            command=VersionedCommand(command_key="act:recheck:approve", expected_version=7),
        )
    assert row.status == "act_pending_approval"


def test_history_and_receipts_are_immutable_and_audit_has_no_business_content(world):
    service, row = create_case(world)
    world["db"].commit()
    version = world["db"].scalar(select(SupplyCaseVersion).where(SupplyCaseVersion.supply_case_id == row.id))
    receipt = world["db"].scalar(select(SupplyCommandReceipt).where(SupplyCommandReceipt.supply_case_id == row.id))
    receipt_id = receipt.id
    version.event = "tampered"
    with pytest.raises(ValueError, match="supply_history_is_append_only"):
        world["db"].flush()
    world["db"].rollback()

    receipt = world["db"].get(SupplyCommandReceipt, receipt_id)
    receipt.result_status = "tampered"
    with pytest.raises(ValueError, match="supply_history_is_append_only"):
        world["db"].flush()
    world["db"].rollback()

    audits = list(world["db"].scalars(select(AuditLog).where(AuditLog.entity_type == "supply_case")))
    assert audits
    assert all(audit.details is None for audit in audits)
    assert not any("Synthetic" in (audit.action + (audit.details or "")) for audit in audits)


def test_rejected_low_confidence_request_cannot_enter_procurement_chain(world):
    low = alternate_evidence(world, number=41, confidence=0.2)
    service, row = create_case(world, evidence=low)
    rejected = service.review_request(
        world["db"], organization_id=row.organization_id, project_id=row.project_id,
        supply_case_id=row.id, actor_user_id=world["manager"].id,
        command=ReviewSupplyRequest(
            command_key="review:reject:0001", expected_version=1, decision="reject"
        ),
    )
    assert rejected.status == "request_rejected"
    with pytest.raises(SupplyConflict, match="invalid_supply_transition"):
        service.approve_request(
            world["db"], organization_id=row.organization_id, project_id=row.project_id,
            supply_case_id=row.id, actor_user_id=world["manager"].id,
            command=VersionedCommand(command_key="approve:rejected:01", expected_version=2),
        )
