"""DB-backed, explicit authority for the inactive synthetic CONFIRM pilot."""
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.v54_interfaces import AuditAppend, RequestScope
from app.core.v54_refs import ObjectRef, require_same_tenant
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_authority import AuthorityState


PILOT_SCOPE = "v54.synthetic.confirm"
PILOT_OPERATIONS = frozenset({
    "identity", "write", "observe", "metadata", "fragment", "review", "dispatch", "audit",
    "audit.append", "mailbox.bootstrap", "claim.extract", "claim.review", "context.confirm",
    "action.freeze", "action.approve", "action.revoke", "action.dispatch", "action.execute",
    "action.receipt.read", "task.assign", "task.assignee", "authority.manage",
    "mailbox.reconcile", "mailbox.read", "mailbox.action",
})
HUMAN_ONLY = frozenset({"claim.review", "context.confirm", "action.approve", "action.revoke", "authority.manage", "mailbox.reconcile"})


class AuthorityDenied(ValueError):
    """Content-free fail-closed boundary error."""


def _deny():
    raise AuthorityDenied("resource_unavailable")


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@dataclass(frozen=True)
class AuthoritySnapshot:
    organization_id: int
    project_id: int
    principal_kind: str
    principal_id: str
    scope: str
    membership_role: str | None
    permissions: tuple[str, ...]
    authority_epoch: int
    record_version: int
    valid_until: datetime


class AuthorityResolver:
    """Resolve and mutate one explicit pilot mandate in the caller transaction.

    Lock order is Project (tenant authority guard) then AuthorityState.  Role and
    membership writers must use ``change`` to share that order.  Legacy writers
    are fail-closed because every read also compares the live ProjectMember row.
    """

    def __init__(self, *, scope=PILOT_SCOPE, clock=lambda: datetime.now(timezone.utc)):
        self.scope, self.clock = scope, clock

    @staticmethod
    def _ids(scope: RequestScope):
        require_same_tenant(scope.tenant, scope.actor, scope.project)
        if scope.actor.type != "user" or scope.project.type != "project":
            _deny()
        return int(scope.tenant.value), int(scope.project.id.value), scope.actor.id.value

    def _project(self, db, tenant_id, project_id, *, lock):
        query = select(Project).where(
            Project.id == project_id,
            Project.organization_id == tenant_id,
            Project.archived_at.is_(None),
        ).execution_options(populate_existing=True)
        if lock:
            query = query.with_for_update()
        project = db.scalar(query)
        if project is None:
            _deny()
        return project

    def _state(self, db, tenant_id, project_id, principal_kind, principal_id, *, lock):
        query = select(AuthorityState).where(
            AuthorityState.organization_id == tenant_id,
            AuthorityState.project_id == project_id,
            AuthorityState.principal_kind == principal_kind,
            AuthorityState.principal_id == str(principal_id),
            AuthorityState.scope == self.scope,
        ).execution_options(populate_existing=True)
        if lock:
            query = query.with_for_update()
        return db.scalar(query)

    @staticmethod
    def _permissions(row):
        values = row.permissions
        if not isinstance(values, list) or not values or any(
            not isinstance(value, str) or value not in PILOT_OPERATIONS for value in values
        ) or len(values) != len(set(values)):
            _deny()
        return tuple(sorted(values))

    def require_principal(self, db, *, tenant_id: int, project_id: int, principal_kind: str,
                          principal_id: str, operation: str, now: datetime, lock=True):
        if not db.in_transaction() or now.tzinfo is None or operation not in PILOT_OPERATIONS:
            _deny()
        self._project(db, tenant_id, project_id, lock=lock)
        row = self._state(db, tenant_id, project_id, principal_kind, principal_id, lock=lock)
        permissions = self._permissions(row) if row is not None else ()
        if (row is None or row.state != "active" or row.authority_epoch <= 0 or row.record_version <= 0
                or _utc(row.valid_until) is None or _utc(row.valid_until) <= now
                or operation not in permissions or (principal_kind == "service" and operation in HUMAN_ONLY)):
            _deny()
        if principal_kind == "user":
            if not str(principal_id).isdigit() or db.get(User, int(principal_id)) is None:
                _deny()
            member = db.scalar(select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == int(principal_id),
            ))
            if member is None or not row.membership_role or member.role != row.membership_role:
                _deny()
        elif principal_kind != "service" or row.membership_role is not None:
            _deny()
        return AuthoritySnapshot(
            organization_id=tenant_id, project_id=project_id, principal_kind=principal_kind,
            principal_id=str(principal_id), scope=row.scope, membership_role=row.membership_role,
            permissions=permissions, authority_epoch=row.authority_epoch,
            record_version=row.record_version, valid_until=_utc(row.valid_until),
        )

    def require(self, db, scope: RequestScope, operation: str, now: datetime, *, lock=True):
        tenant_id, project_id, actor_id = self._ids(scope)
        return self.require_principal(
            db, tenant_id=tenant_id, project_id=project_id, principal_kind="user",
            principal_id=actor_id, operation=operation, now=now, lock=lock,
        )

    def change(self, db, *, scope: RequestScope, principal_id: int, membership_role: str,
               permissions, state: str, expected_epoch: int):
        """CAS role/mandate change; no commit and no implicit production policy."""
        now = self.clock()
        manager = self.require(db, scope, "authority.manage", now, lock=True)
        tenant_id, project_id, _ = self._ids(scope)
        if (type(principal_id) is not int or principal_id <= 0 or not membership_role
                or state not in {"active", "revoked"} or type(expected_epoch) is not int
                or expected_epoch <= 0):
            _deny()
        values = sorted(set(permissions)) if isinstance(permissions, (set, frozenset, tuple, list)) else []
        if not values or any(value not in PILOT_OPERATIONS for value in values):
            _deny()
        row = self._state(db, tenant_id, project_id, "user", principal_id, lock=True)
        member = db.scalar(select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == principal_id,
        ).with_for_update())
        if row is None or member is None or row.authority_epoch != expected_epoch:
            _deny()
        row.membership_role = membership_role
        row.permissions = values
        row.state = state
        row.authority_epoch += 1
        row.record_version += 1
        row.updated_at = now
        row.updated_by_user_id = int(scope.actor.id.value)
        member.role = membership_role
        db.flush()

        # The Project row is already the locked audit stream guard.  Use the
        # existing sole writer; no role names, permissions or payload are logged.
        from app.action_trust.guards import sequence
        from app.core import v54_transactions
        subject = scope.project
        event = AuditAppend(subject=subject, sequence=sequence(db, subject), event="AUTHORITY_CHANGED")
        v54_transactions.append_audit(
            db, scope=scope, event=event,
            authorize=lambda session, request, target: bool(
                session is db and request == scope and target == subject
                and "authority.manage" in manager.permissions
            ),
        )
        return row.authority_epoch

    def authorize_subject(self, db, scope: RequestScope, operation: str, subject: ObjectRef, *, lock=True):
        require_same_tenant(scope.tenant, subject)
        snapshot = self.require(db, scope, operation, self.clock(), lock=lock)
        if operation == "task.assign":
            if subject.type != "user":
                _deny()
            member = db.scalar(select(ProjectMember.id).where(
                ProjectMember.project_id == snapshot.project_id,
                ProjectMember.user_id == int(subject.id.value),
            ))
            if member is None:
                _deny()
        return True
