from datetime import date
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.auth import require_admin, require_project_role, require_user
from app.database import get_db
from app.models.organization_contract import Contract, Organization
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.governance import Decision, Risk
from app.models.management import Obligation
from app.models.task import Task
from app.models.execution_finance import ScheduleBaseline
from app.core.integration_types import StorageObject
from app.governance_engine import create_governance_items
from app.task_engine import create_tasks_from_files
from sqlalchemy import func
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.audit_log import AuditLog

router = APIRouter(tags=["organizations", "contracts"])


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class ContractCreate(BaseModel):
    number: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    counterparty: str | None = Field(default=None, max_length=500)
    signed_at: date | None = None
    status: str = Field(default="active", pattern="^(draft|active|completed|terminated)$")
    source_document_id: int | None = None
    notes: str | None = Field(default=None, max_length=5000)


class ContractLinkUpdate(BaseModel):
    source_document_id: int | None = None


def _normalized(value: str | None) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", " ", (value or "").casefold()).strip()


def _contract_document_score(row: Contract, document: Document, content: str) -> tuple[int, list[str]]:
    """Rank a possible contract source without mutating either record."""
    name = _normalized(document.name)
    body = _normalized(content[:120_000])
    haystack = f"{name} {body}"
    score = 0
    reasons: list[str] = []

    number = _normalized(row.number)
    compact_number = re.sub(r"\s+", "", number)
    compact_haystack = re.sub(r"\s+", "", haystack)
    if compact_number and len(compact_number) >= 4 and compact_number in compact_haystack:
        score += 60
        reasons.append("совпадает номер договора")

    counterparty = _normalized(row.counterparty)
    if counterparty and len(counterparty) >= 4 and counterparty in haystack:
        score += 25
        reasons.append("совпадает контрагент")

    title_tokens = {
        token for token in _normalized(row.title).split()
        if len(token) >= 5 and token not in {"выполнению", "работ", "системы", "области"}
    }
    matched = sorted(token for token in title_tokens if token in haystack)
    if matched:
        score += min(30, len(matched) * 5)
        reasons.append(f"совпадает предмет договора: {', '.join(matched[:3])}")

    contract_markers = ("договор", "контракт", "государственн контракт", "заказчик", "подрядчик")
    marker_count = sum(1 for marker in contract_markers if marker in haystack)
    if marker_count:
        score += min(15, marker_count * 4)
        reasons.append("обнаружены реквизиты договора")

    if any(marker in name for marker in ("договор", "контракт", "гк ", "гк-")):
        score += 15
        reasons.append("название похоже на договор")
    if any(marker in name for marker in ("приложение", "график", "акт", "письмо", "счет", "счёт")):
        score -= 12
        reasons.append("возможно приложение или связанный документ")
    return max(0, min(score, 100)), reasons


@router.get("/organizations")
def list_organizations(db: Session = Depends(get_db), user: User = Depends(require_user)):
    query = select(Organization).order_by(Organization.id)
    if not user.is_admin:
        query = query.join(Project, Project.organization_id == Organization.id).join(ProjectMember).where(ProjectMember.user_id == user.id).distinct()
    rows = db.scalars(query).all()
    return {"organizations": [{"id": row.id, "name": row.name} for row in rows]}


@router.post("/organizations")
def create_organization(payload: OrganizationCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    row = Organization(name=payload.name.strip())
    db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "name": row.name}


@router.get("/projects/{project_id}/contracts")
def list_contracts(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    rows = db.scalars(select(Contract).where(Contract.project_id == project_id).order_by(Contract.id.desc())).all()
    return {"contracts": [_contract(row, db) for row in rows]}


@router.post("/projects/{project_id}/contracts")
def create_contract(project_id: int, payload: ContractCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    if db.get(Project, project_id) is None:
        raise HTTPException(404, "Project not found")
    row = Contract(project_id=project_id, **payload.model_dump())
    db.add(row); db.flush()
    version = (db.scalar(select(func.max(ScheduleBaseline.version)).where(ScheduleBaseline.project_id == project_id)) or 0) + 1
    db.add(ScheduleBaseline(
        project_id=project_id, contract_id=row.id, created_by_user_id=user.id,
        name=f"ГПР по договору {row.number}", version=version,
        note="Автоматически создано при добавлении договора; заполните этапы и сроки.",
    ))
    db.add(AuditLog(action="contract_created", entity_type="contract", entity_id=row.id, details=f"Contract: {row.number}"))
    db.commit(); db.refresh(row)
    return _contract(row, db)


@router.patch("/projects/{project_id}/contracts/{contract_id}")
def update_contract_links(project_id: int, contract_id: int, payload: ContractLinkUpdate,
                          db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    row = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if row is None:
        raise HTTPException(404, "Contract not found")
    if payload.source_document_id is not None and not db.scalar(select(Document.id).where(
        Document.id == payload.source_document_id, Document.project_id == project_id,
    )):
        raise HTTPException(422, "Документ не принадлежит выбранному проекту")
    row.source_document_id = payload.source_document_id
    db.add(AuditLog(action="contract_source_linked", entity_type="contract", entity_id=row.id,
                    details=f"document={row.source_document_id or 'none'}"))
    db.commit(); db.refresh(row)
    return _contract(row, db)


def _contract_source_text(document: Document, db: Session | None = None) -> str:
    parts = [part.strip() for part in (document.summary, document.notes) if part and part.strip()]
    if db is not None:
        latest = db.scalar(select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
        ).order_by(DocumentVersion.version_number.desc()))
        if latest and latest.content and latest.content.strip():
            parts.append(latest.content.strip())
    return "\n".join(dict.fromkeys(parts))


@router.get("/projects/{project_id}/contracts/{contract_id}/source-candidates")
def contract_source_candidates(project_id: int, contract_id: int,
                               db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Return explainable, read-only source suggestions for a contract."""
    require_project_role(db, user, project_id, "viewer")
    row = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if row is None:
        raise HTTPException(404, "Contract not found")
    documents = db.execute(select(Document, DocumentVersion.content).outerjoin(
        DocumentVersion,
        and_(
            DocumentVersion.document_id == Document.id,
            DocumentVersion.version_number == Document.current_version,
        ),
    ).where(Document.project_id == project_id).order_by(Document.id.desc())).all()
    candidates = []
    for document, extracted_content in documents:
        content = "\n".join(dict.fromkeys(
            part.strip() for part in (document.summary, document.notes, extracted_content)
            if part and part.strip()
        ))
        score, reasons = _contract_document_score(row, document, content)
        candidates.append({
            "document_id": document.id,
            "name": document.name,
            "source": document.source,
            "mime_type": document.mime_type,
            "score": score,
            "reasons": reasons,
            "text_ready": bool(content),
        })
    candidates.sort(key=lambda item: (item["score"], item["text_ready"], item["document_id"]), reverse=True)
    return {
        "contract_id": row.id,
        "recommended_document_id": candidates[0]["document_id"] if candidates and candidates[0]["score"] >= 50 else None,
        "candidates": candidates[:100],
    }


@router.post("/projects/{project_id}/contracts/{contract_id}/analyze")
def analyze_contract(project_id: int, contract_id: int,
                     db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Extract proposed controls from the linked contract without changing its source document."""
    require_project_role(db, user, project_id, "editor")
    row = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if row is None:
        raise HTTPException(404, "Contract not found")
    if row.source_document_id is None:
        raise HTTPException(409, "Сначала выберите документ договора")
    document = db.scalar(select(Document).where(
        Document.id == row.source_document_id, Document.project_id == project_id,
    ))
    if document is None:
        raise HTTPException(404, "Contract source document not found")
    content = _contract_source_text(document, db)
    if not content:
        raise HTTPException(409, "Документ ещё не проанализирован. Сначала завершите анализ рабочей папки")
    source_id = document.external_id or f"document:{document.id}"
    source = StorageObject(
        id=source_id, name=document.name, mime_type=document.mime_type or "application/octet-stream",
        parent_id=document.parent_external_id or "contracts", content_text=content,
        object_type="file", provider=document.source,
    )
    created_tasks = create_tasks_from_files(db, project_id, None, [source], source_type="contract_analysis")
    created_risks, created_decisions = create_governance_items(
        db, project_id, [source], source_type="contract_analysis",
    )
    task_ids = list(db.scalars(select(Task.id).where(
        Task.project_id == project_id, Task.source_file_id == source_id,
    )))
    linked_obligations = list(db.scalars(select(Obligation).where(
        Obligation.project_id == project_id, Obligation.task_id.in_(task_ids) if task_ids else False,
    )))
    for obligation in linked_obligations:
        obligation.contract_id = row.id
    db.add(AuditLog(
        action="contract_analyzed", entity_type="contract", entity_id=row.id,
        details=(f"document={document.id}; tasks_created={len(created_tasks)}; "
                 f"obligations_linked={len(linked_obligations)}; risks_created={len(created_risks)}; "
                 f"decisions_created={len(created_decisions)}; originals_changed=false"),
    ))
    db.commit()
    result = _contract(row, db)
    result["created"] = {
        "tasks": len(created_tasks), "risks": len(created_risks), "decisions": len(created_decisions),
    }
    return result


@router.post("/projects/{project_id}/contracts/{contract_id}/initialize-control")
def initialize_contract_control(project_id: int, contract_id: int,
                                db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Create the missing GPR anchor after an explicit user action; safe to repeat."""
    require_project_role(db, user, project_id, "editor")
    row = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project_id))
    if row is None:
        raise HTTPException(404, "Contract not found")
    baseline = db.scalar(select(ScheduleBaseline).where(
        ScheduleBaseline.project_id == project_id,
        ScheduleBaseline.contract_id == contract_id,
    ).order_by(ScheduleBaseline.version.desc()))
    created = baseline is None
    if created:
        version = (db.scalar(select(func.max(ScheduleBaseline.version)).where(
            ScheduleBaseline.project_id == project_id,
        )) or 0) + 1
        baseline = ScheduleBaseline(
            project_id=project_id,
            contract_id=contract_id,
            created_by_user_id=user.id,
            name=f"ГПР по договору {row.number}",
            version=version,
            note="Создано по команде пользователя; заполните этапы и сроки.",
        )
        db.add(baseline)
        db.flush()
        db.add(AuditLog(
            action="contract_control_initialized",
            entity_type="contract",
            entity_id=row.id,
            details=f"baseline={baseline.id}",
        ))
        db.commit()
        db.refresh(baseline)
    return {"created": created, "baseline_id": baseline.id, "contract_id": contract_id}


def _contract(row: Contract, db: Session | None = None) -> dict:
    result = {
        "id": row.id, "project_id": row.project_id, "number": row.number,
        "title": row.title, "counterparty": row.counterparty,
        "signed_at": row.signed_at, "status": row.status,
        "source_document_id": row.source_document_id, "notes": row.notes,
    }
    if db is not None:
        document = db.get(Document, row.source_document_id) if row.source_document_id else None
        source_id = (document.external_id or f"document:{document.id}") if document else None
        task_ids = list(db.scalars(select(Task.id).where(
            Task.project_id == row.project_id, Task.source_file_id == source_id,
        ))) if source_id else []
        result["analysis"] = {
            "source_ready": bool(document and _contract_source_text(document, db)),
            "tasks": len(task_ids),
            "obligations": db.scalar(select(func.count(Obligation.id)).where(
                Obligation.project_id == row.project_id, Obligation.contract_id == row.id,
            )) or 0,
            "risks": db.scalar(select(func.count(Risk.id)).where(
                Risk.project_id == row.project_id, Risk.source_id == source_id,
            )) or 0 if source_id else 0,
            "decisions": db.scalar(select(func.count(Decision.id)).where(
                Decision.project_id == row.project_id, Decision.source_id == source_id,
            )) or 0 if source_id else 0,
        }
    return result
