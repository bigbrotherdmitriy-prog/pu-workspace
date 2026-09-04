"""Small DB-only building blocks; transaction ownership stays with the caller."""
from typing import Callable

from sqlalchemy.orm import Session

from app.core.v54_interfaces import AuditAppend, RequestScope
from app.core.v54_refs import ObjectRef, TaggedId, require_same_tenant
from app.models.audit_log import AuditLog
from app.models.v54_pilot import AuditExtension


def append_audit(db: Session, *, scope: RequestScope, event: AuditAppend,
                 authorize: Callable[[Session, RequestScope, ObjectRef], bool]) -> ObjectRef:
    """Join existing transaction; owner locks stream and authorizes before append.

    No implicit authorization, transaction start, commit, rollback or side effect.
    DB unique sequence handles concurrent append: caller handles conflict/rollback.
    Audit does not authorize the underlying action; T2 must pass all live gates.
    """
    require_same_tenant(scope.tenant, event.subject)
    if not db.in_transaction() or authorize(db, scope, event.subject) is not True:
        raise ValueError("resource_unavailable")
    audit = AuditLog(action="v54." + event.event, entity_type=event.subject.type,
                     entity_id=int(event.subject.id.value) if event.subject.id.kind == "int" else None,
                     details=None)
    db.add(audit)
    db.flush()
    extension = AuditExtension(
        organization_id=int(scope.tenant.value), audit_log_id=audit.id,
        subject_type=event.subject.type, subject_id=event.subject.id.value,
        sequence=event.sequence, actor_id=int(scope.actor.id.value),
        project_id=int(scope.project.id.value), correlation_id=scope.correlation_id,
        action_pin=event.action.model_dump(mode="json") if event.action else None,
        subject_pin=event.subject_pin.model_dump(mode="json") if event.subject_pin else None,
        approval_id=event.approval.id.value if event.approval else None,
        receipt_id=event.receipt.id.value if event.receipt else None,
        job_id=int(event.job.id.value) if event.job else None,
        relation_refs=[r.model_dump(mode="json") for r in event.relations],
    )
    db.add(extension)
    db.flush()
    return ObjectRef(namespace="pu", type="ledger_event", tenant_id=scope.tenant,
                     id=TaggedId(kind="uuid", value=extension.id))
