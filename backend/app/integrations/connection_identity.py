"""SYNTHETIC identity lifecycle; no OAuth, credentials mutation or provider I/O."""
from app.core.v54_permissions import (
    boundary, check_ref, deny, identifier, load, object_ref, positive, utcnow,
)
from app.models.v54_pilot import ConnectionIdentity
from app.source_evidence.common import audit, cas


class IdentityFacade:
    def __init__(self, policy, clock=utcnow):
        self.policy, self.clock = policy, clock

    @boundary
    def register(self, db, *, scope, account_key):
        now = self.clock()
        self.policy.require(db, scope, "identity", now, lock=True)
        self.policy.require(db, scope, "audit", now)
        identifier(account_key)
        self.policy.account(account_key)
        row = load(db, ConnectionIdentity, ConnectionIdentity.organization_id == self.policy.tenant_id,
                   ConnectionIdentity.provider == "synthetic", ConnectionIdentity.account_key == account_key, lock=True)
        if row:
            self.policy.identity(row)  # Never resurrect revoked/legacy/unknown epoch.
            return object_ref(scope, "connection_identity", row.id)
        row = ConnectionIdentity(organization_id=self.policy.tenant_id, provider="synthetic",
                                 account_key=account_key, state="verified", credential_generation=1,
                                 verified_at=now)
        db.add(row)
        db.flush()
        ref = object_ref(scope, "connection_identity", row.id)
        audit(db, self.policy, scope, ref, "SOURCE_OBSERVED", now)
        return ref

    def _locked(self, db, scope, identity, now):
        self.policy.require(db, scope, "identity", now, lock=True)
        self.policy.require(db, scope, "audit", now)
        check_ref(scope, identity, "connection_identity")
        row = load(db, ConnectionIdentity, ConnectionIdentity.id == identity.id.value,
                   ConnectionIdentity.organization_id == self.policy.tenant_id, lock=True)
        self.policy.identity(row)
        return row

    @boundary
    def refresh(self, db, *, scope, identity, account_key, expected_version):
        now = self.clock()
        row = self._locked(db, scope, identity, now)
        if account_key != row.account_key:
            deny()
        # Synthetic successful same-account refresh: no secret rotation/reauth.
        cas(db, ConnectionIdentity, [ConnectionIdentity.id == row.id,
            ConnectionIdentity.organization_id == self.policy.tenant_id], expected_version, verified_at=now)
        audit(db, self.policy, scope, identity, "SOURCE_OBSERVED", now)
        return identity

    @boundary
    def revoke(self, db, *, scope, identity, expected_version):
        now = self.clock()
        row = self._locked(db, scope, identity, now)
        cas(db, ConnectionIdentity, [ConnectionIdentity.id == row.id,
            ConnectionIdentity.organization_id == self.policy.tenant_id], expected_version,
            state="revoked", binding_epoch=row.binding_epoch + 1,
            credential_generation=row.credential_generation + 1)
        audit(db, self.policy, scope, identity, "BLOCKED", now)

    @boundary
    def replace_account(self, db, *, scope, identity, account_key, expected_version):
        now = self.clock()
        row = self._locked(db, scope, identity, now)
        identifier(account_key)
        self.policy.account(account_key)
        if account_key == row.account_key:
            deny()
        positive(expected_version)
        # Same caller transaction. A failure in either operation requires caller rollback.
        self.revoke(db, scope=scope, identity=identity, expected_version=expected_version)
        return self.register(db, scope=scope, account_key=account_key)
