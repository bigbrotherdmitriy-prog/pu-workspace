"""Narrow CONFIRM DTOs shared by the three pilot streams; not HTTP endpoints."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Literal

from pydantic import StrictBool, StrictInt, StrictStr, model_validator

from app.core.v54_refs import ObjectRef, StrictDTO, VersionPin, require_same_tenant


def canonical_json(value) -> str:
    """pu-action-c14n-v1; no float coercion or Unicode normalization."""
    def check(item):
        if type(item) is dict:
            if any(type(k) is not str or not k.isascii() for k in item):
                raise ValueError("noncanonical key")
            for child in item.values():
                check(child)
        elif type(item) is list:
            for child in item:
                check(child)
        elif type(item) is str:
            item.encode("utf-8", errors="strict")
        elif type(item) is int:
            if abs(item) > 2**53 - 1:
                raise ValueError("noncanonical integer")
        elif item is not None and type(item) is not bool:
            raise ValueError("noncanonical scalar")
    check(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def canonical_hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_envelope_json(raw: str) -> "ActionEnvelope":
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    canonical_json(value)
    return ActionEnvelope.model_validate(value)


class CreateTaskPayload(StrictDTO):
    title: StrictStr
    due_date: StrictStr
    timezone: StrictStr
    assignee_ref: ObjectRef
    contract_ref: ObjectRef
    publish_external: StrictBool
    create_obligation: StrictBool

    @model_validator(mode="after")
    def validate_payload(self):
        if (not self.title or len(self.title) > 500 or self.publish_external or self.create_obligation
                or self.assignee_ref.type != "user" or self.contract_ref.type != "contract"
                or self.timezone not in {"Europe/Moscow", "UTC"}
                or date.fromisoformat(self.due_date).isoformat() != self.due_date):
            raise ValueError("unsupported synthetic task payload")
        return self


class CancelTaskPayload(StrictDTO):
    expected_status: Literal["assigned"]
    reason: StrictStr


class ActionEnvelope(StrictDTO):
    schema_version: Literal["v54.integration.1"]
    canonicalization: Literal["pu-action-c14n-v1"]
    action_ref: ObjectRef
    revision: StrictInt
    action_type: Literal["task.internal.create", "task.internal.cancel"]
    action_type_version: StrictInt
    executor_version: Literal["task-db-v1"]
    stage: Literal["PROPOSE"]
    project_ref: ObjectRef
    requested_by: ObjectRef
    target: VersionPin
    source_versions: tuple[VersionPin, ...]
    evidence: tuple[VersionPin, ...]
    claim: VersionPin
    relations: tuple[VersionPin, ...]
    expected_context_version: StrictInt
    connection_ref: ObjectRef
    policy: VersionPin
    policy_sha256: StrictStr
    risk: Literal["LOW"]
    autonomy: Literal["CONFIRM"]
    reversal: Literal["COMPENSATABLE"]
    effects: tuple[StrictStr, ...]
    payload: CreateTaskPayload | CancelTaskPayload
    idempotency_key: StrictStr
    compensates_action_ref: ObjectRef | None

    @model_validator(mode="after")
    def validate_envelope(self):
        refs = [self.project_ref, self.requested_by, self.target.ref, self.connection_ref,
                self.policy.ref, self.claim.ref]
        for pins, expected in ((self.source_versions, "source_version"), (self.evidence, "evidence"),
                               (self.relations, "context_relation")):
            if not pins or any(p.ref.type != expected or p.version_kind != "revision" for p in pins):
                raise ValueError("invalid required pins")
            keys = [canonical_json(p.model_dump(mode="json")) for p in pins]
            if keys != sorted(set(keys)):
                raise ValueError("pins must be unique and canonically sorted")
            refs.extend(p.ref for p in pins)
        if self.compensates_action_ref:
            refs.append(self.compensates_action_ref)
        if (self.action_ref.type != "action" or self.project_ref.type != "project"
                or self.requested_by.type != "user" or self.connection_ref.type != "connection_identity"
                or self.policy.ref.type != "policy" or self.claim.ref.type != "deadline_claim"
                or self.policy.version_kind != "revision" or self.claim.version_kind != "revision"
                or self.action_type_version != 1 or self.revision <= 0 or self.expected_context_version <= 0
                or not re.fullmatch("[0-9a-f]{64}", self.policy_sha256)
                or not self.idempotency_key or len(self.idempotency_key) > 200):
            raise ValueError("invalid sealed envelope")
        if self.action_type == "task.internal.create":
            if (not isinstance(self.payload, CreateTaskPayload) or self.compensates_action_ref is not None
                    or self.target.ref != self.project_ref
                    or self.effects != ("internal_task.create", "task_history.append")):
                raise ValueError("invalid create effect")
            refs.extend([self.payload.assignee_ref, self.payload.contract_ref])
        elif (not isinstance(self.payload, CancelTaskPayload) or self.target.ref.type != "task"
              or not self.compensates_action_ref or self.compensates_action_ref.type != "action"
              or self.compensates_action_ref == self.action_ref
              or self.effects != ("internal_task.cancel", "task_history.append")):
            raise ValueError("invalid cancel effect")
        require_same_tenant(self.action_ref.tenant_id, *refs)
        canonical_json(self.model_dump(mode="json"))
        return self


class DeadlineClaimInput(StrictDTO):
    anchor: ObjectRef
    revision: StrictInt
    message: ObjectRef
    due_date: StrictStr
    timezone: Literal["Europe/Moscow", "UTC"]
    evidence: tuple[VersionPin, ...]
    # Human review is a separate command; extraction cannot set verification.
    @model_validator(mode="after")
    def validate_claim(self):
        require_same_tenant(self.anchor.tenant_id, self.message, *(p.ref for p in self.evidence))
        if (self.anchor.type != "deadline_claim" or self.message.type != "message" or self.revision <= 0
                or not self.evidence or any(p.ref.type != "evidence" or p.version_kind != "revision" for p in self.evidence)
                or date.fromisoformat(self.due_date).isoformat() != self.due_date):
            raise ValueError("invalid deadline claim")
        return self
