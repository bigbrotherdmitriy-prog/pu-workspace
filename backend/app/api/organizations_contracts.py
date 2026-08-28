from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_admin, require_project_role, require_user
from app.database import get_db
from app.models.organization_contract import Contract, Organization
from app.models.document import Document
from app.models.execution_finance import ScheduleBaseline
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
    return {"contracts": [_contract(row) for row in rows]}


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
    return _contract(row)


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
    return _contract(row)


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


def _contract(row: Contract) -> dict:
    return {
        "id": row.id, "project_id": row.project_id, "number": row.number,
        "title": row.title, "counterparty": row.counterparty,
        "signed_at": row.signed_at, "status": row.status,
        "source_document_id": row.source_document_id, "notes": row.notes,
    }
