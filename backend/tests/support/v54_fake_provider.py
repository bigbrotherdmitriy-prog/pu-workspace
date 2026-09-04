"""Synthetic provider-effect acceptance harness for the v5.4 contract.

This module deliberately imports no product code.  It stores only opaque IDs,
payload digests and safe counters; it is not an email or document fixture.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from hashlib import sha256
from typing import Protocol


def payload_digest(synthetic_payload: str) -> str:
    """Return a seal input digest without retaining the supplied test payload."""
    return sha256(synthetic_payload.encode("utf-8")).hexdigest()


class Mode(str, Enum):
    ASSIST = "ASSIST"
    CONFIRM = "CONFIRM"
    AUTO = "AUTO"


class Risk(str, Enum):
    LOW = "LOW"
    HIGH = "HIGH"


class EffectKind(str, Enum):
    INTERNAL_TASK = "internal_task.create"
    EXTERNAL_DRAFT = "external_message.draft"
    EXTERNAL_SEND = "external_message.send"
    INTERNAL_CANCEL = "internal_task.cancel"
    CORRECTIVE_FOLLOW_UP = "external_message.corrective_follow_up"


class Reversibility(str, Enum):
    REVERSIBLE = "REVERSIBLE"
    COMPENSATABLE = "COMPENSATABLE"
    IRREVERSIBLE = "IRREVERSIBLE"


class Outcome(str, Enum):
    ASSISTED = "ASSISTED"
    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    UNKNOWN = "UNKNOWN"
    ROLLED_BACK = "ROLLED_BACK"


class Fault(str, Enum):
    TIMEOUT_BEFORE_EFFECT = "timeout_before_effect"
    TIMEOUT_AFTER_EFFECT = "timeout_after_effect"


class ContractError(RuntimeError):
    """Error exposing only an allowlisted code."""

    ALLOWED = {
        "approval_required",
        "approval_mismatch",
        "authority_stale",
        "auto_denied",
        "capability_stale",
        "command_conflict",
        "compensation_required",
        "corrective_action_invalid",
        "credential_stale",
        "irreversible_action",
        "mailbox_scope_mismatch",
        "project_scope_mismatch",
        "rollback_not_available",
        "unknown_outcome",
    }

    def __init__(self, code: str):
        if code not in self.ALLOWED:
            code = "approval_mismatch"
        self.code = code
        super().__init__(code)


class TimeoutBeforeEffect(TimeoutError):
    pass


class TimeoutAfterEffect(TimeoutError):
    pass


@dataclass(frozen=True)
class MailboxIdentity:
    provider: str
    account_id: str
    namespace: str
    credential_generation: int

    @property
    def key(self) -> str:
        raw = f"{self.provider}\x1f{self.account_id}\x1f{self.namespace}"
        return sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CapabilitySnapshot:
    mailbox_key: str
    version: int
    allows_internal_task: bool = True
    allows_draft: bool = True
    allows_send: bool = True
    supports_idempotency: bool = True
    supports_lookup: bool = True


@dataclass(frozen=True)
class ContextRevision:
    mailbox_key: str
    message_id: str
    revision: int
    project_id: str
    contract_id: str
    evidence_pins: tuple[str, ...]


@dataclass(frozen=True)
class SealedAction:
    action_id: str
    revision: int
    mode: Mode
    risk: Risk
    effect_kind: EffectKind
    reversibility: Reversibility
    mailbox: MailboxIdentity
    project_id: str
    context_revision: int
    evidence_pins: tuple[str, ...]
    payload_hash: str
    command_key: str
    capability_version: int
    authority_epoch: int
    corrects_action_id: str | None = None


@dataclass(frozen=True)
class ExactApproval:
    approval_id: str
    action_id: str
    action_revision: int
    payload_hash: str
    mailbox_key: str
    project_id: str
    capability_version: int
    credential_generation: int
    authority_epoch: int


@dataclass(frozen=True)
class EffectReceipt:
    action_id: str
    action_revision: int
    command_key: str
    payload_hash: str
    mailbox_key: str
    outcome: Outcome
    external_id: str | None = None
    retry_safe: bool = False


@dataclass(frozen=True)
class ProviderRequest:
    action_id: str
    action_revision: int
    command_key: str
    payload_hash: str
    mailbox_key: str
    effect_kind: EffectKind
    reversibility: Reversibility
    capability_version: int
    credential_generation: int


class CommunicationActionPort(Protocol):
    """Minimum future adapter contract; no dependency on parallel branches."""

    def correct_context(self, previous: ContextRevision, corrected: ContextRevision) -> None: ...

    def approve(self, action: SealedAction, approval_id: str) -> ExactApproval: ...

    def execute(self, action: SealedAction, approval: ExactApproval | None) -> EffectReceipt: ...

    def reconcile(self, action: SealedAction) -> EffectReceipt: ...


class StrictFakeProvider:
    """Deterministic provider with scoped idempotency and injected timeouts."""

    def __init__(self) -> None:
        self._mailboxes: dict[str, MailboxIdentity] = {}
        self._capabilities: dict[str, CapabilitySnapshot] = {}
        self._bindings: dict[tuple[str, str], ProviderRequest] = {}
        self._effects: dict[tuple[str, str], EffectReceipt] = {}
        self._external: dict[tuple[str, str], EffectReceipt] = {}
        self._faults: dict[tuple[str, str], Fault] = {}
        self._calls: dict[str, int] = {"dispatch": 0, "lookup": 0, "effects": 0}
        self._mailbox_effect_counts: dict[str, int] = {}
        self._journal: list[dict[str, str | int]] = []

    def register(self, mailbox: MailboxIdentity, capability_version: int = 1) -> CapabilitySnapshot:
        snapshot = CapabilitySnapshot(mailbox.key, capability_version)
        self._mailboxes[mailbox.key] = mailbox
        self._capabilities[mailbox.key] = snapshot
        return snapshot

    def revoke_credentials(self, mailbox: MailboxIdentity) -> MailboxIdentity:
        current = self._mailboxes[mailbox.key]
        revoked = replace(current, credential_generation=current.credential_generation + 1)
        self._mailboxes[mailbox.key] = revoked
        self._record("credential_revoked", mailbox.key)
        return revoked

    def refresh_capabilities(self, mailbox: MailboxIdentity) -> CapabilitySnapshot:
        current = self._capabilities[mailbox.key]
        refreshed = replace(current, version=current.version + 1)
        self._capabilities[mailbox.key] = refreshed
        self._record("capability_refreshed", mailbox.key)
        return refreshed

    def inject_fault(self, mailbox: MailboxIdentity, command_key: str, fault: Fault) -> None:
        self._faults[(mailbox.key, command_key)] = fault

    def current_mailbox(self, mailbox_key: str) -> MailboxIdentity | None:
        return self._mailboxes.get(mailbox_key)

    def capability_snapshot(self, mailbox_key: str) -> CapabilitySnapshot | None:
        return self._capabilities.get(mailbox_key)

    def dispatch(self, request: ProviderRequest) -> EffectReceipt:
        self._calls["dispatch"] += 1
        current_mailbox = self._mailboxes.get(request.mailbox_key)
        if current_mailbox is None:
            raise ContractError("mailbox_scope_mismatch")
        if current_mailbox.credential_generation != request.credential_generation:
            raise ContractError("credential_stale")
        capability = self._capabilities[request.mailbox_key]
        if capability.version != request.capability_version:
            raise ContractError("capability_stale")
        self._require_capability(capability, request.effect_kind)

        key = (request.mailbox_key, request.command_key)
        bound_request = self._bindings.get(key)
        if bound_request is not None and bound_request != request:
            raise ContractError("command_conflict")
        existing = self._effects.get(key)
        if existing is not None:
            self._record("effect_replayed", request.mailbox_key)
            return existing
        self._bindings[key] = request

        fault = self._faults.pop(key, None)
        if fault is Fault.TIMEOUT_BEFORE_EFFECT:
            self._record("timeout_before_effect", request.mailbox_key)
            raise TimeoutBeforeEffect("timeout_before_effect")

        sequence = self._calls["effects"] + 1
        mailbox_sequence = self._mailbox_effect_counts.get(request.mailbox_key, 0) + 1
        external_id = f"provider-object-{mailbox_sequence}"
        receipt = EffectReceipt(
            action_id=request.action_id,
            action_revision=request.action_revision,
            command_key=request.command_key,
            payload_hash=request.payload_hash,
            mailbox_key=request.mailbox_key,
            outcome=Outcome.APPLIED,
            external_id=external_id,
        )
        self._effects[key] = receipt
        self._external[(request.mailbox_key, external_id)] = receipt
        self._calls["effects"] = sequence
        self._mailbox_effect_counts[request.mailbox_key] = mailbox_sequence
        self._record("effect_applied", request.mailbox_key)
        if fault is Fault.TIMEOUT_AFTER_EFFECT:
            self._record("timeout_after_effect", request.mailbox_key)
            raise TimeoutAfterEffect("timeout_after_effect")
        return receipt

    def lookup(self, mailbox: MailboxIdentity, command_key: str) -> EffectReceipt | None:
        self._calls["lookup"] += 1
        self._record("lookup", mailbox.key)
        return self._effects.get((mailbox.key, command_key))

    def lookup_external(self, mailbox: MailboxIdentity, external_id: str) -> EffectReceipt | None:
        self._calls["lookup"] += 1
        self._record("lookup_external", mailbox.key)
        return self._external.get((mailbox.key, external_id))

    @property
    def counters(self) -> dict[str, int]:
        return dict(self._calls)

    @property
    def journal(self) -> tuple[dict[str, str | int], ...]:
        return tuple(dict(row) for row in self._journal)

    def _record(self, event: str, mailbox_key: str) -> None:
        self._journal.append({"event": event, "mailbox_key": mailbox_key, "sequence": len(self._journal) + 1})

    @staticmethod
    def _require_capability(snapshot: CapabilitySnapshot, effect: EffectKind) -> None:
        allowed = {
            EffectKind.INTERNAL_TASK: snapshot.allows_internal_task,
            EffectKind.INTERNAL_CANCEL: snapshot.allows_internal_task,
            EffectKind.EXTERNAL_DRAFT: snapshot.allows_draft,
            EffectKind.EXTERNAL_SEND: snapshot.allows_send,
            EffectKind.CORRECTIVE_FOLLOW_UP: snapshot.allows_send,
        }[effect]
        if not allowed:
            raise ContractError("capability_stale")


class SyntheticCommunicationActionHarness(CommunicationActionPort):
    """Fail-closed acceptance facade around :class:`StrictFakeProvider`."""

    def __init__(
        self,
        provider: StrictFakeProvider,
        mailbox_projects: dict[str, str],
        *,
        authority_epoch: int = 1,
    ) -> None:
        self.provider = provider
        self.mailbox_projects = dict(mailbox_projects)
        self._contexts: dict[tuple[str, str], list[ContextRevision]] = {}
        self._states: dict[tuple[str, str], EffectReceipt] = {}
        self._actions: dict[tuple[str, str], SealedAction] = {}
        self._approvals: dict[str, ExactApproval] = {}
        self._audit: list[dict[str, str | int]] = []
        self._authority_epoch = authority_epoch

    def revoke_authority(self) -> None:
        self._authority_epoch += 1
        self._audit_event("authority_changed", "authority", self._authority_epoch)

    def record_context(self, context: ContextRevision) -> None:
        context_key = (context.mailbox_key, context.message_id)
        history = self._contexts.setdefault(context_key, [])
        if history and context.revision != history[-1].revision + 1:
            raise ContractError("approval_mismatch")
        if not history and context.revision != 1:
            raise ContractError("approval_mismatch")
        history.append(context)
        self._audit_event("context_recorded", context.message_id, context.revision)

    def correct_context(self, previous: ContextRevision, corrected: ContextRevision) -> None:
        context_key = (previous.mailbox_key, previous.message_id)
        history = self._contexts.get(context_key, [])
        if (
            not history
            or history[-1] != previous
            or corrected.message_id != previous.message_id
            or corrected.mailbox_key != previous.mailbox_key
        ):
            raise ContractError("approval_mismatch")
        self.record_context(corrected)

    def approve(self, action: SealedAction, approval_id: str) -> ExactApproval:
        if action.mode is Mode.AUTO:
            raise ContractError("auto_denied")
        approval = ExactApproval(
            approval_id=approval_id,
            action_id=action.action_id,
            action_revision=action.revision,
            payload_hash=action.payload_hash,
            mailbox_key=action.mailbox.key,
            project_id=action.project_id,
            capability_version=action.capability_version,
            credential_generation=action.mailbox.credential_generation,
            authority_epoch=action.authority_epoch,
        )
        self._approvals[approval_id] = approval
        self._audit_event("approval_granted", action.action_id, action.revision)
        return approval

    def execute(self, action: SealedAction, approval: ExactApproval | None) -> EffectReceipt:
        if action.mode is Mode.ASSIST:
            self._audit_event("assist_only", action.action_id, action.revision)
            return self._receipt(action, Outcome.ASSISTED)
        if action.mode is Mode.AUTO:
            raise ContractError("auto_denied")
        if action.effect_kind is EffectKind.CORRECTIVE_FOLLOW_UP and (
            not action.corrects_action_id or action.corrects_action_id == action.action_id
        ):
            raise ContractError("corrective_action_invalid")
        self._require_scope(action)
        self._require_approval(action, approval)
        if action.effect_kind is EffectKind.CORRECTIVE_FOLLOW_UP:
            self._require_corrective_target(action)

        key = (action.mailbox.key, action.command_key)
        existing = self._states.get(key)
        if existing is not None:
            self._require_exact_state(action, existing)
            if existing.outcome is Outcome.UNKNOWN:
                return existing
            return existing

        request = ProviderRequest(
            action_id=action.action_id,
            action_revision=action.revision,
            command_key=action.command_key,
            payload_hash=action.payload_hash,
            mailbox_key=action.mailbox.key,
            effect_kind=action.effect_kind,
            reversibility=action.reversibility,
            capability_version=action.capability_version,
            credential_generation=action.mailbox.credential_generation,
        )
        try:
            receipt = self.provider.dispatch(request)
        except TimeoutBeforeEffect:
            receipt = self._receipt(action, Outcome.NOT_APPLIED, retry_safe=True)
            self._states[key] = receipt
            self._actions[key] = action
            self._audit_event("provider_not_applied", action.action_id, action.revision)
            return receipt
        except TimeoutAfterEffect:
            receipt = self._receipt(action, Outcome.UNKNOWN)
            self._states[key] = receipt
            self._actions[key] = action
            self._audit_event("provider_unknown", action.action_id, action.revision)
            return receipt
        self._states[key] = receipt
        self._actions[key] = action
        self._audit_event("provider_applied", action.action_id, action.revision)
        return receipt

    def retry_not_applied(self, action: SealedAction, approval: ExactApproval) -> EffectReceipt:
        self._require_scope(action)
        self._require_approval(action, approval)
        key = (action.mailbox.key, action.command_key)
        prior = self._states.get(key)
        if prior is None:
            return self.execute(action, approval)
        if prior.outcome is Outcome.UNKNOWN:
            raise ContractError("unknown_outcome")
        if prior.outcome is not Outcome.NOT_APPLIED or not prior.retry_safe:
            return prior
        del self._states[key]
        return self.execute(action, approval)

    def reconcile(self, action: SealedAction) -> EffectReceipt:
        self._require_scope(action)
        key = (action.mailbox.key, action.command_key)
        current = self._states.get(key)
        if current is None or current.outcome is not Outcome.UNKNOWN:
            raise ContractError("unknown_outcome")
        self._require_exact_state(action, current)
        found = self.provider.lookup(action.mailbox, action.command_key)
        if found is None:
            return current
        self._require_exact_state(action, found)
        if found.outcome is not Outcome.APPLIED:
            raise ContractError("unknown_outcome")
        self._states[key] = found
        self._audit_event("provider_reconciled", action.action_id, action.revision)
        return found

    def mark_rolled_back(self, action: SealedAction, approval: ExactApproval) -> EffectReceipt:
        self._require_scope(action)
        self._require_approval(action, approval)
        if action.reversibility is Reversibility.IRREVERSIBLE:
            raise ContractError("irreversible_action")
        if action.reversibility is Reversibility.COMPENSATABLE:
            raise ContractError("compensation_required")
        key = (action.mailbox.key, action.command_key)
        prior = self._states.get(key)
        if prior is None:
            raise ContractError("rollback_not_available")
        self._require_exact_state(action, prior)
        if prior.outcome is Outcome.ROLLED_BACK:
            return prior
        if prior.outcome is not Outcome.APPLIED:
            raise ContractError("rollback_not_available")
        receipt = self._receipt(action, Outcome.ROLLED_BACK)
        self._states[key] = receipt
        self._audit_event("effect_rolled_back", action.action_id, action.revision)
        return receipt

    @property
    def audit(self) -> tuple[dict[str, str | int], ...]:
        return tuple(dict(row) for row in self._audit)

    def context_history(self, mailbox_key: str, message_id: str) -> tuple[ContextRevision, ...]:
        return tuple(self._contexts.get((mailbox_key, message_id), ()))

    def _require_scope(self, action: SealedAction) -> None:
        expected = self.mailbox_projects.get(action.mailbox.key)
        if expected is None:
            raise ContractError("mailbox_scope_mismatch")
        if expected != action.project_id:
            raise ContractError("project_scope_mismatch")
        mailbox = self.provider.current_mailbox(action.mailbox.key)
        if mailbox is None:
            raise ContractError("mailbox_scope_mismatch")
        if mailbox.credential_generation != action.mailbox.credential_generation:
            raise ContractError("credential_stale")
        snapshot = self.provider.capability_snapshot(action.mailbox.key)
        if snapshot is None:
            raise ContractError("mailbox_scope_mismatch")
        if snapshot.version != action.capability_version:
            raise ContractError("capability_stale")
        if action.authority_epoch != self._authority_epoch:
            raise ContractError("authority_stale")

    def _require_approval(self, action: SealedAction, approval: ExactApproval | None) -> None:
        if approval is None:
            raise ContractError("approval_required")
        expected = (
            action.action_id,
            action.revision,
            action.payload_hash,
            action.mailbox.key,
            action.project_id,
            action.capability_version,
            action.mailbox.credential_generation,
            action.authority_epoch,
        )
        actual = (
            approval.action_id,
            approval.action_revision,
            approval.payload_hash,
            approval.mailbox_key,
            approval.project_id,
            approval.capability_version,
            approval.credential_generation,
            approval.authority_epoch,
        )
        if expected != actual or self._approvals.get(approval.approval_id) != approval:
            raise ContractError("approval_mismatch")

    def _require_exact_state(self, action: SealedAction, receipt: EffectReceipt) -> None:
        key = (action.mailbox.key, action.command_key)
        if (
            receipt.action_id != action.action_id
            or receipt.action_revision != action.revision
            or receipt.command_key != action.command_key
            or receipt.payload_hash != action.payload_hash
            or receipt.mailbox_key != action.mailbox.key
            or self._actions.get(key) != action
        ):
            raise ContractError("command_conflict")

    def _require_corrective_target(self, action: SealedAction) -> None:
        matches = [
            (key, prior)
            for key, prior in self._actions.items()
            if prior.action_id == action.corrects_action_id
        ]
        if len(matches) != 1:
            raise ContractError("corrective_action_invalid")
        key, prior = matches[0]
        receipt = self._states.get(key)
        if (
            receipt is None
            or receipt.outcome is not Outcome.APPLIED
            or prior.mailbox.key != action.mailbox.key
            or prior.project_id != action.project_id
            or prior.effect_kind is not EffectKind.EXTERNAL_SEND
            or prior.reversibility is not Reversibility.IRREVERSIBLE
        ):
            raise ContractError("corrective_action_invalid")

    @staticmethod
    def _receipt(action: SealedAction, outcome: Outcome, retry_safe: bool = False) -> EffectReceipt:
        return EffectReceipt(
            action_id=action.action_id,
            action_revision=action.revision,
            command_key=action.command_key,
            payload_hash=action.payload_hash,
            mailbox_key=action.mailbox.key,
            outcome=outcome,
            retry_safe=retry_safe,
        )

    def _audit_event(self, event: str, subject_id: str, revision: int) -> None:
        self._audit.append({"event": event, "subject_id": subject_id, "revision": revision,
                            "sequence": len(self._audit) + 1})
        "compensation_required",
        "corrective_action_invalid",
