"""Shared v54.integration.1 wire types. Parsing is NOT authorization."""
from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, model_validator

INT_TYPES = frozenset({"organization", "user", "project", "contract", "message",
                       "task", "response_draft", "background_job"})
UUID_TYPES = frozenset({"connection_identity", "mail_connection", "source", "source_version",
                        "evidence", "deadline_claim", "context_relation", "action", "policy",
                        "approval", "receipt", "ledger_event", "mailbox_origin_decision",
                        "mailbox_origin_binding", "mailbox_credential_generation",
                        "materialization"})
REVISION_TYPES = frozenset({"source_version", "evidence", "deadline_claim", "context_relation",
                            "action", "policy"})
RECORD_TYPES = (INT_TYPES | UUID_TYPES) - {"source_version", "evidence", "action", "policy",
                                         "approval", "receipt", "ledger_event"}


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaggedId(StrictDTO):
    kind: Literal["int", "uuid"]
    value: StrictStr

    @model_validator(mode="after")
    def validate_identity(self):
        if self.kind == "int":
            if not re.fullmatch(r"[1-9][0-9]{0,18}", self.value) or int(self.value) > 2**63 - 1:
                raise ValueError("invalid integer identity")
        elif str(UUID(self.value)) != self.value:
            raise ValueError("UUID must be canonical lowercase")
        return self


class ObjectRef(StrictDTO):
    namespace: Literal["pu"]
    type: StrictStr
    tenant_id: TaggedId
    id: TaggedId

    @model_validator(mode="after")
    def validate_registry(self):
        if self.type not in INT_TYPES | UUID_TYPES:
            raise ValueError("unregistered object type")
        if self.tenant_id.kind != "int":
            raise ValueError("tenant must use existing integer PK")
        if self.id.kind != ("int" if self.type in INT_TYPES else "uuid"):
            raise ValueError("identity kind does not match registry")
        if self.type == "organization" and self.id != self.tenant_id:
            raise ValueError("organization scope mismatch")
        return self


class VersionPin(StrictDTO):
    ref: ObjectRef
    version_kind: Literal["revision", "record_version"]
    value: StrictInt

    @model_validator(mode="after")
    def validate_version(self):
        permitted = REVISION_TYPES if self.version_kind == "revision" else RECORD_TYPES
        if self.ref.type not in permitted or not 0 < self.value <= 2**53 - 1:
            raise ValueError("invalid version pin")
        if self.ref.type in {"source_version", "evidence"} and self.value != 1:
            raise ValueError("immutable observation requires revision 1")
        return self


def require_same_tenant(tenant: TaggedId, *refs: ObjectRef) -> None:
    if tenant.kind != "int" or any(ref.tenant_id != tenant for ref in refs):
        raise ValueError("resource_unavailable")
