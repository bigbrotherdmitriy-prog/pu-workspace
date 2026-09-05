from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import ScheduleBaseline, ScheduleItem
from app.models.execution_finance import BudgetLine, CashFlowEntry
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.v54_pilot import Evidence, EvidenceAssessment, SourceCurrent, SourceReference, SourceVersion
from app.mvp4.supply.contracts import (
    CreateSupplyRequest,
    CreateDdsProposal,
    DdsProposalResult,
    EvidenceLink,
    PrepareOrder,
    ProposeAcceptanceAct,
    RecordDelivery,
    RecordOrder,
    ResolveDiscrepancy,
    ReviewSupplyRequest,
    SupplyMutationResult,
    VersionedCommand,
)
from app.mvp4.supply.models import SupplyCase, SupplyCaseVersion, SupplyCommandReceipt


MIN_AUTOMATION_CONFIDENCE = 0.85


class SupplyDenied(ValueError):
    pass


class SupplyConflict(ValueError):
    pass


def _canonical_payload(command) -> tuple[dict, str]:
    value = command.model_dump(mode="json")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return value, hashlib.sha256(encoded).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _evidence_pin(link: EvidenceLink) -> dict:
    return {
        "evidence_id": str(link.evidence_id),
        "evidence_revision": link.evidence_revision,
        "source_version_id": str(link.source_version_id),
        "document_version_id": link.document_version_id,
    }


class SupplyService:
    """DB-only supply workflow. Caller owns commit/rollback and authorization.

    The service performs no provider calls, job enqueue, purchase, payment,
    signature or document mutation. Role checks remain at the API boundary.
    """

    def __init__(self, *, minimum_confidence: float = MIN_AUTOMATION_CONFIDENCE):
        if not 0 < minimum_confidence <= 1:
            raise ValueError("invalid confidence threshold")
        self.minimum_confidence = minimum_confidence

    @staticmethod
    def _require_role(db: Session, *, actor_user_id: int, project_id: int, minimum: str) -> None:
        levels = {"viewer": 1, "editor": 2, "manager": 3, "owner": 4}
        role = db.scalar(select(ProjectMember.role).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == actor_user_id,
        ))
        if role not in levels or levels[role] < levels[minimum]:
            raise SupplyDenied("resource_unavailable")

    @staticmethod
    def _snapshot(row: SupplyCase) -> dict:
        return {
            "record_version": row.record_version,
            "organization_id": row.organization_id,
            "project_id": row.project_id,
            "contract_id": row.contract_id,
            "schedule_baseline_id": row.schedule_baseline_id,
            "schedule_baseline_version": row.schedule_baseline_version,
            "schedule_item_id": row.schedule_item_id,
            "task_id": row.task_id,
            "document_version_id": row.document_version_id,
            "evidence_id": row.evidence_id,
            "evidence_revision": row.evidence_revision,
            "source_id": row.source_id,
            "source_version_id": row.source_version_id,
            "title": row.title,
            "supplier": row.supplier,
            "requested_quantity": str(row.requested_quantity),
            "unit": row.unit,
            "unit_price": str(row.unit_price),
            "currency": row.currency,
            "status": row.status,
            "review_state": row.review_state,
            "ordered_quantity": str(row.ordered_quantity),
            "delivered_quantity": str(row.delivered_quantity),
            "accepted_quantity": str(row.accepted_quantity),
            "pending_acceptance_quantity": str(row.pending_acceptance_quantity),
            "order_reference": row.order_reference,
            "act_number": row.act_number,
            "discrepancy_code": row.discrepancy_code,
            "external_action_status": "not_created",
        }

    @staticmethod
    def _audit(db: Session, *, event: str, row: SupplyCase) -> None:
        # Audit metadata intentionally excludes names, quantities, money,
        # evidence/provider identifiers, document content and free-form notes.
        db.add(AuditLog(action=f"mvp4.supply.{event}", entity_type="supply_case", entity_id=row.id, details=None))

    def _append_version(
        self,
        db: Session,
        *,
        row: SupplyCase,
        event: str,
        actor_user_id: int,
        evidence: EvidenceLink | None = None,
    ) -> None:
        sequence = db.scalar(
            select(SupplyCaseVersion.sequence)
            .where(SupplyCaseVersion.supply_case_id == row.id)
            .order_by(SupplyCaseVersion.sequence.desc())
            .limit(1)
        )
        db.add(
            SupplyCaseVersion(
                supply_case_id=row.id,
                organization_id=row.organization_id,
                project_id=row.project_id,
                sequence=(sequence or 0) + 1,
                event=event,
                resulting_record_version=row.record_version,
                snapshot=self._snapshot(row),
                evidence_pin=_evidence_pin(evidence) if evidence else None,
                actor_user_id=actor_user_id,
            )
        )
        self._audit(db, event=event, row=row)

    @staticmethod
    def _result(row: SupplyCase, *, already_applied: bool = False, status: str | None = None,
                version: int | None = None) -> SupplyMutationResult:
        return SupplyMutationResult(
            supply_case_id=row.id,
            status=status or row.status,
            record_version=version or row.record_version,
            already_applied=already_applied,
            external_action_created=False,
        )

    @staticmethod
    def _validate_scope_links(db: Session, command: CreateSupplyRequest) -> None:
        project = db.scalar(select(Project).where(Project.id == command.project_id).with_for_update())
        if project is None or project.organization_id != command.organization_id:
            raise SupplyDenied("resource_unavailable")
        contract = db.get(Contract, command.contract_id)
        if contract is None or contract.project_id != command.project_id:
            raise SupplyDenied("resource_unavailable")
        baseline = db.get(ScheduleBaseline, command.schedule_baseline_id)
        if (
            baseline is None
            or baseline.project_id != command.project_id
            or baseline.contract_id not in {None, command.contract_id}
            or baseline.version != command.schedule_baseline_version
            or baseline.status != "approved"
        ):
            raise SupplyDenied("resource_unavailable")
        stage = db.get(ScheduleItem, command.schedule_item_id)
        if stage is None or stage.project_id != command.project_id or stage.baseline_id != baseline.id:
            raise SupplyDenied("resource_unavailable")
        task = db.get(Task, command.task_id)
        if task is None or task.project_id != command.project_id:
            raise SupplyDenied("resource_unavailable")

    def _validate_evidence(
        self,
        db: Session,
        *,
        organization_id: int,
        project_id: int,
        link: EvidenceLink,
        require_verified: bool,
    ) -> tuple[Evidence, bool, bool]:
        evidence_id = str(link.evidence_id)
        source_version_id = str(link.source_version_id)
        evidence = db.scalar(
            select(Evidence).where(
                Evidence.id == evidence_id,
                Evidence.organization_id == organization_id,
                Evidence.source_version_id == source_version_id,
                Evidence.revision == link.evidence_revision,
            )
        )
        source_version = db.scalar(
            select(SourceVersion).where(
                SourceVersion.id == source_version_id,
                SourceVersion.organization_id == organization_id,
            )
        )
        source = db.get(SourceReference, source_version.source_id) if source_version is not None else None
        current = db.scalar(select(SourceCurrent).where(
            SourceCurrent.organization_id == organization_id,
            SourceCurrent.source_id == source_version.source_id,
        )) if source_version is not None else None
        document_version = db.get(DocumentVersion, link.document_version_id)
        document = db.get(Document, document_version.document_id) if document_version is not None else None
        if (
            evidence is None
            or source_version is None
            or source is None
            or document_version is None
            or document is None
            or evidence.source_id != source_version.source_id
            or source.origin_project_id != project_id
            or source_version.legacy_document_version_id != link.document_version_id
            or current is None
            or current.version_id != source_version.id
            or document.project_id != project_id
            or not isinstance(evidence.locator, dict)
            or not evidence.locator
        ):
            raise SupplyDenied("resource_unavailable")
        assessment = db.scalar(
            select(EvidenceAssessment).where(
                EvidenceAssessment.evidence_id == evidence_id,
                EvidenceAssessment.organization_id == organization_id,
            )
        )
        now = datetime.now(timezone.utc)
        available = bool(
            assessment is not None
            and assessment.freshness == "fresh"
            and assessment.availability == "available"
            and _aware(assessment.valid_until) is not None
            and _aware(assessment.valid_until) > now
        )
        verified = bool(
            available
            and assessment.verification == "verified"
            and assessment.reviewed_by is not None
            and evidence.confidence is not None
            and evidence.confidence >= self.minimum_confidence
            and evidence.confidence_kind not in {"", "unknown"}
        )
        if require_verified and not verified:
            raise SupplyDenied("manual_review_required")
        return evidence, verified, available

    @staticmethod
    def _case_evidence(row: SupplyCase) -> EvidenceLink:
        return EvidenceLink(
            evidence_id=UUID(row.evidence_id),
            evidence_revision=1,
            source_version_id=UUID(row.source_version_id),
            document_version_id=row.document_version_id,
        )

    def _require_case_evidence_ready(self, db: Session, *, row: SupplyCase) -> None:
        _, automation_ready, available = self._validate_evidence(
            db,
            organization_id=row.organization_id,
            project_id=row.project_id,
            link=self._case_evidence(row),
            require_verified=False,
        )
        if not available or (not automation_ready and row.reviewed_by_user_id is None):
            raise SupplyDenied("manual_review_required")

    def _require_latest_event_evidence(self, db: Session, *, row: SupplyCase) -> None:
        latest = db.scalar(
            select(SupplyCaseVersion)
            .where(SupplyCaseVersion.supply_case_id == row.id)
            .order_by(SupplyCaseVersion.sequence.desc())
            .limit(1)
        )
        if latest is None or not latest.evidence_pin:
            raise SupplyDenied("manual_review_required")
        self._validate_evidence(
            db,
            organization_id=row.organization_id,
            project_id=row.project_id,
            link=EvidenceLink(
                evidence_id=UUID(latest.evidence_pin["evidence_id"]),
                evidence_revision=latest.evidence_pin["evidence_revision"],
                source_version_id=UUID(latest.evidence_pin["source_version_id"]),
                document_version_id=latest.evidence_pin["document_version_id"],
            ),
            require_verified=True,
        )

    def create_request(self, db: Session, *, actor_user_id: int,
                       command: CreateSupplyRequest) -> SupplyMutationResult:
        self._require_role(
            db, actor_user_id=actor_user_id, project_id=command.project_id, minimum="editor"
        )
        _, payload_hash = _canonical_payload(command)
        existing = db.scalar(
            select(SupplyCase).where(
                SupplyCase.organization_id == command.organization_id,
                SupplyCase.project_id == command.project_id,
                SupplyCase.request_key == command.command_key,
            )
        )
        if existing is not None:
            if existing.request_payload_hash != payload_hash:
                raise SupplyConflict("idempotency_key_conflict")
            receipt = db.scalar(select(SupplyCommandReceipt).where(
                SupplyCommandReceipt.supply_case_id == existing.id,
                SupplyCommandReceipt.command_key == command.command_key,
            ))
            if receipt is None:
                raise SupplyConflict("idempotency_receipt_missing")
            return self._result(
                existing,
                already_applied=True,
                status=receipt.result_status,
                version=receipt.resulting_record_version,
            )

        self._validate_scope_links(db, command)
        # The project lock serializes request-key admission in PostgreSQL.  A
        # second check converts a concurrent identical request into a replay.
        existing = db.scalar(
            select(SupplyCase).where(
                SupplyCase.organization_id == command.organization_id,
                SupplyCase.project_id == command.project_id,
                SupplyCase.request_key == command.command_key,
            )
        )
        if existing is not None:
            if existing.request_payload_hash != payload_hash:
                raise SupplyConflict("idempotency_key_conflict")
            receipt = db.scalar(select(SupplyCommandReceipt).where(
                SupplyCommandReceipt.supply_case_id == existing.id,
                SupplyCommandReceipt.command_key == command.command_key,
            ))
            if receipt is None:
                raise SupplyConflict("idempotency_receipt_missing")
            return self._result(existing, already_applied=True, status=receipt.result_status,
                                version=receipt.resulting_record_version)
        evidence, verified, _ = self._validate_evidence(
            db,
            organization_id=command.organization_id,
            project_id=command.project_id,
            link=command.evidence,
            require_verified=False,
        )
        row = SupplyCase(
            organization_id=command.organization_id,
            project_id=command.project_id,
            contract_id=command.contract_id,
            schedule_baseline_id=command.schedule_baseline_id,
            schedule_baseline_version=command.schedule_baseline_version,
            schedule_item_id=command.schedule_item_id,
            task_id=command.task_id,
            document_version_id=command.evidence.document_version_id,
            evidence_id=str(command.evidence.evidence_id),
            evidence_revision=command.evidence.evidence_revision,
            source_id=evidence.source_id,
            source_version_id=str(command.evidence.source_version_id),
            request_key=command.command_key,
            request_payload_hash=payload_hash,
            title=command.title,
            supplier=command.supplier,
            requested_quantity=command.requested_quantity,
            unit=command.unit,
            unit_price=command.unit_price,
            currency=command.currency,
            status="request_pending_approval" if verified else "needs_review",
            review_state="verified" if verified else "needs_review",
            evidence_confidence=evidence.confidence,
            external_action_status="not_created",
            created_by_user_id=actor_user_id,
        )
        db.add(row)
        db.flush()
        self._append_version(
            db,
            row=row,
            event="request_created",
            actor_user_id=actor_user_id,
            evidence=command.evidence,
        )
        db.add(
            SupplyCommandReceipt(
                supply_case_id=row.id,
                organization_id=row.organization_id,
                project_id=row.project_id,
                command_key=command.command_key,
                payload_hash=payload_hash,
                event="request_created",
                result_status=row.status,
                resulting_record_version=1,
                actor_user_id=actor_user_id,
            )
        )
        db.flush()
        return self._result(row)

    @staticmethod
    def _locked(db: Session, *, organization_id: int, project_id: int, supply_case_id: int) -> SupplyCase:
        row = db.scalar(
            select(SupplyCase)
            .where(
                SupplyCase.id == supply_case_id,
                SupplyCase.organization_id == organization_id,
                SupplyCase.project_id == project_id,
            )
            .with_for_update()
        )
        if row is None:
            raise SupplyDenied("resource_unavailable")
        return row

    def _start_mutation(self, db: Session, *, row: SupplyCase, command: VersionedCommand
                        ) -> tuple[str, SupplyMutationResult | None]:
        _, payload_hash = _canonical_payload(command)
        receipt = db.scalar(
            select(SupplyCommandReceipt).where(
                SupplyCommandReceipt.supply_case_id == row.id,
                SupplyCommandReceipt.command_key == command.command_key,
            )
        )
        if receipt is not None:
            if receipt.payload_hash != payload_hash:
                raise SupplyConflict("idempotency_key_conflict")
            return payload_hash, self._result(
                row,
                already_applied=True,
                status=receipt.result_status,
                version=receipt.resulting_record_version,
            )
        if row.record_version != command.expected_version:
            raise SupplyConflict("record_version_conflict")
        return payload_hash, None

    def _finish_mutation(
        self,
        db: Session,
        *,
        row: SupplyCase,
        actor_user_id: int,
        command: VersionedCommand,
        payload_hash: str,
        event: str,
        evidence: EvidenceLink | None = None,
    ) -> SupplyMutationResult:
        row.record_version += 1
        self._append_version(db, row=row, event=event, actor_user_id=actor_user_id, evidence=evidence)
        db.add(
            SupplyCommandReceipt(
                supply_case_id=row.id,
                organization_id=row.organization_id,
                project_id=row.project_id,
                command_key=command.command_key,
                payload_hash=payload_hash,
                event=event,
                result_status=row.status,
                resulting_record_version=row.record_version,
                actor_user_id=actor_user_id,
            )
        )
        db.flush()
        return self._result(row)

    def create_dds_proposal(
        self,
        db: Session,
        *,
        organization_id: int,
        project_id: int,
        supply_case_id: int,
        actor_user_id: int,
        command: CreateDdsProposal,
    ) -> DdsProposalResult:
        """Create an evidence-backed plan entry, never a payment or provider effect."""
        self._require_role(db, actor_user_id=actor_user_id, project_id=project_id, minimum="editor")
        row = self._locked(
            db, organization_id=organization_id, project_id=project_id, supply_case_id=supply_case_id
        )
        _, payload_hash = _canonical_payload(command)
        receipt = db.scalar(select(SupplyCommandReceipt).where(
            SupplyCommandReceipt.supply_case_id == row.id,
            SupplyCommandReceipt.command_key == command.command_key,
        ))
        if receipt is not None:
            if receipt.payload_hash != payload_hash or not receipt.result_status.startswith("dds:"):
                raise SupplyConflict("idempotency_key_conflict")
            return DdsProposalResult(
                supply_case_id=row.id,
                cash_flow_id=int(receipt.result_status.removeprefix("dds:")),
                supply_record_version=receipt.resulting_record_version,
                already_applied=True,
            )
        if row.record_version != command.expected_version:
            raise SupplyConflict("record_version_conflict")
        if (
            row.review_state != "verified"
            or row.status not in {
                "order_recorded", "partially_delivered", "delivered",
                "act_pending_approval", "partially_accepted", "accepted",
            }
            or not row.order_reference
            or command.contract_id != row.contract_id
            or command.schedule_item_id != row.schedule_item_id
            or command.currency != row.currency
        ):
            raise SupplyConflict("dds_proposal_not_ready")

        budget = db.scalar(select(BudgetLine).where(
            BudgetLine.id == command.budget_line_id,
            BudgetLine.project_id == project_id,
        ).with_for_update())
        if (
            budget is None
            or budget.contract_id != row.contract_id
            or budget.schedule_item_id != row.schedule_item_id
            or budget.currency != row.currency
            or budget.status not in {"approved", "active"}
            or budget.review_status != "confirmed"
        ):
            raise SupplyDenied("resource_unavailable")

        evidence, verified, available = self._validate_evidence(
            db,
            organization_id=organization_id,
            project_id=project_id,
            link=command.evidence,
            require_verified=True,
        )
        if not verified or not available:
            raise SupplyDenied("manual_review_required")
        assessment = db.scalar(select(EvidenceAssessment).where(
            EvidenceAssessment.organization_id == organization_id,
            EvidenceAssessment.evidence_id == str(command.evidence.evidence_id),
            EvidenceAssessment.record_version == command.evidence_assessment_version,
        ))
        if assessment is None:
            raise SupplyDenied("manual_review_required")
        document_version = db.get(DocumentVersion, command.evidence.document_version_id)
        if document_version is None:
            raise SupplyDenied("resource_unavailable")

        cash_flow = CashFlowEntry(
            project_id=project_id,
            contract_id=row.contract_id,
            schedule_item_id=row.schedule_item_id,
            budget_line_id=budget.id,
            task_id=row.task_id,
            source_document_id=document_version.document_id,
            source_document_version_id=document_version.id,
            evidence_id=str(command.evidence.evidence_id),
            evidence_revision=command.evidence.evidence_revision,
            evidence_assessment_version=command.evidence_assessment_version,
            confidence=evidence.confidence,
            review_status="pending_confirmation",
            direction="outflow",
            title=f"Снабжение #{row.id}: {row.title}",
            planned_date=command.planned_date,
            planned_amount=command.amount,
            actual_amount=Decimal("0"),
            counterparty=row.supplier,
            status="proposed",
        )
        db.add(cash_flow)
        db.flush()
        row.record_version += 1
        self._append_version(
            db, row=row, event="dds_proposed", actor_user_id=actor_user_id, evidence=command.evidence
        )
        db.add(SupplyCommandReceipt(
            supply_case_id=row.id,
            organization_id=row.organization_id,
            project_id=row.project_id,
            command_key=command.command_key,
            payload_hash=payload_hash,
            event="dds_proposed",
            result_status=f"dds:{cash_flow.id}",
            resulting_record_version=row.record_version,
            actor_user_id=actor_user_id,
        ))
        db.flush()
        return DdsProposalResult(
            supply_case_id=row.id,
            cash_flow_id=cash_flow.id,
            supply_record_version=row.record_version,
        )

    def review_request(self, db: Session, *, organization_id: int, project_id: int,
                       supply_case_id: int, actor_user_id: int,
                       command: ReviewSupplyRequest) -> SupplyMutationResult:
        self._require_role(db, actor_user_id=actor_user_id, project_id=project_id, minimum="manager")
        row = self._locked(db, organization_id=organization_id, project_id=project_id, supply_case_id=supply_case_id)
        payload_hash, replay = self._start_mutation(db, row=row, command=command)
        if replay:
            return replay
        if row.status != "needs_review":
            raise SupplyConflict("invalid_supply_transition")
        if command.decision == "reject":
            row.status = "request_rejected"
            row.review_state = "rejected"
            event = "request_review_rejected"
        else:
            _, _, available = self._validate_evidence(
                db, organization_id=organization_id, project_id=project_id,
                link=self._case_evidence(row), require_verified=False,
            )
            if not available:
                raise SupplyDenied("manual_review_required")
            if command.corrected_title is not None:
                row.title = command.corrected_title.strip()
            if command.corrected_supplier is not None:
                row.supplier = command.corrected_supplier.strip()
            if command.corrected_quantity is not None:
                row.requested_quantity = command.corrected_quantity
            if command.corrected_unit_price is not None:
                row.unit_price = command.corrected_unit_price
            row.status = "request_pending_approval"
            row.review_state = "verified"
            row.reviewed_by_user_id = actor_user_id
            event = "request_review_confirmed"
        return self._finish_mutation(
            db, row=row, actor_user_id=actor_user_id, command=command, payload_hash=payload_hash, event=event
        )

    def approve_request(self, db: Session, *, organization_id: int, project_id: int,
                        supply_case_id: int, actor_user_id: int,
                        command: VersionedCommand) -> SupplyMutationResult:
        self._require_role(db, actor_user_id=actor_user_id, project_id=project_id, minimum="manager")
        row = self._locked(db, organization_id=organization_id, project_id=project_id, supply_case_id=supply_case_id)
        payload_hash, replay = self._start_mutation(db, row=row, command=command)
        if replay:
            return replay
        if row.status != "request_pending_approval" or row.review_state != "verified":
            raise SupplyConflict("invalid_supply_transition")
        self._require_case_evidence_ready(db, row=row)
        row.status = "request_approved"
        row.request_approved_by_user_id = actor_user_id
        return self._finish_mutation(
            db, row=row, actor_user_id=actor_user_id, command=command,
            payload_hash=payload_hash, event="request_approved",
        )

    def prepare_order(self, db: Session, *, organization_id: int, project_id: int,
                      supply_case_id: int, actor_user_id: int,
                      command: PrepareOrder) -> SupplyMutationResult:
        self._require_role(db, actor_user_id=actor_user_id, project_id=project_id, minimum="editor")
        row = self._locked(db, organization_id=organization_id, project_id=project_id, supply_case_id=supply_case_id)
        payload_hash, replay = self._start_mutation(db, row=row, command=command)
        if replay:
            return replay
        if row.status != "request_approved" or command.ordered_quantity > row.requested_quantity:
            raise SupplyConflict("invalid_supply_transition")
        row.ordered_quantity = command.ordered_quantity
        row.order_reference = command.order_reference.strip()
        row.status = "order_draft"
        return self._finish_mutation(
            db, row=row, actor_user_id=actor_user_id, command=command,
            payload_hash=payload_hash, event="order_prepared",
        )

    def approve_order(self, db: Session, *, organization_id: int, project_id: int,
                      supply_case_id: int, actor_user_id: int,
                      command: VersionedCommand) -> SupplyMutationResult:
        self._require_role(db, actor_user_id=actor_user_id, project_id=project_id, minimum="manager")
        row = self._locked(db, organization_id=organization_id, project_id=project_id, supply_case_id=supply_case_id)
        payload_hash, replay = self._start_mutation(db, row=row, command=command)
        if replay:
            return replay
        if row.status != "order_draft" or row.request_approved_by_user_id is None:
            raise SupplyConflict("invalid_supply_transition")
        self._require_case_evidence_ready(db, row=row)
        row.status = "order_approved"
        row.order_approved_by_user_id = actor_user_id
        return self._finish_mutation(
            db, row=row, actor_user_id=actor_user_id, command=command,
            payload_hash=payload_hash, event="order_approved_internal",
        )

    def record_order(self, db: Session, *, organization_id: int, project_id: int,
                     supply_case_id: int, actor_user_id: int,
                     command: RecordOrder) -> SupplyMutationResult:
        self._require_role(db, actor_user_id=actor_user_id, project_id=project_id, minimum="editor")
        row = self._locked(db, organization_id=organization_id, project_id=project_id, supply_case_id=supply_case_id)
        payload_hash, replay = self._start_mutation(db, row=row, command=command)
        if replay:
            return replay
        if row.status != "order_approved" or row.order_approved_by_user_id is None:
            raise SupplyConflict("invalid_supply_transition")
        self._validate_evidence(
            db, organization_id=organization_id, project_id=project_id,
            link=command.evidence, require_verified=True,
        )
        row.status = "order_recorded"
        return self._finish_mutation(
            db, row=row, actor_user_id=actor_user_id, command=command,
            payload_hash=payload_hash, event="order_recorded", evidence=command.evidence,
        )

    def record_delivery(self, db: Session, *, organization_id: int, project_id: int,
                        supply_case_id: int, actor_user_id: int,
                        command: RecordDelivery) -> SupplyMutationResult:
        self._require_role(db, actor_user_id=actor_user_id, project_id=project_id, minimum="editor")
        row = self._locked(db, organization_id=organization_id, project_id=project_id, supply_case_id=supply_case_id)
        payload_hash, replay = self._start_mutation(db, row=row, command=command)
        if replay:
            return replay
        if row.status not in {"order_recorded", "partially_delivered", "delivered", "partially_accepted"}:
            raise SupplyConflict("invalid_supply_transition")
        self._validate_evidence(
            db, organization_id=organization_id, project_id=project_id,
            link=command.evidence, require_verified=True,
        )
        resulting = row.delivered_quantity + command.delivered_quantity
        if resulting > row.ordered_quantity and command.discrepancy_code is None:
            raise SupplyConflict("delivery_exceeds_order")
        row.delivered_quantity = resulting
        row.discrepancy_code = command.discrepancy_code
        row.discrepancy_note = command.discrepancy_note
        if command.discrepancy_code is not None or resulting > row.ordered_quantity:
            row.status = "delivery_discrepancy"
        elif resulting < row.ordered_quantity:
            row.status = "partially_delivered"
        else:
            row.status = "delivered"
        return self._finish_mutation(
            db, row=row, actor_user_id=actor_user_id, command=command,
            payload_hash=payload_hash, event="delivery_recorded", evidence=command.evidence,
        )

    def resolve_discrepancy(self, db: Session, *, organization_id: int, project_id: int,
                            supply_case_id: int, actor_user_id: int,
                            command: ResolveDiscrepancy) -> SupplyMutationResult:
        self._require_role(db, actor_user_id=actor_user_id, project_id=project_id, minimum="manager")
        row = self._locked(db, organization_id=organization_id, project_id=project_id, supply_case_id=supply_case_id)
        payload_hash, replay = self._start_mutation(db, row=row, command=command)
        if replay:
            return replay
        if row.status != "delivery_discrepancy":
            raise SupplyConflict("invalid_supply_transition")
        if row.delivered_quantity > row.ordered_quantity:
            raise SupplyConflict("order_correction_required")
        row.discrepancy_code = None
        row.discrepancy_note = None
        row.status = "delivered" if row.delivered_quantity == row.ordered_quantity else "partially_delivered"
        event = (
            "delivery_discrepancy_accepted_internal"
            if command.decision == "accept_recorded_quantity"
            else "delivery_discrepancy_returned"
        )
        return self._finish_mutation(
            db, row=row, actor_user_id=actor_user_id, command=command, payload_hash=payload_hash, event=event
        )

    def propose_acceptance_act(self, db: Session, *, organization_id: int, project_id: int,
                               supply_case_id: int, actor_user_id: int,
                               command: ProposeAcceptanceAct) -> SupplyMutationResult:
        self._require_role(db, actor_user_id=actor_user_id, project_id=project_id, minimum="editor")
        row = self._locked(db, organization_id=organization_id, project_id=project_id, supply_case_id=supply_case_id)
        payload_hash, replay = self._start_mutation(db, row=row, command=command)
        if replay:
            return replay
        if row.status not in {"partially_delivered", "delivered", "partially_accepted"}:
            raise SupplyConflict("invalid_supply_transition")
        self._validate_evidence(
            db, organization_id=organization_id, project_id=project_id,
            link=command.evidence, require_verified=True,
        )
        available = row.delivered_quantity - row.accepted_quantity
        if command.accepted_quantity > available:
            raise SupplyConflict("acceptance_exceeds_delivery")
        row.pending_acceptance_quantity = command.accepted_quantity
        row.act_number = command.act_number.strip()
        row.status = "act_pending_approval"
        return self._finish_mutation(
            db, row=row, actor_user_id=actor_user_id, command=command,
            payload_hash=payload_hash, event="acceptance_act_proposed", evidence=command.evidence,
        )

    def approve_acceptance_act(self, db: Session, *, organization_id: int, project_id: int,
                               supply_case_id: int, actor_user_id: int,
                               command: VersionedCommand) -> SupplyMutationResult:
        self._require_role(db, actor_user_id=actor_user_id, project_id=project_id, minimum="manager")
        row = self._locked(db, organization_id=organization_id, project_id=project_id, supply_case_id=supply_case_id)
        payload_hash, replay = self._start_mutation(db, row=row, command=command)
        if replay:
            return replay
        if row.status != "act_pending_approval" or row.pending_acceptance_quantity <= 0:
            raise SupplyConflict("invalid_supply_transition")
        self._require_latest_event_evidence(db, row=row)
        row.accepted_quantity += row.pending_acceptance_quantity
        row.pending_acceptance_quantity = Decimal("0")
        row.act_approved_by_user_id = actor_user_id
        row.status = (
            "accepted"
            if row.accepted_quantity == row.delivered_quantity == row.ordered_quantity
            else "partially_accepted"
        )
        return self._finish_mutation(
            db, row=row, actor_user_id=actor_user_id, command=command,
            payload_hash=payload_hash, event="acceptance_act_approved_internal",
        )

    @staticmethod
    def history(db: Session, *, organization_id: int, project_id: int,
                supply_case_id: int) -> list[SupplyCaseVersion]:
        row = db.scalar(
            select(SupplyCase.id).where(
                SupplyCase.id == supply_case_id,
                SupplyCase.organization_id == organization_id,
                SupplyCase.project_id == project_id,
            )
        )
        if row is None:
            raise SupplyDenied("resource_unavailable")
        return list(
            db.scalars(
                select(SupplyCaseVersion)
                .where(SupplyCaseVersion.supply_case_id == supply_case_id)
                .order_by(SupplyCaseVersion.sequence)
            )
        )
