"""Read-only cross-record binding checks; each domain retains its own writer."""
from sqlalchemy import func, select

from app.action_trust.guards import TrustConflict, reference
from app.core.v54_dto import CreateTaskPayload
from app.core.v54_refs import ObjectRef, VersionPin
from app.models.ai_secretary import Message
from app.models.task import Task
from app.models.v54_pilot import (
    ActionReceipt, ConnectionIdentity, ContextRelation, DeadlineClaim, Evidence,
    MailConnection, PilotAction, SourceReference, SourceVersion,
)


def live_pins(db, *, guards, scope, envelope, action, operation):
    """Caller holds authority/policy -> action; lock Message -> claim/evidence -> Task.

    Source/resolver guards must be compatible with this shared ordering. No source
    or context rows are written here; SQL checks supplement, never replace ACL.
    """
    e = envelope
    message = db.scalar(select(Message).where(Message.id == action.message_id,
        Message.organization_id == int(scope.tenant.value), Message.project_id == action.project_id)
        .with_for_update().execution_options(populate_existing=True))
    if (message is None or action.project_id != int(scope.project.id.value)
            or e.project_ref != scope.project or message.context_version != e.expected_context_version
            or not message.mail_connection_id or not message.source_reference_id):
        raise TrustConflict("context_unavailable")
    mail = db.get(MailConnection, message.mail_connection_id, populate_existing=True)
    identity = db.get(ConnectionIdentity, e.connection_ref.id.value, populate_existing=True)
    if (mail is None or identity is None or mail.organization_id != action.organization_id
            or identity.organization_id != action.organization_id or mail.identity_id != identity.id
            or mail.state != "active" or identity.state != "verified"):
        raise TrustConflict("connection_unavailable")
    guards.resolve(db, scope, VersionPin(ref=e.connection_ref, version_kind="record_version",
                                       value=identity.record_version), operation)
    relation_types = set()
    for pin in e.relations:
        row = db.scalar(select(ContextRelation).where(ContextRelation.id == pin.ref.id.value,
            ContextRelation.organization_id == action.organization_id).with_for_update()
            .execution_options(populate_existing=True))
        if (row is None or row.revision != pin.value or row.message_id != message.id
                or row.state != "confirmed" or row.applicability != "current"
                or row.confirmed_by is None or row.confirmed_at is None
                or row.relation_type in relation_types):
            raise TrustConflict("context_unavailable")
        target = ObjectRef.model_validate(row.target_ref)
        expected = e.project_ref if row.relation_type == "communication.project" else reference(scope, "contract", message.contract_id)
        if (row.relation_type not in {"communication.project", "communication.contract"}
                or target != expected or ObjectRef.model_validate(row.scope_ref) != scope.project):
            raise TrustConflict("context_unavailable")
        if isinstance(e.payload, CreateTaskPayload) and row.relation_type == "communication.contract" and target != e.payload.contract_ref:
            raise TrustConflict("context_unavailable")
        relation_types.add(row.relation_type)
        guards.resolve(db, scope, pin, operation)
        guards.resolve(db, scope, VersionPin.model_validate(row.expected_target), operation)
    if relation_types != {"communication.project", "communication.contract"}:
        raise TrustConflict("context_unavailable")
    claim = db.scalar(select(DeadlineClaim).where(DeadlineClaim.id == e.claim.ref.id.value,
        DeadlineClaim.revision == e.claim.value, DeadlineClaim.organization_id == action.organization_id)
        .with_for_update().execution_options(populate_existing=True))
    latest = db.scalar(select(func.max(DeadlineClaim.revision)).where(
        DeadlineClaim.id == e.claim.ref.id.value, DeadlineClaim.organization_id == action.organization_id))
    if (claim is None or latest != e.claim.value or claim.message_id != message.id
            or claim.verification != "confirmed" or claim.reviewed_by is None or claim.reviewed_at is None
            or claim.evidence_pins != [p.model_dump(mode="json") for p in e.evidence]):
        raise TrustConflict("claim_unverified_or_changed")
    if isinstance(e.payload, CreateTaskPayload):
        # The current Task mutation contract is date-only. Never silently
        # truncate an exact DeadlineClaim time while sealing an action.
        if claim.due_time is not None:
            raise TrustConflict("claim_precision_unsupported")
        if e.payload.due_date != claim.due_date.isoformat() or e.payload.timezone != claim.timezone:
            raise TrustConflict("claim_payload_mismatch")
        guards.allow(db, scope, "task.assign", e.payload.assignee_ref)
    for pin in (e.claim, *e.evidence, *e.source_versions):
        result = guards.resolve(db, scope, pin, operation)
        if pin.ref.type in {"evidence", "deadline_claim"} and result.verification != "verified":
            raise TrustConflict("resource_unavailable")
    sources = set()
    for pin in e.source_versions:
        version = db.get(SourceVersion, pin.ref.id.value, populate_existing=True)
        source = db.get(SourceReference, version.source_id, populate_existing=True) if version else None
        if (source is None or version.organization_id != action.organization_id
                or source.organization_id != action.organization_id or source.identity_id != identity.id
                or source.namespace != mail.namespace):
            raise TrustConflict("source_binding_mismatch")
        sources.add(source.id)
    if message.source_reference_id not in sources:
        raise TrustConflict("source_binding_mismatch")
    versions = {p.ref.id.value for p in e.source_versions}
    for pin in e.evidence:
        evidence = db.get(Evidence, pin.ref.id.value, populate_existing=True)
        if (evidence is None or evidence.organization_id != action.organization_id
                or evidence.source_version_id not in versions):
            raise TrustConflict("evidence_binding_mismatch")
    guards.resolve(db, scope, e.target, operation)
    if e.action_type == "task.internal.cancel":
        original = db.get(PilotAction, e.compensates_action_ref.id.value, populate_existing=True)
        receipt = db.scalar(select(ActionReceipt).where(ActionReceipt.action_id == e.compensates_action_ref.id.value,
                                                        ActionReceipt.organization_id == action.organization_id))
        task = db.scalar(select(Task).where(Task.id == int(e.target.ref.id.value))
                         .with_for_update().execution_options(populate_existing=True))
        if (original is None or original.organization_id != action.organization_id
                or original.message_id != message.id or original.claim_id != action.claim_id
                or original.action_type != "task.internal.create" or original.business_state != "SUCCEEDED"
                or receipt is None or receipt.outcome != "APPLIED" or receipt.target_ref != e.target.ref.model_dump(mode="json")
                or task is None or task.project_id != action.project_id or task.record_version != e.target.value
                or task.status != "assigned" or task.external_action_status not in {"proposed", "not_requested"}
                or task.google_task_id or task.google_calendar_event_id):
            raise TrustConflict("cancel_target_changed")
        # Financial/dependent side effects must additionally be refused by the
        # supplied domain TaskMutation. That helper belongs to the integrator.
