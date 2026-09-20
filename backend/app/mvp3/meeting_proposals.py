"""Proposal-only meeting minutes flow with exact source authority."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app.document_extraction import extract_for_text, match_assignee_hint
from app.models.audit_log import AuditLog
from app.models.governance import Decision, Risk
from app.models.management import ManagementHistory, Meeting, MeetingProposal, MeetingSourceBinding
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.user import User
from app.source_evidence.meeting_authority import MeetingSourceDenied, require_current_local_upload_source


class MeetingProposalDenied(RuntimeError):
    pass


class MeetingProposalConflict(RuntimeError):
    pass


def _canonical(payload: dict) -> str:
    return json.dumps(jsonable_encoder(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _uuid(value: str) -> str:
    try:
        parsed = str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        raise MeetingProposalDenied("invalid_command") from None
    if parsed != value:
        raise MeetingProposalDenied("invalid_command")
    return value


def _history(db, *, organization_id: int, project_id: int, entity_type: str,
             entity_id: int, record_version: int, action: str, actor_id: int,
             old_values: dict, new_values: dict, evidence: dict) -> None:
    db.add(ManagementHistory(
        organization_id=organization_id, project_id=project_id,
        entity_type=entity_type, entity_id=entity_id, record_version=record_version,
        action=action, actor_user_id=actor_id, old_values=jsonable_encoder(old_values),
        new_values=jsonable_encoder(new_values), evidence=evidence,
    ))


def serialize_proposal(row: MeetingProposal) -> dict:
    return {
        "id": row.id, "record_version": row.record_version,
        "project_id": row.project_id, "meeting_id": row.meeting_id,
        "binding_id": row.binding_id, "proposal_type": row.proposal_type,
        "payload": row.payload, "status": row.status,
        "target_entity_type": row.target_entity_type, "target_entity_id": row.target_entity_id,
    }


def _serialize_binding(binding: MeetingSourceBinding, proposals: list[MeetingProposal]) -> dict:
    return {
        "meeting_id": binding.meeting_id,
        "meeting_record_version": binding.meeting_record_version,
        "binding_id": binding.id, "source_id": binding.source_id,
        "source_version_id": binding.source_version_id, "evidence_id": binding.evidence_id,
        "materialization_id": binding.materialization_id,
        "proposal_count": len(proposals),
        "proposals": [serialize_proposal(row) for row in proposals],
        "external_actions_created": False,
    }


def _proposal_payloads(db, meeting: Meeting) -> list[tuple[str, dict]]:
    extraction = extract_for_text(db, meeting.project_id, meeting.minutes, f"Протокол: {meeting.title}")
    payloads: list[tuple[str, dict]] = []
    for item in extraction.obligations:
        payloads.append(("task", {
            "title": item.title[:500], "excerpt": item.excerpt,
            "due_date": item.due_date.isoformat() if item.due_date else None,
            "priority": item.priority, "confidence": item.confidence,
            "review_reasons": list(item.review_reasons),
            "amount": str(item.amount) if item.amount is not None else None,
            "amount_currency": item.amount_currency,
            "amount_evidence_quote": item.amount_evidence_quote,
            "due_date_evidence_quote": item.due_date_evidence_quote,
            "assignee_hint": item.assignee_hint,
            "assignee_evidence_quote": item.assignee_evidence_quote,
            "extraction_method": item.extraction_method,
        }))
    for item in extraction.risks:
        payloads.append(("risk", {
            "title": item.title[:500], "excerpt": item.evidence_quote,
            "kind": item.kind, "criticality": item.criticality, "confidence": item.confidence,
        }))
    for item in extraction.decisions:
        payloads.append(("decision", {
            "question": item.question, "excerpt": item.evidence_quote, "confidence": item.confidence,
        }))
    return payloads


def bind_current_source(db, *, meeting_id: int, actor_user_id: int,
                        expected_record_version: int, command_id: str,
                        source_id: str, source_version_id: str,
                        evidence_id: str, materialization_id: str) -> dict:
    command_id = _uuid(command_id)
    meeting = db.scalar(select(Meeting).where(Meeting.id == meeting_id).with_for_update())
    if meeting is None:
        raise MeetingProposalDenied("resource_unavailable")
    command = {
        "meeting_id": meeting_id, "expected_record_version": expected_record_version,
        "source_id": source_id, "source_version_id": source_version_id,
        "evidence_id": evidence_id, "materialization_id": materialization_id,
        "actor_user_id": actor_user_id,
    }
    command_hash = _hash(command)
    prior = db.scalar(select(MeetingSourceBinding).where(
        MeetingSourceBinding.meeting_id == meeting_id,
        MeetingSourceBinding.command_id == command_id,
    ))
    if prior is not None:
        if prior.command_hash != command_hash or meeting.record_version != prior.meeting_record_version:
            raise MeetingProposalConflict("command_conflict")
        proposals = list(db.scalars(select(MeetingProposal).where(
            MeetingProposal.binding_id == prior.id,
        ).order_by(MeetingProposal.id)))
        return _serialize_binding(prior, proposals)
    if meeting.record_version != expected_record_version:
        raise MeetingProposalConflict("record_version_conflict")
    if meeting.status != "completed" or not (meeting.minutes or "").strip():
        raise MeetingProposalDenied("meeting_not_completed")
    try:
        authority = require_current_local_upload_source(
            db, actor_user_id=actor_user_id, project_id=meeting.project_id,
            source_id=source_id, source_version_id=source_version_id,
            evidence_id=evidence_id, materialization_id=materialization_id,
        )
    except MeetingSourceDenied as exc:
        raise MeetingProposalDenied(str(exc)) from None
    meeting.record_version += 1
    binding = MeetingSourceBinding(
        id=str(uuid4()), organization_id=authority.organization_id,
        project_id=meeting.project_id, meeting_id=meeting.id,
        meeting_record_version=meeting.record_version,
        source_id=source_id, source_version_id=source_version_id,
        evidence_id=evidence_id, materialization_id=materialization_id,
        command_id=command_id, command_hash=command_hash, bound_by_user_id=actor_user_id,
    )
    db.add(binding); db.flush()
    proposals = []
    for proposal_type, payload in _proposal_payloads(db, meeting):
        fingerprint = _hash({"binding_id": binding.id, "type": proposal_type, "payload": payload})
        row = MeetingProposal(
            organization_id=authority.organization_id, project_id=meeting.project_id,
            meeting_id=meeting.id, binding_id=binding.id, proposal_type=proposal_type,
            payload=payload, fingerprint=fingerprint, created_by_user_id=actor_user_id,
        )
        db.add(row); proposals.append(row)
    db.flush()
    _history(
        db, organization_id=authority.organization_id, project_id=meeting.project_id,
        entity_type="meeting", entity_id=meeting.id, record_version=meeting.record_version,
        action="source_bound_proposals_created", actor_id=actor_user_id,
        old_values={"record_version": expected_record_version},
        new_values={"record_version": meeting.record_version, "proposal_count": len(proposals)},
        evidence={"source_id": source_id, "source_version_id": source_version_id,
                  "evidence_id": evidence_id, "materialization_id": materialization_id},
    )
    db.add(AuditLog(action="meeting_source_bound", entity_type="meeting", entity_id=meeting.id,
                    details=f"binding_id={binding.id};proposals={len(proposals)};user={actor_user_id}"))
    return _serialize_binding(binding, proposals)


def _default_assignee(db, project_id: int) -> User | None:
    rows = db.execute(select(User, ProjectMember.role)
                      .join(ProjectMember, ProjectMember.user_id == User.id)
                      .where(ProjectMember.project_id == project_id)).all()
    order = {"owner": 0, "manager": 1, "editor": 2, "member": 3, "viewer": 4}
    rows.sort(key=lambda row: (order.get(row.role, 9), row.User.id))
    return rows[0].User if rows else db.scalar(select(User).where(User.is_admin.is_(True)).order_by(User.id))


def _create_target(db, proposal: MeetingProposal, actor_user_id: int):
    payload = proposal.payload
    source_hash = hashlib.sha256(f"meeting-proposal:{proposal.id}".encode()).hexdigest()
    source_id = f"meeting:{proposal.meeting_id}:proposal:{proposal.id}"
    source_name = f"Протокол встречи #{proposal.meeting_id}"
    if proposal.proposal_type == "task":
        assignee = match_assignee_hint(db, proposal.project_id, payload.get("assignee_hint")) or _default_assignee(db, proposal.project_id)
        if assignee is None:
            raise MeetingProposalDenied("assignee_unavailable")
        return Task(
            project_id=proposal.project_id, assignee_user_id=assignee.id,
            created_by_user_id=actor_user_id, title=payload["title"],
            description="Создано после явного подтверждения предложения из протокола.",
            status="assigned", priority=payload.get("priority") or "normal",
            due_date=date.fromisoformat(payload["due_date"]) if payload.get("due_date") else None,
            source_type="meeting", source_file_id=source_id, source_file_name=source_name,
            source_excerpt=payload["excerpt"], source_excerpt_hash=source_hash,
            confidence=float(payload["confidence"]), needs_review=True,
            amount=Decimal(payload["amount"]) if payload.get("amount") else None,
            amount_currency=payload.get("amount_currency"),
            amount_evidence_quote=payload.get("amount_evidence_quote"),
            due_date_evidence_quote=payload.get("due_date_evidence_quote"),
            assignee_hint=payload.get("assignee_hint"),
            assignee_evidence_quote=payload.get("assignee_evidence_quote"),
            extraction_method=payload.get("extraction_method") or "regex",
        )
    owner = _default_assignee(db, proposal.project_id)
    if owner is None:
        raise MeetingProposalDenied("owner_unavailable")
    if proposal.proposal_type == "risk":
        return Risk(
            project_id=proposal.project_id, owner_user_id=owner.id, kind=payload["kind"],
            title=payload["title"], description=payload["excerpt"],
            criticality=payload["criticality"], status="needs_confirmation",
            source_type="meeting", source_id=source_id, source_name=source_name,
            source_excerpt=payload["excerpt"], source_hash=source_hash,
            confidence=float(payload["confidence"]),
        )
    return Decision(
        project_id=proposal.project_id, initiator_user_id=actor_user_id,
        question=payload["question"], status="needs_confirmation",
        source_type="meeting", source_id=source_id, source_name=source_name,
        source_excerpt=payload["excerpt"], source_hash=source_hash,
        confidence=float(payload["confidence"]),
    )


def confirm_proposal(db, *, proposal_id: int, actor_user_id: int,
                     expected_record_version: int, command_id: str) -> dict:
    command_id = _uuid(command_id)
    proposal = db.scalar(select(MeetingProposal).where(MeetingProposal.id == proposal_id).with_for_update())
    if proposal is None:
        raise MeetingProposalDenied("resource_unavailable")
    command_hash = _hash({"proposal_id": proposal_id, "actor_user_id": actor_user_id,
                          "expected_record_version": expected_record_version})
    if proposal.status == "confirmed":
        if proposal.confirmation_command_id != command_id or proposal.confirmation_hash != command_hash:
            raise MeetingProposalConflict("already_confirmed")
        return serialize_proposal(proposal)
    if proposal.record_version != expected_record_version:
        raise MeetingProposalConflict("record_version_conflict")
    binding = db.scalar(select(MeetingSourceBinding).where(MeetingSourceBinding.id == proposal.binding_id).with_for_update())
    meeting = db.scalar(select(Meeting).where(Meeting.id == proposal.meeting_id).with_for_update())
    if binding is None or meeting is None or meeting.record_version != binding.meeting_record_version:
        raise MeetingProposalDenied("stale_meeting_source")
    try:
        authority = require_current_local_upload_source(
            db, actor_user_id=actor_user_id, project_id=proposal.project_id,
            source_id=binding.source_id, source_version_id=binding.source_version_id,
            evidence_id=binding.evidence_id, materialization_id=binding.materialization_id,
        )
    except MeetingSourceDenied:
        raise MeetingProposalDenied("resource_unavailable") from None
    target = _create_target(db, proposal, actor_user_id)
    db.add(target); db.flush()
    proposal.status = "confirmed"; proposal.record_version += 1
    proposal.confirmation_command_id = command_id; proposal.confirmation_hash = command_hash
    proposal.target_entity_type = proposal.proposal_type; proposal.target_entity_id = target.id
    proposal.confirmed_by_user_id = actor_user_id; proposal.confirmed_at = datetime.now(timezone.utc)
    _history(
        db, organization_id=authority.organization_id, project_id=proposal.project_id,
        entity_type="meeting_proposal", entity_id=proposal.id,
        record_version=proposal.record_version, action="confirmed", actor_id=actor_user_id,
        old_values={"status": "proposed"},
        new_values={"status": "confirmed", "target_entity_type": proposal.proposal_type,
                    "target_entity_id": target.id},
        evidence={"binding_id": binding.id, "source_id": binding.source_id,
                  "source_version_id": binding.source_version_id, "evidence_id": binding.evidence_id},
    )
    db.add(AuditLog(action="meeting_proposal_confirmed", entity_type="meeting_proposal",
                    entity_id=proposal.id, details=f"target={proposal.proposal_type}:{target.id};user={actor_user_id}"))
    return serialize_proposal(proposal)
