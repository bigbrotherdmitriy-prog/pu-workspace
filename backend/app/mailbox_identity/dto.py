from typing import Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from app.core.v54_refs import StrictDTO


class ReconciliationCommand(StrictDTO):
    decision_key: StrictStr = Field(min_length=1, max_length=200)
    message_id: StrictInt
    expected_message_origin_version: StrictInt
    expected_current_origin_version: StrictInt
    identity_id: StrictStr
    mail_connection_id: StrictStr
    binding_epoch: StrictInt
    credential_generation: StrictInt
    source_reference_id: StrictStr
    source_version_id: StrictStr
    evidence_refs: tuple[StrictStr, ...] = ()
    reason_code: StrictStr = Field(min_length=1, max_length=50)
    correlation_id: StrictStr = Field(min_length=1, max_length=100)
    actor_user_id: StrictInt
    authority_version: StrictInt
    outcome: Literal["CONFIRM", "REJECT", "LEAVE_UNRESOLVED"]

    @model_validator(mode="after")
    def positive_versions(self):
        values = (self.message_id, self.expected_message_origin_version,
                  self.expected_current_origin_version, self.binding_epoch,
                  self.credential_generation, self.actor_user_id, self.authority_version)
        if any(value <= 0 for value in values) or len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("invalid_reconciliation_command")
        return self


class ReconciliationResult(StrictDTO):
    decision_id: StrictStr
    binding_id: StrictStr
    origin_version: StrictInt
    state: Literal["unresolved", "confirmed", "rejected"]
    idempotent_replay: bool = False
