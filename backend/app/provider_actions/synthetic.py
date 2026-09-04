from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from enum import Enum

from app.provider_actions.contracts import (
    ActionEnvelope,
    LiveAuthority,
    ProviderActionError,
    ProviderReceipt,
    ProviderRequest,
    TimeoutAfterEffect,
    TimeoutBeforeEffect,
)


class Fault(str, Enum):
    TIMEOUT_BEFORE_EFFECT = "timeout_before_effect"
    TIMEOUT_AFTER_EFFECT = "timeout_after_effect"
    PROCESS_EXIT_AFTER_EFFECT = "process_exit_after_effect"


class ProcessExitAfterEffect(BaseException):
    """Synthetic process-fault signal intentionally not caught by the runtime."""


class StrictSyntheticProvider:
    """Deterministic adapter with exact scoped idempotency and no live I/O."""

    name = "synthetic"

    def __init__(self):
        self._mailboxes: dict[str, tuple[int, int]] = {}
        self._bindings: dict[tuple[str, str], ProviderRequest] = {}
        self._effects: dict[tuple[str, str], ProviderReceipt] = {}
        self._faults: dict[tuple[str, str], Fault] = {}
        self._counts = {"dispatch": 0, "lookup": 0, "effects": 0}

    def register(self, mailbox_key: str, *, capability_version: int, credential_generation: int):
        self._mailboxes[mailbox_key] = (capability_version, credential_generation)

    def inject_fault(self, mailbox_key: str, command_key: str, fault: Fault):
        self._faults[(mailbox_key, command_key)] = fault

    @property
    def counters(self):
        return dict(self._counts)

    def dispatch(self, request: ProviderRequest) -> ProviderReceipt:
        self._counts["dispatch"] += 1
        versions = self._mailboxes.get(request.mailbox_key)
        if versions is None:
            raise ProviderActionError("mailbox_scope_mismatch")
        if versions[0] != request.capability_version:
            raise ProviderActionError("capability_stale")
        if versions[1] != request.credential_generation:
            raise ProviderActionError("credential_stale")
        key = (request.mailbox_key, request.command_key)
        prior = self._bindings.get(key)
        if prior is not None and prior != request:
            raise ProviderActionError("command_conflict")
        if key in self._effects:
            return self._effects[key]
        self._bindings[key] = request
        fault = self._faults.pop(key, None)
        if fault is Fault.TIMEOUT_BEFORE_EFFECT:
            raise TimeoutBeforeEffect()
        receipt = ProviderReceipt(
            action_id=request.action_id, revision=request.revision,
            organization_id=request.organization_id, project_id=request.project_id,
            mailbox_key=request.mailbox_key, command_key=request.command_key,
            idempotency_key=request.idempotency_key, payload_hash=request.payload_hash,
            outcome="APPLIED", external_ref=f"synthetic-object-{self._counts['effects'] + 1}",
        )
        self._effects[key] = receipt
        self._counts["effects"] += 1
        if fault is Fault.TIMEOUT_AFTER_EFFECT:
            raise TimeoutAfterEffect()
        if fault is Fault.PROCESS_EXIT_AFTER_EFFECT:
            raise ProcessExitAfterEffect()
        return receipt

    def lookup(self, request: ProviderRequest) -> ProviderReceipt | None:
        self._counts["lookup"] += 1
        prior = self._bindings.get((request.mailbox_key, request.command_key))
        if prior is not None and prior != request:
            raise ProviderActionError("command_conflict")
        return self._effects.get((request.mailbox_key, request.command_key))


class SyntheticAuthority:
    """Mutable test authority proving that runtime checks are live, not snapshots."""

    def __init__(self, *, now=lambda: datetime.now(timezone.utc)):
        self.now = now
        self._states: dict[tuple[int, str], LiveAuthority] = {}

    def grant(self, envelope: ActionEnvelope, *, valid_until: datetime):
        self._states[(envelope.organization_id, envelope.mailbox_key)] = LiveAuthority(
            organization_id=envelope.organization_id, project_id=envelope.project_id,
            mailbox_key=envelope.mailbox_key, authority_epoch=envelope.authority_epoch,
            capability_version=envelope.capability_version,
            credential_generation=envelope.credential_generation, evidence_pins=envelope.evidence_pins,
            valid_until=valid_until, can_dispatch=True, can_reconcile=True,
        )

    def _change(self, envelope: ActionEnvelope, **values):
        key = (envelope.organization_id, envelope.mailbox_key)
        self._states[key] = replace(self._states[key], **values)

    def revoke_authority(self, envelope):
        self._change(envelope, authority_epoch=envelope.authority_epoch + 1)

    def revoke_capability(self, envelope):
        self._change(envelope, capability_version=envelope.capability_version + 1)

    def revoke_credential(self, envelope):
        self._change(envelope, credential_generation=envelope.credential_generation + 1)

    def revoke_evidence(self, envelope):
        self._change(envelope, evidence_pins=())

    def resolve(self, envelope: ActionEnvelope, *, operation: str) -> LiveAuthority:
        current = self._states.get((envelope.organization_id, envelope.mailbox_key))
        if current is None or current.mailbox_key != envelope.mailbox_key:
            raise ProviderActionError("mailbox_scope_mismatch")
        if current.project_id != envelope.project_id:
            raise ProviderActionError("project_scope_mismatch")
        if current.authority_epoch != envelope.authority_epoch or current.valid_until <= self.now():
            raise ProviderActionError("authority_stale")
        if current.capability_version != envelope.capability_version:
            raise ProviderActionError("capability_stale")
        if current.credential_generation != envelope.credential_generation:
            raise ProviderActionError("credential_stale")
        if current.evidence_pins != envelope.evidence_pins:
            raise ProviderActionError("evidence_stale")
        if (operation == "dispatch" and not current.can_dispatch) or (operation == "reconcile" and not current.can_reconcile):
            raise ProviderActionError("authority_stale")
        return current
