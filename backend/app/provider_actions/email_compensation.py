"""Fail-closed read/propose flow for corrective email follow-ups.

The module only creates a protected ResponseDraft plus a FROZEN action in the
existing provider Action Ledger. It has no approval, outbox, worker, adapter,
credential, network, or provider-send capability.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select

from app.models.project import Project
from app.models.response_draft import ResponseDraft
from app.models.v54_provider_action import ProviderAction, ProviderOutcomeObservation
from app.provider_actions.contracts import ActionEnvelope, ProviderActionError
from app.provider_actions.runtime import ProviderActionRuntime


DIRECT_UNDO_MESSAGE = "Отменить отправку нельзя"
SOURCE_UNAVAILABLE = "source_unavailable"
SOURCE_STALE = "source_stale"
SOURCE_AMBIGUOUS = "source_ambiguous"
SOURCE_SEND_COMMAND = "response-draft:{draft_id}:send"
CORRECTIVE_COMMAND = re.compile(r"response-draft:(\d+):corrective:[0-9a-f]{32}")


class EmailCompensationError(RuntimeError):
    ALLOWED = {SOURCE_UNAVAILABLE, SOURCE_STALE, SOURCE_AMBIGUOUS}

    def __init__(self, code: str):
        self.code = code if code in self.ALLOWED else SOURCE_UNAVAILABLE
        super().__init__(self.code)


def source_send_command_key(draft_id: int) -> str:
    return SOURCE_SEND_COMMAND.format(draft_id=draft_id)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(encoded.encode()).hexdigest()


def unavailable_email_compensation(reason: str = SOURCE_UNAVAILABLE) -> dict:
    return {
        "direct_undo_possible": False,
        "message": DIRECT_UNDO_MESSAGE,
        "status": "UNAVAILABLE",
        "can_propose": False,
        "unavailable_reason": reason,
    }


def _latest_observation(db, source: ProviderAction):
    return db.scalar(select(ProviderOutcomeObservation).where(
        ProviderOutcomeObservation.organization_id == source.organization_id,
        ProviderOutcomeObservation.action_id == source.action_id,
        ProviderOutcomeObservation.revision == source.revision,
    ).order_by(ProviderOutcomeObservation.sequence.desc()).limit(1))


def _source_etag(source: ProviderAction, observation: ProviderOutcomeObservation) -> str:
    return _digest({
        "action_id": source.action_id,
        "revision": source.revision,
        "envelope_hash": source.envelope_hash,
        "state": source.state,
        "observation_id": observation.id,
        "observation_sequence": observation.sequence,
        "outcome": observation.outcome,
        "mailbox_key": source.mailbox_key,
        "project_id": source.project_id,
        "evidence_pins": source.evidence_pins,
    })


def _source_for_draft(db, draft: ResponseDraft):
    project = db.get(Project, draft.project_id)
    if project is None or draft.status != "sent" or not draft.sent_at or not draft.sent_external_id:
        return None, None, SOURCE_UNAVAILABLE
    rows = list(db.scalars(select(ProviderAction).where(
        ProviderAction.organization_id == project.organization_id,
        ProviderAction.project_id == draft.project_id,
        ProviderAction.command_key == source_send_command_key(draft.id),
    )))
    if len(rows) != 1:
        return None, None, SOURCE_AMBIGUOUS if rows else SOURCE_UNAVAILABLE
    source = rows[0]
    revisions = list(db.scalars(select(ProviderAction.revision).where(
        ProviderAction.organization_id == source.organization_id,
        ProviderAction.action_id == source.action_id,
    )))
    observation = _latest_observation(db, source)
    exact_receipt = observation and (
        observation.mailbox_key == source.mailbox_key
        and observation.command_key == source.command_key
        and observation.idempotency_key == source.idempotency_key
        and observation.payload_hash == source.payload_hash
        and observation.envelope_hash == source.envelope_hash
    )
    eligible = (
        len(revisions) == 1
        and source.provider == "synthetic"
        and source.synthetic_only is True
        and source.mode == "CONFIRM"
        and source.action_kind == "synthetic.effect.send"
        and source.reversibility == "IRREVERSIBLE"
        and source.state == "APPLIED"
        and isinstance(source.evidence_pins, list)
        and bool(source.evidence_pins)
        and observation is not None
        and observation.outcome == "APPLIED"
        and exact_receipt
    )
    return (source, observation, None) if eligible else (None, None, SOURCE_STALE)


def _corrective_payload_hash(draft: ResponseDraft, source: ProviderAction,
                             observation: ProviderOutcomeObservation) -> str:
    return _digest({
        "kind": "corrective-follow-up-draft",
        "protected_draft_id": draft.id,
        "subject_hash": sha256(draft.subject.encode()).hexdigest(),
        "body_hash": sha256(draft.body.encode()).hexdigest(),
        "recipient_hash": sha256((draft.recipient_to or "").encode()).hexdigest(),
        "source_action_id": source.action_id,
        "source_revision": source.revision,
        "source_observation_id": observation.id,
        "source_observation_sequence": observation.sequence,
        "source_outcome": observation.outcome,
        "source_envelope_hash": source.envelope_hash,
        "mailbox_key": source.mailbox_key,
        "project_id": source.project_id,
        "evidence_pins": source.evidence_pins,
    })


def _existing_proposal(db, source: ProviderAction,
                       observation: ProviderOutcomeObservation) -> dict | None:
    rows = list(db.scalars(select(ProviderAction).where(
        ProviderAction.organization_id == source.organization_id,
        ProviderAction.project_id == source.project_id,
        ProviderAction.mailbox_key == source.mailbox_key,
        ProviderAction.relation_kind == "CORRECTIVE",
        ProviderAction.relation_action_id == source.action_id,
        ProviderAction.action_kind == "synthetic.effect.corrective",
    )))
    if len(rows) > 1:
        raise EmailCompensationError(SOURCE_AMBIGUOUS)
    if not rows:
        return None
    action = rows[0]
    match = CORRECTIVE_COMMAND.fullmatch(action.command_key)
    draft = db.get(ResponseDraft, int(match.group(1))) if match else None
    expected_etag = _source_etag(source, observation)
    if (draft is None or draft.project_id != source.project_id
            or draft.status != "draft" or action.state != "FROZEN"
            or action.evidence_pins != source.evidence_pins
            or action.mode != "CONFIRM" or action.provider != "synthetic"
            or action.synthetic_only is not True
            or action.reversibility != "IRREVERSIBLE"
            or action.context_revision != source.context_revision
            or action.authority_epoch != source.authority_epoch
            or action.capability_version != source.capability_version
            or action.credential_generation != source.credential_generation
            or draft.source_file_id != f"provider-action:{source.action_id}:{source.revision}"
            or draft.source_excerpt != "APPLIED"
            or draft.source_excerpt_hash != expected_etag
            or action.payload_hash != _corrective_payload_hash(draft, source, observation)):
        raise EmailCompensationError(SOURCE_STALE)
    return {
        "action_id": action.action_id,
        "revision": action.revision,
        "state": "PROPOSED",
        "ledger_state": action.state,
        "approval_mode": "CONFIRM",
        "draft_id": draft.id,
    }


def describe_email_compensation(db, draft: ResponseDraft) -> dict:
    source, observation, reason = _source_for_draft(db, draft)
    if reason:
        return unavailable_email_compensation(reason)
    try:
        proposal = _existing_proposal(db, source, observation)
    except EmailCompensationError as exc:
        return unavailable_email_compensation(exc.code)
    result = {
        "direct_undo_possible": False,
        "message": DIRECT_UNDO_MESSAGE,
        "status": "PROPOSED" if proposal else "AVAILABLE",
        "can_propose": proposal is None,
        "source_action_id": source.action_id,
        "source_revision": source.revision,
        "source_etag": _source_etag(source, observation),
        "approval_mode": "CONFIRM",
    }
    if proposal:
        result["proposal"] = proposal
    return result


def propose_email_compensation(db, draft: ResponseDraft, *, expected_source_etag: str,
                               actor_id: str, correlation_id: str,
                               clock=lambda: datetime.now(timezone.utc)) -> dict:
    current = describe_email_compensation(db, draft)
    if current["status"] == "UNAVAILABLE":
        raise EmailCompensationError(current["unavailable_reason"])
    if current["source_etag"] != expected_source_etag:
        raise EmailCompensationError(SOURCE_STALE)
    if current.get("proposal"):
        return current

    source, observation, reason = _source_for_draft(db, draft)
    if reason:
        raise EmailCompensationError(reason)
    nonce = uuid4().hex
    action_id = f"corrective-{uuid4().hex}"
    protected_draft = ResponseDraft(
        project_id=draft.project_id,
        reviewer_user_id=int(actor_id),
        message_id=draft.message_id,
        subject="Корректировка к отправленному письму",
        body="Уточните, что именно нужно исправить в ранее отправленном письме.",
        recipient_to=draft.recipient_to,
        status="draft",
        source_file_id=f"provider-action:{source.action_id}:{source.revision}",
        source_file_name="corrective-follow-up",
        source_excerpt="APPLIED",
        source_excerpt_hash=_source_etag(source, observation),
        confidence=1.0,
    )
    db.add(protected_draft)
    db.flush()

    payload_hash = _corrective_payload_hash(protected_draft, source, observation)
    envelope = ActionEnvelope(
        action_id=action_id,
        revision=1,
        organization_id=source.organization_id,
        project_id=source.project_id,
        mailbox_key=source.mailbox_key,
        provider="synthetic",
        mode="CONFIRM",
        synthetic_only=True,
        action_kind="synthetic.effect.corrective",
        reversibility="IRREVERSIBLE",
        payload_hash=payload_hash,
        command_key=f"response-draft:{protected_draft.id}:corrective:{nonce}",
        idempotency_key=f"corrective-follow-up:{uuid4().hex}",
        context_revision=source.context_revision,
        evidence_pins=tuple(source.evidence_pins),
        authority_epoch=source.authority_epoch,
        capability_version=source.capability_version,
        credential_generation=source.credential_generation,
        relation_kind="CORRECTIVE",
        relation_action_id=source.action_id,
    )
    try:
        ProviderActionRuntime.freeze_in_session(
            db, envelope, actor_id=f"user:{actor_id}",
            correlation_id=correlation_id, clock=clock,
        )
    except ProviderActionError as exc:
        raise EmailCompensationError(SOURCE_STALE) from exc
    ProviderActionRuntime._audit(
        db, "email_correction_proposed", envelope.action_id, envelope.revision,
        f"user:{actor_id}", correlation_id,
        source_action_id=source.action_id,
        source_revision=source.revision,
        source_observation_id=observation.id,
        source_observation_sequence=observation.sequence,
        source_outcome=observation.outcome,
        source_envelope_hash=source.envelope_hash,
        mailbox_key=source.mailbox_key,
        project_id=source.project_id,
        evidence_pin_count=len(source.evidence_pins),
        payload_hash=envelope.payload_hash,
        protected_draft_id=protected_draft.id,
        approval_mode="CONFIRM",
    )
    db.flush()
    return describe_email_compensation(db, draft)
