from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.auth import ROLE_LEVEL
from app.core.v54_refs import VersionPin
from app.models.governance import Decision, GovernanceHistory, Risk
from app.models.management import Obligation, ObligationHistory
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task, TaskHistory
from app.models.v54_pilot import Evidence, EvidenceAssessment, SourceCurrent, SourceReference, SourceVersion


class ManagementDenied(ValueError):
    pass


class ManagementConflict(ValueError):
    pass


@dataclass(frozen=True)
class ManagementScope:
    organization_id: int
    project_id: int
    actor_user_id: int
    role: str


OBLIGATION_TRANSITIONS = {
    "needs_confirmation": {"confirmed", "dismissed"},
    "confirmed": {"in_progress", "fulfilled", "breached", "dismissed"},
    "in_progress": {"confirmed", "fulfilled", "breached"},
    "fulfilled": {"in_progress"},
    "breached": {"in_progress"},
    "dismissed": {"needs_confirmation"},
}
RISK_TRANSITIONS = {
    "needs_confirmation": {"confirmed", "dismissed"},
    "confirmed": {"mitigating", "resolved", "dismissed"},
    "mitigating": {"confirmed", "resolved"},
    "resolved": {"mitigating"},
    "dismissed": {"needs_confirmation"},
}
DECISION_TRANSITIONS = {
    "needs_confirmation": {"confirmed", "dismissed"},
    "confirmed": {"decided", "dismissed"},
    "decided": {"confirmed", "executed"},
    "executed": {"decided"},
    "dismissed": {"needs_confirmation"},
}
TASK_STATE = {
    "assigned": "OPEN",
    "in_progress": "IN_PROGRESS",
    "completed": "COMPLETED",
    "cancelled": "CANCELLED",
}


def _text(value: str | None, *, required: bool = False, limit: int = 5000) -> str | None:
    result = (value or "").strip()
    if (required and not result) or len(result) > limit:
        raise ManagementDenied("invalid_input")
    return result or None


def _deadline_policy(value: dict | None) -> dict:
    policy = dict(value or {})
    offsets = policy.get("reminder_days", [7, 3, 1])
    if not isinstance(offsets, list) or any(type(v) is not int or v < 0 or v > 365 for v in offsets):
        raise ManagementDenied("invalid_deadline_policy")
    quiet = policy.get("quiet_hours", {"start": "20:00", "end": "08:00"})
    if not isinstance(quiet, dict) or set(quiet) != {"start", "end"}:
        raise ManagementDenied("invalid_deadline_policy")
    try:
        time.fromisoformat(quiet["start"]); time.fromisoformat(quiet["end"])
    except (TypeError, ValueError):
        raise ManagementDenied("invalid_deadline_policy")
    return {"reminder_days": sorted(set(offsets), reverse=True), "quiet_hours": quiet}


class ManagementLifecycle:
    """Caller-owned transaction; no provider calls, queue writes or commits."""

    def scope(self, db: Session, *, project_id: int, actor_user_id: int, minimum: str = "editor") -> ManagementScope:
        project = db.get(Project, project_id)
        membership = db.scalar(select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == actor_user_id,
        ))
        if project is None or membership is None or ROLE_LEVEL.get(membership.role, 0) < ROLE_LEVEL[minimum]:
            raise ManagementDenied("resource_unavailable")
        return ManagementScope(project.organization_id, project.id, actor_user_id, membership.role)

    def evidence(self, db: Session, scope: ManagementScope, raw_pins: list[dict]) -> tuple[list[dict], bool, float]:
        if not raw_pins:
            raise ManagementDenied("evidence_required")
        pins: list[dict] = []
        verified, confidence = True, 1.0
        for raw in raw_pins:
            try:
                pin = VersionPin.model_validate(raw)
            except ValueError as exc:
                raise ManagementDenied("resource_unavailable") from exc
            if (pin.ref.type != "evidence" or pin.version_kind != "revision" or pin.value != 1
                    or pin.ref.tenant_id.value != str(scope.organization_id)):
                raise ManagementDenied("resource_unavailable")
            row = db.get(Evidence, pin.ref.id.value)
            assessment = db.get(EvidenceAssessment, pin.ref.id.value)
            source = db.get(SourceReference, row.source_id) if row else None
            version = db.get(SourceVersion, row.source_version_id) if row else None
            current = db.get(SourceCurrent, row.source_id) if row else None
            if (not row or not assessment or not source or not version or not current
                    or row.organization_id != scope.organization_id
                    or source.organization_id != scope.organization_id
                    or source.origin_project_id != scope.project_id
                    or version.source_id != source.id or row.source_version_id != version.id
                    or current.version_id != version.id
                    or source.availability != "available"
                    or assessment.availability != "available"
                    or assessment.freshness != "fresh"):
                raise ManagementDenied("resource_unavailable")
            pins.append(pin.model_dump(mode="json"))
            confidence = min(confidence, row.confidence if row.confidence is not None else 0.0)
            verified = verified and assessment.verification == "verified"
        return pins, verified, confidence

    @staticmethod
    def _cas(db: Session, model, entity_id: int, project_id: int, expected: int, **changes) -> int:
        if expected < 1:
            raise ManagementConflict("version_conflict")
        db.flush()
        result = db.execute(update(model).where(
            model.id == entity_id, model.project_id == project_id, model.record_version == expected,
        ).values(**changes, record_version=expected + 1).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise ManagementConflict("version_conflict")
        db.expire_all()
        return expected + 1

    def create_obligation(self, db: Session, *, scope: ManagementScope, title: str, owner_user_id: int,
                          evidence_pins: list[dict], due_date: date | None = None, due_time: time | None = None,
                          timezone_name: str = "Europe/Moscow", contract_id: int | None = None,
                          deadline_policy: dict | None = None) -> Obligation:
        if not db.scalar(select(ProjectMember.id).where(ProjectMember.project_id == scope.project_id,
                                                        ProjectMember.user_id == owner_user_id)):
            raise ManagementDenied("resource_unavailable")
        if contract_id is not None and not db.scalar(select(Contract.id).where(
                Contract.id == contract_id, Contract.project_id == scope.project_id)):
            raise ManagementDenied("resource_unavailable")
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ManagementDenied("invalid_timezone") from exc
        pins, verified, confidence = self.evidence(db, scope, evidence_pins)
        digest = hashlib.sha256(repr(pins).encode()).hexdigest()
        row = Obligation(project_id=scope.project_id, contract_id=contract_id, owner_user_id=owner_user_id,
                         title=_text(title, required=True, limit=500), status="needs_confirmation",
                         due_date=due_date, due_time=due_time, timezone=timezone_name,
                         deadline_policy=_deadline_policy(deadline_policy), source_type="evidence",
                         source_id=pins[0]["ref"]["id"]["value"], source_name="Evidence",
                         source_excerpt="Exact evidence pin", source_hash=digest, confidence=confidence,
                         evidence_pins=pins, review_state="verified" if verified and confidence >= .85 else "needs_review")
        db.add(row); db.flush()
        db.add(ObligationHistory(obligation_id=row.id, project_id=scope.project_id, sequence=1,
                                 event="created", to_status=row.status, resulting_version=1,
                                 actor_user_id=scope.actor_user_id, evidence_pins=pins))
        return row

    def transition_obligation(self, db: Session, *, scope: ManagementScope, obligation_id: int,
                              expected_version: int, status: str, reason: str | None = None,
                              result_note: str | None = None) -> Obligation:
        row = db.get(Obligation, obligation_id)
        if row is None or row.project_id != scope.project_id:
            raise ManagementDenied("resource_unavailable")
        if status not in OBLIGATION_TRANSITIONS.get(row.status, set()):
            raise ManagementDenied("invalid_transition")
        reason = _text(reason, required=row.status in {"fulfilled", "breached", "dismissed"}, limit=2000)
        note = _text(result_note, required=status in {"fulfilled", "breached"})
        if status == "confirmed" and row.review_state == "needs_review":
            self.scope(db, project_id=scope.project_id, actor_user_id=scope.actor_user_id, minimum="manager")
        old_status, history_pins = row.status, row.evidence_pins
        new_version = self._cas(db, Obligation, row.id, scope.project_id, expected_version,
                                status=status, result_note=note if result_note is not None else row.result_note,
                                review_state="verified" if status == "confirmed" else row.review_state)
        seq = db.scalar(select(func.coalesce(func.max(ObligationHistory.sequence), 0)).where(
            ObligationHistory.obligation_id == row.id)) + 1
        db.add(ObligationHistory(obligation_id=row.id, project_id=scope.project_id, sequence=seq,
                                 event="transitioned", from_status=old_status, to_status=status,
                                 resulting_version=new_version, actor_user_id=scope.actor_user_id,
                                 reason=reason, evidence_pins=history_pins))
        return db.get(Obligation, row.id)

    def ensure_internal_task(self, db: Session, *, scope: ManagementScope, obligation_id: int,
                             expected_version: int) -> Task:
        row = db.get(Obligation, obligation_id)
        if row is None or row.project_id != scope.project_id or row.status not in {"confirmed", "in_progress"}:
            raise ManagementDenied("resource_unavailable")
        if row.record_version != expected_version:
            raise ManagementConflict("version_conflict")
        if row.task_id:
            task = db.get(Task, row.task_id)
            if task is None or task.project_id != scope.project_id:
                raise ManagementDenied("resource_unavailable")
            return task
        digest = hashlib.sha256(f"obligation:{row.id}".encode()).hexdigest()
        task = Task(project_id=scope.project_id, assignee_user_id=row.owner_user_id,
                    created_by_user_id=scope.actor_user_id, title=row.title, description="Исполнение обязательства",
                    status="assigned", priority="high", due_date=row.due_date, source_type="obligation",
                    source_file_id=f"obligation:{row.id}", source_file_name=row.source_name,
                    source_excerpt=row.source_excerpt, source_excerpt_hash=digest, confidence=row.confidence,
                    needs_review=False, external_action_status="proposed")
        db.add(task); db.flush()
        new_version = self._cas(db, Obligation, row.id, scope.project_id, expected_version, task_id=task.id)
        db.add(TaskHistory(task_id=task.id, action="mapped_from_obligation", new_status=task.status,
                           details=f"obligation_id={row.id}; obligation_version={new_version}",
                           changed_by_user_id=scope.actor_user_id))
        return task

    def create_risk(self, db: Session, *, scope: ManagementScope, title: str, owner_user_id: int,
                    evidence_pins: list[dict], criticality: str = "medium", obligation_id: int | None = None,
                    task_id: int | None = None) -> Risk:
        if criticality not in {"low", "medium", "high", "critical"}:
            raise ManagementDenied("invalid_input")
        if not db.scalar(select(ProjectMember.id).where(ProjectMember.project_id == scope.project_id,
                                                        ProjectMember.user_id == owner_user_id)):
            raise ManagementDenied("resource_unavailable")
        pins, verified, confidence = self.evidence(db, scope, evidence_pins)
        if obligation_id and not db.scalar(select(Obligation.id).where(Obligation.id == obligation_id,
                                                                       Obligation.project_id == scope.project_id)):
            raise ManagementDenied("resource_unavailable")
        if task_id and not db.scalar(select(Task.id).where(Task.id == task_id, Task.project_id == scope.project_id)):
            raise ManagementDenied("resource_unavailable")
        digest = hashlib.sha256(("risk:" + repr(pins) + title).encode()).hexdigest()
        row = Risk(project_id=scope.project_id, owner_user_id=owner_user_id, obligation_id=obligation_id,
                   task_id=task_id, title=_text(title, required=True, limit=500), description=title,
                   criticality=criticality, source_type="evidence", source_id=pins[0]["ref"]["id"]["value"],
                   source_name="Evidence", source_excerpt="Exact evidence pin", source_hash=digest,
                   confidence=confidence, evidence_pins=pins,
                   review_state="verified" if verified and confidence >= .85 else "needs_review")
        db.add(row); db.flush()
        self._governance_history(db, row, "risk", None, row.status, scope.actor_user_id, "created")
        return row

    def create_decision(self, db: Session, *, scope: ManagementScope, question: str, owner_user_id: int,
                        evidence_pins: list[dict], obligation_id: int | None = None,
                        task_id: int | None = None, risk_id: int | None = None) -> Decision:
        if not db.scalar(select(ProjectMember.id).where(ProjectMember.project_id == scope.project_id,
                                                        ProjectMember.user_id == owner_user_id)):
            raise ManagementDenied("resource_unavailable")
        for model, identity in ((Obligation, obligation_id), (Task, task_id), (Risk, risk_id)):
            if identity is not None and not db.scalar(select(model.id).where(
                    model.id == identity, model.project_id == scope.project_id)):
                raise ManagementDenied("resource_unavailable")
        pins, verified, confidence = self.evidence(db, scope, evidence_pins)
        digest = hashlib.sha256(("decision:" + repr(pins) + question).encode()).hexdigest()
        row = Decision(project_id=scope.project_id, initiator_user_id=scope.actor_user_id,
                       owner_user_id=owner_user_id, obligation_id=obligation_id, task_id=task_id, risk_id=risk_id,
                       question=_text(question, required=True), source_type="evidence",
                       source_id=pins[0]["ref"]["id"]["value"], source_name="Evidence",
                       source_excerpt="Exact evidence pin", source_hash=digest, confidence=confidence,
                       evidence_pins=pins, review_state="verified" if verified and confidence >= .85 else "needs_review")
        db.add(row); db.flush()
        self._governance_history(db, row, "decision", None, row.status, scope.actor_user_id, "created")
        return row

    def transition_governance(self, db: Session, *, scope: ManagementScope, entity_type: str, entity_id: int,
                              expected_version: int, status: str, reason: str | None = None,
                              action_note: str | None = None, decision_text: str | None = None):
        if entity_type not in {"risk", "decision"}:
            raise ManagementDenied("resource_unavailable")
        model, transitions = (Risk, RISK_TRANSITIONS) if entity_type == "risk" else (Decision, DECISION_TRANSITIONS)
        row = db.get(model, entity_id)
        if row is None or row.project_id != scope.project_id or status not in transitions.get(row.status, set()):
            raise ManagementDenied("resource_unavailable")
        if status in {"confirmed", "decided", "resolved", "executed"} and row.review_state == "needs_review":
            self.scope(db, project_id=scope.project_id, actor_user_id=scope.actor_user_id, minimum="manager")
        changes = {"status": status}
        if model is Risk:
            note = _text(action_note, required=status in {"mitigating", "resolved"})
            if action_note is not None: changes["action_note"] = note
        else:
            text = _text(decision_text, required=status in {"decided", "executed"})
            if decision_text is not None: changes["decision_text"] = text
        if status == "confirmed": changes["review_state"] = "verified"
        old_status = row.status
        new_version = self._cas(db, model, row.id, scope.project_id, expected_version, **changes)
        self._governance_history(db, row, entity_type, old_status, status, scope.actor_user_id,
                                 "transitioned", reason, new_version)
        return db.get(model, row.id)

    @staticmethod
    def _governance_history(db, row, entity_type, before, after, actor, event, reason=None, version=1):
        seq = db.scalar(select(func.coalesce(func.max(GovernanceHistory.sequence), 0)).where(
            GovernanceHistory.entity_type == entity_type, GovernanceHistory.entity_id == row.id)) + 1
        db.add(GovernanceHistory(project_id=row.project_id, entity_type=entity_type, entity_id=row.id,
                                 sequence=seq, event=event, from_status=before, to_status=after,
                                 resulting_version=version, actor_user_id=actor, reason=reason,
                                 evidence_pins=row.evidence_pins))


def normalized_task_state(status: str) -> str:
    try:
        return TASK_STATE[status]
    except KeyError as exc:
        raise ManagementDenied("unknown_task_state") from exc
