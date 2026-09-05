from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.organizations_contracts import (
    _contract_financial_terms,
    _contract_source_text,
    _create_payment_schedule_proposals,
    _ensure_contract_baseline,
    _record_contract_version,
)
from app.core.auth import require_project_role, require_user
from app.core.contract_roles import cash_flow_direction
from app.core.integration_types import StorageObject
from app.database import get_db
from app.governance_engine import create_governance_items
from app.models.audit_log import AuditLog
from app.models.contract_document_link import ContractDocumentLink
from app.models.document import Document
from app.models.organization_contract import Contract
from app.models.user import User


router = APIRouter(tags=["contracts"])


class ContractApplicationsRequest(BaseModel):
    expected_record_version: int = Field(gt=0)
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
    if contract.record_version != payload.expected_record_version:
        raise HTTPException(409, "Договор уже изменён другим пользователем. Обновите карточку и повторите действие")
    documents = list(db.scalars(select(Document).where(Document.project_id == project_id, Document.id.in_(payload.document_ids))))
    if len(documents) != len(set(payload.document_ids)):
        raise HTTPException(404, "Один или несколько документов проекта не найдены")
    _ensure_contract_baseline(db, contract, actor_user_id=user.id)
    created = 0
    for document in documents:
        existing = db.scalar(select(ContractDocumentLink.id).where(
            ContractDocumentLink.contract_id == contract_id, ContractDocumentLink.document_id == document.id,
        ))
        if not existing and document.id != contract.source_document_id:
            db.add(ContractDocumentLink(project_id=project_id, contract_id=contract_id,
                                        document_id=document.id, role=payload.role))
            created += 1
    if created:
        next_version = contract.record_version + 1
        result = db.execute(update(Contract).where(
            Contract.id == contract_id,
            Contract.project_id == project_id,
            Contract.record_version == payload.expected_record_version,
        ).values(record_version=next_version))
        if result.rowcount != 1:
            raise HTTPException(409, "Договор уже изменён другим пользователем. Обновите карточку и повторите действие")
        db.flush(); db.expire(contract); db.refresh(contract)
        _record_contract_version(
            db, contract, event="linked", changed_fields={"linked_document_ids"}, actor_user_id=user.id,
        )
    db.add(AuditLog(action="contract_documents_attached", entity_type="contract", entity_id=contract_id,
                    details=f"role={payload.role}; documents={len(documents)}; created={created}; version={contract.record_version}; originals_changed=false"))
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
    financial_entries = risks = decisions = 0
    for document in documents:
        content = _contract_source_text(document, db)
        if not content:
            issues.append({"document_id": document.id, "document_name": document.name,
                           "title": "Текст не извлечён", "severity": "error"})
            continue
        issues.extend(_financial_issues(contract, document, content))
        financial_entries += len(_create_payment_schedule_proposals(db, contract, document, content))
        source = StorageObject(id=document.external_id or f"document:{document.id}", name=document.name,
                               mime_type=document.mime_type or "application/octet-stream",
                               parent_id=document.parent_external_id or "contracts", content_text=content)
        created_risks, created_decisions = create_governance_items(db, project_id, [source], source_type="contract_application")
        risks += len(created_risks); decisions += len(created_decisions)
    direction = cash_flow_direction(contract.contract_kind)
    db.add(AuditLog(action="contract_package_analyzed", entity_type="contract", entity_id=contract_id,
                    details=f"documents={len(documents)}; issues={len(issues)}; financial={financial_entries}; direction={direction or 'context'}"))
    db.commit()
    return {"contract_id": contract_id, "documents": len(documents), "applications": len(application_ids),
            "issues": issues, "issue_count": len(issues), "financial_entries": financial_entries,
            "financial_direction": direction, "risks": risks, "decisions": decisions,
            "payments_confirmed": False, "originals_changed": False}
