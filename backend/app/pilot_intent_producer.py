"""Durable, fail-closed producer for owner-confirmed inbound Gmail intents.

The API only enqueues this producer.  The producer seals one Product AUTO
intent and reserves dispatch; scheduler/worker processes own the later Task
effect.  No provider action is performed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid5

from sqlalchemy import select

from app.action_trust.guards import TrustConflict, reference, revision
from app.core.v54_dto import ActionEnvelope, CreateTaskPayload, canonical_json
from app.core.v54_interfaces import RequestScope
from app.core.v54_refs import ObjectRef, TaggedId, VersionPin
from app.database import SessionLocal
from app.models.ai_secretary import Message
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.task import Task
from app.models.v54_pilot import (
    ConnectionIdentity, ContextRelation, DeadlineClaim, Evidence,
    EvidenceAssessment, MailConnection, PilotAction, SourceCurrent,
    SourceReference, SourceVersion,
)
from app.owner_context_confirmation import require_current_owner_context_confirmation
from app.pilot_dispatch import pilot_command_key
from app.pilot_product import ProductPilotComposition, ProductPilotSettings, load_product_pilot_settings


PRODUCT_INTENT_KIND = "v54.product_intent.produce"
_NS = UUID("23fa8077-47d4-49a8-a2f3-b7cd1f0f7ad6")


class ProductIntentNotApplicable(ValueError):
    """Stable, content-free reason; the ordinary CONFIRM path remains active."""


@dataclass(frozen=True, slots=True)
class ProductIntentResult:
    state: str
    reason: str
    action_id: str | None = None


def producer_job_key(message: Message) -> str:
    return f"v54-product-intent:{message.organization_id}:{message.mail_connection_id}:{message.provider_message_id}"


def _deny(reason: str) -> None:
    raise ProductIntentNotApplicable(reason)


def _id(kind: str, mailbox_id: str, provider_message_id: str) -> str:
    return str(uuid5(_NS, f"{kind}:{mailbox_id}:{provider_message_id}"))


def _scope(message: Message, owner_user_id: int) -> RequestScope:
    tenant = TaggedId(kind="int", value=str(message.organization_id))
    return RequestScope(
        tenant=tenant,
        actor=ObjectRef(namespace="pu", type="user", tenant_id=tenant,
                        id=TaggedId(kind="int", value=str(owner_user_id))),
        project=ObjectRef(namespace="pu", type="project", tenant_id=tenant,
                          id=TaggedId(kind="int", value=str(message.project_id))),
        correlation_id=f"product-intent:{message.id}",
    )


def _record_pin(scope: RequestScope, kind: str, value: int, version: int) -> VersionPin:
    return VersionPin(
        ref=ObjectRef(namespace="pu", type=kind, tenant_id=scope.tenant,
                      id=TaggedId(kind="int", value=str(value))),
        version_kind="record_version", value=version,
    )


def _revision_pin(scope: RequestScope, kind: str, value: str, version: int = 1) -> VersionPin:
    return revision(reference(scope, kind, value), version)


def _task_candidate(db, message: Message, settings: ProductPilotSettings) -> Task:
    rows = list(db.scalars(select(Task).where(
        Task.project_id == message.project_id,
        Task.message_id == message.id,
        Task.source_type == "email",
        Task.source_file_id == f"message:{message.id}",
        Task.status == "assigned",
        Task.needs_review.is_(True),
        Task.due_date.is_not(None),
        Task.confidence >= settings.minimum_confidence,
    ).order_by(Task.id).with_for_update()))
    if len(rows) != 1:
        _deny("task_candidate_ambiguous" if rows else "task_candidate_unavailable")
    task = rows[0]
    quote = (task.source_excerpt or "").strip()
    content = message.content or ""
    if (not quote or not task.title or len(task.title) > 500
            or content.find(quote) < 0 or content.find(quote) != content.rfind(quote)
            or task.external_action_status != "proposed"
            or task.google_task_id is not None or task.google_calendar_event_id is not None):
        _deny("verified_verbatim_evidence_required")
    return task


def _ensure_evidence(db, *, scope, message, task, source, version, now, valid_until):
    quote = task.source_excerpt.strip()
    start = message.content.find(quote)
    ident = _id("evidence", message.mail_connection_id, message.provider_message_id)
    locator = {"kind": "text_range", "unit": "unicode_codepoint",
               "start": start, "end": start + len(quote)}
    row = db.get(Evidence, ident)
    if row is None:
        row = Evidence(
            id=ident, organization_id=message.organization_id,
            source_id=source.id, source_version_id=version.id, revision=1,
            locator=locator,
            extractor={"name": "pu-product-inbound", "version": "1",
                       "method": "sealed_task_proposal"},
            confidence=float(task.confidence), confidence_kind="model_or_fallback",
            extracted_at=message.context_confirmed_by_user_at or now,
            policy_pins=source.policy_pins,
        )
        db.add(row); db.flush()
    elif any((row.organization_id != message.organization_id,
              row.source_id != source.id, row.source_version_id != version.id,
              row.revision != 1, row.locator != locator,
              row.confidence != float(task.confidence))):
        _deny("evidence_binding_conflict")
    assessment = db.get(EvidenceAssessment, ident)
    if assessment is None:
        db.add(EvidenceAssessment(
            evidence_id=ident, organization_id=message.organization_id,
            record_version=1, verification="verified", freshness="fresh",
            availability="available", checked_at=now, valid_until=valid_until,
            reviewed_by=int(scope.actor.id.value),
            reviewed_at=message.context_confirmed_by_user_at or now,
        )); db.flush()
    elif any((assessment.organization_id != message.organization_id,
              assessment.verification != "verified", assessment.freshness != "fresh",
              assessment.availability != "available",
              assessment.reviewed_by != int(scope.actor.id.value))):
        _deny("evidence_binding_conflict")
    return _revision_pin(scope, "evidence", ident)


def _ensure_relation(db, *, scope, message, target, evidence, kind, now):
    ident = _id(kind, message.mail_connection_id, message.provider_message_id)
    target_ref = target.ref.model_dump(mode="json")
    expected_target = target.model_dump(mode="json")
    evidence_pins = [evidence.model_dump(mode="json")]
    relation_type = f"communication.{kind}"
    row = db.get(ContextRelation, ident)
    if row is None:
        row = ContextRelation(
            id=ident, organization_id=message.organization_id, message_id=message.id,
            lineage_id=ident, revision=1, relation_type=relation_type,
            target_ref=target_ref, scope_ref=scope.project.model_dump(mode="json"),
            expected_target=expected_target, expected_context_version=message.context_version,
            evidence_pins=evidence_pins,
            provenance={"kind": "explicit_owner_auto_confirmation"},
            state="confirmed", applicability="current", record_version=1,
            confirmed_by=int(scope.actor.id.value),
            confirmed_at=message.context_confirmed_by_user_at or now,
        )
        db.add(row); db.flush()
    elif any((row.organization_id != message.organization_id, row.message_id != message.id,
              row.relation_type != relation_type, row.target_ref != target_ref,
              row.expected_target != expected_target,
              row.expected_context_version != message.context_version,
              row.evidence_pins != evidence_pins, row.state != "confirmed",
              row.applicability != "current", row.confirmed_by != int(scope.actor.id.value))):
        _deny("context_relation_conflict")
    return _revision_pin(scope, "context_relation", ident)


def _ensure_claim(db, *, scope, message, task, evidence, now):
    ident = _id("deadline", message.mail_connection_id, message.provider_message_id)
    evidence_pins = [evidence.model_dump(mode="json")]
    row = db.get(DeadlineClaim, (ident, 1))
    if row is None:
        row = DeadlineClaim(
            id=ident, revision=1, organization_id=message.organization_id,
            message_id=message.id, due_date=task.due_date, due_time=None,
            timezone="Europe/Moscow", evidence_pins=evidence_pins,
            provenance={"kind": "explicit_owner_auto_confirmation"},
            verification="confirmed", record_version=1,
            reviewed_by=int(scope.actor.id.value),
            reviewed_at=message.context_confirmed_by_user_at or now,
        )
        db.add(row); db.flush()
    elif any((row.organization_id != message.organization_id, row.message_id != message.id,
              row.due_date != task.due_date, row.timezone != "Europe/Moscow",
              row.evidence_pins != evidence_pins, row.verification != "confirmed",
              row.reviewed_by != int(scope.actor.id.value))):
        _deny("deadline_claim_conflict")
    return _revision_pin(scope, "deadline_claim", ident)


def produce_product_intent(db, *, message_id: int, expected_context_version: int,
                           owner_user_id: int, project_id: int,
                           settings: ProductPilotSettings, clock) -> ProductIntentResult:
    if not settings.enabled or not settings.producer_enabled:
        return ProductIntentResult("confirm", "producer_disabled")
    now = clock()
    if now.tzinfo is None:
        _deny("invalid_clock")
    message = db.scalar(select(Message).where(Message.id == message_id).with_for_update()
                        .execution_options(populate_existing=True))
    if message is None:
        _deny("message_unavailable")
    if (message.context_version != expected_context_version
            or message.project_id != project_id or project_id != settings.project_id
            or owner_user_id != settings.owner_user_id
            or message.context_confirmed_by_user_id != owner_user_id
            or message.contract_id is None
            or message.context_confidence < settings.minimum_confidence
            or message.source_type != "email"
            or not message.mail_connection_id or not message.provider_message_id
            or not message.source_reference_id):
        return ProductIntentResult("confirm", "producer_gate_not_applicable")
    try:
        require_current_owner_context_confirmation(db, message)
    except ValueError:
        return ProductIntentResult("confirm", "owner_context_confirmation_required")

    scope = _scope(message, owner_user_id)
    component = ProductPilotComposition(settings=settings, clock=clock)
    try:
        _policy_row, policy = component.autonomy.lock_current_view(db, scope=scope)
    except (ValueError, TypeError):
        return ProductIntentResult("confirm", "autonomy_policy_unavailable")
    if policy is None or not policy.enabled or policy.create_internal_task != "AUTO":
        return ProductIntentResult("confirm", "autonomy_policy_confirm")

    project = db.get(Project, project_id)
    contract = db.get(Contract, message.contract_id)
    mail = db.get(MailConnection, message.mail_connection_id)
    source = db.get(SourceReference, message.source_reference_id)
    current = db.get(SourceCurrent, message.source_reference_id)
    version = db.get(SourceVersion, current.version_id) if current else None
    identity = db.get(ConnectionIdentity, mail.identity_id) if mail else None
    if (project is None or contract is None or contract.project_id != project.id
            or mail is None or identity is None or source is None or version is None
            or mail.namespace != "gmail" or mail.state != "active"
            or source.identity_id != identity.id or source.external_id != message.provider_message_id
            or source.object_kind != "message"):
        return ProductIntentResult("confirm", "mailbox_origin_unavailable")

    task = _task_candidate(db, message, settings)
    evidence = _ensure_evidence(
        db, scope=scope, message=message, task=task, source=source, version=version,
        now=now, valid_until=min(policy.valid_until, now + timedelta(minutes=30)),
    )
    project_pin = _record_pin(scope, "project", project.id, project.record_version)
    contract_pin = _record_pin(scope, "contract", contract.id, contract.record_version)
    relations = tuple(sorted((
        _ensure_relation(db, scope=scope, message=message, target=project_pin,
                         evidence=evidence, kind="project", now=now),
        _ensure_relation(db, scope=scope, message=message, target=contract_pin,
                         evidence=evidence, kind="contract", now=now),
    ), key=lambda item: canonical_json(item.model_dump(mode="json"))))
    claim = _ensure_claim(db, scope=scope, message=message, task=task, evidence=evidence, now=now)
    action_ref = reference(scope, "action", _id(
        "task-action", message.mail_connection_id, message.provider_message_id,
    ))
    envelope = ActionEnvelope(
        schema_version="v54.integration.1", canonicalization="pu-action-c14n-v1",
        action_ref=action_ref, revision=1, action_type="task.internal.create",
        action_type_version=1, executor_version="task-db-v1", stage="PROPOSE",
        project_ref=scope.project, requested_by=scope.actor, target=project_pin,
        source_versions=(_revision_pin(scope, "source_version", version.id, version.revision),),
        evidence=(evidence,), claim=claim, relations=relations,
        expected_context_version=message.context_version,
        connection_ref=reference(scope, "connection_identity", identity.id),
        policy=policy.policy, policy_sha256=policy.policy_sha256,
        risk="LOW", autonomy="AUTO", reversal="COMPENSATABLE",
        effects=("internal_task.create", "task_history.append"),
        payload=CreateTaskPayload(
            title=task.title, due_date=task.due_date.isoformat(), timezone="Europe/Moscow",
            assignee_ref=scope.actor, contract_ref=contract_pin.ref,
            publish_external=False, create_obligation=False,
        ),
        idempotency_key=pilot_command_key(action_ref, 1), compensates_action_ref=None,
    )
    try:
        action = component.context(db, scope).handoff(
            db, scope=scope, message=reference(scope, "message", message.id),
            envelope=envelope, trust=component.trust,
        )
        row = db.get(PilotAction, action.ref.id.value, populate_existing=True)
        component.trust.request_dispatch(
            db, scope=scope, action=action, approval=None,
            expected_record_version=row.record_version,
        )
    except (TrustConflict, ValueError) as error:
        _deny(str(error) or "product_intent_rejected")
    return ProductIntentResult("pending", "durable_dispatch_requested", action.ref.id.value)


def run_product_intent_job(payload: dict, *, sessions=SessionLocal,
                           settings: ProductPilotSettings | None = None,
                           clock=lambda: datetime.now(timezone.utc)) -> dict:
    required = {"message_id", "expected_context_version", "owner_user_id", "project_id"}
    if set(payload) != required or any(type(payload[key]) is not int or payload[key] <= 0 for key in required):
        raise ValueError("invalid_product_intent_payload")
    settings = settings or load_product_pilot_settings()
    try:
        with sessions.begin() as db:
            result = produce_product_intent(db, settings=settings, clock=clock, **payload)
    except ProductIntentNotApplicable as error:
        return {"state": "confirm", "reason": str(error)}
    return {"state": result.state, "reason": result.reason, "action_id": result.action_id}
