"""DB-backed v5.4 autonomy policy assignment and model-independent decisions.

This reuses ``ActionPolicy`` as the versioned policy record.  Its legacy ``mode``
column remains the conservative CONFIRM default; per-capability overrides live in
the sealed rules document.  This module never executes an action or fabricates a
human approval for AUTO.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, StrictBool, StrictInt, StrictStr, model_validator
from sqlalchemy import select

from app.action_trust.guards import reference, revision, sequence, utc
from app.core import v54_transactions
from app.core.v54_authority import AuthorityDenied, AuthorityResolver
from app.core.v54_dto import canonical_hash
from app.core.v54_interfaces import AuditAppend, RequestScope
from app.core.v54_refs import ObjectRef, StrictDTO, TaggedId, VersionPin, require_same_tenant
from app.models.v54_pilot import ActionPolicy


POLICY_SCOPE = "v54.autonomy.organization"
POLICY_SCHEMA = "v54.autonomy-policy.1"
CREATE_INTERNAL_TASK = "task.internal.create"
SEND_EXTERNAL_MESSAGE = "message.external.send"
_AUTO_EFFECTS = ("internal_task.create", "task_history.append")
_CONFIRM_PREFIXES = ("message.external.", "email.", "finance.", "legal.", "destructive.", "access.")


class AutonomyDenied(ValueError):
    """Fixed fail-closed boundary error."""


class AutonomyConflict(ValueError):
    """A policy or decision CAS binding is stale."""


def _deny() -> None:
    raise AutonomyDenied("resource_unavailable")


class PolicyAssignmentCommand(StrictDTO):
    expected_policy_id: StrictStr | None = None
    expected_revision: StrictInt
    expected_policy_hash: StrictStr | None = None
    expected_authority_epoch: StrictInt
    create_internal_task: Literal["AUTO", "CONFIRM"]
    send_external_message: Literal["CONFIRM"] = "CONFIRM"
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def validate_cas(self):
        creating = self.expected_revision == 0
        if self.expected_revision < 0 or self.expected_authority_epoch <= 0:
            raise ValueError("invalid policy CAS")
        if creating != (self.expected_policy_id is None and self.expected_policy_hash is None):
            raise ValueError("invalid policy CAS")
        if not creating:
            if str(UUID(self.expected_policy_id)) != self.expected_policy_id:
                raise ValueError("invalid policy id")
            if not re.fullmatch("[0-9a-f]{64}", self.expected_policy_hash or ""):
                raise ValueError("invalid policy hash")
        return self


class PolicyRevokeCommand(StrictDTO):
    expected_policy_id: StrictStr
    expected_revision: StrictInt
    expected_policy_hash: StrictStr
    expected_authority_epoch: StrictInt

    @model_validator(mode="after")
    def validate_cas(self):
        if (self.expected_revision <= 0 or self.expected_authority_epoch <= 0
                or str(UUID(self.expected_policy_id)) != self.expected_policy_id
                or not re.fullmatch("[0-9a-f]{64}", self.expected_policy_hash)):
            raise ValueError("invalid policy CAS")
        return self


class ActionCandidate(StrictDTO):
    action_type: StrictStr
    stage: Literal["ANALYZE", "DRAFT", "PROPOSE", "EXECUTE"]
    risk: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
    reversal: Literal["REVERSIBLE", "COMPENSATABLE", "IRREVERSIBLE", "UNKNOWN"]
    effects: tuple[StrictStr, ...]
    envelope_sha256: StrictStr
    payload_sha256: StrictStr

    @model_validator(mode="after")
    def validate_binding(self):
        if (not re.fullmatch(r"[a-z][a-z0-9_.]{2,99}", self.action_type)
                or not self.effects or tuple(sorted(set(self.effects))) != self.effects
                or any(not re.fullmatch(r"[a-z][a-z0-9_.]{2,99}", item) for item in self.effects)
                or not re.fullmatch("[0-9a-f]{64}", self.envelope_sha256)
                or not re.fullmatch("[0-9a-f]{64}", self.payload_sha256)):
            raise ValueError("invalid action binding")
        return self


class AutonomyPolicyView(StrictDTO):
    policy: VersionPin
    policy_sha256: StrictStr
    enabled: StrictBool
    create_internal_task: Literal["AUTO", "CONFIRM"]
    send_external_message: Literal["CONFIRM"]
    authority_epoch: StrictInt
    changed_by: ObjectRef
    changed_at: AwareDatetime
    valid_until: AwareDatetime


class AutonomyDecision(StrictDTO):
    mode: Literal["ASSIST", "CONFIRM", "AUTO", "DENY"]
    reason: StrictStr
    policy: VersionPin | None
    policy_sha256: StrictStr | None
    policy_authority_epoch: StrictInt | None
    action_type: StrictStr
    stage: Literal["ANALYZE", "DRAFT", "PROPOSE", "EXECUTE"]
    risk: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
    reversal: Literal["REVERSIBLE", "COMPENSATABLE", "IRREVERSIBLE", "UNKNOWN"]
    effects: tuple[StrictStr, ...]
    envelope_sha256: StrictStr
    payload_sha256: StrictStr
    decided_at: AwareDatetime
    valid_until: AwareDatetime


def validate_stored_rules(rules: dict) -> dict:
    """Validate the complete sealed policy document; unknown keys fail closed."""
    required = {
        "schema_version", "policy_kind", "policy_ref", "organization_id", "project_ref",
        "scope", "revision", "enabled", "default_modes", "action_modes", "changed_by",
        "authority_epoch", "changed_at", "valid_until",
    }
    if type(rules) is not dict or set(rules) != required:
        _deny()
    if (rules["schema_version"] != POLICY_SCHEMA or rules["policy_kind"] != "autonomy"
            or rules["scope"] != POLICY_SCOPE or type(rules["organization_id"]) is not int
            or type(rules["revision"]) is not int or rules["revision"] <= 0
            or type(rules["enabled"]) is not bool
            or rules["default_modes"] != {"advisory": "ASSIST", "execute": "CONFIRM"}
            or set(rules["action_modes"]) != {CREATE_INTERNAL_TASK, SEND_EXTERNAL_MESSAGE}
            or rules["action_modes"].get(CREATE_INTERNAL_TASK) not in {"AUTO", "CONFIRM"}
            or rules["action_modes"].get(SEND_EXTERNAL_MESSAGE) != "CONFIRM"
            or (not rules["enabled"] and rules["action_modes"].get(CREATE_INTERNAL_TASK) != "CONFIRM")
            or type(rules["authority_epoch"]) is not int or rules["authority_epoch"] <= 0):
        _deny()
    policy_ref = ObjectRef.model_validate(rules["policy_ref"])
    project_ref = ObjectRef.model_validate(rules["project_ref"])
    changed_by = ObjectRef.model_validate(rules["changed_by"])
    if policy_ref.type != "policy" or project_ref.type != "project" or changed_by.type != "user":
        _deny()
    tenant = TaggedId(kind="int", value=str(rules["organization_id"]))
    require_same_tenant(tenant, policy_ref, project_ref, changed_by)
    changed_at = datetime.fromisoformat(rules["changed_at"])
    valid_until = datetime.fromisoformat(rules["valid_until"])
    if changed_at.tzinfo is None or valid_until.tzinfo is None or valid_until <= changed_at:
        _deny()
    return rules


class AutonomyPolicyService:
    def __init__(self, *, authority: AuthorityResolver, clock=lambda: datetime.now(timezone.utc)):
        self.authority = authority
        self.clock = clock

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            _deny()
        return now.astimezone(timezone.utc)

    def _owner(self, db, scope: RequestScope, expected_epoch: int | None = None):
        try:
            owner = self.authority.require(db, scope, "autonomy.policy.manage", self._now(), lock=True)
        except AuthorityDenied:
            _deny()
        if owner.principal_kind != "user" or owner.membership_role != "owner":
            _deny()
        if expected_epoch is not None and owner.authority_epoch != expected_epoch:
            raise AutonomyConflict("stale_authority_epoch")
        return owner

    @staticmethod
    def _matches_scope(row: ActionPolicy, scope: RequestScope) -> bool:
        try:
            rules = row.rules
            return (type(rules) is dict and rules.get("policy_kind") == "autonomy"
                    and ObjectRef.model_validate(row.scope_ref) == scope.project)
        except Exception:
            return False

    def _current(self, db, scope: RequestScope) -> ActionPolicy | None:
        rows = list(db.scalars(select(ActionPolicy).where(
            ActionPolicy.organization_id == int(scope.tenant.value),
        ).order_by(ActionPolicy.revision.desc()).with_for_update().execution_options(populate_existing=True)))
        matches = [row for row in rows if self._matches_scope(row, scope)]
        if not matches:
            return None
        ids = {row.id for row in matches}
        if len(ids) != 1:
            _deny()
        return matches[0]

    def _view(self, db, row: ActionPolicy, scope: RequestScope, *, require_live_owner: bool) -> AutonomyPolicyView:
        try:
            rules = validate_stored_rules(row.rules)
            pin = revision(ObjectRef.model_validate(rules["policy_ref"]), row.revision)
            changed_by = ObjectRef.model_validate(rules["changed_by"])
            changed_at = datetime.fromisoformat(rules["changed_at"])
            valid_until = datetime.fromisoformat(rules["valid_until"])
        except (ValueError, TypeError, KeyError):
            _deny()
        if (row.mode != "CONFIRM" or row.revision != rules["revision"]
                or row.id != pin.ref.id.value or row.organization_id != int(scope.tenant.value)
                or rules["organization_id"] != row.organization_id
                or ObjectRef.model_validate(rules["project_ref"]) != scope.project
                or ObjectRef.model_validate(row.scope_ref) != scope.project
                or row.policy_hash != canonical_hash(rules) or utc(row.valid_until) != valid_until
                or valid_until <= self._now()):
            _deny()
        effective_valid_until = valid_until
        if require_live_owner:
            try:
                live = self.authority.require_principal(
                    # The enabling human remains authoritative at every AUTO decision.
                    db, tenant_id=row.organization_id, project_id=int(scope.project.id.value),
                    principal_kind="user", principal_id=changed_by.id.value,
                    operation="autonomy.policy.manage", now=self._now(), lock=True,
                )
            except AuthorityDenied:
                _deny()
            if live.membership_role != "owner" or live.authority_epoch != rules["authority_epoch"]:
                _deny()
            effective_valid_until = min(valid_until, live.valid_until)
        return AutonomyPolicyView(
            policy=pin, policy_sha256=row.policy_hash, enabled=rules["enabled"],
            create_internal_task=rules["action_modes"][CREATE_INTERNAL_TASK],
            send_external_message="CONFIRM", authority_epoch=rules["authority_epoch"],
            changed_by=changed_by, changed_at=changed_at, valid_until=effective_valid_until,
        )

    def _rules(self, *, scope, policy_ref, revision_number, owner, enabled,
               create_mode, valid_until, now):
        return {
            "schema_version": POLICY_SCHEMA,
            "policy_kind": "autonomy",
            "policy_ref": policy_ref.model_dump(mode="json"),
            "organization_id": int(scope.tenant.value),
            "project_ref": scope.project.model_dump(mode="json"),
            "scope": POLICY_SCOPE,
            "revision": revision_number,
            "enabled": enabled,
            "default_modes": {"advisory": "ASSIST", "execute": "CONFIRM"},
            "action_modes": {CREATE_INTERNAL_TASK: create_mode, SEND_EXTERNAL_MESSAGE: "CONFIRM"},
            "changed_by": scope.actor.model_dump(mode="json"),
            "authority_epoch": owner.authority_epoch,
            "changed_at": now.isoformat(),
            "valid_until": valid_until.isoformat(),
        }

    def _append(self, db, scope, row, event, owner):
        subject = reference(scope, "policy", row.id)
        v54_transactions.append_audit(
            db, scope=scope,
            event=AuditAppend(subject=subject, subject_pin=revision(subject, row.revision),
                              sequence=sequence(db, subject), event=event),
            authorize=lambda session, request, target: bool(
                session is db and request == scope and target == subject
                and owner.membership_role == "owner"
                and "autonomy.policy.manage" in owner.permissions
            ),
        )

    def assign(self, db, *, scope: RequestScope, command: PolicyAssignmentCommand) -> AutonomyPolicyView:
        if not db.in_transaction():
            _deny()
        now = self._now()
        owner = self._owner(db, scope, command.expected_authority_epoch)
        valid_until = command.valid_until.astimezone(timezone.utc)
        if valid_until <= now or valid_until > owner.valid_until:
            _deny()
        current = self._current(db, scope)
        if command.expected_revision == 0:
            if current is not None:
                raise AutonomyConflict("policy_exists")
            policy_ref = reference(scope, "policy", str(uuid4()))
            next_revision = 1
        else:
            if (current is None or current.id != command.expected_policy_id
                    or current.revision != command.expected_revision
                    or current.policy_hash != command.expected_policy_hash):
                raise AutonomyConflict("stale_policy")
            policy_ref = reference(scope, "policy", current.id)
            next_revision = current.revision + 1
        rules = self._rules(
            scope=scope, policy_ref=policy_ref, revision_number=next_revision,
            owner=owner, enabled=True, create_mode=command.create_internal_task,
            valid_until=valid_until, now=now,
        )
        row = ActionPolicy(
            id=policy_ref.id.value, revision=next_revision,
            organization_id=int(scope.tenant.value), mode="CONFIRM",
            policy_hash=canonical_hash(rules), scope_ref=scope.project.model_dump(mode="json"),
            rules=rules, valid_until=valid_until,
        )
        db.add(row)
        db.flush()
        self._append(db, scope, row, "AUTONOMY_POLICY_CHANGED", owner)
        return self._view(db, row, scope, require_live_owner=False)

    def revoke(self, db, *, scope: RequestScope, command: PolicyRevokeCommand) -> AutonomyPolicyView:
        if not db.in_transaction():
            _deny()
        now = self._now()
        owner = self._owner(db, scope, command.expected_authority_epoch)
        current = self._current(db, scope)
        if (current is None or current.id != command.expected_policy_id
                or current.revision != command.expected_revision
                or current.policy_hash != command.expected_policy_hash):
            raise AutonomyConflict("stale_policy")
        live = self._view(db, current, scope, require_live_owner=False)
        valid_until = min(live.valid_until, owner.valid_until)
        if valid_until <= now:
            _deny()
        rules = self._rules(
            scope=scope, policy_ref=live.policy.ref, revision_number=current.revision + 1,
            owner=owner, enabled=False, create_mode="CONFIRM", valid_until=valid_until, now=now,
        )
        row = ActionPolicy(
            id=current.id, revision=current.revision + 1, organization_id=current.organization_id,
            mode="CONFIRM", policy_hash=canonical_hash(rules), scope_ref=current.scope_ref,
            rules=rules, valid_until=valid_until,
        )
        db.add(row)
        db.flush()
        self._append(db, scope, row, "AUTONOMY_POLICY_REVOKED", owner)
        return self._view(db, row, scope, require_live_owner=False)

    def get(self, db, *, scope: RequestScope) -> AutonomyPolicyView | None:
        self._owner(db, scope)
        row = self._current(db, scope)
        return self._view(db, row, scope, require_live_owner=False) if row else None

    def decide(self, db, *, scope: RequestScope, candidate: ActionCandidate) -> AutonomyDecision:
        if not db.in_transaction():
            _deny()
        now = self._now()
        try:
            requester = self.authority.require(db, scope, "action.freeze", now, lock=True)
        except AuthorityDenied:
            _deny()
        row = self._current(db, scope)
        view = None
        if row is not None:
            view = self._view(db, row, scope, require_live_owner=True)
        if candidate.stage != "EXECUTE":
            mode, reason = "ASSIST", "advisory_stage"
        elif candidate.action_type == CREATE_INTERNAL_TASK:
            safe = (candidate.risk == "LOW" and candidate.reversal == "COMPENSATABLE"
                    and candidate.effects == _AUTO_EFFECTS)
            if safe and view and view.enabled and view.create_internal_task == "AUTO":
                mode, reason = "AUTO", "explicit_low_risk_policy"
            else:
                mode, reason = "CONFIRM", "human_confirmation_required"
        elif candidate.action_type == SEND_EXTERNAL_MESSAGE or candidate.action_type.startswith(_CONFIRM_PREFIXES):
            mode, reason = "CONFIRM", "protected_effect"
        else:
            mode, reason = "DENY", "unknown_capability"
        # Defense in depth: no malformed/high-risk candidate can become AUTO.
        if mode == "AUTO" and (candidate.risk != "LOW" or candidate.action_type != CREATE_INTERNAL_TASK):
            mode, reason = "CONFIRM", "human_confirmation_required"
        default_until = now + timedelta(days=366)
        valid_until = min(view.valid_until, requester.valid_until, default_until) if view else min(
            requester.valid_until, default_until
        )
        return AutonomyDecision(
            mode=mode, reason=reason, policy=view.policy if view else None,
            policy_sha256=view.policy_sha256 if view else None,
            policy_authority_epoch=view.authority_epoch if view else None,
            action_type=candidate.action_type, stage=candidate.stage, risk=candidate.risk,
            reversal=candidate.reversal, effects=candidate.effects,
            envelope_sha256=candidate.envelope_sha256, payload_sha256=candidate.payload_sha256,
            decided_at=now, valid_until=valid_until,
        )

    def recheck(self, db, *, scope: RequestScope, candidate: ActionCandidate,
                decision: AutonomyDecision) -> AutonomyDecision:
        exact = (
            decision.action_type == candidate.action_type,
            decision.stage == candidate.stage,
            decision.risk == candidate.risk,
            decision.reversal == candidate.reversal,
            decision.effects == candidate.effects,
            decision.envelope_sha256 == candidate.envelope_sha256,
            decision.payload_sha256 == candidate.payload_sha256,
        )
        if not all(exact) or decision.valid_until <= self._now():
            raise AutonomyConflict("stale_action_binding")
        live = self.decide(db, scope=scope, candidate=candidate)
        if (live.mode != decision.mode or live.policy != decision.policy
                or live.policy_sha256 != decision.policy_sha256
                or live.policy_authority_epoch != decision.policy_authority_epoch):
            raise AutonomyConflict("stale_policy_decision")
        return live
