from datetime import datetime, timedelta, timezone
import base64
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.mailbox_identity.dto import ReconciliationCommand, ReconciliationEvidencePin
from app.mailbox_identity.oauth import OIDCVerificationError, verified_google_subject
from app.mailbox_identity.runtime import observe_gmail_message, runtime_for_message, runtime_for_project_connection
from app.mailbox_identity.service import MailboxConflict, MailboxIdentityService
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.google_token import GoogleOAuthToken
from app.models.mailbox_identity import (
    MailboxAuthorityState, MailboxCutoverFlags, MailboxOriginBinding,
    MailboxOriginCurrent, MailboxOriginDecision,
)
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.user import User
from app.models.v54_pilot import Evidence, EvidenceAssessment, SourceCurrent, SourceReference, SourceVersion

NOW = datetime.now(timezone.utc)


def enable_rollout(flags, through):
    order = ("shadow_write", "shadow_read_compare", "pilot_write", "primary_read", "actions")
    for name in order[:order.index(through) + 1]:
        setattr(flags, name, True)


def world(db_session, user_factory):
    db = db_session
    user = user_factory()
    org = Organization(name="Synthetic mailbox org"); db.add(org); db.flush()
    project = Project(name="Synthetic mailbox project", organization_id=org.id); db.add(project); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
    token = GoogleOAuthToken(project_id=project.id, token_uri="https://oauth2.googleapis.com/token")
    db.add(token); db.flush()
    identity, mail, generation = MailboxIdentityService().bind_verified_google_subject(
        db, organization_id=org.id, google_token_id=token.id, subject="oidc-subject-synthetic", now=NOW)
    db.add(MailboxAuthorityState(organization_id=org.id, mail_connection_id=mail.id,
        principal_kind="user", principal_id=str(user.id), permissions=["reconcile", "ingest", "action"], state="active",
        authority_version=1, valid_until=NOW + timedelta(days=1)))
    source = SourceReference(organization_id=org.id, origin_project_id=project.id,
        identity_id=identity.id, namespace="gmail", external_id="provider-message-synthetic",
        external_id_kind="provider_message_id", incarnation=1, object_kind="message",
        canonical_locator={"kind": "gmail_message", "provider_message_id": "provider-message-synthetic",
                           "provider_thread_id": "provider-thread-synthetic"},
        record_version=1, freshness="fresh",
        sync_state="observed", availability="available")
    db.add(source); db.flush()
    version = SourceVersion(organization_id=org.id, source_id=source.id, revision=1,
        observation_key="observation-synthetic", consistency="metadata_only",
        locator_at_observation={"kind": "gmail_message", "provider_message_id": "provider-message-synthetic",
                                "provider_thread_id": "provider-thread-synthetic"},
        integrity=[], observed_at=NOW)
    db.add(version); db.flush()
    db.add(SourceCurrent(source_id=source.id, organization_id=org.id, version_id=version.id))
    evidence = Evidence(organization_id=org.id, source_id=source.id, source_version_id=version.id,
        revision=1, locator={"kind": "message"}, extractor={"kind": "provider_export"},
        confidence=1.0, confidence_kind="provider", extracted_at=NOW)
    db.add(evidence); db.flush()
    db.add(EvidenceAssessment(evidence_id=evidence.id, organization_id=org.id, record_version=1,
        verification="verified", freshness="fresh", availability="available", checked_at=NOW,
        valid_until=NOW + timedelta(days=1), reviewed_by=user.id, reviewed_at=NOW))
    message = Message(organization_id=org.id, project_id=project.id, created_by_user_id=user.id,
        source_type="email", source_external_id="provider-message-synthetic", source_name="Synthetic",
        content="Synthetic", summary="Synthetic", context_evidence="Synthetic", attachments_json="[]")
    db.add(message); db.flush()
    return SimpleNamespace(db=db, user=user, org=org, project=project, token=token,
        identity=identity, mail=mail, generation=generation, source=source, version=version,
        evidence=evidence, message=message)


def command(w, **changes):
    current = w.db.get(MailboxOriginCurrent, w.message.id)
    values = dict(decision_key="decision-synthetic", message_id=w.message.id,
        expected_message_origin_version=w.message.origin_version,
        expected_current_origin_version=current.record_version if current else 1,
        identity_id=w.identity.id, identity_record_version=w.identity.record_version,
        mail_connection_id=w.mail.id, mail_connection_record_version=w.mail.record_version,
        binding_epoch=w.identity.binding_epoch, credential_generation=w.generation,
        source_reference_id=w.source.id, source_reference_record_version=w.source.record_version,
        source_version_id=w.version.id, source_version_revision=w.version.revision,
        evidence_refs=(ReconciliationEvidencePin(evidence_id=w.evidence.id, evidence_revision=1,
            assessment_record_version=1),), reason_code="provider_export_verified",
        correlation_id="correlation-synthetic",
        authority_version=1, outcome="CONFIRM")
    values.update(changes)
    return ReconciliationCommand(**values)


def test_verified_sub_creates_and_reuses_identity_with_new_generation(db_session, user_factory):
    w = world(db_session, user_factory)
    identity, mail, generation = MailboxIdentityService().bind_verified_google_subject(
        w.db, organization_id=w.org.id, google_token_id=w.token.id,
        subject="oidc-subject-synthetic", now=NOW)
    assert identity.id == w.identity.id and mail.id == w.mail.id and generation == 2
    assert identity.account_key == "oidc-subject-synthetic"


def test_email_project_or_token_row_cannot_replace_verified_identity(db_session, user_factory):
    w = world(db_session, user_factory)
    with pytest.raises(MailboxConflict, match="explicit_revoke_required"):
        MailboxIdentityService().bind_verified_google_subject(w.db, organization_id=w.org.id,
            google_token_id=w.token.id, subject="different-verified-subject", now=NOW)
    assert w.identity.account_key == "oidc-subject-synthetic"


def test_oidc_verifies_issuer_expiry_and_sub(monkeypatch):
    monkeypatch.setattr("app.mailbox_identity.oauth.id_token.verify_oauth2_token", lambda *a, **k: {
        "iss": "https://accounts.google.com", "sub": "verified-sub", "exp": int(NOW.timestamp()) + 60})
    assert verified_google_subject("signed-token", "client", request=object(), now=NOW) == "verified-sub"
    monkeypatch.setattr("app.mailbox_identity.oauth.id_token.verify_oauth2_token", lambda *a, **k: {
        "iss": "invalid.example.test", "sub": "verified-sub", "exp": int(NOW.timestamp()) + 60})
    with pytest.raises(OIDCVerificationError): verified_google_subject("bad", "client", request=object(), now=NOW)
    for claims in (
        {"iss": "accounts.google.com", "sub": "verified-sub", "exp": int(NOW.timestamp()) - 1},
        {"iss": "accounts.google.com", "exp": int(NOW.timestamp()) + 60},
        {"iss": "accounts.google.com", "sub": 123, "exp": int(NOW.timestamp()) + 60},
    ):
        monkeypatch.setattr("app.mailbox_identity.oauth.id_token.verify_oauth2_token",
                            lambda *a, _claims=claims, **k: _claims)
        with pytest.raises(OIDCVerificationError):
            verified_google_subject("bad", "client", request=object(), now=NOW)


def test_invalid_oidc_callback_persists_no_credential(db_session, monkeypatch):
    from app.api import google_drive
    from fastapi import HTTPException
    db = db_session
    org = Organization(name="OIDC synthetic"); db.add(org); db.flush()
    project = Project(name="OIDC project", organization_id=org.id); db.add(project); db.commit()
    credentials = SimpleNamespace(id_token="invalid", token="access", refresh_token="refresh",
                                  token_uri="https://oauth2.googleapis.com/token", scopes=[])
    flow = SimpleNamespace(fetch_token=lambda **kwargs: None, credentials=credentials)
    monkeypatch.setattr(google_drive, "_project_from_oauth_state", lambda state: project.id)
    monkeypatch.setattr(google_drive, "google_config", lambda: ({"web": {"client_id": "client"}}, "https://callback.example.test"))
    monkeypatch.setattr(google_drive.Flow, "from_client_config", lambda *a, **k: flow)
    monkeypatch.setattr(google_drive, "verified_google_subject", lambda *a, **k: (_ for _ in ()).throw(OIDCVerificationError("failed")))
    with pytest.raises(HTTPException) as exc: google_drive.google_callback("code", "state", db)
    assert exc.value.status_code == 401
    assert db.scalar(select(GoogleOAuthToken)) is None


def test_flags_are_created_false(db_session, user_factory):
    w = world(db_session, user_factory)
    flags = w.db.scalar(select(MailboxCutoverFlags))
    assert not any((flags.shadow_write, flags.shadow_read_compare, flags.pilot_write, flags.primary_read, flags.actions))


def test_same_provider_id_is_unique_per_mailbox(db_session, user_factory):
    w = world(db_session, user_factory)
    token2 = GoogleOAuthToken(project_id=w.project.id + 100, token_uri="https://oauth2.googleapis.com/token")
    # A real second project is required by the token FK.
    project2 = Project(id=w.project.id + 100, name="Second", organization_id=w.org.id)
    w.db.add(project2); w.db.flush(); w.db.add(token2); w.db.flush()
    identity2, mail2, _ = MailboxIdentityService().bind_verified_google_subject(w.db,
        organization_id=w.org.id, google_token_id=token2.id, subject="second-sub", now=NOW)
    source2 = SourceReference(organization_id=w.org.id, origin_project_id=project2.id,
        identity_id=identity2.id, namespace="gmail", external_id=w.source.external_id,
        external_id_kind="provider_message_id", incarnation=1, object_kind="message",
        canonical_locator={"kind": "gmail_message"}, record_version=1, freshness="fresh",
        sync_state="observed", availability="available")
    w.db.add(source2); w.db.flush()
    first = Message(organization_id=w.org.id, project_id=w.project.id, created_by_user_id=w.user.id,
        source_type="email", source_external_id=w.source.external_id, source_name="A", content="A",
        summary="A", context_evidence="A", attachments_json="[]", mail_connection_id=w.mail.id,
        provider_message_id=w.source.external_id, source_reference_id=w.source.id)
    second = Message(organization_id=w.org.id, project_id=project2.id, created_by_user_id=w.user.id,
        source_type="email_outgoing", source_external_id=w.source.external_id, source_name="B", content="B",
        summary="B", context_evidence="B", attachments_json="[]", mail_connection_id=mail2.id,
        provider_message_id=w.source.external_id, source_reference_id=source2.id,
        source_thread_id="same-thread")
    first.source_thread_id = "same-thread"
    w.db.add_all([first, second]); w.db.flush()
    assert first.id != second.id


def test_same_provider_id_in_one_mailbox_cannot_duplicate(db_session, user_factory):
    w = world(db_session, user_factory)
    rows = [Message(organization_id=w.org.id, project_id=w.project.id, created_by_user_id=w.user.id,
        source_type=direction, source_external_id=w.source.external_id, source_name=direction,
        content="Synthetic", summary="Synthetic", context_evidence="Synthetic", attachments_json="[]",
        mail_connection_id=w.mail.id, provider_message_id=w.source.external_id,
        source_reference_id=w.source.id) for direction in ("email", "email_outgoing")]
    w.db.add(rows[0]); w.db.flush()
    with pytest.raises(IntegrityError), w.db.begin_nested():
        w.db.add(rows[1]); w.db.flush()


def test_reconcile_is_idempotent_append_only_and_origin_survives_project_move(db_session, user_factory):
    w = world(db_session, user_factory)
    service = MailboxIdentityService()
    original = command(w)
    first = service.reconcile(w.db, original, actor=w.user)
    replay = service.reconcile(w.db, original, actor=w.user)
    assert replay.idempotent_replay and replay.binding_id == first.binding_id
    origin = (w.message.mail_connection_id, w.message.provider_message_id, w.message.source_reference_id)
    other = Project(name="Other context", organization_id=w.org.id); w.db.add(other); w.db.flush()
    w.message.project_id = other.id; w.db.flush()
    assert (w.message.mail_connection_id, w.message.provider_message_id, w.message.source_reference_id) == origin
    binding = w.db.get(MailboxOriginBinding, first.binding_id)
    binding.state = "rejected"
    with pytest.raises(ValueError, match="append_only_record"): w.db.flush()


def test_same_decision_key_different_payload_conflicts(db_session, user_factory):
    w = world(db_session, user_factory); service = MailboxIdentityService()
    service.reconcile(w.db, command(w), actor=w.user)
    with pytest.raises(MailboxConflict, match="idempotency_conflict"):
        service.reconcile(w.db, command(w, correlation_id="different-correlation"), actor=w.user)


def test_stale_cas_has_no_partial_decision(db_session, user_factory):
    w = world(db_session, user_factory)
    before = len(list(w.db.scalars(select(MailboxOriginBinding))))
    with pytest.raises(MailboxConflict, match="origin_version_conflict"):
        MailboxIdentityService().reconcile(w.db, command(w, expected_message_origin_version=99), actor=w.user)
    assert len(list(w.db.scalars(select(MailboxOriginBinding)))) == before


def test_cross_tenant_source_binding_fails_closed(db_session, user_factory):
    w = world(db_session, user_factory)
    other = Organization(name="Other tenant"); w.db.add(other); w.db.flush()
    project = Project(name="Other tenant project", organization_id=other.id); w.db.add(project); w.db.flush()
    w.source.organization_id = other.id; w.source.origin_project_id = project.id
    with pytest.raises((MailboxConflict, ValueError)):
        MailboxIdentityService().reconcile(w.db, command(w), actor=w.user)


def test_unresolved_never_guesses_mailbox_and_actions_fail_closed(db_session, user_factory):
    w = world(db_session, user_factory)
    result = MailboxIdentityService().reconcile(w.db, command(w, outcome="LEAVE_UNRESOLVED"), actor=w.user)
    assert result.state == "unresolved" and w.message.mail_connection_id is None
    with pytest.raises(ValueError, match="resource_unavailable"):
        runtime_for_message(w.db, w.message, actor=w.user, action=True)


def test_service_actor_cannot_reconcile(db_session, user_factory):
    w = world(db_session, user_factory)
    authority = w.db.scalar(select(MailboxAuthorityState)); authority.principal_kind = "service"
    with pytest.raises(MailboxConflict):
        MailboxIdentityService().reconcile(w.db, command(w), actor=w.user)


def test_revoked_generation_and_actions_false_deny(db_session, user_factory):
    w = world(db_session, user_factory)
    MailboxIdentityService().reconcile(w.db, command(w), actor=w.user)
    with pytest.raises(ValueError): runtime_for_message(w.db, w.message, actor=w.user, action=True)
    flags = w.db.scalar(select(MailboxCutoverFlags)); enable_rollout(flags, "actions")
    w.identity.state = "revoked"; w.db.flush()
    with pytest.raises(ValueError): runtime_for_message(w.db, w.message, actor=w.user, action=True)


def test_audit_omits_provider_and_pii(db_session, user_factory):
    w = world(db_session, user_factory)
    MailboxIdentityService().reconcile(w.db, command(w), actor=w.user)
    audit = w.db.scalar(select(AuditLog).where(AuditLog.action == "mailbox_origin_reconciled"))
    assert w.source.external_id not in audit.details
    assert "@" not in audit.details and "token" not in audit.details.lower()


def test_reply_uses_origin_mailbox_after_context_move(db_session, user_factory, monkeypatch):
    from app.api import gmail
    w = world(db_session, user_factory)
    MailboxIdentityService().reconcile(w.db, command(w), actor=w.user)
    flags = w.db.scalar(select(MailboxCutoverFlags)); enable_rollout(flags, "actions")
    other = Project(name="Moved context", organization_id=w.org.id); w.db.add(other); w.db.flush()
    w.message.project_id = other.id
    w.message.source_thread_id = "legacy-wrong-thread"
    draft = ResponseDraft(project_id=other.id, reviewer_user_id=w.user.id, message_id=w.message.id,
        subject="Synthetic reply", body="Synthetic body", recipient_to="recipient@example.test",
        status="approved", source_file_id="synthetic", source_file_name="synthetic",
        source_excerpt="synthetic", source_excerpt_hash="a" * 64, confidence=1)
    w.db.add(draft); w.db.flush()
    calls = []
    class Service:
        def users(self): return self
        def messages(self): return self
        def send(self, **kwargs): calls.append(kwargs); return SimpleNamespace(execute=lambda: {"id": "sent-synthetic"})
    monkeypatch.setattr(gmail, "require_project_role", lambda *a, **k: None)
    monkeypatch.setattr(gmail, "google_workspace_for_project", lambda *a, **k: pytest.fail("project fallback"))
    monkeypatch.setattr(gmail, "google_workspace_for_mailbox", lambda token_id, db: SimpleNamespace(service=lambda *a: Service()))
    gmail.send_gmail(draft.id, w.db, w.user)
    assert calls and calls[0]["body"]["threadId"] == "provider-thread-synthetic"
    assert draft.status == "sent"


def test_attachment_uses_origin_mailbox_adapter(db_session, user_factory, monkeypatch):
    from app.api import gmail
    from app.models.job import BackgroundJob
    from app.staging.gmail import GmailAttachmentStageResult
    w = world(db_session, user_factory)
    MailboxIdentityService().reconcile(w.db, command(w), actor=w.user)
    flags = w.db.scalar(select(MailboxCutoverFlags)); enable_rollout(flags, "actions")
    w.message.source_external_id = "legacy-wrong-message"
    w.message.attachments_json = '[{"name":"safe.txt","mime_type":"text/plain","size":4,"attachment_id":"attachment-synthetic","document_external_id":"document-synthetic"}]'
    calls = []
    class Service:
        def users(self): return self
        def messages(self): return self
        def attachments(self): return self
        def get(self, **kwargs): calls.append(kwargs); return SimpleNamespace(execute=lambda: {"data": "dGVzdA=="})
    monkeypatch.setattr(gmail, "require_project_role", lambda *a, **k: None)
    monkeypatch.setattr(gmail, "google_workspace_for_project", lambda *a, **k: pytest.fail("project fallback"))
    monkeypatch.setattr(gmail, "google_workspace_for_mailbox", lambda token_id, db: SimpleNamespace(service=lambda *a: Service()))
    def stage(_db, _binding, provider, *, max_bytes):
        opened = provider.open()
        assert opened.stream.read() == b"test" and max_bytes == gmail.MAX_ATTACHMENT_BYTES
        return GmailAttachmentStageResult("a" * 32)
    monkeypatch.setattr(gmail, "stage_gmail_attachment", stage)
    result = gmail.import_gmail_attachment(w.message.id, 0, w.db, w.user)
    assert result["job_id"] == w.db.scalar(select(BackgroundJob.id))
    assert calls[0]["messageId"] == "provider-message-synthetic"
    assert w.db.scalar(select(BackgroundJob)).payload == {"staging_id": "a" * 32}
    audit = w.db.scalar(select(AuditLog).where(AuditLog.action == "gmail_attachment_staged"))
    assert "safe.txt" not in audit.details


@pytest.mark.parametrize("outcome, state", [("REJECT", "rejected"), ("LEAVE_UNRESOLVED", "unresolved")])
def test_reconcile_revokes_confirmed_runtime_without_rewriting_history(db_session, user_factory, outcome, state):
    w = world(db_session, user_factory); service = MailboxIdentityService()
    first = service.reconcile(w.db, command(w), actor=w.user)
    flags = w.db.scalar(select(MailboxCutoverFlags)); enable_rollout(flags, "actions")
    assert runtime_for_message(w.db, w.message, actor=w.user, action=True).provider_message_id == w.source.external_id
    result = service.reconcile(w.db, command(w, decision_key=f"decision-{state}", outcome=outcome), actor=w.user)
    assert result.state == state
    assert w.message.mail_connection_id is None
    current = w.db.get(MailboxOriginCurrent, w.message.id)
    assert current.binding_id == result.binding_id
    assert w.db.get(MailboxOriginBinding, first.binding_id).state == "confirmed"
    with pytest.raises(ValueError, match="resource_unavailable"):
        runtime_for_message(w.db, w.message, actor=w.user, action=True)


def test_reconcile_can_replace_confirmed_origin_append_only(db_session, user_factory):
    w = world(db_session, user_factory); service = MailboxIdentityService()
    first = service.reconcile(w.db, command(w), actor=w.user)
    second = service.reconcile(w.db, command(w, decision_key="decision-correction"), actor=w.user)
    bindings = list(w.db.scalars(select(MailboxOriginBinding).order_by(MailboxOriginBinding.revision)))
    assert [row.id for row in bindings] == [first.binding_id, second.binding_id]
    assert [row.revision for row in bindings] == [1, 2]
    assert w.db.get(MailboxOriginCurrent, w.message.id).binding_id == second.binding_id


def test_runtime_requires_current_binding_exact_generation_and_locator(db_session, user_factory):
    w = world(db_session, user_factory)
    w.message.mail_connection_id = w.mail.id
    w.message.provider_message_id = w.source.external_id
    w.message.source_reference_id = w.source.id
    flags = w.db.scalar(select(MailboxCutoverFlags)); enable_rollout(flags, "actions")
    with pytest.raises(ValueError, match="resource_unavailable"):
        runtime_for_message(w.db, w.message, actor=w.user, action=True)
    MailboxIdentityService().reconcile(w.db, command(w), actor=w.user)
    runtime = runtime_for_message(w.db, w.message, actor=w.user, action=True)
    assert runtime.generation == w.generation
    assert runtime.provider_message_id == w.source.external_id
    assert runtime.provider_thread_id == "provider-thread-synthetic"


def test_reconcile_rejects_stale_source_and_evidence(db_session, user_factory):
    w = world(db_session, user_factory)
    w.source.freshness = "stale"; w.source.record_version += 1; w.db.flush()
    with pytest.raises(MailboxConflict, match="resource_unavailable"):
        MailboxIdentityService().reconcile(w.db, command(w), actor=w.user)
    w.source.freshness = "fresh"; w.source.record_version += 1
    assessment = w.db.get(EvidenceAssessment, w.evidence.id)
    assessment.freshness = "stale"; assessment.record_version += 1; w.db.flush()
    pin = ReconciliationEvidencePin(evidence_id=w.evidence.id, evidence_revision=1,
                                    assessment_record_version=assessment.record_version)
    with pytest.raises(MailboxConflict, match="resource_unavailable"):
        MailboxIdentityService().reconcile(w.db, command(w, evidence_refs=(pin,)), actor=w.user)


def test_reconcile_actor_is_trusted_context_not_command_data(db_session, user_factory):
    w = world(db_session, user_factory)
    other = user_factory(); w.db.add(ProjectMember(project_id=w.project.id, user_id=other.id, role="manager")); w.db.flush()
    with pytest.raises(MailboxConflict, match="resource_unavailable"):
        MailboxIdentityService().reconcile(w.db, command(w), actor=other)


def test_provider_observed_ingress_creates_authoritative_current_binding(db_session, user_factory):
    w = world(db_session, user_factory)
    w.message.mail_connection_id = w.mail.id
    w.message.provider_message_id = w.source.external_id
    w.message.source_reference_id = w.source.id
    runtime = runtime_for_project_connection(w.db, w.project.id)
    binding = MailboxIdentityService().record_provider_observed_origin(
        w.db, message=w.message, runtime=runtime, source=w.source, source_version=w.version, actor=w.user)
    current = w.db.get(MailboxOriginCurrent, w.message.id)
    assert current.binding_id == binding.id and binding.state == "confirmed"
    assert w.db.get(MailboxOriginDecision, binding.decision_id).reason_code == "provider_observed_ingress"
    assert MailboxIdentityService().record_provider_observed_origin(
        w.db, message=w.message, runtime=runtime, source=w.source,
        source_version=w.version, actor=w.user).id == binding.id
    w.source.record_version += 1; w.db.flush()
    replacement = MailboxIdentityService().record_provider_observed_origin(
        w.db, message=w.message, runtime=runtime, source=w.source,
        source_version=w.version, actor=w.user)
    assert replacement.revision == 2
    assert w.db.get(MailboxOriginCurrent, w.message.id).binding_id == replacement.id


def test_gmail_pilot_ingress_wires_observation_binding_and_current(db_session, user_factory, monkeypatch):
    from app.api import ai_secretary, gmail
    w = world(db_session, user_factory)
    flags = w.db.scalar(select(MailboxCutoverFlags)); enable_rollout(flags, "pilot_write")
    item = {"id": "new-provider-message", "threadId": "new-provider-thread", "historyId": "history-1",
        "labelIds": ["INBOX"], "payload": {"mimeType": "text/plain", "headers": [
            {"name": "Subject", "value": "Synthetic"},
            {"name": "From", "value": "sender@example.test"}],
            "body": {"data": base64.urlsafe_b64encode(b"Synthetic body").decode()}}}
    class FakeGmail:
        def users(self): return self
        def messages(self): return self
        def list(self, **kwargs): return SimpleNamespace(execute=lambda: {"messages": [{"id": item["id"]}]})
        def get(self, **kwargs): return SimpleNamespace(execute=lambda: item)
    monkeypatch.setattr(gmail, "google_workspace_for_project", lambda *a, **k: pytest.fail("project fallback"))
    monkeypatch.setattr(gmail, "google_workspace_for_mailbox",
                        lambda *a, **k: SimpleNamespace(service=lambda *a: FakeGmail()))
    monkeypatch.setattr(gmail, "project_candidate", lambda *a, **k: (w.project.id, .4, "synthetic"))
    monkeypatch.setattr(gmail, "contact_for_sender", lambda *a, **k: None)
    monkeypatch.setattr(gmail, "notify_telegram", lambda *a, **k: None)
    monkeypatch.setattr(ai_secretary, "create_tasks_from_files", lambda *a, **k: [])
    monkeypatch.setattr(ai_secretary, "create_response_drafts", lambda *a, **k: [])
    monkeypatch.setattr(ai_secretary, "create_governance_items", lambda *a, **k: ([], []))
    monkeypatch.setattr(ai_secretary, "brief_summary", lambda *a, **k: "Synthetic")
    monkeypatch.setattr(ai_secretary, "configured_action_adapter", lambda *a, **k: SimpleNamespace(provider="test"))
    result = gmail.sync_gmail_project(w.project.id, w.db, w.user, query="newer_than:7d", max_results=1)
    message = w.db.scalar(select(Message).where(Message.provider_message_id == item["id"]))
    current = w.db.get(MailboxOriginCurrent, message.id)
    binding = w.db.get(MailboxOriginBinding, current.binding_id)
    assert result["processed"] == 1 and binding.state == "confirmed"
    assert binding.provider_message_id == item["id"]


def test_shared_subject_project_resolves_current_mailbox_generation(db_session, user_factory):
    w = world(db_session, user_factory)
    project2 = Project(name="Second project", organization_id=w.org.id); w.db.add(project2); w.db.flush()
    token2 = GoogleOAuthToken(project_id=project2.id, token_uri="https://oauth2.googleapis.com/token")
    w.db.add(token2); w.db.flush()
    _identity, _mail, generation2 = MailboxIdentityService().bind_verified_google_subject(
        w.db, organization_id=w.org.id, google_token_id=token2.id,
        subject=w.identity.account_key, now=NOW)
    current_flags = w.db.scalar(select(MailboxCutoverFlags).where(
        MailboxCutoverFlags.credential_generation == generation2))
    enable_rollout(current_flags, "pilot_write"); w.db.flush()
    runtime = runtime_for_project_connection(w.db, w.project.id)
    assert runtime.generation == generation2 and runtime.google_token_id == token2.id


def test_rotated_mailbox_cohort_never_falls_back_to_project_token(db_session, user_factory, monkeypatch):
    from app.api import gmail
    w = world(db_session, user_factory)
    old_flags = w.db.scalar(select(MailboxCutoverFlags)); enable_rollout(old_flags, "pilot_write")
    project2 = Project(name="Credential owner", organization_id=w.org.id); w.db.add(project2); w.db.flush()
    token2 = GoogleOAuthToken(project_id=project2.id, token_uri="https://oauth2.googleapis.com/token")
    w.db.add(token2); w.db.flush()
    MailboxIdentityService().bind_verified_google_subject(
        w.db, organization_id=w.org.id, google_token_id=token2.id,
        subject=w.identity.account_key, now=NOW)
    runtime = runtime_for_project_connection(w.db, w.project.id)
    assert runtime.mailbox_cohort and not runtime.flags.pilot_write
    monkeypatch.setattr(gmail, "google_workspace_for_project", lambda *a, **k: pytest.fail("project fallback"))
    monkeypatch.setattr(gmail, "google_workspace_for_mailbox", lambda *a, **k: pytest.fail("provider call"))
    with pytest.raises(HTTPException) as exc:
        gmail.sync_gmail_project(w.project.id, w.db, w.user, query="newer_than:7d", max_results=1)
    assert exc.value.status_code == 409


def test_runtime_requires_actor_mailbox_action_authority(db_session, user_factory):
    w = world(db_session, user_factory)
    MailboxIdentityService().reconcile(w.db, command(w), actor=w.user)
    flags = w.db.scalar(select(MailboxCutoverFlags)); enable_rollout(flags, "actions")
    authority = w.db.scalar(select(MailboxAuthorityState)); authority.permissions = ["reconcile"]
    with pytest.raises(ValueError, match="resource_unavailable"):
        runtime_for_message(w.db, w.message, actor=w.user, action=True)


def test_runtime_rechecks_exact_evidence_assessment_pin(db_session, user_factory):
    w = world(db_session, user_factory)
    MailboxIdentityService().reconcile(w.db, command(w), actor=w.user)
    flags = w.db.scalar(select(MailboxCutoverFlags)); enable_rollout(flags, "actions")
    assessment = w.db.get(EvidenceAssessment, w.evidence.id)
    assessment.record_version += 1; assessment.freshness = "stale"; w.db.flush()
    with pytest.raises(ValueError, match="resource_unavailable"):
        runtime_for_message(w.db, w.message, actor=w.user, action=True)


def test_migrations_are_ordered_additive_then_guarded_cutover():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    expand = (root / "backend/migrations/versions/a54f001c0a03_v54_mailbox_identity_expand.py").read_text("utf8")
    cutover = (root / "backend/migrations/versions/a54f001c0a04_v54_mailbox_dedup_cutover.py").read_text("utf8")
    assert 'revision = "a54f001c0a03"' in expand and 'down_revision = "a54f001c0a02"' in expand
    assert "INSERT INTO" not in expand.upper() and "UPDATE MESSAGES SET" not in expand.upper()
    assert 'revision = "a54f001c0a04"' in cutover and 'down_revision = "a54f001c0a03"' in cutover
    assert cutover.index("create_index") < cutover.index('drop_constraint("uq_message_source"')
    assert "Global Message identity cannot be restored without data loss" in cutover
    assert "v54_deny_append_only_mutation" in expand
