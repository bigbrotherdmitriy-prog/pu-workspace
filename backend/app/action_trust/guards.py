"""Dependency plumbing, not a permissions backend or another audit writer."""
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.v54_interfaces import PilotGate, RequestScope, Resolver, require_resolution
from app.core.v54_refs import ObjectRef, TaggedId, VersionPin, require_same_tenant
from app.models.v54_pilot import AuditExtension


class TrustConflict(ValueError):
    """Only fixed, content-free error codes leave this facade."""


def utc(value: datetime) -> datetime:
    # SQLite drops tzinfo on persisted DateTime(timezone=True); DB values are UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def reference(scope: RequestScope, kind: str, value) -> ObjectRef:
    from app.core.v54_refs import INT_TYPES
    return ObjectRef(namespace="pu", type=kind, tenant_id=scope.tenant,
                     id=TaggedId(kind="int" if kind in INT_TYPES else "uuid", value=str(value)))


def revision(ref: ObjectRef, number: int) -> VersionPin:
    return VersionPin(ref=ref, version_kind="revision", value=number)


def sequence(db: Session, subject: ObjectRef) -> int:
    """Caller holds the subject stream lock; append_audit remains sole writer."""
    return 1 + (db.scalar(select(func.max(AuditExtension.sequence)).where(
        AuditExtension.organization_id == int(subject.tenant_id.value),
        AuditExtension.subject_type == subject.type,
        AuditExtension.subject_id == subject.id.value)) or 0)


class Guards:
    def __init__(self, *, resolver: Resolver, authorize: Callable,
                 gate: Callable[[Session, RequestScope], PilotGate],
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        """All dependencies required; none infer roles, policy TTL or human status.

        authorize(db, scope, operation, subject, lock=True) must return exactly
        True and hold the shared tenant authority guard until caller commit/rollback.
        All calls (including read/approve/revoke) take that SAME guard first;
        per-subject locks alone do not implement the required ordering.
        It distinguishes human review/approve/revoke from execution and reads.
        Resolver.lock=True participates in the same authority/revocation protocol.
        Every injected callback is DB-only: no provider I/O or transaction ownership
        changes. Source refresh happens outside this transaction, by its owner.
        This injection seam is NOT an implemented production ACL service.
        """
        self.resolver, self.authorize, self.gate, self.clock = resolver, authorize, gate, clock

    def now(self):
        now = self.clock()
        if now.tzinfo is None:
            raise TrustConflict("clock_unavailable")
        return now

    def allow(self, db, scope, operation, subject):
        if not db.in_transaction():
            raise TrustConflict("caller_transaction_required")
        require_same_tenant(scope.tenant, subject)
        if self.authorize(db, scope, operation, subject, lock=True) is not True:
            raise TrustConflict("resource_unavailable")

    def enabled(self, db, scope, action_type="task.internal.create"):
        self.gate(db, scope).require_confirm(mode="CONFIRM", action_type=action_type, now=self.now())

    def resolve(self, db, scope, pin, operation):
        result = self.resolver.resolve(db, scope=scope, pin=pin, operation=operation, lock=True)
        require_resolution(result, scope=scope, pin=pin, operation=operation, now=self.now())
        return result

    def audit_allowed(self, db, scope, subject):
        return self.authorize(db, scope, "audit.append", subject, lock=True) is True
