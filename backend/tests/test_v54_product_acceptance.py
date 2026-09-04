"""One HTTP-bound, synthetic-only MVP5 product acceptance scenario."""
from __future__ import annotations

from datetime import timedelta
from hashlib import sha256

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.responses import router as responses_router
from app.api.v54_sandbox_acceptance import (
    AcceptanceResult,
    StageResult,
    get_synthetic_acceptance_runtime,
    router as acceptance_router,
)
from app.autonomy_policy import AutonomyPolicyService, PolicyAssignmentCommand
from app.core.v54_authority import AuthorityResolver
from app.core.v54_dto import (
    ActionEnvelope as InternalEnvelope,
    DeadlineClaimInput,
    canonical_json,
)
from app.core.v54_interfaces import ContextConfirmation, ReviewCommand
from app.core.v54_refs import ObjectRef, VersionPin
from app.database import get_db
from app.models.auth_session import AuthSession
from app.models.job import BackgroundJob
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import (
    ActionApproval,
    ActionReceipt,
    ContextRelation,
    DeadlineClaim,
    Evidence,
    EvidenceAssessment,
    PendingDispatch,
    PilotAction,
)
from app.models.v54_provider_action import (
    ProviderAction,
    ProviderActionApproval,
    ProviderOutcomeObservation,
)
from app.pilot_dispatch import synthetic_command_key
from app.provider_actions.contracts import ActionEnvelope as ProviderEnvelope
from app.provider_actions.email_compensation import describe_email_compensation, source_send_command_key
from app.provider_actions.runtime import ProviderActionRuntime
from app.provider_actions.synthetic import Fault, StrictSyntheticProvider, SyntheticAuthority
from test_v54_pilot_integration import seed_composition, vp
from test_v54_source_evidence_pilot import scope
from v54_pilot_fixture import NOW, envelopes, ref, uid


TOKEN = "synthetic-product-acceptance-token"


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class _AcceptanceRuntime:
    def __init__(self, integrated):
        self.integrated = integrated
        self.external_provider = StrictSyntheticProvider()

    def _prepare_intake(self):
        sessions, component, _runtime, identity = self.integrated
        with sessions.begin() as db:
            context = component.context(db, scope())
            mailbox = context.bootstrap_mail_connection(
                db,
                scope=scope(),
                identity=VersionPin(ref=identity, version_kind="record_version", value=1),
                namespace="synthetic-mailbox",
            )
            source = component.source.register_source(
                db,
                scope=scope(),
                identity=identity,
                namespace="synthetic-mailbox",
                external_id="synthetic-product-message",
                object_kind="message",
            )
            source, version = component.source.observe(
                db,
                scope=scope(),
                source=source,
                identity=identity,
                namespace="synthetic-mailbox",
                observation_key="synthetic-product-message-v1",
                provider_revision="v1",
            )
            attachment = component.source.register_source(
                db,
                scope=scope(),
                identity=identity,
                namespace="synthetic-mailbox",
                external_id="synthetic-product-attachment",
                object_kind="attachment",
                parent=source.ref,
            )
            attachment, attached_version = component.source.observe(
                db,
                scope=scope(),
                source=attachment,
                identity=identity,
                namespace="synthetic-mailbox",
                observation_key="synthetic-product-attachment-v1",
                provider_revision="v1",
            )
            evidence = component.source.create_evidence(
                db,
                scope=scope(),
                source=attachment.ref,
                version=attached_version,
                evidence_id=uid(710),
            )
            component.source.review(
                db,
                scope=scope(3),
                command=ReviewCommand(
                    subject=evidence,
                    expected_record_version=1,
                    decision="confirmed",
                ),
            )
            message = context.register(
                db,
                scope=scope(),
                mailbox=mailbox,
                source=source,
                attachment=attachment,
            )
            relations = context.propose(
                db,
                scope=scope(),
                message=message,
                expected_context_version=1,
                project=vp("project", 4, version_kind="record_version"),
                contract=vp("contract", 5, version_kind="record_version"),
                evidence=(evidence,),
            )
            context.confirm(
                db,
                scope=scope(),
                command=ContextConfirmation(
                    message=message,
                    project_relation=relations[0],
                    contract_relation=relations[1],
                    expected_context_version=1,
                    expected_project_relation_record_version=1,
                    expected_contract_relation_record_version=1,
                ),
            )
            claim = component.claims.extract(
                db,
                scope=scope(),
                claim=DeadlineClaimInput(
                    anchor=ObjectRef.model_validate(ref("deadline_claim", uid(711))),
                    revision=1,
                    message=message,
                    due_date="2026-09-10",
                    timezone="Europe/Moscow",
                    evidence=(evidence,),
                ),
            )
            component.claims.review(
                db,
                scope=scope(3),
                command=ReviewCommand(
                    subject=claim,
                    expected_record_version=1,
                    decision="confirmed",
                ),
            )
        return identity, version, attached_version, evidence, message, relations, claim

    def _run_internal_auto(self):
        sessions, component, runtime, _identity = self.integrated
        identity, version, attached_version, evidence, _message, relations, claim = self._prepare_intake()
        with sessions.begin() as db:
            state = db.scalar(select(AuthorityState).where(
                AuthorityState.organization_id == 1,
                AuthorityState.project_id == 4,
                AuthorityState.principal_kind == "user",
                AuthorityState.principal_id == "2",
            ))
            state.permissions = sorted(set(state.permissions) | {"autonomy.policy.manage"})
            state.authority_epoch += 1
            state.record_version += 1
            state.updated_at = NOW + timedelta(seconds=1)
            state.updated_by_user_id = 2
            policy_now = NOW + timedelta(seconds=1)
            autonomy = AutonomyPolicyService(
                authority=AuthorityResolver(clock=lambda: policy_now),
                clock=lambda: policy_now,
            )
            view = autonomy.assign(db, scope=scope(), command=PolicyAssignmentCommand(
                expected_policy_id=None,
                expected_revision=0,
                expected_policy_hash=None,
                expected_authority_epoch=2,
                create_internal_task="AUTO",
                send_external_message="CONFIRM",
                valid_until=NOW + timedelta(minutes=4),
            ))
            component.trust.autonomy = autonomy
            raw = envelopes()[0]
            action_ref = ref("action", uid(712))
            raw.update(
                action_ref=action_ref,
                claim=claim.model_dump(mode="json"),
                evidence=[evidence.model_dump(mode="json")],
                source_versions=sorted(
                    [pin.model_dump(mode="json") for pin in (version, attached_version)],
                    key=canonical_json,
                ),
                relations=sorted(
                    [pin.model_dump(mode="json") for pin in relations],
                    key=canonical_json,
                ),
                expected_context_version=2,
                connection_ref=identity.model_dump(mode="json"),
                autonomy="AUTO",
                policy=view.policy.model_dump(mode="json"),
                policy_sha256=view.policy_sha256,
                idempotency_key=synthetic_command_key(
                    ObjectRef.model_validate(action_ref),
                    1,
                ),
            )
            envelope = InternalEnvelope.model_validate(raw)
            action = component.trust.freeze(db, scope=scope(), envelope=envelope)
            component.trust.request_dispatch(
                db,
                scope=scope(),
                action=action,
                approval=None,
                expected_record_version=db.get(PilotAction, action.ref.id.value).record_version,
            )
            pending = db.get(PendingDispatch, action.ref.id.value)
            assert pending.authorization_origin == "SERVER_POLICY"
            assert db.scalar(select(func.count()).select_from(ActionApproval).where(
                ActionApproval.action_id == action.ref.id.value,
            )) == 0
        job_id = runtime.enqueue_action(envelope.action_ref.id.value, uid(799))
        with sessions() as db:
            job = db.get(BackgroundJob, job_id)
            job.status = "running"
            job.worker_id = "synthetic-product-worker"
            job.attempts = 1
            job.locked_at = NOW
            job.lease_expires_at = NOW + timedelta(minutes=3)
            db.commit()
            owner = (job.id, job.worker_id, job.attempts, job.locked_at)
            payload = dict(job.payload)
        result = runtime.execute(payload, owner)
        replay = runtime.execute(payload, owner)
        assert replay == result
        return envelope, result

    def _run_external_confirm(self, evidence_pin: str):
        sessions, _component, _runtime, identity = self.integrated
        with sessions.begin() as db:
            draft = ResponseDraft(
                project_id=4,
                reviewer_user_id=2,
                message_id=1,
                subject="Synthetic follow-up",
                body="SYNTHETIC_PRIVATE_BODY",
                recipient_to="recipient@example.test",
                status="sent",
                source_file_id="synthetic-product-source",
                source_file_name="synthetic-product",
                source_excerpt="SYNTHETIC_PRIVATE_EXCERPT",
                source_excerpt_hash=_digest("SYNTHETIC_PRIVATE_EXCERPT"),
                confidence=1.0,
                sent_external_id="synthetic-provider-message",
                sent_at=NOW,
            )
            db.add(draft)
            db.flush()
            draft_id = draft.id
        mailbox_key = _digest(identity.id.value)
        envelope = ProviderEnvelope(
            action_id="synthetic-product-send",
            revision=1,
            organization_id=1,
            project_id=4,
            mailbox_key=mailbox_key,
            provider="synthetic",
            mode="CONFIRM",
            synthetic_only=True,
            action_kind="synthetic.effect.send",
            reversibility="IRREVERSIBLE",
            payload_hash=_digest("SYNTHETIC_EXTERNAL_PAYLOAD"),
            command_key=source_send_command_key(draft_id),
            idempotency_key="synthetic-product-send-v1",
            context_revision=2,
            evidence_pins=(evidence_pin,),
            authority_epoch=1,
            capability_version=1,
            credential_generation=1,
        )
        authority = SyntheticAuthority(now=lambda: NOW)
        authority.grant(envelope, valid_until=NOW + timedelta(minutes=10))
        self.external_provider.register(mailbox_key, capability_version=1, credential_generation=1)
        runtime = ProviderActionRuntime(
            sessions=sessions,
            adapter=self.external_provider,
            authority=authority,
            clock=lambda: NOW,
        )
        runtime.freeze(envelope, actor_id="2", correlation_id="synthetic-external-freeze")
        approval = runtime.approve(
            envelope.action_id,
            envelope.revision,
            approval_id="synthetic-product-approval",
            actor_id="3",
            expires_at=NOW + timedelta(minutes=5),
            correlation_id="synthetic-external-approve",
        )
        runtime.request_dispatch(
            envelope.action_id,
            envelope.revision,
            approval.id,
            actor_id="2",
            correlation_id="synthetic-external-dispatch",
        )
        job_id = runtime.enqueue_action(envelope.action_id, envelope.revision)
        self.external_provider.inject_fault(mailbox_key, envelope.command_key, Fault.TIMEOUT_AFTER_EFFECT)
        with sessions.begin() as db:
            job = db.get(BackgroundJob, job_id)
            job.status = "running"
            job.worker_id = "synthetic-provider-one"
            job.attempts = 1
            job.locked_at = NOW
            job.lease_expires_at = NOW + timedelta(minutes=3)
        payload = {"organization_id": 1, "action_id": envelope.action_id, "revision": 1}
        first = runtime.execute_job(payload, (job_id, "synthetic-provider-one", 1, NOW))
        with sessions.begin() as db:
            job = db.get(BackgroundJob, job_id)
            job.status = "running"
            job.worker_id = "synthetic-provider-two"
            job.attempts = 2
            job.locked_at = NOW + timedelta(seconds=1)
            job.lease_expires_at = NOW + timedelta(minutes=3)
        final = runtime.execute_job(
            payload,
            (job_id, "synthetic-provider-two", 2, NOW + timedelta(seconds=1)),
        )
        return draft_id, first, final

    def run(self, *, scenario: str, fault: str, user_id: int) -> AcceptanceResult:
        assert scenario == "mvp5-communication-to-action"
        assert fault == "timeout_after_effect"
        assert user_id == 2
        internal_envelope, _internal_result = self._run_internal_auto()
        evidence_pin = f"{internal_envelope.evidence[0].ref.id.value}@{internal_envelope.evidence[0].value}"
        draft_id, first, final = self._run_external_confirm(evidence_pin)
        sessions = self.integrated[0]
        with sessions() as db:
            assessment = db.get(EvidenceAssessment, internal_envelope.evidence[0].ref.id.value)
            deadline = db.get(DeadlineClaim, (
                internal_envelope.claim.ref.id.value,
                internal_envelope.claim.value,
            ))
            provider_actions = db.scalar(select(func.count()).select_from(ProviderAction))
            compensation = describe_email_compensation(db, db.get(ResponseDraft, draft_id))
            result = AcceptanceResult(
                schema_name="puw.v54.product-acceptance.v1",
                status="PASS",
                synthetic_only=True,
                project_id=4,
                context=StageResult(
                    state="confirmed",
                    revision=2,
                    count=db.scalar(select(func.count()).select_from(ContextRelation).where(
                        ContextRelation.state == "confirmed",
                    )),
                ),
                evidence=StageResult(state=assessment.verification, revision=1, count=1),
                deadline=StageResult(state=deadline.verification, revision=deadline.revision, count=1),
                internal_action=StageResult(
                    state="APPLIED",
                    count=db.scalar(select(func.count()).select_from(Task)),
                    mode="AUTO",
                    authorization_origin=db.scalar(select(ActionReceipt.authorization_origin).where(
                        ActionReceipt.action_id == internal_envelope.action_ref.id.value,
                    )),
                ),
                external_action=StageResult(
                    state="APPLIED",
                    count=self.external_provider.counters["effects"],
                    mode="CONFIRM",
                    authorization_origin="HUMAN_APPROVAL",
                    first_outcome=first["outcome"],
                    final_outcome=final["outcome"],
                ),
                compensation=StageResult(
                    state=compensation["status"],
                    mode=compensation["approval_mode"],
                    direct_undo_possible=compensation["direct_undo_possible"],
                ),
                ledger=StageResult(
                    state="immutable",
                    count=provider_actions + db.scalar(select(func.count()).select_from(ActionReceipt)),
                ),
                raw_content_published=False,
            )
            assert db.scalar(select(func.count()).select_from(ProviderActionApproval)) == 1
            assert [row.outcome for row in db.scalars(select(ProviderOutcomeObservation).order_by(
                ProviderOutcomeObservation.sequence,
            ))] == ["UNKNOWN", "APPLIED"]
            assert db.scalar(select(func.count()).select_from(Evidence)) >= 1
            return result


def test_product_http_acceptance_is_default_off_and_runs_complete_synthetic_mvp5(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'product-acceptance.db'}",
        connect_args={"check_same_thread": False},
    )
    integrated = seed_composition(engine)
    sessions = integrated[0]
    with sessions.begin() as db:
        db.add(AuthSession(
            user_id=2,
            token_hash=_digest(TOKEN),
            expires_at=NOW + timedelta(days=3650),
        ))

    app = FastAPI()
    app.include_router(acceptance_router)
    app.include_router(responses_router)

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_synthetic_acceptance_runtime] = lambda: _AcceptanceRuntime(integrated)
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "X-PU-V54-Synthetic-Acceptance": "synthetic-v1",
    }
    command = {"scenario": "mvp5-communication-to-action", "fault": "timeout_after_effect"}

    disabled = client.post("/api/v54/sandbox/acceptance", headers=headers, json=command)
    assert disabled.status_code == 404

    monkeypatch.setenv("PU_V54_SYNTHETIC_ACCEPTANCE", "true")
    response = client.post("/api/v54/sandbox/acceptance", headers=headers, json=command)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "PASS" and result["synthetic_only"] is True
    assert result["context"] == {"state": "confirmed", "revision": 2, "count": 3,
                                  "mode": None, "authorization_origin": None,
                                  "first_outcome": None, "final_outcome": None,
                                  "direct_undo_possible": None}
    assert result["evidence"]["state"] == "verified"
    assert result["deadline"]["state"] == "confirmed"
    assert result["internal_action"]["mode"] == "AUTO"
    assert result["internal_action"]["authorization_origin"] == "SERVER_POLICY"
    assert result["internal_action"]["count"] == 1
    assert result["external_action"]["mode"] == "CONFIRM"
    assert result["external_action"]["first_outcome"] == "UNKNOWN"
    assert result["external_action"]["final_outcome"] == "APPLIED"
    assert result["external_action"]["count"] == 1
    assert result["compensation"]["direct_undo_possible"] is False
    assert result["compensation"]["state"] == "AVAILABLE"
    assert result["compensation"]["mode"] == "CONFIRM"

    with sessions() as db:
        draft_id = db.scalar(select(ResponseDraft.id).where(ResponseDraft.status == "sent"))
    offered = client.get(f"/response-drafts/{draft_id}/email-compensation", headers=headers)
    assert offered.status_code == 200 and offered.json()["can_propose"] is True
    proposal = client.post(
        f"/response-drafts/{draft_id}/email-compensation/proposals",
        headers=headers,
        json={"expected_source_etag": offered.json()["source_etag"]},
    )
    assert proposal.status_code == 200
    assert proposal.json()["proposal"]["approval_mode"] == "CONFIRM"
    assert proposal.json()["proposal"]["ledger_state"] == "FROZEN"

    serialized = response.text + offered.text + proposal.text
    for forbidden in (
        "SYNTHETIC_PRIVATE_BODY",
        "SYNTHETIC_PRIVATE_EXCERPT",
        "recipient@example.test",
        "synthetic-provider-message",
        "postgresql://",
    ):
        assert forbidden not in serialized
    engine.dispose()
