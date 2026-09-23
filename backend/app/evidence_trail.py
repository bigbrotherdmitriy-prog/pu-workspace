"""Project-scoped, read-only projection of existing evidence and action records.

This module deliberately owns no persistence.  It projects the current domain,
v5.4 trust and provider tables without turning the projection into a source of
authority or a second audit journal.  Free-form ``AuditLog.details`` and source
body/quotes are never exposed.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.automation_rule import AutomationRule, AutomationRun
from app.models.document import Document
from app.models.execution_finance import (
    AcceptanceAct,
    BudgetLine,
    CashFlowEntry,
    InvoiceExtractionProposal,
    PaymentEvent,
    ProcurementItem,
    ScheduleBaseline,
    ScheduleItem,
)
from app.models.governance import Decision, Risk
from app.models.management import Meeting, MeetingProposal, MeetingSourceBinding, Obligation
from app.models.organization_contract import Contract, ContractVersion
from app.models.organizer import OrganizerProposal, OrganizerSession
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.task_completion_suggestion import TaskCompletionSuggestion
from app.models.v54_pilot import (
    ActionApproval,
    ActionReceipt,
    ActionRevision,
    AuditExtension,
    ContextRelation,
    DeadlineClaim,
    Evidence,
    PilotAction,
    SourceReference,
    SourceVersion,
)
from app.models.v54_provider_action import (
    ProviderAction,
    ProviderActionApproval,
    ProviderOutcomeObservation,
)


class TrailRef(BaseModel):
    type: str
    id: str
    revision: int | None = None


class TrailActor(BaseModel):
    kind: str
    id: str


class EvidenceTrailItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    occurred_at: datetime
    phase: str
    event: str
    subject: TrailRef
    linkage: str
    authorization: str = "UNKNOWN"
    outcome: str | None = None
    actor: TrailActor | None = None
    correlation_id: str | None = None
    action_ref: TrailRef | None = None
    approval_ref: TrailRef | None = None
    receipt_ref: TrailRef | None = None
    job_ref: TrailRef | None = None
    source_refs: list[TrailRef] = Field(default_factory=list)
    evidence_refs: list[TrailRef] = Field(default_factory=list)
    relation_refs: list[TrailRef] = Field(default_factory=list)
    ai: dict[str, Any] | None = None


class EvidenceTrailPage(BaseModel):
    project_id: int
    items: list[EvidenceTrailItem]
    next_cursor: str | None


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _ref(value: Any, *, default_type: str | None = None) -> TrailRef | None:
    """Project a v54 ObjectRef/VersionPin without trusting arbitrary JSON."""
    if not isinstance(value, dict):
        return None
    pin = value if isinstance(value.get("ref"), dict) else None
    raw = pin["ref"] if pin else value
    object_type = raw.get("type") or default_type
    raw_id = raw.get("id")
    object_id = raw_id.get("value") if isinstance(raw_id, dict) else raw_id
    revision = pin.get("value") if pin else value.get("revision")
    if not object_type or object_id in (None, ""):
        return None
    return TrailRef(type=str(object_type), id=str(object_id),
                    revision=revision if type(revision) is int else None)


def _refs(values: Any, *, default_type: str | None = None) -> list[TrailRef]:
    if not isinstance(values, (list, tuple)):
        return []
    result: list[TrailRef] = []
    seen: set[tuple[str, str, int | None]] = set()
    for value in values:
        item = _ref(value, default_type=default_type)
        if item is None:
            continue
        key = (item.type, item.id, item.revision)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _phase(event: str) -> str:
    value = event.casefold()
    if any(token in value for token in ("correct", "reverse", "rollback", "cancel")):
        return "CORRECTION"
    if any(token in value for token in ("outcome", "succeed", "failed", "reconcil", "synced")):
        return "OUTCOME"
    if any(token in value for token in ("dispatch", "queued", "send", "execut", "applied")):
        return "EXECUTION"
    if any(token in value for token in ("policy", "authority", "authorized")):
        return "ACTION_AUTHORIZATION"
    if any(token in value for token in ("confirm", "approve", "review")):
        return "CONTEXT_CONFIRMATION"
    if any(token in value for token in ("proposal", "proposed", "frozen", "draft")):
        return "PROPOSAL"
    if any(token in value for token in ("analysis", "analyz", "extract", "processed", "ai_")):
        return "ANALYSIS"
    if any(token in value for token in ("source", "observed", "materialization", "bound")):
        return "SOURCE"
    return "ACTIVITY"


def _outcome(event: str) -> str | None:
    value = event.casefold()
    if "unknown" in value:
        return "UNKNOWN"
    if any(token in value for token in ("failed", "blocked", "rejected")):
        return "NOT_APPLIED"
    if any(token in value for token in ("succeeded", "confirmed", "approved", "applied", "sent")):
        return "APPLIED"
    return None


def _cursor(item: EvidenceTrailItem) -> str:
    raw = json.dumps(
        {"at": item.occurred_at.isoformat(), "id": item.id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        parsed = json.loads(raw)
        if set(parsed) != {"at", "id"} or not all(isinstance(parsed[key], str) for key in parsed):
            raise ValueError
        datetime.fromisoformat(parsed["at"])
        return parsed["at"], parsed["id"]
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("invalid_cursor") from None


def _ids(db: Session, model: type, project_id: int) -> dict[str, Any]:
    rows = db.scalars(select(model).where(model.project_id == project_id)).all()
    return {str(row.id): row for row in rows}


def _scoped_entities(db: Session, project_id: int) -> dict[str, dict[str, Any]]:
    """Only exact project relations are admitted; free-form audit details are ignored."""
    result = {
        "message": _ids(db, Message, project_id),
        "task": _ids(db, Task, project_id),
        "response_draft": _ids(db, ResponseDraft, project_id),
        "task_completion_suggestion": _ids(db, TaskCompletionSuggestion, project_id),
        "risk": _ids(db, Risk, project_id),
        "decision": _ids(db, Decision, project_id),
        "obligation": _ids(db, Obligation, project_id),
        "meeting": _ids(db, Meeting, project_id),
        "meeting_proposal": _ids(db, MeetingProposal, project_id),
        "invoice_extraction_proposal": _ids(db, InvoiceExtractionProposal, project_id),
        "budget_line": _ids(db, BudgetLine, project_id),
        "cash_flow": _ids(db, CashFlowEntry, project_id),
        "schedule_baseline": _ids(db, ScheduleBaseline, project_id),
        "schedule_item": _ids(db, ScheduleItem, project_id),
        "procurement": _ids(db, ProcurementItem, project_id),
        "acceptance_act": _ids(db, AcceptanceAct, project_id),
        "payment_event": _ids(db, PaymentEvent, project_id),
        "contract": _ids(db, Contract, project_id),
        "contract_version": _ids(db, ContractVersion, project_id),
        "document": _ids(db, Document, project_id),
        "organizer_session": _ids(db, OrganizerSession, project_id),
        "organizer_proposal": _ids(db, OrganizerProposal, project_id),
        "automation_rule": _ids(db, AutomationRule, project_id),
    }
    rule_ids = [int(value) for value in result["automation_rule"]]
    runs = db.scalars(select(AutomationRun).where(AutomationRun.rule_id.in_(rule_ids))).all() if rule_ids else []
    result["automation_run"] = {str(row.id): row for row in runs}
    return result


def _legacy_metadata(
    entity_type: str,
    row: Any,
    *,
    meeting_binding: MeetingSourceBinding | None = None,
) -> tuple[list[TrailRef], list[TrailRef], dict[str, Any] | None, TrailActor | None]:
    sources: list[TrailRef] = []
    evidence: list[TrailRef] = []
    ai: dict[str, Any] | None = None
    actor: TrailActor | None = None

    source_reference_id = getattr(row, "source_reference_id", None)
    source_version_id = getattr(row, "source_document_version_id", None)
    source_document_id = getattr(row, "source_document_id", None)
    if source_reference_id:
        sources.append(TrailRef(type="source", id=str(source_reference_id)))
    if source_document_id:
        sources.append(TrailRef(type="document", id=str(source_document_id)))
    if source_version_id:
        sources.append(TrailRef(type="document_version", id=str(source_version_id)))
    if getattr(row, "message_id", None):
        sources.append(TrailRef(type="message", id=str(row.message_id)))
    if getattr(row, "source_file_id", None):
        sources.append(TrailRef(type="legacy_source", id=str(row.source_file_id)))

    confidence = getattr(row, "confidence", None)
    if entity_type == "message":
        confidence = getattr(row, "context_confidence", None)
    extraction_method = getattr(row, "extraction_method", None)
    if confidence is not None or extraction_method:
        ai = {"confidence": confidence, "extraction_method": extraction_method or "unknown"}

    for field in ("confirmed_by_user_id", "approved_by_user_id", "reviewed_by_user_id", "bound_by_user_id"):
        value = getattr(row, field, None)
        if value is not None:
            actor = TrailActor(kind="user", id=str(value))
            break

    if isinstance(row, MeetingProposal):
        binding = meeting_binding
        if binding is not None:
            sources.extend([
                TrailRef(type="source", id=str(binding.source_id)),
                TrailRef(type="source_version", id=str(binding.source_version_id), revision=1),
            ])
            evidence.append(TrailRef(type="evidence", id=str(binding.evidence_id), revision=1))
    return sources, evidence, ai, actor


def _audit_items(db: Session, project_id: int, scoped: dict[str, dict[str, Any]]) -> list[EvidenceTrailItem]:
    extensions = list(db.scalars(select(AuditExtension).where(AuditExtension.project_id == project_id)))
    extension_by_audit = {row.audit_log_id: row for row in extensions}
    audit_ids = set(extension_by_audit)

    predicates = [
        and_(AuditLog.entity_type == "project", AuditLog.entity_id == project_id),
    ]
    if audit_ids:
        predicates.append(AuditLog.id.in_(audit_ids))
    for entity_type, rows in scoped.items():
        integer_ids = [int(value) for value in rows if value.isdigit()]
        if integer_ids:
            predicates.append(and_(AuditLog.entity_type == entity_type, AuditLog.entity_id.in_(integer_ids)))
    legacy_logs = list(db.scalars(select(AuditLog).where(or_(*predicates))))
    for row in legacy_logs:
        if row.entity_type == "project" and row.entity_id == project_id:
            audit_ids.add(row.id)
        elif row.entity_id is not None and str(row.entity_id) in scoped.get(row.entity_type, {}):
            audit_ids.add(row.id)
    logs = {row.id: row for row in legacy_logs if row.id in audit_ids}

    receipt_ids = {str(row.receipt_id) for row in extensions if row.receipt_id}
    receipts = {
        str(row.id): row for row in db.scalars(
            select(ActionReceipt).where(ActionReceipt.id.in_(receipt_ids))
        )
    } if receipt_ids else {}
    revision_keys = {
        (ref.id, ref.revision)
        for row in extensions
        if (ref := _ref(row.action_pin)) is not None and ref.revision is not None
    }
    revisions = {
        (row.action_id, row.revision): row for row in db.scalars(select(ActionRevision).where(or_(*[
            and_(ActionRevision.action_id == action_id, ActionRevision.revision == revision)
            for action_id, revision in revision_keys
        ])))
    } if revision_keys else {}
    binding_ids = {
        row.binding_id for row in scoped.get("meeting_proposal", {}).values()
    }
    bindings = {
        row.id: row for row in db.scalars(
            select(MeetingSourceBinding).where(MeetingSourceBinding.id.in_(binding_ids))
        )
    } if binding_ids else {}
    items: list[EvidenceTrailItem] = []
    for audit_id in sorted(audit_ids):
        log = logs.get(audit_id)
        if log is None:
            continue
        extension = extension_by_audit.get(audit_id)
        row = scoped.get(log.entity_type, {}).get(str(log.entity_id)) if log.entity_id is not None else None
        subject = TrailRef(type=extension.subject_type, id=extension.subject_id) if extension else TrailRef(
            type=log.entity_type, id=str(log.entity_id if log.entity_id is not None else "unknown"),
        )
        sources: list[TrailRef] = []
        evidence: list[TrailRef] = []
        ai = None
        inferred_actor = None
        if row is not None:
            binding = bindings.get(row.binding_id) if isinstance(row, MeetingProposal) else None
            sources, evidence, ai, inferred_actor = _legacy_metadata(
                log.entity_type, row, meeting_binding=binding,
            )

        action_ref = _ref(extension.action_pin) if extension else None
        approval_ref = TrailRef(type="approval", id=str(extension.approval_id)) if extension and extension.approval_id else None
        receipt_ref = TrailRef(type="receipt", id=str(extension.receipt_id)) if extension and extension.receipt_id else None
        job_ref = TrailRef(type="background_job", id=str(extension.job_id)) if extension and extension.job_id else None
        relations = _refs(extension.relation_refs) if extension else []
        authorization = "UNKNOWN"
        receipt = None
        if extension and extension.receipt_id:
            receipt = receipts.get(str(extension.receipt_id))
            if receipt:
                authorization = "AUTO" if receipt.authorization_origin == "SERVER_POLICY" else "CONFIRM"
        if extension and action_ref and action_ref.revision:
            revision = revisions.get((action_ref.id, action_ref.revision))
            if revision and isinstance(revision.envelope, dict):
                evidence.extend(_refs(revision.envelope.get("evidence"), default_type="evidence"))
                sources.extend(_refs(revision.envelope.get("source_versions"), default_type="source_version"))
                autonomy = revision.envelope.get("autonomy")
                if autonomy in {"AUTO", "CONFIRM"}:
                    authorization = autonomy
        actor = None
        if extension:
            actor = TrailActor(kind="user", id=str(extension.actor_id)) if extension.actor_id is not None else TrailActor(
                kind="service", id=str(extension.service_principal),
            )
        else:
            actor = inferred_actor
            if actor and any(token in log.action.casefold() for token in ("confirm", "approve", "review")):
                authorization = "CONFIRM"
        items.append(EvidenceTrailItem(
            id=f"audit:{log.id}", occurred_at=_utc(log.created_at), phase=_phase(log.action), event=log.action,
            subject=subject, linkage="typed" if extension else "legacy_unlinked",
            authorization=authorization, outcome=receipt.outcome if receipt else _outcome(log.action),
            actor=actor, correlation_id=extension.correlation_id if extension else None,
            action_ref=action_ref, approval_ref=approval_ref, receipt_ref=receipt_ref, job_ref=job_ref,
            source_refs=sources, evidence_refs=evidence, relation_refs=relations, ai=ai,
        ))
    return items


def _pilot_items(db: Session, project_id: int) -> list[EvidenceTrailItem]:
    actions = list(db.scalars(select(PilotAction).where(PilotAction.project_id == project_id)))
    action_ids = [row.id for row in actions]
    if not action_ids:
        return []
    revisions = list(db.scalars(select(ActionRevision).where(ActionRevision.action_id.in_(action_ids))))
    approvals_by_revision: dict[tuple[str, int], list[ActionApproval]] = {}
    for approval in db.scalars(select(ActionApproval).where(ActionApproval.action_id.in_(action_ids))):
        approvals_by_revision.setdefault((approval.action_id, approval.revision), []).append(approval)
    receipts_by_revision = {
        (receipt.action_id, receipt.revision): receipt
        for receipt in db.scalars(select(ActionReceipt).where(ActionReceipt.action_id.in_(action_ids)))
    }
    revisions_by_action: dict[str, list[ActionRevision]] = {}
    for revision in revisions:
        revisions_by_action.setdefault(revision.action_id, []).append(revision)
    result: list[EvidenceTrailItem] = []
    for action in actions:
        for revision in revisions_by_action.get(action.id, []):
            envelope = revision.envelope if isinstance(revision.envelope, dict) else {}
            authorization = envelope.get("autonomy") if envelope.get("autonomy") in {"AUTO", "CONFIRM"} else "UNKNOWN"
            evidence = _refs(envelope.get("evidence"), default_type="evidence")
            sources = _refs(envelope.get("source_versions"), default_type="source_version")
            action_ref = TrailRef(type="action", id=str(action.id), revision=revision.revision)
            result.append(EvidenceTrailItem(
                id=f"action-revision:{action.id}:{revision.revision}", occurred_at=_utc(revision.created_at),
                phase="PROPOSAL", event="ACTION_FROZEN", subject=action_ref, linkage="typed",
                authorization=authorization, actor=TrailActor(kind="user", id=str(revision.requested_by)),
                action_ref=action_ref, source_refs=sources, evidence_refs=evidence,
            ))
            for approval in approvals_by_revision.get((action.id, revision.revision), []):
                result.append(EvidenceTrailItem(
                    id=f"approval:{approval.id}", occurred_at=_utc(approval.granted_at),
                    phase="ACTION_AUTHORIZATION", event=f"APPROVAL_{approval.state}", subject=action_ref,
                    linkage="typed", authorization="CONFIRM", actor=TrailActor(kind="user", id=str(approval.approver_id)),
                    action_ref=action_ref, approval_ref=TrailRef(type="approval", id=str(approval.id)),
                    source_refs=sources, evidence_refs=evidence,
                ))
            receipt = receipts_by_revision.get((action.id, revision.revision))
            if receipt:
                result.append(EvidenceTrailItem(
                    id=f"receipt:{receipt.id}", occurred_at=_utc(receipt.recorded_at), phase="OUTCOME",
                    event="ACTION_RECEIPT", subject=action_ref, linkage="typed",
                    authorization="AUTO" if receipt.authorization_origin == "SERVER_POLICY" else "CONFIRM",
                    outcome=receipt.outcome, action_ref=action_ref,
                    approval_ref=TrailRef(type="approval", id=str(receipt.approval_id)) if receipt.approval_id else None,
                    receipt_ref=TrailRef(type="receipt", id=str(receipt.id)),
                    job_ref=TrailRef(type="background_job", id=str(receipt.job_id)),
                    source_refs=sources, evidence_refs=evidence,
                ))
    return result


def _provider_items(db: Session, project_id: int) -> list[EvidenceTrailItem]:
    actions = list(db.scalars(select(ProviderAction).where(ProviderAction.project_id == project_id)))
    action_ids = [row.action_id for row in actions]
    if not action_ids:
        return []
    approvals = {
        (row.action_id, row.revision): row
        for row in db.scalars(select(ProviderActionApproval).where(ProviderActionApproval.action_id.in_(action_ids)))
    }
    observations: dict[tuple[str, int], list[ProviderOutcomeObservation]] = {}
    for row in db.scalars(select(ProviderOutcomeObservation).where(
        ProviderOutcomeObservation.action_id.in_(action_ids)
    )):
        observations.setdefault((row.action_id, row.revision), []).append(row)
    result: list[EvidenceTrailItem] = []
    for action in actions:
        subject = TrailRef(type="provider_action", id=action.action_id, revision=action.revision)
        evidence = _refs(action.evidence_pins, default_type="evidence")
        result.append(EvidenceTrailItem(
            id=f"provider-action:{action.action_id}:{action.revision}", occurred_at=_utc(action.created_at),
            phase="PROPOSAL", event="PROVIDER_ACTION_FROZEN", subject=subject, linkage="typed",
            authorization=action.mode, actor=TrailActor(kind="service", id=action.created_by),
            action_ref=subject, evidence_refs=evidence,
        ))
        approval = approvals.get((action.action_id, action.revision))
        if approval:
            result.append(EvidenceTrailItem(
                id=f"provider-approval:{approval.id}", occurred_at=_utc(approval.granted_at),
                phase="ACTION_AUTHORIZATION", event=f"PROVIDER_APPROVAL_{approval.state}", subject=subject,
                linkage="typed", authorization="CONFIRM", actor=TrailActor(kind="user", id=approval.approved_by),
                action_ref=subject, approval_ref=TrailRef(type="provider_approval", id=approval.id),
                evidence_refs=evidence,
            ))
        for observation in observations.get((action.action_id, action.revision), []):
            result.append(EvidenceTrailItem(
                id=f"provider-observation:{observation.id}", occurred_at=_utc(observation.recorded_at),
                phase="OUTCOME", event=f"PROVIDER_{observation.source}", subject=subject,
                linkage="typed", authorization=action.mode, outcome=observation.outcome,
                action_ref=subject, job_ref=TrailRef(type="background_job", id=str(observation.job_id)) if observation.job_id else None,
                evidence_refs=evidence,
            ))
    return result


def _source_evidence_items(db: Session, project_id: int, scoped: dict[str, dict[str, Any]]) -> list[EvidenceTrailItem]:
    """Project immutable source/evidence facts without locators, bodies or quotes."""
    sources = list(db.scalars(select(SourceReference).where(SourceReference.origin_project_id == project_id)))
    source_ids = [row.id for row in sources]
    result: list[EvidenceTrailItem] = []
    if source_ids:
        versions = list(db.scalars(select(SourceVersion).where(SourceVersion.source_id.in_(source_ids))))
        for version in versions:
            version_ref = TrailRef(type="source_version", id=str(version.id), revision=version.revision)
            result.append(EvidenceTrailItem(
                id=f"source-version:{version.id}", occurred_at=_utc(version.observed_at),
                phase="SOURCE", event="SOURCE_OBSERVED", subject=version_ref, linkage="typed",
                source_refs=[TrailRef(type="source", id=str(version.source_id)), version_ref],
            ))
        for evidence in db.scalars(select(Evidence).where(Evidence.source_id.in_(source_ids))):
            evidence_ref = TrailRef(type="evidence", id=str(evidence.id), revision=evidence.revision)
            extractor = evidence.extractor if isinstance(evidence.extractor, dict) else {}
            ai = {
                "confidence": evidence.confidence,
                "extraction_method": str(extractor.get("name") or "unknown"),
            }
            result.append(EvidenceTrailItem(
                id=f"evidence:{evidence.id}", occurred_at=_utc(evidence.extracted_at),
                phase="ANALYSIS", event="EVIDENCE_EXTRACTED", subject=evidence_ref,
                linkage="typed", source_refs=[
                    TrailRef(type="source", id=str(evidence.source_id)),
                    TrailRef(type="source_version", id=str(evidence.source_version_id), revision=1),
                ], evidence_refs=[evidence_ref], ai=ai,
            ))

    message_ids = [int(value) for value in scoped.get("message", {}) if value.isdigit()]
    if message_ids:
        for claim in db.scalars(select(DeadlineClaim).where(DeadlineClaim.message_id.in_(message_ids))):
            if claim.reviewed_at is None:
                continue
            result.append(EvidenceTrailItem(
                id=f"deadline-claim:{claim.id}:{claim.revision}", occurred_at=_utc(claim.reviewed_at),
                phase="CONTEXT_CONFIRMATION", event=f"DEADLINE_{claim.verification.upper()}",
                subject=TrailRef(type="deadline_claim", id=str(claim.id), revision=claim.revision),
                linkage="typed", authorization="CONFIRM",
                actor=TrailActor(kind="user", id=str(claim.reviewed_by)),
                source_refs=[TrailRef(type="message", id=str(claim.message_id))],
                evidence_refs=_refs(claim.evidence_pins, default_type="evidence"),
            ))
        for relation in db.scalars(select(ContextRelation).where(ContextRelation.message_id.in_(message_ids))):
            if relation.confirmed_at is None:
                continue
            target = _ref(relation.target_ref)
            result.append(EvidenceTrailItem(
                id=f"context-relation:{relation.id}:{relation.revision}", occurred_at=_utc(relation.confirmed_at),
                phase="CONTEXT_CONFIRMATION", event=f"CONTEXT_{relation.state.upper()}",
                subject=TrailRef(type="context_relation", id=str(relation.id), revision=relation.revision),
                linkage="typed", authorization="CONFIRM",
                actor=TrailActor(kind="user", id=str(relation.confirmed_by)),
                source_refs=[TrailRef(type="message", id=str(relation.message_id))],
                evidence_refs=_refs(relation.evidence_pins, default_type="evidence"),
                relation_refs=[target] if target else [],
                receipt_ref=TrailRef(type="receipt", id=str(relation.receipt_id)) if relation.receipt_id else None,
            ))
    return result


def project_evidence_trail(db: Session, *, project_id: int, limit: int, cursor: str | None) -> EvidenceTrailPage:
    decoded = _decode_cursor(cursor)
    scoped = _scoped_entities(db, project_id)
    items = (
        _source_evidence_items(db, project_id, scoped)
        + _audit_items(db, project_id, scoped)
        + _pilot_items(db, project_id)
        + _provider_items(db, project_id)
    )
    # Stable descending order.  IDs break timestamp ties without relying on table PK compatibility.
    items.sort(key=lambda item: (item.occurred_at.isoformat(), item.id), reverse=True)
    if decoded:
        items = [item for item in items if (item.occurred_at.isoformat(), item.id) < decoded]
    page = items[:limit]
    return EvidenceTrailPage(
        project_id=project_id,
        items=page,
        next_cursor=_cursor(page[-1]) if len(items) > limit and page else None,
    )
