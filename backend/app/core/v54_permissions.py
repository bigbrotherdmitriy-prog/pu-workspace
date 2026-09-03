"""Explicit, injected SYNTHETIC grants only. No production policy defaults."""
from dataclasses import dataclass
from typing import Any
from datetime import datetime, timedelta, timezone
from functools import wraps
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.v54_refs import ObjectRef, VersionPin, require_same_tenant
from app.models.project import Project
from app.models.user import User


class SourceEvidenceError(ValueError):
    """Safe boundary error: never includes source, identifiers or SQL parameters."""


def deny():
    raise SourceEvidenceError("resource_unavailable")


def boundary(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SourceEvidenceError:
            raise
        except (ValueError, TypeError, KeyError, SQLAlchemyError):
            raise SourceEvidenceError("resource_unavailable") from None
    return call


def utcnow():
    return datetime.now(timezone.utc)


def utc(value):
    # SQLite drops timezone information on round-trip; PG uses timestamptz.
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def positive(value):
    if type(value) is not int or value <= 0:
        deny()


def identifier(value, limit=255):
    if type(value) is not str or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        deny()
    return value


def object_ref(scope, kind, value):
    return ObjectRef(namespace="pu", type=kind, tenant_id=scope.tenant,
                     id={"kind": "uuid", "value": value})


def check_ref(scope, ref, kind):
    if not isinstance(ref, ObjectRef) or ref.type != kind:
        deny()
    require_same_tenant(scope.tenant, ref)


def check_pin(scope, pin, kind):
    if not isinstance(pin, VersionPin):
        deny()
    check_ref(scope, pin.ref, kind)
    if pin.version_kind != ("record_version" if kind == "source" else "revision"):
        deny()


def load(db, model, *conditions, lock=False):
    # populate_existing must not discard pending caller changes with no_autoflush.
    # This flush still belongs to the caller's transaction.
    db.flush()
    query = select(model).where(*conditions).execution_options(populate_existing=True)
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


@dataclass(frozen=True)
class SyntheticPolicy:
    """Server-injected test policy. Never deserialize this from an HTTP/model body.

    Grants: exact (actor_id, operation); operations never imply one another.
    All fields required, including unknown/deny-valued fields. No wildcard account,
    namespace, implicit project admin, or automatically generated permission.
    """
    tenant_id: int
    project_id: int
    pin: VersionPin
    grants: frozenset[tuple[int, str]]
    accounts: frozenset[str]
    namespaces: frozenset[str]
    binding_epochs: tuple[tuple[str, int], ...]
    valid_until: datetime | None
    freshness_ttl: timedelta | None
    authority_epoch: int | None
    acl: str
    retention_known: bool
    residency_allowed: bool
    synthetic_only: bool
    # Optional only for the inactive integrated pilot.  The remaining fields are
    # source/retention fixture facts; grants/authority_epoch then cease to be the
    # authority source.
    authority: Any = None

    def require(self, db, scope, operation, now, *, lock=False):
        if not db.in_transaction():
            deny()
        if (not isinstance(now, datetime) or now.tzinfo is None
                or self.synthetic_only is not True or self.acl != "allow"
                or self.retention_known is not True or self.residency_allowed is not True
                or self.valid_until is None or self.valid_until.tzinfo is None or self.valid_until <= now
                or self.freshness_ttl is None or self.freshness_ttl <= timedelta(0)
                or (self.authority is None and
                    (type(self.authority_epoch) is not int or self.authority_epoch <= 0))
                or int(scope.tenant.value) != self.tenant_id or int(scope.project.id.value) != self.project_id
                or (self.authority is None and (int(scope.actor.id.value), operation) not in self.grants)):
            deny()
        check_pin(scope, self.pin, "policy")
        # Do not allow arbitrary caller strings to leak through correlation_id in audit.
        if str(UUID(scope.correlation_id)) != scope.correlation_id:
            deny()
        project = load(db, Project, Project.id == self.project_id,
                       Project.organization_id == self.tenant_id, lock=lock)
        if not project or project.archived_at is not None or not db.get(User, int(scope.actor.id.value)):
            deny()
        deadline = min(self.valid_until, now + self.freshness_ttl)
        if self.authority is not None:
            snapshot = self.authority.require(db, scope, operation, now, lock=lock)
            deadline = min(deadline, snapshot.valid_until)
        return deadline

    def resolved_authority_epoch(self, db, scope, operation, now, *, lock=False):
        if self.authority is None:
            return self.authority_epoch
        return self.authority.require(db, scope, operation, now, lock=lock).authority_epoch

    def permits(self, db, scope, actor_id, operation, now, *, lock=False):
        if self.authority is None:
            return (actor_id, operation) in self.grants
        actor = scope.model_copy(update={"actor": ObjectRef(
            namespace="pu", type="user", tenant_id=scope.tenant,
            id={"kind": "int", "value": str(actor_id)},
        )})
        self.authority.require(db, actor, operation, now, lock=lock)
        return True

    def account(self, account, namespace=None):
        if account not in self.accounts or (namespace is not None and namespace not in self.namespaces):
            deny()

    def identity(self, row, namespace=None):
        if (not row or row.organization_id != self.tenant_id or row.provider != "synthetic"
                or row.state != "verified" or row.credential_id is not None
                or row.verified_at is None
                or type(row.credential_generation) is not int or row.credential_generation <= 0
                or dict(self.binding_epochs).get(row.id) != row.binding_epoch):
            deny()
        self.account(row.account_key, namespace)

    def policy_pins(self):
        return {key: self.pin.model_dump(mode="json") for key in ("access", "retention", "residency")}
