"""Owner-scoped CAS and calls to the EXISTING audit helper; no journal implementation."""
from sqlalchemy import func, select, update
from app.core import v54_transactions
from app.core.v54_interfaces import AuditAppend
from app.core.v54_permissions import SourceEvidenceError, positive
from app.models.v54_pilot import AuditExtension


def cas(db, model, conditions, expected, **changes):
    positive(expected)
    # Preserve caller pending writes even inside a caller no_autoflush block.
    db.flush()
    result = db.execute(update(model).where(*conditions, model.record_version == expected)
                        .values(**changes, record_version=expected + 1)
                        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise SourceEvidenceError("version_conflict")
    db.expire_all()


def audit(db, policy, scope, subject, event, now, pin=None):
    # Caller already locks the stream's owner row (and project in this pilot).
    sequence = db.scalar(select(func.coalesce(func.max(AuditExtension.sequence), 0)).where(
        AuditExtension.organization_id == policy.tenant_id,
        AuditExtension.subject_type == subject.type, AuditExtension.subject_id == subject.id.value)) + 1

    def authorize(session, request, reference):
        policy.require(session, request, "audit", now)
        return reference == subject

    return v54_transactions.append_audit(
        db, scope=scope, event=AuditAppend(subject=subject, subject_pin=pin, sequence=sequence, event=event),
        authorize=authorize)
