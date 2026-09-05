from __future__ import annotations

import json
import inspect
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
import app.provider_actions.email_compensation as email_compensation
from app.api.responses import DraftUpdate, EmailCompensationProposal, router, update_draft
from app.api.gmail import send_gmail
from app.database import Base
from app.models.audit_log import AuditLog
from app.models.job import BackgroundJob
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.user import User
from app.models.v54_provider_action import (
    ProviderAction,
    ProviderActionApproval,
    ProviderDispatchOutbox,
    ProviderOutcomeObservation,
)
from app.provider_actions.contracts import ActionEnvelope
from app.provider_actions.email_compensation import (
    EmailCompensationError,
    describe_email_compensation,
    propose_email_compensation,
    source_send_command_key,
)
from app.provider_actions.runtime import ProviderActionRuntime
from app.provider_actions.synthetic import StrictSyntheticProvider, SyntheticAuthority


NOW = datetime(2035, 1, 1, tzinfo=timezone.utc)


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


@pytest.fixture
def sent_email_runtime():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as db:
        organization = Organization(name="Synthetic organization")
        user = User(name="Synthetic manager", email="manager@example.test", is_admin=False)
        db.add_all([organization, user])
        db.flush()
        project = Project(name="Synthetic project", organization_id=organization.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        draft = ResponseDraft(
            project_id=project.id,
            reviewer_user_id=user.id,
            subject="Original subject",
            body="private-original-body-marker",
            recipient_to="recipient@example.test",
            status="sent",
            source_file_id="source-original",
            source_file_name="original",
            source_excerpt="private-original-excerpt",
            source_excerpt_hash=digest("private-original-excerpt"),
            confidence=1.0,
            sent_external_id="provider-raw-id-marker",
            sent_at=NOW,
        )
        db.add(draft)
        db.flush()
        ids = organization.id, project.id, user.id, draft.id

    organization_id, project_id, user_id, draft_id = ids
    source = ActionEnvelope(
        action_id="source-send-action",
        revision=1,
        organization_id=organization_id,
        project_id=project_id,
        mailbox_key=digest("synthetic-mailbox"),
        provider="synthetic",
        mode="CONFIRM",
        synthetic_only=True,
        action_kind="synthetic.effect.send",
        reversibility="IRREVERSIBLE",
        payload_hash=digest("recipient@example.test|private-original-body-marker"),
        command_key=source_send_command_key(draft_id),
        idempotency_key="source-send-idempotency",
        context_revision=3,
        evidence_pins=("evidence-opaque-1@1", "evidence-opaque-2@1"),
        authority_epoch=5,
        capability_version=2,
        credential_generation=4,
    )
    provider = StrictSyntheticProvider()
    provider.register(source.mailbox_key, capability_version=2, credential_generation=4)
    authority = SyntheticAuthority(now=lambda: NOW)
    authority.grant(source, valid_until=NOW + timedelta(hours=1))
    runtime = ProviderActionRuntime(
        sessions=sessions, adapter=provider, authority=authority, clock=lambda: NOW,
    )
    runtime.freeze(source, actor_id=str(user_id), correlation_id="freeze-source")
    approval = runtime.approve(
        source.action_id, source.revision,
        approval_id="source-send-approval", actor_id=str(user_id),
        expires_at=NOW + timedelta(minutes=30), correlation_id="approve-source",
    )
    runtime.request_dispatch(
        source.action_id, source.revision, approval.id,
        actor_id=str(user_id), correlation_id="dispatch-source",
    )
    job_id = runtime.enqueue_action(source.action_id, source.revision)
    with sessions.begin() as db:
        job = db.get(BackgroundJob, job_id)
        job.status = "running"
        job.worker_id = "synthetic-worker"
        job.attempts = 1
        job.locked_at = NOW
        job.lease_expires_at = NOW + timedelta(minutes=5)
    runtime.execute_job(
        {"organization_id": organization_id, "action_id": source.action_id, "revision": 1},
        (job_id, "synthetic-worker", 1, NOW),
    )
    yield sessions, source, user_id, draft_id, provider
    engine.dispose()


def test_read_and_propose_corrective_follow_up_in_existing_ledger(sent_email_runtime):
    sessions, source, user_id, draft_id, provider = sent_email_runtime
    counters_before = provider.counters
    with sessions.begin() as db:
        draft = db.get(ResponseDraft, draft_id)
        offered = describe_email_compensation(db, draft)
        assert offered == {
            "direct_undo_possible": False,
            "message": "Отменить отправку нельзя",
            "status": "AVAILABLE",
            "can_propose": True,
            "source_action_id": source.action_id,
            "source_revision": source.revision,
            "source_etag": offered["source_etag"],
            "approval_mode": "CONFIRM",
        }
        result = propose_email_compensation(
            db, draft, expected_source_etag=offered["source_etag"],
            actor_id=str(user_id), correlation_id="request-correction", clock=lambda: NOW,
        )
        corrective = db.get(ProviderAction, (result["proposal"]["action_id"], 1))
        protected_draft = db.get(ResponseDraft, result["proposal"]["draft_id"])

        assert result["status"] == "PROPOSED"
        assert corrective.action_id != source.action_id
        assert corrective.command_key != source.command_key
        assert corrective.idempotency_key != source.idempotency_key
        assert corrective.payload_hash != source.payload_hash
        assert corrective.relation_kind == "CORRECTIVE"
        assert corrective.relation_action_id == source.action_id
        assert corrective.project_id == source.project_id
        assert corrective.mailbox_key == source.mailbox_key
        assert corrective.evidence_pins == list(source.evidence_pins)
        assert corrective.mode == "CONFIRM"
        assert corrective.action_kind == "synthetic.effect.corrective"
        assert corrective.reversibility == "IRREVERSIBLE"
        assert corrective.state == "FROZEN"
        assert protected_draft.status == "draft"
        assert protected_draft.recipient_to == "recipient@example.test"
        assert protected_draft.source_file_name == "corrective-follow-up"
        with pytest.raises(HTTPException, match="CONFIRM approval") as approval_error:
            update_draft(
                protected_draft.id, DraftUpdate(status="approved"),
                db=db, user=db.get(User, user_id),
            )
        assert approval_error.value.status_code == 409
        with pytest.raises(HTTPException, match="CONFIRM provider action") as send_error:
            send_gmail(protected_draft.id, db=db, user=db.get(User, user_id))
        assert send_error.value.status_code == 409

        proposal_audit = db.scalar(select(AuditLog).where(
            AuditLog.action == "v54.provider.email_correction_proposed",
            AuditLog.details.contains(corrective.action_id),
        ))
        audit_details = json.loads(proposal_audit.details)
        assert audit_details["source_action_id"] == source.action_id
        assert audit_details["source_revision"] == source.revision
        assert audit_details["source_outcome"] == "APPLIED"
        assert audit_details["mailbox_key"] == source.mailbox_key
        assert audit_details["project_id"] == source.project_id
        assert audit_details["evidence_pin_count"] == len(source.evidence_pins)
        assert audit_details["approval_mode"] == "CONFIRM"

        assert db.scalar(select(ProviderActionApproval).where(
            ProviderActionApproval.action_id == corrective.action_id)) is None
        assert db.get(ProviderDispatchOutbox, (corrective.action_id, 1)) is None
        assert db.scalar(select(BackgroundJob).where(
            BackgroundJob.idempotency_key == corrective.idempotency_key)) is None
        assert provider.counters == counters_before

        source_after = db.get(ProviderAction, (source.action_id, source.revision))
        observations = list(db.scalars(select(ProviderOutcomeObservation).where(
            ProviderOutcomeObservation.action_id == source.action_id)))
        assert source_after.state == "APPLIED"
        assert [row.outcome for row in observations] == ["APPLIED"]


def test_proposal_is_pii_free_outside_protected_draft(sent_email_runtime):
    sessions, _source, user_id, draft_id, _provider = sent_email_runtime
    with sessions.begin() as db:
        draft = db.get(ResponseDraft, draft_id)
        offered = describe_email_compensation(db, draft)
        result = propose_email_compensation(
            db, draft, expected_source_etag=offered["source_etag"],
            actor_id=str(user_id), correlation_id="recipient@example.test private-original-body-marker",
            clock=lambda: NOW,
        )
        action_id = result["proposal"]["action_id"]
        action = db.get(ProviderAction, (action_id, 1))
        audits = list(db.scalars(select(AuditLog).where(
            AuditLog.action.in_((
                "v54.provider.action_frozen",
                "v54.provider.email_correction_proposed",
            )),
        )))
        serialized = json.dumps({
            "action": {column.name: getattr(action, column.name) for column in action.__table__.columns},
            "audits": [audit.details for audit in audits],
        }, default=str, sort_keys=True)
        for forbidden in (
            "recipient@example.test", "private-original-body-marker",
            "private-original-excerpt", "provider-raw-id-marker",
        ):
            assert forbidden not in serialized


def test_read_and_propose_fail_closed_for_stale_or_unavailable_source(sent_email_runtime):
    sessions, source, user_id, draft_id, _provider = sent_email_runtime
    with sessions.begin() as db:
        draft = db.get(ResponseDraft, draft_id)
        offered = describe_email_compensation(db, draft)
        with pytest.raises(EmailCompensationError, match="source_stale"):
            propose_email_compensation(
                db, draft, expected_source_etag="0" * 64,
                actor_id=str(user_id), correlation_id="stale-client", clock=lambda: NOW,
            )
        source_row = db.get(ProviderAction, (source.action_id, source.revision))
        source_row.state = "UNKNOWN"
        db.flush()
        unavailable = describe_email_compensation(db, draft)
        assert unavailable["can_propose"] is False
        assert unavailable["unavailable_reason"] == "source_stale"
        assert unavailable["message"] == "Отменить отправку нельзя"


def test_existing_proposal_fails_closed_when_source_outcome_snapshot_advances(sent_email_runtime):
    sessions, source, user_id, draft_id, _provider = sent_email_runtime
    with sessions.begin() as db:
        draft = db.get(ResponseDraft, draft_id)
        offered = describe_email_compensation(db, draft)
        propose_email_compensation(
            db, draft, expected_source_etag=offered["source_etag"],
            actor_id=str(user_id), correlation_id="request-correction", clock=lambda: NOW,
        )
        first = db.scalar(select(ProviderOutcomeObservation).where(
            ProviderOutcomeObservation.action_id == source.action_id,
            ProviderOutcomeObservation.revision == source.revision,
        ))
        db.add(ProviderOutcomeObservation(
            action_id=first.action_id,
            revision=first.revision,
            organization_id=first.organization_id,
            sequence=2,
            attempt_id="later-observation",
            job_id=first.job_id,
            mailbox_key=first.mailbox_key,
            command_key=first.command_key,
            idempotency_key=first.idempotency_key,
            payload_hash=first.payload_hash,
            envelope_hash=first.envelope_hash,
            outcome="APPLIED",
            retry_safe=False,
            source="LATE_RECEIPT",
            late=True,
            external_ref=None,
            safe_code="later_observation",
            recorded_at=NOW + timedelta(minutes=1),
        ))
        db.flush()

        result = describe_email_compensation(db, draft)
        assert result["status"] == "UNAVAILABLE"
        assert result["can_propose"] is False
        assert result["unavailable_reason"] == "source_stale"


def test_proposal_rechecks_source_after_optimistic_read(sent_email_runtime, monkeypatch):
    """A late receipt between GET and POST must not be silently repinned."""
    sessions, source, user_id, draft_id, _provider = sent_email_runtime
    original_describe = email_compensation.describe_email_compensation

    with sessions.begin() as db:
        draft = db.get(ResponseDraft, draft_id)
        offered = original_describe(db, draft)

        def advance_after_read(local_db, local_draft):
            result = original_describe(local_db, local_draft)
            first = local_db.scalar(select(ProviderOutcomeObservation).where(
                ProviderOutcomeObservation.action_id == source.action_id,
                ProviderOutcomeObservation.revision == source.revision,
            ))
            local_db.add(ProviderOutcomeObservation(
                action_id=first.action_id,
                revision=first.revision,
                organization_id=first.organization_id,
                sequence=2,
                attempt_id="racing-observation",
                job_id=first.job_id,
                mailbox_key=first.mailbox_key,
                command_key=first.command_key,
                idempotency_key=first.idempotency_key,
                payload_hash=first.payload_hash,
                envelope_hash=first.envelope_hash,
                outcome="APPLIED",
                retry_safe=False,
                source="LATE_RECEIPT",
                late=True,
                external_ref=None,
                safe_code="racing_observation",
                recorded_at=NOW + timedelta(seconds=1),
            ))
            local_db.flush()
            return result

        monkeypatch.setattr(
            email_compensation, "describe_email_compensation", advance_after_read,
        )
        with pytest.raises(EmailCompensationError, match="source_stale"):
            propose_email_compensation(
                db, draft, expected_source_etag=offered["source_etag"],
                actor_id=str(user_id), correlation_id="racing-client", clock=lambda: NOW,
            )
        assert db.scalar(select(ProviderAction).where(
            ProviderAction.relation_kind == "CORRECTIVE",
        )) is None


def test_proposal_serializes_concurrent_creation_on_source_action(sent_email_runtime):
    sessions, _source, user_id, draft_id, _provider = sent_email_runtime
    with sessions.begin() as db:
        draft = db.get(ResponseDraft, draft_id)
        offered = describe_email_compensation(db, draft)
        statements = []

        @event.listens_for(db, "do_orm_execute")
        def capture(statement):
            statements.append(statement.statement)

        propose_email_compensation(
            db, draft, expected_source_etag=offered["source_etag"],
            actor_id=str(user_id), correlation_id="concurrent-client", clock=lambda: NOW,
        )
        assert any(
            getattr(statement, "_for_update_arg", None) is not None
            for statement in statements
        )


def test_api_contract_is_read_propose_only_and_rejects_bad_etag():
    paths = {(route.path, method) for route in router.routes for method in route.methods}
    assert ("/response-drafts/{draft_id}/email-compensation", "GET") in paths
    assert ("/response-drafts/{draft_id}/email-compensation/proposals", "POST") in paths
    with pytest.raises(ValidationError):
        EmailCompensationProposal(expected_source_etag="AUTO")
    send_source = inspect.getsource(send_gmail)
    assert 'draft.source_file_name == "corrective-follow-up"' in send_source
    assert "requires separate CONFIRM provider action" in send_source
