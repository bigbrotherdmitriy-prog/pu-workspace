"""C01 uses corpus bytes as input and the existing A/B/C/Trust implementation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.v54_dto import ActionEnvelope, canonical_json
from app.core.v54_interfaces import ContextConfirmation, ReviewCommand
from app.core.v54_permissions import SourceEvidenceError
from app.core.v54_refs import ObjectRef, VersionPin
from app.models.management import Obligation
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.task import Task
from app.models.v54_pilot import ContextRelation, DeadlineClaim, Evidence, EvidenceAssessment
from app.pilot_dispatch import synthetic_command_key
from app.source_evidence.synthetic_content import (
    SyntheticCorpusContentPipeline,
    extract_synthetic_content,
)
from app.source_evidence.fragment_reader import TextRangeLocator
from test_v54_pilot_integration import approve_dispatch, execute, integrated
from test_v54_source_evidence_pilot import scope
from v54_pilot_fixture import envelopes, ref, uid


ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "docs/acceptance/v54-corpus"
CONTENT_CASES = json.loads((CORPUS / "cases/content.json").read_text(encoding="utf8"))
C01 = next(case for case in CONTENT_CASES["cases"] if case["case_id"] == "C01")
MESSAGE_BYTES = (CORPUS / "sources/clear_mail.txt").read_bytes()
ATTACHMENT_BYTES = (CORPUS / "sources/clear_attachment.md").read_bytes()


def _prepare_world(integrated):
    sessions, component, _runtime, identity = integrated
    with sessions.begin() as db:
        db.get(Project, 4).name = "Альфа-Макет"
        db.get(Contract, 5).number = "TEST-A-42"
        db.add(Project(id=8, name="Бета-Макет", organization_id=1))
    with sessions.begin() as db:
        context = component.context(db, scope())
        mailbox = context.bootstrap_mail_connection(
            db,
            scope=scope(),
            identity=VersionPin(ref=identity, version_kind="record_version", value=1),
            namespace="synthetic-mailbox",
        )
        message_source = component.source.register_source(
            db,
            scope=scope(),
            identity=identity,
            namespace="synthetic-mailbox",
            external_id="synthetic-C01",
            object_kind="message",
        )
        message_source, message_version = component.source.observe(
            db,
            scope=scope(),
            source=message_source,
            identity=identity,
            namespace="synthetic-mailbox",
            observation_key="obs-clear-mail",
            provider_revision="r1",
        )
        attachment_source = component.source.register_source(
            db,
            scope=scope(),
            identity=identity,
            namespace="synthetic-mailbox",
            external_id="attachment-clear",
            object_kind="attachment",
            parent=message_source.ref,
        )
        attachment_source, attachment_version = component.source.observe(
            db,
            scope=scope(),
            source=attachment_source,
            identity=identity,
            namespace="synthetic-mailbox",
            observation_key="obs-clear-attachment",
            provider_revision="r1",
        )
        message_ref = context.register(
            db,
            scope=scope(),
            mailbox=mailbox,
            source=message_source,
            attachment=attachment_source,
        )
    return (
        integrated, message_ref, message_source, message_version,
        attachment_source, attachment_version, identity,
    )


def _analyse(world, *, attachment_bytes=ATTACHMENT_BYTES):
    (
        integrated, message_ref, message_source, message_version,
        attachment_source, attachment_version, _identity,
    ) = world
    sessions, component, _runtime, _identity = integrated
    with sessions.begin() as db:
        pipeline = SyntheticCorpusContentPipeline(
            source=component.source,
            context=component.context(db, scope()),
            claims=component.claims,
        )
        return pipeline.analyse(
            db,
            scope=scope(),
            message_ref=message_ref,
            message_source=message_source,
            message_version=message_version,
            message_bytes=MESSAGE_BYTES,
            attachment_source=attachment_source,
            attachment_version=attachment_version,
            attachment_bytes=attachment_bytes,
            evidence_ids=(uid(810), uid(811)),
            claim_anchor=ObjectRef.model_validate(ref("deadline_claim", uid(812))),
            active_project_id=8,
        )


def test_c01_extractor_uses_source_bytes_not_oracle_and_preserves_exact_coordinates():
    result = extract_synthetic_content(message=MESSAGE_BYTES, attachment=ATTACHMENT_BYTES)

    assert result.status == "ready"
    assert result.project_name == "Альфа-Макет"
    assert result.contract_number == "TEST-A-42"
    assert result.due_date == C01["expected"]["claims"]["normalized_date"]
    expected = {
        item["asset_id"]: (item["start"], item["end"])
        for item in C01["evidence"]
    }
    assert (result.spans[0].start, result.spans[0].end) == expected["clear_mail"]
    assert (result.spans[1].start, result.spans[1].end) == expected["clear_attachment"]


def test_c01_real_source_evidence_context_claim_and_trust_pipeline(integrated):
    world = _prepare_world(integrated)
    integrated, message_ref, _message_source, message_version, _attachment_source, attachment_version, identity = world
    sessions, component, _runtime, _identity = integrated

    result = _analyse(world)
    assert result.status == "awaiting_human_review"
    assert result.manual_review_required is True
    assert result.active_project_selected is False
    assert result.project.ref.id.value == "4"
    assert result.contract.ref.id.value == "5"
    assert result.due_date == "2030-04-17"

    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(Obligation)) == 0
        assert {db.get(EvidenceAssessment, pin.ref.id.value).verification for pin in result.evidence} == {"unverified"}
        assert {db.get(ContextRelation, pin.ref.id.value).state for pin in result.relations} == {"hypothesis"}
        claim = db.get(DeadlineClaim, (result.claim.ref.id.value, 1))
        assert claim.verification == "unverified"
        locators = [db.get(Evidence, pin.ref.id.value).locator for pin in result.evidence]
        assert all(locator["kind"] == "text_range" for locator in locators)
        assert all(locator["unit"] == "unicode_codepoint" for locator in locators)

    with sessions.begin() as db:
        for pin in result.evidence:
            component.source.review(
                db,
                scope=scope(3),
                command=ReviewCommand(subject=pin, expected_record_version=1, decision="confirmed"),
            )
        component.context(db, scope()).confirm(
            db,
            scope=scope(),
            command=ContextConfirmation(
                message=message_ref,
                project_relation=result.relations[0],
                contract_relation=result.relations[1],
                expected_context_version=1,
                expected_project_relation_record_version=1,
                expected_contract_relation_record_version=1,
            ),
        )
        component.claims.review(
            db,
            scope=scope(3),
            command=ReviewCommand(subject=result.claim, expected_record_version=1, decision="confirmed"),
        )

        raw = envelopes()[0]
        action_ref = ObjectRef.model_validate(ref("action", uid(813)))
        raw.update(
            action_ref=action_ref.model_dump(mode="json"),
            claim=result.claim.model_dump(mode="json"),
            evidence=[pin.model_dump(mode="json") for pin in result.evidence],
            source_versions=sorted(
                [pin.model_dump(mode="json") for pin in (message_version, attachment_version)],
                key=canonical_json,
            ),
            relations=sorted(
                [pin.model_dump(mode="json") for pin in result.relations],
                key=canonical_json,
            ),
            expected_context_version=2,
            connection_ref=identity.model_dump(mode="json"),
            idempotency_key=synthetic_command_key(action_ref, 1),
        )
        raw["payload"]["due_date"] = result.due_date
        envelope = ActionEnvelope.model_validate(raw)
        action = component.context(db, scope()).handoff(
            db,
            scope=scope(),
            message=message_ref,
            envelope=envelope,
            trust=component.trust,
        )
        approve_dispatch(db, component, envelope, action)

    execute(integrated, envelope)
    with sessions() as db:
        task = db.scalar(select(Task))
        assert task.due_date.isoformat() == C01["expected"]["business"]["due_date"]
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        assert db.scalar(select(func.count()).select_from(Obligation)) == 0


def test_low_confidence_or_conflicting_deadline_has_no_abc_side_effects(integrated):
    world = _prepare_world(integrated)
    integrated, *_rest = world
    sessions = integrated[0]
    conflicting = ATTACHMENT_BYTES.replace("17 апреля 2030".encode(), "18 апреля 2030".encode())
    result = _analyse(world, attachment_bytes=conflicting)

    assert result.status == "manual_review_required"
    assert result.reason_code == "ambiguous_deadline"
    assert result.evidence == () and result.relations == () and result.claim is None
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Evidence)) == 0
        assert db.scalar(select(func.count()).select_from(ContextRelation)) == 0
        assert db.scalar(select(func.count()).select_from(DeadlineClaim)) == 0
        assert db.scalar(select(func.count()).select_from(Task)) == 0


def test_extracted_evidence_pin_cannot_be_rebound_to_new_coordinates(integrated):
    world = _prepare_world(integrated)
    integrated, _message_ref, message_source, message_version, *_rest = world
    sessions, component, *_ = integrated
    result = _analyse(world)
    with sessions() as db:
        message_evidence = next(
            pin for pin in result.evidence
            if db.get(Evidence, pin.ref.id.value).source_id == message_source.ref.id.value
        )

    with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
        with sessions.begin() as db:
            component.source.create_text_evidence(
                db,
                scope=scope(),
                source=message_source.ref,
                version=message_version,
                evidence_id=message_evidence.ref.id.value,
                char_start=0,
                char_end=1,
                confidence=1.0,
            )
def test_timed_deadline_is_not_silently_truncated_to_date():
    timed = MESSAGE_BYTES + "\nВстреча 17 апреля 2030 года в 18:30.\n".encode()
    result = extract_synthetic_content(message=timed, attachment=ATTACHMENT_BYTES)
    assert result.status == "manual_review_required"
    assert result.reason_code == "deadline_precision_unsupported"


def test_text_range_locator_rejects_empty_or_inverted_coordinates():
    assert TextRangeLocator(
        kind="text_range", unit="unicode_codepoint", start=159, end=271,
    ).model_dump() == {
        "kind": "text_range", "unit": "unicode_codepoint", "start": 159, "end": 271,
    }
    with pytest.raises(ValueError):
        TextRangeLocator(kind="text_range", unit="unicode_codepoint", start=5, end=5)
