from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.organizations_contracts import (
    _contract_financial_terms,
    _contract_source_text,
    _create_payment_schedule_proposals,
)
from app.core.auth import require_project_role, require_user
from app.core.contract_roles import cash_flow_direction
from app.core.integration_types import StorageObject
from app.database import SessionLocal, get_db
from app.governance_engine import create_governance_items
from app.jobs.queue import enqueue, update_cooperative_progress
from app.models.audit_log import AuditLog
from app.models.contract_document_link import ContractDocumentLink
from app.models.document import Document
from app.models.job import BackgroundJob
from app.models.organization_contract import Contract
from app.models.user import User


router = APIRouter(tags=["contracts"])


class ContractApplicationsRequest(BaseModel):
    document_ids: list[int] = Field(min_length=1, max_length=200)
    role: str = Field(default="application", pattern="^(application|schedule|budget|cash_flow)$")


def _financial_issues(contract: Contract, document: Document, content: str) -> list[dict]:
    terms = _contract_financial_terms(content)
    issues = []
    for field, label in (("amount", "Сумма договора"), ("advance_amount", "Аванс"), ("retention_percent", "Удержание")):
        extracted = terms.get(field)
        current = getattr(contract, field)
        if extracted is not None and current is not None and Decimal(extracted) != Decimal(current):
            issues.append({"document_id": document.id, "document_name": document.name, "field": field,
                           "title": f"{label}: расхождение", "contract_value": str(current),
                           "document_value": str(extracted), "severity": "warning"})
    return issues


@router.post("/projects/{project_id}/contracts/{contract_id}/applications")
@router.post("/projects/{project_id}/contracts/{contract_id}/documents")
def attach_contract_applications(project_id: int, contract_id: int, payload: ContractApplicationsRequest,
                                 db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    contract = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if contract is None:
        raise HTTPException(404, "Contract not found")
    documents = list(db.scalars(select(Document).where(Document.project_id == project_id, Document.id.in_(payload.document_ids))))
    if len(documents) != len(set(payload.document_ids)):
        raise HTTPException(404, "Один или несколько документов проекта не найдены")
    created = 0
    for document in documents:
        existing = db.scalar(select(ContractDocumentLink.id).where(
            ContractDocumentLink.contract_id == contract_id, ContractDocumentLink.document_id == document.id,
        ))
        if not existing and document.id != contract.source_document_id:
            db.add(ContractDocumentLink(project_id=project_id, contract_id=contract_id,
                                        document_id=document.id, role=payload.role))
            created += 1
    db.add(AuditLog(action="contract_documents_attached", entity_type="contract", entity_id=contract_id,
                    details=f"role={payload.role}; documents={len(documents)}; created={created}; originals_changed=false"))
    db.commit()
    return {"contract_id": contract_id, "role": payload.role, "attached": created,
            "documents": len(documents), "originals_changed": False}


@router.post("/projects/{project_id}/contracts/{contract_id}/analyze-package")
def analyze_contract_package(project_id: int, contract_id: int, db: Session = Depends(get_db),
                             user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    contract = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if contract is None:
        raise HTTPException(404, "Contract not found")
    application_ids = list(db.scalars(select(ContractDocumentLink.document_id).where(
        ContractDocumentLink.project_id == project_id, ContractDocumentLink.contract_id == contract_id,
    )))
    document_ids = list(dict.fromkeys([contract.source_document_id, *application_ids])) if contract.source_document_id else application_ids
    documents = list(db.scalars(select(Document).where(Document.project_id == project_id, Document.id.in_(document_ids))))
    issues: list[dict] = []
    financial_entries = 0
    governance_document_ids: list[int] = []
    for document in documents:
        content = _contract_source_text(document, db)
        if not content:
            issues.append({"document_id": document.id, "document_name": document.name,
                           "title": "Текст не извлечён", "severity": "error"})
            continue
        issues.extend(_financial_issues(contract, document, content))
        financial_entries += len(_create_payment_schedule_proposals(db, contract, document, content))
        governance_document_ids.append(document.id)
    direction = cash_flow_direction(contract.contract_kind)
    db.add(AuditLog(action="contract_package_analyzed", entity_type="contract", entity_id=contract_id,
                    details=f"documents={len(documents)}; issues={len(issues)}; financial={financial_entries}; direction={direction or 'context'}"))
    db.commit()

    # §11 (async UX): financial issues/schedule proposals above are regex,
    # stay synchronous. create_governance_items is the part that can now
    # take seconds per document (LLM extraction) -- deferred to a job. See
    # app/jobs/handlers.py:"contract_package.analyze_governance".
    governance_job_id = governance_status = None
    governance_already_running = False
    active_job = db.scalar(select(BackgroundJob).where(
        BackgroundJob.kind == "contract_package.analyze_governance",
        BackgroundJob.status.in_(("queued", "retrying", "running")),
        BackgroundJob.payload["contract_id"].as_integer() == contract_id,
    ).order_by(BackgroundJob.id.desc()))
    if active_job is not None:
        governance_job_id, governance_status, governance_already_running = active_job.id, active_job.status, True
    elif governance_document_ids:
        job = enqueue(db, "contract_package.analyze_governance", {
            "project_id": project_id, "contract_id": contract_id,
            "document_ids": governance_document_ids, "source_type": "contract_application",
        })
        job.payload = {**dict(job.payload or {}), "job_id": job.id}
        db.commit()
        governance_job_id, governance_status = job.id, job.status

    return {"contract_id": contract_id, "documents": len(documents), "applications": len(application_ids),
            "issues": issues, "issue_count": len(issues), "financial_entries": financial_entries,
            "financial_direction": direction, "risks": None, "decisions": None,
            "governance_job_id": governance_job_id, "governance_status": governance_status,
            "governance_already_running": governance_already_running,
            "payments_confirmed": False, "originals_changed": False}


def _analyze_governance_job(payload: dict) -> dict:
    """Background handler for "contract_package.analyze_governance" -- see app/jobs/handlers.py.

    Re-extracts each document's text (cheap, local -- DocumentVersion.content)
    and runs the exact create_governance_items call analyze_contract_package
    used to run inline, one document at a time.
    """
    project_id = int(payload["project_id"])
    contract_id = int(payload["contract_id"])
    document_ids = [int(value) for value in payload["document_ids"]]
    source_type = payload.get("source_type", "contract_application")
    job_id = payload.get("job_id")
    risks = decisions = 0
    with SessionLocal() as db:
        documents = list(db.scalars(select(Document).where(
            Document.project_id == project_id, Document.id.in_(document_ids),
        )))
        total = len(documents)
        cancelled = _package_job_control(db, job_id, completed=0, total=total)
        for index, document in enumerate(documents):
            if cancelled or _package_job_control(db, job_id, completed=index, total=total, document_id=document.id):
                cancelled = True
                break
            content = _contract_source_text(document, db)
            if not content:
                continue
            source = StorageObject(id=document.external_id or f"document:{document.id}", name=document.name,
                                   mime_type=document.mime_type or "application/octet-stream",
                                   parent_id=document.parent_external_id or "contracts", content_text=content)
            created_risks, created_decisions = create_governance_items(db, project_id, [source], source_type=source_type)
            risks += len(created_risks); decisions += len(created_decisions)
        _package_job_control(db, job_id, completed=total, total=total)
        db.add(AuditLog(action="contract_package_governance_analyzed", entity_type="contract", entity_id=contract_id,
                        details=f"documents={total}; risks={risks}; decisions={decisions}; cancelled={cancelled}"))
        db.commit()
    return {"risks": risks, "decisions": decisions, "documents": total, "cancelled": cancelled}


def _package_job_control(db: Session, job_id: int | None, *, completed: int, total: int, document_id: int | None = None) -> bool:
    """Publish bounded progress and read cooperative cancellation -- same shape as ocr_batch._job_control."""
    if job_id is None:
        return False
    percent = round((completed / total) * 100) if total else 100
    _, cancel_requested = update_cooperative_progress(
        db, job_id, percent, {"completed": completed, "total": total, "percent": percent, "document_id": document_id},
    )
    return cancel_requested


@router.get("/projects/{project_id}/contracts/{contract_id}/analyze-package/{job_id}")
def get_contract_package_analysis_job(project_id: int, contract_id: int, job_id: int,
                                      db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    job = db.get(BackgroundJob, job_id)
    if (job is None or job.kind != "contract_package.analyze_governance"
            or int((job.payload or {}).get("contract_id", -1)) != contract_id):
        raise HTTPException(404, "Governance analysis job not found")
    result = dict(job.result or {})
    effective_status = "cancelled" if result.get("cancelled") else job.status
    return {
        "job_id": job.id,
        "status": "succeeded" if effective_status == "completed" else effective_status,
        "progress": job.progress, "result": job.result, "attempts": job.attempts,
        "duration_ms": job.duration_ms,
        "error": job.last_error if job.status in {"failed", "dead_letter"} else None,
        "created_at": job.created_at, "updated_at": job.updated_at,
    }
