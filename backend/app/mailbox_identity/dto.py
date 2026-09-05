from typing import Literal

from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from app.core.v54_refs import StrictDTO


class ReconciliationEvidencePin(StrictDTO):
    evidence_id: StrictStr = Field(min_length=36, max_length=36)
    evidence_revision: StrictInt
    assessment_record_version: StrictInt

    @model_validator(mode="after")
    def positive_versions(self):
        if self.evidence_revision != 1 or self.assessment_record_version <= 0:
            raise ValueError("invalid_evidence_pin")
        return self


class ReconciliationCommand(StrictDTO):
    decision_key: StrictStr = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    message_id: StrictInt
    expected_message_origin_version: StrictInt
    expected_current_origin_version: StrictInt
    identity_id: StrictStr = Field(min_length=36, max_length=36)
    identity_record_version: StrictInt
    mail_connection_id: StrictStr = Field(min_length=36, max_length=36)
    mail_connection_record_version: StrictInt
    binding_epoch: StrictInt
    credential_generation: StrictInt
    source_reference_id: StrictStr = Field(min_length=36, max_length=36)
    source_reference_record_version: StrictInt
    source_version_id: StrictStr = Field(min_length=36, max_length=36)
    source_version_revision: StrictInt
    evidence_refs: tuple[ReconciliationEvidencePin, ...] = Field(min_length=1, max_length=50)
    reason_code: Literal["provider_export_verified", "manual_correction"]
    correlation_id: StrictStr = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    authority_version: StrictInt
    outcome: Literal["CONFIRM", "REJECT", "LEAVE_UNRESOLVED"]

    @model_validator(mode="after")
    def positive_versions(self):
        values = (self.message_id, self.expected_message_origin_version,
                  self.expected_current_origin_version, self.identity_record_version,
                  self.mail_connection_record_version, self.binding_epoch,
                  self.credential_generation, self.source_reference_record_version,
                  self.source_version_revision, self.authority_version)
        evidence_ids = [pin.evidence_id for pin in self.evidence_refs]
        if any(value <= 0 for value in values) or len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("invalid_reconciliation_command")
        return self


class ReconciliationResult(StrictDTO):
    decision_id: StrictStr
    binding_id: StrictStr
    origin_version: StrictInt
    state: Literal["unresolved", "confirmed", "rejected"]
    idempotent_replay: bool = False


class MailboxRolloutTransition(StrictDTO):
    """One explicitly confirmed transition for one exact mailbox generation."""

    organization_id: StrictInt
    mail_connection_id: StrictStr = Field(min_length=36, max_length=36)
    credential_generation: StrictInt
    binding_epoch: StrictInt
    authority_version: StrictInt
    flag: Literal[
        "shadow_write", "shadow_read_compare", "pilot_write", "primary_read", "actions"
    ]
    enabled: StrictBool
    approval: Literal["CONFIRM"]

    @model_validator(mode="after")
    def exact_positive_pins(self):
        if any(type(value) is not int or value <= 0 for value in (
            self.organization_id,
            self.credential_generation,
            self.binding_epoch,
            self.authority_version,
        )):
            raise ValueError("invalid_rollout_transition")
        try:
            UUID(self.mail_connection_id)
        except (TypeError, ValueError, AttributeError):
            raise ValueError("invalid_rollout_transition") from None
        return self


class MailboxRolloutResult(StrictDTO):
    flag: Literal[
        "shadow_write", "shadow_read_compare", "pilot_write", "primary_read", "actions"
    ]
    enabled: StrictBool
    record_version: StrictInt
    shadow_write: StrictBool
    shadow_read_compare: StrictBool
    pilot_write: StrictBool
    primary_read: StrictBool
    actions: StrictBool
