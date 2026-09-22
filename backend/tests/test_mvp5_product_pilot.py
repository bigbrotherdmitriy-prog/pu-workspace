from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import os
from threading import Barrier
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.action_trust.guards import TrustConflict, reference, revision
from app.autonomy_policy import PolicyAssignmentCommand
from app.core.v54_authority import PILOT_OPERATIONS, PILOT_SCOPE
from app.core.v54_dto import (
    ActionEnvelope,
    CreateInternalNotificationPayload,
    CreateTaskPayload,
    canonical_json,
)
from app.core.v54_interfaces import RequestScope
from app.core.v54_refs import ObjectRef, TaggedId, VersionPin
from app.database import Base
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.integration_credential import IntegrationCredential
from app.models.job import BackgroundJob
from app.models.mailbox_identity import MailboxCredentialGeneration
from app.models.management import ManagementHistory, Notification
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.user import User
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import (
    ActionReceipt, ActionRevision, AuditExtension,
    ConnectionIdentity,
    ContextRelation,
    DeadlineClaim,
    Evidence,
    EvidenceAssessment,
    MailConnection,
    PendingDispatch,
    PilotAction,
    SourceCurrent,
    SourceReference,
    SourceVersion,
)
from app.pilot_dispatch import PRODUCT_KIND, ProductDispatch, pilot_command_key
from app.pilot_incidents import ALERT_AUDIT_ACTION, notify_product_auto_incidents_once
from app.pilot_product import (
    ProductPilotComposition,
    ProductPilotSettings,
    install_product_pilot_runtime,
    load_product_pilot_settings,
)


NOW = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
NS = UUID("9804d558-81c8-4e74-b94d-dac91ea89fe1")


def ident(kind: str, number: int) -> str:
    return str(uuid5(NS, f"{kind}:{number}"))


def ref(kind: str, value, tenant: int = 1) -> ObjectRef:
    return ObjectRef(
        namespace="pu",
        type=kind,
        tenant_id=TaggedId(kind="int", value=str(tenant)),
        id=TaggedId(kind="int" if kind in {
            "message", "project", "contract", "task", "notification", "user"
        } else "uuid",
                    value=str(value)),
    )


def scope(project_id=10, owner_id=2) -> RequestScope:
    tenant = TaggedId(kind="int", value="1")
    return RequestScope(
        tenant=tenant,
        actor=ref("user", owner_id),
        project=ref("project", project_id),
        correlation_id=str(uuid4()),
    )


@pytest.fixture
def product_world(tmp_path):
    pg_url = os.getenv("PUW_V54_INTEGRATION_DATABASE_URL")
    admin = schema = None
    if pg_url:
        parsed = make_url(pg_url)
        assert parsed.get_backend_name() == "postgresql"
        assert parsed.host in {"localhost", "127.0.0.1", "::1", "db"} or (
            os.getenv("GITHUB_ACTIONS") == "true" and parsed.host == "postgres"
        )
        assert (parsed.database or "").startswith("puw_v54_test_") and not parsed.query
        schema = "mvp5_product_" + uuid4().hex
        admin = create_engine(pg_url, hide_parameters=True, connect_args={"connect_timeout": 5})
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            pg_url,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 5,
                "options": f"-csearch_path={schema} -clock_timeout=8000 -cstatement_timeout=15000",
            },
        )
    else:
        engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'mvp5-product.db'}")

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    settings = ProductPilotSettings(
        enabled=True, project_id=10, owner_user_id=2,
        hourly_quota=2, policy_ttl_hours=24, minimum_confidence=0.9,
        notification_enabled=True,
    )
    component = ProductPilotComposition(settings=settings, clock=lambda: NOW)
    runtime = ProductDispatch(
        sessions=sessions, composition_for_scope=lambda _scope: component,
        project_id=10, owner_user_id=2, notification_enabled=True,
        allow_sqlite_for_tests=True,
    )
    with sessions.begin() as db:
        db.add(Organization(id=1, name="Owner tenant"))
        db.add(User(id=2, name="Owner", email="owner@example.test"))
        db.flush()
        db.add(Project(id=10, name="Owner project", organization_id=1))
        db.flush()
        db.add(ProjectMember(project_id=10, user_id=2, role="owner"))
        db.add(Contract(id=20, project_id=10, number="PILOT", title="Pilot contract"))
        db.add(AuthorityState(
            organization_id=1, project_id=10, principal_kind="user", principal_id="2",
            scope=PILOT_SCOPE, membership_role="owner", permissions=sorted(PILOT_OPERATIONS),
            state="active", authority_epoch=1, record_version=1,
            valid_until=NOW + timedelta(hours=48), updated_at=NOW,
        ))
        db.flush()
        credential = IntegrationCredential(
            project_id=10, provider="google_drive", capability="storage",
            access_token="encrypted-placeholder", account_external_id="subject-placeholder",
        )
        db.add(credential)
        db.flush()
        identity = ConnectionIdentity(
            id=ident("identity", 1), organization_id=1, provider="google_workspace",
            account_key="account-placeholder", state="verified", binding_epoch=1,
            record_version=1, credential_id=credential.id, credential_generation=1,
            verified_at=NOW,
        )
        db.add(identity)
        db.flush()
        db.add(MailboxCredentialGeneration(
            organization_id=1, connection_identity_id=identity.id,
            generation=1, binding_epoch=1, integration_credential_id=credential.id,
            state="active", verified_at=NOW,
        ))
        db.add(MailConnection(
            id=ident("mail", 1), organization_id=1, identity_id=identity.id,
            namespace="gmail", state="active", record_version=1,
        ))
        db.flush()
        view = component.autonomy.assign(db, scope=scope(), command=PolicyAssignmentCommand(
            expected_policy_id=None, expected_revision=0, expected_policy_hash=None,
            expected_authority_epoch=1, create_internal_task="AUTO",
            create_internal_notification="AUTO",
            valid_until=NOW + timedelta(hours=24),
        ))
    try:
        yield sessions, component, runtime, settings, view
    finally:
        engine.dispose()
        if admin is not None and schema is not None:
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def seed_intent(db, component, view, number: int, *, confidence=0.95):
    text = f"Подготовь акт {number}. Срок: 25.09.2026."
    quote = "Срок: 25.09.2026."
    start = text.index(quote)
    source = SourceReference(
        id=ident("source", number), organization_id=1, origin_project_id=10,
        identity_id=ident("identity", 1), parent_source_id=None, namespace="gmail",
        external_id=f"message-{number}", external_id_kind="stable_id", incarnation=1,
        object_kind="message", canonical_locator={"kind": "opaque_id", "value": f"message-{number}",
                                                      "normalization_version": "1"},
        record_version=1, freshness="fresh", sync_state="current", availability="available",
        last_seen_at=NOW, last_checked_at=NOW, next_check_at=NOW + timedelta(minutes=30),
        policy_pins={"access": "owner", "retention": "24h", "residency": "configured"},
        residency={"source_location": "google_workspace", "assurance": "owner_pilot"},
    )
    version = SourceVersion(
        id=ident("version", number), organization_id=1, source_id=source.id,
        revision=1, observation_key=f"observation-{number}", provider_revision=f"r{number}",
        consistency="revision_bound", locator_at_observation=dict(source.canonical_locator),
        integrity=[], observed_at=NOW,
    )
    evidence = Evidence(
        id=ident("evidence", number), organization_id=1, source_id=source.id,
        source_version_id=version.id, revision=1,
        locator={"kind": "text_range", "unit": "unicode_codepoint", "start": start,
                 "end": start + len(quote)},
        extractor={"name": "product-test", "version": "1"}, confidence=confidence,
        confidence_kind="heuristic", extracted_at=NOW,
        policy_pins=dict(source.policy_pins),
    )
    db.add_all([source, version])
    db.flush()
    db.add(SourceCurrent(source_id=source.id, organization_id=1, version_id=version.id))
    db.add(evidence)
    db.flush()
    db.add(EvidenceAssessment(
        evidence_id=evidence.id, organization_id=1, record_version=1,
        verification="verified", freshness="fresh", availability="available",
        checked_at=NOW, valid_until=NOW + timedelta(minutes=30), reviewed_by=2, reviewed_at=NOW,
    ))
    message = Message(
        organization_id=1, project_id=10, contract_id=20, created_by_user_id=2,
        source_type="email", source_external_id=f"message-{number}", source_name="Owner pilot email",
        content=text, attachments_json="[]", summary="", context_confidence=1.0,
        context_evidence="owner confirmed", context_confirmed=True, status="ready",
        mail_connection_id=ident("mail", 1), provider_message_id=f"message-{number}",
        source_reference_id=source.id, context_version=2, origin_version=1,
    )
    db.add(message)
    db.flush()
    evidence_pin = revision(ref("evidence", evidence.id), 1)
    project_relation = ContextRelation(
        id=ident("project-relation", number), organization_id=1, message_id=message.id,
        lineage_id=ident("project-lineage", number), revision=1,
        relation_type="communication.project", target_ref=ref("project", 10).model_dump(mode="json"),
        scope_ref=ref("project", 10).model_dump(mode="json"),
        expected_target=VersionPin(ref=ref("project", 10), version_kind="record_version", value=1).model_dump(mode="json"),
        expected_context_version=2, evidence_pins=[evidence_pin.model_dump(mode="json")],
        provenance={"kind": "owner_confirmation"}, state="confirmed", applicability="current",
        record_version=1, confirmed_by=2, confirmed_at=NOW,
    )
    contract_relation = ContextRelation(
        id=ident("contract-relation", number), organization_id=1, message_id=message.id,
        lineage_id=ident("contract-lineage", number), revision=1,
        relation_type="communication.contract", target_ref=ref("contract", 20).model_dump(mode="json"),
        scope_ref=ref("project", 10).model_dump(mode="json"),
        expected_target=VersionPin(ref=ref("contract", 20), version_kind="record_version", value=1).model_dump(mode="json"),
        expected_context_version=2, evidence_pins=[evidence_pin.model_dump(mode="json")],
        provenance={"kind": "owner_confirmation"}, state="confirmed", applicability="current",
        record_version=1, confirmed_by=2, confirmed_at=NOW,
    )
    claim = DeadlineClaim(
        id=ident("claim", number), revision=1, organization_id=1, message_id=message.id,
        due_date=date(2026, 9, 25), due_time=None, timezone="Europe/Moscow",
        evidence_pins=[evidence_pin.model_dump(mode="json")], provenance={"kind": "exact_date"},
        verification="confirmed", record_version=1, reviewed_by=2, reviewed_at=NOW,
    )
    db.add_all([project_relation, contract_relation, claim])
    db.flush()
    action_ref = ref("action", ident("action", number))
    relations = tuple(sorted((
        revision(ref("context_relation", project_relation.id), 1),
        revision(ref("context_relation", contract_relation.id), 1),
    ), key=lambda item: canonical_json(item.model_dump(mode="json"))))
    envelope = ActionEnvelope(
        schema_version="v54.integration.1", canonicalization="pu-action-c14n-v1",
        action_ref=action_ref, revision=1, action_type="task.internal.create",
        action_type_version=1, executor_version="task-db-v1", stage="PROPOSE",
        project_ref=ref("project", 10), requested_by=ref("user", 2),
        target=VersionPin(ref=ref("project", 10), version_kind="record_version", value=1),
        source_versions=(revision(ref("source_version", version.id), 1),),
        evidence=(evidence_pin,), claim=revision(ref("deadline_claim", claim.id), 1),
        relations=relations, expected_context_version=2,
        connection_ref=ref("connection_identity", ident("identity", 1)),
        policy=view.policy, policy_sha256=view.policy_sha256, risk="LOW", autonomy="AUTO",
        reversal="COMPENSATABLE", effects=("internal_task.create", "task_history.append"),
        payload=CreateTaskPayload(
            title=f"Подготовь акт {number}", due_date="2026-09-25", timezone="Europe/Moscow",
            assignee_ref=ref("user", 2), contract_ref=ref("contract", 20),
            publish_external=False, create_obligation=False,
        ),
        idempotency_key=pilot_command_key(action_ref, 1), compensates_action_ref=None,
    )
    action = component.trust.freeze(db, scope=scope(), envelope=envelope)
    return envelope, action


def request_auto(db, component, envelope, action):
    row = db.get(PilotAction, action.ref.id.value)
    component.trust.request_dispatch(
        db, scope=scope(), action=action, approval=None,
        expected_record_version=row.record_version,
    )


def seed_notification_intent(db, component, view, number: int, *, task=None,
                             recipient_id=2, confidence=0.95):
    base, task_action = seed_intent(db, component, view, number, confidence=confidence)
    action_row = db.get(PilotAction, task_action.ref.id.value)
    if task is None:
        task = Task(
            project_id=10, message_id=action_row.message_id, assignee_user_id=2,
            created_by_user_id=2, title=f"Notification target {number}",
            due_date=date(2026, 9, 25), status="assigned", record_version=1,
            source_type="v54_auto", source_file_id=f"notification-target-{number}",
            source_file_name="notification test", source_excerpt="",
            source_excerpt_hash=(f"{number:064x}"[-64:]), confidence=1.0,
            needs_review=False, external_action_status="not_requested",
        )
        db.add(task)
        db.flush()
    action_ref = ref("action", ident("notification-action", number))
    envelope = ActionEnvelope(
        **{
            **base.model_dump(mode="python"),
            "action_ref": action_ref,
            "action_type": "notification.internal.create",
            "executor_version": "notification-db-v1",
            "target": VersionPin(ref=ref("task", task.id), version_kind="record_version",
                                 value=task.record_version),
            "effects": ("management_history.append", "notification.create"),
            "payload": CreateInternalNotificationPayload(
                template_id="task_due_soon", recipient_ref=ref("user", recipient_id),
                condition_key="task_due_soon", due_date="2026-09-25",
            ),
            "idempotency_key": pilot_command_key(action_ref, 1),
        }
    )
    action = component.trust.freeze(db, scope=scope(), envelope=envelope)
    return envelope, action, task


def run_action(sessions, runtime, envelope):
    job_id = runtime.enqueue_action(envelope.action_ref.id.value, str(uuid4()))
    with sessions.begin() as db:
        job = db.get(BackgroundJob, job_id)
        job.status = "running"
        job.worker_id = "mvp5-notification-test"
        job.attempts = 1
        job.locked_at = NOW
        job.lease_expires_at = NOW + timedelta(minutes=3)
        owner = (job.id, job.worker_id, job.attempts, job.locked_at)
        payload = dict(job.payload)
    return runtime.execute(payload, owner), payload, owner


def test_product_pilot_is_default_off_and_rejects_partial_configuration(monkeypatch):
    for name in (
        "PU_V54_AUTO_PILOT_ENABLED", "PU_V54_AUTO_PILOT_PROJECT_ID",
        "PU_V54_AUTO_PILOT_OWNER_USER_ID", "PU_V54_AUTO_PILOT_HOURLY_QUOTA",
        "PU_V54_AUTO_PILOT_POLICY_TTL_HOURS", "PU_V54_AUTO_PILOT_MIN_CONFIDENCE",
        "PU_V54_AUTO_NOTIFICATION_ENABLED", "PU_V54_AUTO_NOTIFICATION_TTL_HOURS",
        "PU_V54_AUTO_NOTIFICATION_PROJECT_HOURLY_QUOTA",
        "PU_V54_AUTO_NOTIFICATION_RECIPIENT_DAILY_QUOTA",
    ):
        monkeypatch.delenv(name, raising=False)
    assert load_product_pilot_settings().enabled is False
    assert install_product_pilot_runtime() is False
    monkeypatch.setenv("PU_V54_AUTO_PILOT_ENABLED", "true")
    with pytest.raises(TrustConflict, match="product_scope_required"):
        load_product_pilot_settings()


def test_notification_auto_has_a_separate_default_off_runtime_gate(product_world):
    sessions, _component, _runtime, settings, view = product_world
    disabled = replace(settings, notification_enabled=False)
    component = ProductPilotComposition(settings=disabled, clock=lambda: NOW)
    runtime = ProductDispatch(
        sessions=sessions, composition_for_scope=lambda _scope: component,
        project_id=10, owner_user_id=2, notification_enabled=False,
        allow_sqlite_for_tests=True,
    )
    with sessions.begin() as db:
        envelope, action, _task = seed_notification_intent(db, component, view, 101)
        with pytest.raises(TrustConflict, match="server_policy_not_applicable"):
            request_auto(db, component, envelope, action)
    with pytest.raises(TrustConflict, match="resource_unavailable"):
        runtime._validate_scope(scope(), "notification.internal.create")


def test_product_dispatch_rejects_every_other_project_and_actor(product_world):
    _sessions, _component, runtime, _settings, _view = product_world
    runtime._validate_scope(scope(), "task.internal.create")
    runtime._validate_scope(scope(), "notification.internal.create")
    with pytest.raises(TrustConflict, match="resource_unavailable"):
        runtime._validate_scope(scope(project_id=11), "task.internal.create")
    with pytest.raises(TrustConflict, match="resource_unavailable"):
        runtime._validate_scope(scope(owner_id=3), "task.internal.create")
    with pytest.raises(TrustConflict, match="resource_unavailable"):
        runtime._validate_scope(scope(), "task.internal.cancel")


def test_notification_auto_ttl_dedupe_receipt_and_fixed_local_effect(product_world):
    sessions, component, runtime, _settings, view = product_world
    with sessions.begin() as db:
        envelope, action, task = seed_notification_intent(db, component, view, 102)
        request_auto(db, component, envelope, action)
        pending = db.get(PendingDispatch, envelope.action_ref.id.value)
        decision_until = pending.authorization_decision["valid_until"]
        assert datetime.fromisoformat(decision_until) <= NOW + timedelta(hours=6)
        task_id = task.id
    first, payload, owner = run_action(sessions, runtime, envelope)
    second = runtime.execute(payload, owner)
    assert first == second
    with sessions() as db:
        rows = list(db.scalars(select(Notification).where(Notification.entity_id == task_id)))
        assert len(rows) == 1
        notification = rows[0]
        assert notification.user_id == 2
        assert notification.kind == "auto_task_due_soon"
        assert notification.title == "Задача требует внимания"
        assert "2026-09-25" in notification.body
        receipt = db.scalar(select(ActionReceipt).where(
            ActionReceipt.action_id == envelope.action_ref.id.value,
        ))
        assert first == {"receipt_id": receipt.id}
        assert receipt.outcome == "APPLIED"
        assert receipt.target_ref == ref("notification", notification.id).model_dump(mode="json")
        history = db.scalar(select(ManagementHistory).where(
            ManagementHistory.entity_type == "notification",
            ManagementHistory.entity_id == notification.id,
        ))
        assert history is not None and history.action == "auto_created"


def test_notification_project_hourly_quota_allows_three_and_blocks_fourth(product_world):
    sessions, component, _runtime, _settings, view = product_world
    with sessions.begin() as db:
        for number in (111, 112, 113):
            envelope, action, _task = seed_notification_intent(db, component, view, number)
            request_auto(db, component, envelope, action)
        envelope, action, _task = seed_notification_intent(db, component, view, 114)
        with pytest.raises(TrustConflict, match="server_policy_not_applicable"):
            request_auto(db, component, envelope, action)
        assert db.scalar(select(func.count()).select_from(PendingDispatch).join(
            PilotAction, PilotAction.id == PendingDispatch.action_id,
        ).where(PilotAction.action_type == "notification.internal.create")) == 3


def test_notification_recipient_daily_quota_blocks_eleventh(product_world):
    sessions, _component, _runtime, settings, view = product_world
    current = [NOW - timedelta(hours=6)]
    component = ProductPilotComposition(settings=settings, clock=lambda: current[0])
    for batch in range(3):
        with sessions.begin() as db:
            for offset in range(3):
                number = 120 + batch * 3 + offset
                envelope, action, _task = seed_notification_intent(db, component, view, number)
                request_auto(db, component, envelope, action)
        current[0] += timedelta(hours=2)
    with sessions.begin() as db:
        envelope, action, _task = seed_notification_intent(db, component, view, 129)
        request_auto(db, component, envelope, action)
    with sessions.begin() as db:
        envelope, action, _task = seed_notification_intent(db, component, view, 130)
        with pytest.raises(TrustConflict, match="server_policy_not_applicable"):
            request_auto(db, component, envelope, action)


def test_notification_recovery_repairs_missing_queue_marker_and_applies_once(product_world):
    sessions, component, runtime, _settings, view = product_world
    with sessions.begin() as db:
        envelope, action, task = seed_notification_intent(db, component, view, 131)
        request_auto(db, component, envelope, action)
        task_id = task.id
    assert runtime.recover() == 1
    assert runtime.recover() == 0
    with sessions.begin() as db:
        pending = db.get(PendingDispatch, envelope.action_ref.id.value)
        job = db.get(BackgroundJob, pending.job_id)
        job.status = "running"
        job.worker_id = "mvp5-notification-recovery"
        job.attempts = 1
        job.locked_at = NOW
        job.lease_expires_at = NOW + timedelta(minutes=3)
        owner = (job.id, job.worker_id, job.attempts, job.locked_at)
        payload = dict(job.payload)
        # Simulate a crash after durable enqueue and before the marker commit.
        pending.job_id = None
    result = runtime.execute(payload, owner)
    with sessions() as db:
        receipt = db.get(ActionReceipt, result["receipt_id"])
        assert receipt is not None and receipt.outcome == "APPLIED"
        assert db.scalar(select(func.count()).select_from(Notification).where(
            Notification.entity_id == task_id,
        )) == 1


def test_notification_same_event_deduplicates_to_one_local_effect(product_world):
    sessions, component, runtime, _settings, view = product_world
    with sessions.begin() as db:
        first, first_action, task = seed_notification_intent(db, component, view, 141)
        second, second_action, _ = seed_notification_intent(
            db, component, view, 142, task=task,
        )
        request_auto(db, component, first, first_action)
        request_auto(db, component, second, second_action)
        task_id = task.id
    first_result, _payload, _owner = run_action(sessions, runtime, first)
    second_result, _payload, _owner = run_action(sessions, runtime, second)
    assert first_result != second_result
    with sessions() as db:
        notifications = list(db.scalars(select(Notification).where(
            Notification.entity_id == task_id,
        )))
        assert len(notifications) == 1
        receipts = list(db.scalars(select(ActionReceipt).where(
            ActionReceipt.action_id.in_([
                first.action_ref.id.value, second.action_ref.id.value,
            ]),
        )))
        assert len(receipts) == 2
        expected = ref("notification", notifications[0].id).model_dump(mode="json")
        assert {canonical_json(item.target_ref) for item in receipts} == {canonical_json(expected)}


def test_high_confidence_verified_verbatim_owner_task_runs_once(product_world):
    sessions, component, runtime, _settings, view = product_world
    with sessions.begin() as db:
        envelope, action = seed_intent(db, component, view, 1)
        request_auto(db, component, envelope, action)
        pending = db.get(PendingDispatch, envelope.action_ref.id.value)
        assert pending.authorization_origin == "SERVER_POLICY"
    job_id = runtime.enqueue_action(envelope.action_ref.id.value, str(uuid4()))
    with sessions.begin() as db:
        job = db.get(BackgroundJob, job_id)
        job.status = "running"
        job.worker_id = "mvp5-product-test"
        job.attempts = 1
        job.locked_at = NOW
        job.lease_expires_at = NOW + timedelta(minutes=3)
        owner = (job.id, job.worker_id, job.attempts, job.locked_at)
        payload = dict(job.payload)
    first = runtime.execute(payload, owner)
    second = runtime.execute(payload, owner)
    assert first == second
    with sessions() as db:
        task = db.scalar(select(Task).where(Task.source_file_id == envelope.action_ref.id.value))
        assert task is not None
        assert task.source_type == "v54_auto" and task.needs_review is False
        assert db.scalar(select(func.count()).select_from(Task).where(
            Task.source_file_id == envelope.action_ref.id.value,
        )) == 1
        receipt = db.scalar(select(ActionReceipt).where(ActionReceipt.action_id == envelope.action_ref.id.value))
        assert receipt.outcome == "APPLIED" and receipt.authorization_origin == "SERVER_POLICY"
        relation = db.scalar(select(ContextRelation).where(ContextRelation.receipt_id == receipt.id))
        audit = db.scalar(select(AuditExtension).where(AuditExtension.receipt_id == receipt.id))
        assert relation is not None and relation.relation_type == "communication.task"
        assert relation.state == "confirmed" and relation.applicability == "current"
        assert relation.confirmed_by == 2
        assert audit is not None and audit.actor_id == 2 and audit.service_principal is None


def test_low_confidence_evidence_fails_closed_before_auto(product_world):
    sessions, component, _runtime, _settings, view = product_world
    with sessions.begin() as db:
        with pytest.raises((TrustConflict, ValueError), match="resource_unavailable"):
            seed_intent(db, component, view, 2, confidence=0.89)


def test_hourly_quota_allows_two_and_blocks_third(product_world):
    sessions, component, _runtime, _settings, view = product_world
    with sessions.begin() as db:
        for number in (11, 12):
            envelope, action = seed_intent(db, component, view, number)
            request_auto(db, component, envelope, action)
        envelope, action = seed_intent(db, component, view, 13)
        with pytest.raises(TrustConflict, match="server_policy_not_applicable"):
            request_auto(db, component, envelope, action)
        assert db.scalar(select(func.count()).select_from(PendingDispatch).where(
            PendingDispatch.authorization_origin == "SERVER_POLICY",
        )) == 2


def test_revoked_credential_generation_blocks_product_auto_recheck(product_world):
    sessions, component, _runtime, _settings, view = product_world
    with sessions.begin() as db:
        envelope, action = seed_intent(db, component, view, 21)
        generation = db.scalar(select(MailboxCredentialGeneration).where(
            MailboxCredentialGeneration.connection_identity_id == ident("identity", 1),
            MailboxCredentialGeneration.generation == 1,
        ))
        identity = db.get(ConnectionIdentity, ident("identity", 1))
        identity.credential_generation = 2
        identity.record_version += 1
        db.add(MailboxCredentialGeneration(
            organization_id=1, connection_identity_id=identity.id,
            generation=2, binding_epoch=identity.binding_epoch,
            integration_credential_id=generation.integration_credential_id,
            state="revoked", verified_at=NOW,
        ))
        with pytest.raises(TrustConflict, match="resource_unavailable"):
            request_auto(db, component, envelope, action)


def test_product_recovery_enqueues_only_product_job_kind(product_world):
    sessions, component, runtime, _settings, view = product_world
    with sessions.begin() as db:
        envelope, action = seed_intent(db, component, view, 22)
        request_auto(db, component, envelope, action)
    assert runtime.recover() == 1
    assert runtime.recover() == 0
    with sessions() as db:
        pending = db.get(PendingDispatch, envelope.action_ref.id.value)
        job = db.get(BackgroundJob, pending.job_id)
        assert job.kind == PRODUCT_KIND
        assert job.idempotency_key == envelope.idempotency_key


def test_postgres_hourly_quota_serializes_competing_third_action(product_world):
    sessions, _component, _runtime, settings, view = product_world
    if sessions.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-lock proof")
    component = ProductPilotComposition(
        settings=replace(settings, hourly_quota=3), clock=lambda: NOW,
    )
    with sessions.begin() as db:
        for number in (31, 32):
            envelope, action = seed_intent(db, component, view, number)
            request_auto(db, component, envelope, action)
        candidates = [seed_intent(db, component, view, number) for number in (33, 34)]

    barrier = Barrier(2)

    def compete(candidate):
        envelope, action = candidate
        try:
            with sessions.begin() as db:
                barrier.wait(timeout=5)
                request_auto(db, component, envelope, action)
            return "AUTO"
        except TrustConflict as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(compete, candidates))
    assert sorted(outcomes) == ["AUTO", "server_policy_not_applicable"]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(PendingDispatch).where(
            PendingDispatch.authorization_origin == "SERVER_POLICY",
        )) == 3


def test_postgres_notification_quota_serializes_competing_fourth_action(product_world):
    sessions, component, _runtime, _settings, view = product_world
    if sessions.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL notification quota row-lock proof")
    with sessions.begin() as db:
        for number in (201, 202):
            envelope, action, _task = seed_notification_intent(db, component, view, number)
            request_auto(db, component, envelope, action)
        candidates = [
            seed_notification_intent(db, component, view, number)[:2]
            for number in (203, 204)
        ]
    barrier = Barrier(2)

    def compete(candidate):
        envelope, action = candidate
        try:
            with sessions.begin() as db:
                barrier.wait(timeout=5)
                request_auto(db, component, envelope, action)
            return "AUTO"
        except TrustConflict as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(compete, candidates))
    assert sorted(outcomes) == ["AUTO", "server_policy_not_applicable"]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(PendingDispatch).join(
            PilotAction, PilotAction.id == PendingDispatch.action_id,
        ).where(PilotAction.action_type == "notification.internal.create")) == 3


def test_postgres_notification_concurrent_duplicate_effect_is_idempotent(product_world):
    sessions, component, runtime, _settings, view = product_world
    if sessions.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL notification dedupe proof")
    with sessions.begin() as db:
        first, first_action, task = seed_notification_intent(db, component, view, 211)
        second, second_action, _ = seed_notification_intent(
            db, component, view, 212, task=task,
        )
        request_auto(db, component, first, first_action)
        request_auto(db, component, second, second_action)
        task_id = task.id
    executions = []
    for envelope in (first, second):
        job_id = runtime.enqueue_action(envelope.action_ref.id.value, str(uuid4()))
        with sessions.begin() as db:
            job = db.get(BackgroundJob, job_id)
            job.status = "running"
            job.worker_id = f"mvp5-notification-race-{job_id}"
            job.attempts = 1
            job.locked_at = NOW
            job.lease_expires_at = NOW + timedelta(minutes=3)
            executions.append((dict(job.payload), (
                job.id, job.worker_id, job.attempts, job.locked_at,
            )))
    barrier = Barrier(2)

    def execute(item):
        payload, owner = item
        barrier.wait(timeout=5)
        return runtime.execute(payload, owner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, executions))
    assert len({item["receipt_id"] for item in results}) == 2
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Notification).where(
            Notification.entity_id == task_id,
        )) == 1
        assert db.scalar(select(func.count()).select_from(ActionReceipt).where(
            ActionReceipt.action_id.in_([
                first.action_ref.id.value, second.action_ref.id.value,
            ]),
        )) == 2


def test_postgres_product_incident_alert_serializes_competing_schedulers(
    product_world, monkeypatch,
):
    sessions, _component, _runtime, _settings, _view = product_world
    if sessions.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL incident-alert row-lock proof")
    delivered = []
    monkeypatch.setattr("app.pilot_incidents.telegram_configured", lambda: True)
    monkeypatch.setattr(
        "app.pilot_incidents.notify_telegram",
        lambda message: delivered.append(message) or True,
    )
    with sessions.begin() as db:
        job = BackgroundJob(
            kind=PRODUCT_KIND, payload={"action_id": "never-render-this"},
            status="dead_letter", attempts=3, max_attempts=3,
            priority=100, progress=0,
        )
        db.add(job)
        db.flush()
        job_id = job.id
    barrier = Barrier(2)

    def compete(_number):
        barrier.wait(timeout=5)
        return notify_product_auto_incidents_once(sessions=sessions, limit=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(compete, (1, 2)))
    assert sorted(outcomes) == [0, 1]
    assert len(delivered) == 1
    assert f"job_id={job_id}" in delivered[0]
    assert "never-render-this" not in delivered[0]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == ALERT_AUDIT_ACTION,
            AuditLog.entity_id == job_id,
        )) == 1
