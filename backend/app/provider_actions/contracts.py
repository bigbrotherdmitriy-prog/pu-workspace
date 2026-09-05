from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal, Protocol


_SHA256 = re.compile(r"[0-9a-f]{64}")
ActionKind = Literal[
    "synthetic.effect.apply",
    "synthetic.effect.send",
    "synthetic.effect.rollback",
    "synthetic.effect.compensate",
    "synthetic.effect.corrective",
    "gmail.message.send",
    "google.tasks.upsert",
    "google.calendar.upsert",
]
Outcome = Literal["APPLIED", "NOT_APPLIED", "UNKNOWN"]


class ProviderActionError(RuntimeError):
    """Fail-closed error whose text is always an allowlisted, non-sensitive code."""

    ALLOWED = {
        "approval_expired", "approval_mismatch", "approval_required", "authority_stale",
        "capability_stale", "command_conflict", "confirm_only", "credential_stale",
        "dispatch_binding_mismatch", "evidence_stale", "invalid_envelope", "mailbox_scope_mismatch",
        "outcome_not_reconcilable", "project_scope_mismatch", "provider_receipt_mismatch",
        "relation_invalid", "synthetic_only", "payload_stale", "resource_unavailable",
    }

    def __init__(self, code: str):
        self.code = code if code in self.ALLOWED else "dispatch_binding_mismatch"
        super().__init__(self.code)


class TimeoutBeforeEffect(TimeoutError):
    """Adapter proves that no provider effect happened."""


class TimeoutAfterEffect(TimeoutError):
    """Adapter cannot prove whether the provider effect happened."""


class ProviderPreconditionFailed(ValueError):
    """The adapter proved that it stopped before any provider mutation."""


@dataclass(frozen=True)
class ActionEnvelope:
    """Immutable, content-free provider command seal.

    Payload bytes, addresses, tokens, DSNs and human labels are deliberately not
    representable. Callers supply only an already-computed SHA-256 payload hash.
    """

    action_id: str
    revision: int
    organization_id: int
    project_id: int
    mailbox_key: str
    provider: str
    mode: str
    synthetic_only: bool
    action_kind: ActionKind
    reversibility: Literal["REVERSIBLE", "COMPENSATABLE", "IRREVERSIBLE"]
    payload_hash: str
    command_key: str
    idempotency_key: str
    context_revision: int
    evidence_pins: tuple[str, ...]
    authority_epoch: int
    capability_version: int
    credential_generation: int
    relation_kind: Literal["ROLLBACK", "COMPENSATION", "CORRECTIVE"] | None = None
    relation_action_id: str | None = None

    def __post_init__(self):
        scalar_strings = (self.action_id, self.mailbox_key, self.provider, self.mode, self.action_kind,
                          self.reversibility, self.payload_hash, self.command_key, self.idempotency_key)
        if (not all(isinstance(value, str) and value for value in scalar_strings)
                or self.revision <= 0 or self.organization_id <= 0 or self.project_id <= 0
                or self.context_revision <= 0 or self.authority_epoch <= 0
                or self.capability_version <= 0 or self.credential_generation <= 0
                or not _SHA256.fullmatch(self.mailbox_key) or not _SHA256.fullmatch(self.payload_hash)
                or len(self.action_id) > 100 or len(self.command_key) > 200 or len(self.idempotency_key) > 200
                or not self.evidence_pins or any(not isinstance(pin, str) or not pin or len(pin) > 200
                                                 for pin in self.evidence_pins)
                or (self.relation_kind is None) != (self.relation_action_id is None)
                or (self.relation_action_id is not None and self.relation_action_id == self.action_id)
                or self.action_kind not in {"synthetic.effect.apply", "synthetic.effect.send",
                                            "synthetic.effect.rollback", "synthetic.effect.compensate",
                                            "synthetic.effect.corrective", "gmail.message.send",
                                            "google.tasks.upsert", "google.calendar.upsert"}
                or self.reversibility not in {"REVERSIBLE", "COMPENSATABLE", "IRREVERSIBLE"}
                or self.relation_kind not in {None, "ROLLBACK", "COMPENSATION", "CORRECTIVE"}):
            raise ProviderActionError("invalid_envelope")
        if self.mode != "CONFIRM":
            raise ProviderActionError("confirm_only")
        synthetic = self.synthetic_only and self.provider == "synthetic" and self.action_kind.startswith("synthetic.")
        product = (not self.synthetic_only and self.provider == "google_workspace"
                   and self.action_kind in {"gmail.message.send", "google.tasks.upsert",
                                            "google.calendar.upsert"})
        if not (synthetic or product):
            raise ProviderActionError("synthetic_only")

    @property
    def envelope_hash(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class LiveAuthority:
    organization_id: int
    project_id: int
    mailbox_key: str
    authority_epoch: int
    capability_version: int
    credential_generation: int
    evidence_pins: tuple[str, ...]
    valid_until: datetime
    can_dispatch: bool
    can_reconcile: bool


@dataclass(frozen=True)
class ExactApproval:
    id: str
    action_id: str
    revision: int
    organization_id: int
    project_id: int
    mailbox_key: str
    command_key: str
    idempotency_key: str
    payload_hash: str
    envelope_hash: str
    authority_epoch: int
    capability_version: int
    credential_generation: int
    expires_at: datetime


@dataclass(frozen=True)
class ProviderRequest:
    action_id: str
    revision: int
    organization_id: int
    project_id: int
    mailbox_key: str
    command_key: str
    idempotency_key: str
    payload_hash: str
    action_kind: str
    capability_version: int
    credential_generation: int


@dataclass(frozen=True)
class ProviderReceipt:
    action_id: str
    revision: int
    organization_id: int
    project_id: int
    mailbox_key: str
    command_key: str
    idempotency_key: str
    payload_hash: str
    outcome: Outcome
    retry_safe: bool = False
    external_ref: str | None = None


class ProviderActionAdapter(Protocol):
    name: str

    def dispatch(self, request: ProviderRequest) -> ProviderReceipt: ...

    def lookup(self, request: ProviderRequest) -> ProviderReceipt | None: ...


class LiveAuthorityResolver(Protocol):
    def resolve(self, envelope: ActionEnvelope, *, operation: Literal["dispatch", "reconcile"]) -> LiveAuthority: ...
