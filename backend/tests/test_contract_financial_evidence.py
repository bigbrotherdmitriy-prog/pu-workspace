from datetime import datetime, timezone
from decimal import Decimal

from app.contract_evidence import extract_contract_evidence, persist_contract_evidence
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.v54_pilot import (
    ConnectionIdentity,
    Evidence,
    EvidenceAssessment,
    SourceCurrent,
    SourceReference,
    SourceVersion,
)
from sqlalchemy import func, select


TEXT = (
    "Договор № DCI-01 от 31.08.2026.\n"
    "Заказчик: ООО Альфа.\n"
    "Подрядчик: ООО Бета.\n"
    "Цена настоящего договора составляет 10 000 000,00 руб.\n"
    "Заказчик выплачивает аванс 2 000 000,00 руб.\n"
    "Гарантийное удержание составляет 5%.\n"
    "Срок действия договора до 31.12.2027.\n"
)


def _document_source(db_session, content=TEXT, *, bind_document_version=True):
    organization = Organization(name="Синтетическая организация")
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Синтетический проект", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    document = Document(
        project_id=project.id,
        external_id="synthetic-contract",
        name="Договор DCI-01.txt",
        source="local_upload",
        current_version=1,
    )
    db_session.add(document)
    db_session.flush()
    document_version = DocumentVersion(document_id=document.id, version_number=1, content=content)
    db_session.add(document_version)
    db_session.flush()
    identity = ConnectionIdentity(
        organization_id=organization.id,
        provider="local_upload",
        account_key="synthetic-owner",
        state="verified",
        binding_epoch=1,
        record_version=1,
        credential_generation=1,
        verified_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    db_session.add(identity)
    db_session.flush()
    source = SourceReference(
        organization_id=organization.id,
        origin_project_id=project.id,
        identity_id=identity.id,
        namespace="local-upload",
        external_id="synthetic-contract",
        external_id_kind="stable_id",
        incarnation=1,
        object_kind="file",
        canonical_locator={"kind": "opaque_id", "value": "synthetic-contract"},
        freshness="fresh",
        sync_state="current",
        availability="available",
        last_seen_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        last_checked_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        policy_pins={"access": {"opaque": "policy"}},
        residency={"source_location": "synthetic", "assurance": "test_only"},
    )
    db_session.add(source)
    db_session.flush()
    version = SourceVersion(
        organization_id=organization.id,
        source_id=source.id,
        observation_key="synthetic-version-1",
        provider_revision="revision-1",
        consistency="revision_bound",
        locator_at_observation={"kind": "opaque_id", "value": "synthetic-contract"},
        integrity=[],
        observed_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        legacy_document_version_id=document_version.id if bind_document_version else None,
    )
    db_session.add(version)
    db_session.flush()
    db_session.add(SourceCurrent(
        source_id=source.id,
        organization_id=organization.id,
        version_id=version.id,
    ))
    contract = Contract(
        project_id=project.id,
        number="DCI-01",
        title="Синтетический договор",
        status="active",
        source_document_id=document.id,
    )
    db_session.add(contract)
    db_session.flush()
    return project, contract, document_version, source, version


def test_extracts_all_contract_terms_with_exact_text_locators():
    result = extract_contract_evidence(TEXT)

    assert result["status"] == "ready"
    assert result["manual_review_required"] is False
    assert result["amount"] == Decimal("10000000.00")
    assert result["advance_amount"] == Decimal("2000000.00")
    assert result["retention_percent"] == Decimal("5")
    assert result["signed_at"].isoformat() == "2026-08-31"
    assert result["term_until"].isoformat() == "2027-12-31"
    assert result["parties"] == {"customer": "ООО Альфа", "contractor": "ООО Бета"}
    for field in ("amount", "advance_amount", "retention_percent", "signed_at", "term_until", "party_customer", "party_contractor"):
        proof = result["field_evidence"][field][0]
        assert proof["locator"]["kind"] == "text_range"
        assert TEXT[proof["locator"]["start"]:proof["locator"]["end"]] == proof["excerpt"]
        assert proof["confidence"] >= 0.95


def test_conflicting_amount_is_fail_closed_and_never_returns_a_value_for_application():
    result = extract_contract_evidence(
        TEXT + "Дополнительное условие: цена договора составляет 11 000 000 руб.\n"
    )

    assert result["status"] == "manual_review_required"
    assert result["manual_review_required"] is True
    assert result["amount"] is None
    assert "amount_conflict" in result["reason_codes"]
    assert len(result["field_evidence"]["amount"]) == 2


def test_low_confidence_derived_advance_requires_review():
    result = extract_contract_evidence(
        "Цена настоящего договора составляет 10 000 000 руб.\n"
        "Размер аванса составляет 20%.\n"
    )

    assert result["advance_amount"] == Decimal("2000000.00")
    assert result["status"] == "manual_review_required"
    assert "advance_amount_low_confidence" in result["reason_codes"]


def test_no_recognized_terms_is_not_reported_as_ready():
    result = extract_contract_evidence("Приложение содержит общие положения без реквизитов.")

    assert result["status"] == "manual_review_required"
    assert result["reason_codes"] == ["no_contract_terms_extracted"]


def test_persists_immutable_evidence_bound_to_exact_source_and_document_version(db_session):
    project, _contract, document_version, source, version = _document_source(db_session)
    extracted = extract_contract_evidence(TEXT)

    persisted = persist_contract_evidence(
        db_session,
        organization_id=project.organization_id,
        project_id=project.id,
        document_version=document_version,
        extraction=extracted,
    )

    assert persisted["status"] == "ready"
    assert persisted["manual_review_required"] is False
    assert persisted["source_id"] == source.id
    assert persisted["source_version_id"] == version.id
    assert persisted["document_version_id"] == document_version.id
    assert persisted["evidence"]
    for proof in persisted["evidence"]:
        row = db_session.get(Evidence, proof["evidence_id"])
        assessment = db_session.get(EvidenceAssessment, proof["evidence_id"])
        assert row.source_id == source.id
        assert row.source_version_id == version.id
        assert row.locator == proof["locator"]
        assert row.confidence == proof["confidence"]
        assert assessment.verification == "unverified"
        assert "ООО Альфа" not in str(row.locator) + str(row.extractor)

    repeated = persist_contract_evidence(
        db_session,
        organization_id=project.organization_id,
        project_id=project.id,
        document_version=document_version,
        extraction=extracted,
    )
    assert repeated == persisted
    assert db_session.scalar(select(func.count()).select_from(Evidence)) == len(persisted["evidence"])


def test_missing_exact_source_version_requires_review_and_writes_no_evidence(db_session):
    project, contract, document_version, _source, _version = _document_source(
        db_session, bind_document_version=False,
    )
    extracted = extract_contract_evidence(TEXT)

    persisted = persist_contract_evidence(
        db_session,
        organization_id=project.organization_id,
        project_id=project.id,
        document_version=document_version,
        extraction=extracted,
    )

    assert persisted["status"] == "manual_review_required"
    assert persisted["reason_codes"] == ["exact_source_version_unavailable"]
    assert persisted["evidence"] == []
    assert contract.amount is None
