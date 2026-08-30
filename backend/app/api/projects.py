from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.organization_contract import Organization
from app.models.audit_log import AuditLog
from app.models.organizer import OrganizerSession
from app.models.document import Document
from app.models.organization_contract import Contract
from app.models.execution_finance import BudgetLine, CashFlowEntry, ScheduleItem
from app.models.project_contact import ProjectContact
from app.models.ai_secretary import Message
from app.core.auth import require_project_role, require_user
from app.organizer import get_drive_service
from app.organizer_engine.drive import DriveClient


router = APIRouter(
    prefix="/projects",
    tags=["projects"],
)


LAUNCH_READY_SOURCE_STATUSES = frozenset({"proposed", "applied", "ready", "completed"})


def _source_session_ready(session: OrganizerSession) -> bool:
    """A source is ready only after a safe copy exists and scanning has finished."""
    return bool(session.copy_folder_id) and session.status in LAUNCH_READY_SOURCE_STATUSES


class ProjectCreate(BaseModel):
    name: str
    organization_id: int | None = None


class ProjectUpdate(BaseModel):
    name: str


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    organization_id: int
    archived_at: datetime | None = None


class SafeCopyCleanup(BaseModel):
    confirmation: str


@router.get("/")
def list_projects(
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    query = select(Project).order_by(Project.id)
    if not include_archived:
        query = query.where(Project.archived_at.is_(None))
    if not user.is_admin:
        query = query.join(ProjectMember).where(ProjectMember.user_id == user.id)
    projects = db.scalars(query).all()

    return {
        "projects": [
            {
                "id": project.id,
                "name": project.name,
                "archived_at": project.archived_at,
            }
            for project in projects
        ]
    }


@router.post(
    "/",
    response_model=ProjectResponse,
)
def create_project(
    project: ProjectCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    organization_id = project.organization_id or db.scalar(select(Organization.id).order_by(Organization.id).limit(1))
    if organization_id is None or db.get(Organization, organization_id) is None:
        raise HTTPException(422, "Organization is required")
    item = Project(name=project.name, organization_id=organization_id)

    db.add(item)
    db.flush()
    db.add(ProjectMember(project_id=item.id, user_id=user.id, role="owner"))
    db.add(AuditLog(action="project_created", entity_type="project", entity_id=item.id, details=f"Project: {item.name}"))
    db.commit()
    db.refresh(item)

    return item


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    item = db.get(Project, project_id)

    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    return item


@router.get("/{project_id}/launch-readiness")
def project_launch_readiness(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Provider-neutral readiness of the minimum project management chain."""
    require_project_role(db, user, project_id, "viewer")
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")

    documents = db.scalar(select(func.count(Document.id)).where(Document.project_id == project_id)) or 0
    analyzed_documents = db.scalar(select(func.count(Document.id)).where(
        Document.project_id == project_id,
        Document.status.in_({"analyzed", "indexed", "ready"}),
    )) or 0
    contracts = list(db.scalars(select(Contract).where(Contract.project_id == project_id)))
    schedule_rows = db.scalar(select(func.count(ScheduleItem.id)).where(ScheduleItem.project_id == project_id)) or 0
    budget_rows = db.scalar(select(func.count(BudgetLine.id)).where(BudgetLine.project_id == project_id)) or 0
    cash_flow_rows = db.scalar(select(func.count(CashFlowEntry.id)).where(CashFlowEntry.project_id == project_id)) or 0
    contacts = db.scalar(select(func.count(ProjectContact.id)).where(
        ProjectContact.project_id == project_id, ProjectContact.active.is_(True),
    )) or 0
    confirmed_contacts = db.scalar(select(func.count(ProjectContact.id)).where(
        ProjectContact.project_id == project_id,
        ProjectContact.active.is_(True),
        ProjectContact.confirmed.is_(True),
    )) or 0
    inbox_messages = db.scalar(select(func.count(Message.id)).where(Message.project_id == project_id)) or 0
    sources = list(db.scalars(select(OrganizerSession).where(
        OrganizerSession.project_id == project_id,
    ).order_by(OrganizerSession.id.desc())))

    result = {
        "project_id": project_id,
        "project_name": project.name,
        "source_folders": len({row.source_folder_id for row in sources if row.source_folder_id}),
        "source_ready": any(_source_session_ready(row) for row in sources),
        "documents": documents,
        "analyzed_documents": analyzed_documents,
        "contracts": len(contracts),
        "linked_contracts": sum(bool(row.source_document_id) for row in contracts),
        "schedule_rows": schedule_rows,
        "budget_rows": budget_rows,
        "cash_flow_rows": cash_flow_rows,
        "contacts": contacts,
        "confirmed_contacts": confirmed_contacts,
        "inbox_messages": inbox_messages,
    }
    steps = [
        {"id": "source", "complete": result["source_ready"] and documents > 0},
        {"id": "documents", "complete": analyzed_documents > 0},
        {"id": "contract", "complete": bool(contracts) and result["linked_contracts"] == len(contracts)},
        {"id": "finance", "complete": schedule_rows > 0 and budget_rows > 0 and cash_flow_rows > 0},
        {"id": "contacts", "complete": confirmed_contacts > 0},
    ]
    completed = sum(step["complete"] for step in steps)
    return {
        **result,
        "ready": completed == len(steps),
        "steps": steps,
        "completed_steps": completed,
        "total_steps": len(steps),
        "progress": completed * 20,
    }


@router.put(
    "/{project_id}",
    response_model=ProjectResponse,
)
def update_project(
    project_id: int,
    project: ProjectUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    item = db.get(Project, project_id)

    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    item.name = project.name

    db.commit()
    db.refresh(item)

    return item


@router.delete("/{project_id}")
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "owner")
    item = db.get(Project, project_id)

    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    item.archived_at = datetime.now(timezone.utc)
    db.add(AuditLog(action="project_archived", entity_type="project", entity_id=item.id, details=f"Project: {item.name}"))
    db.commit()

    return {
        "archived": project_id,
    }


@router.post("/{project_id}/restore")
def restore_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "owner")
    item = db.get(Project, project_id)
    if item is None:
        raise HTTPException(404, "Project not found")
    item.archived_at = None
    db.add(AuditLog(action="project_restored", entity_type="project", entity_id=item.id, details=f"Project: {item.name}"))
    db.commit()
    return {"restored": project_id}


def _safe_copy_sessions(db: Session, project_id: int):
    rows = db.scalars(select(OrganizerSession).where(
        OrganizerSession.project_id == project_id,
        OrganizerSession.copy_folder_id.is_not(None),
    ).order_by(OrganizerSession.id.desc())).all()
    seen: set[str] = set()
    result = []
    for row in rows:
        copy_id = row.copy_folder_id or ""
        if not copy_id or copy_id in seen or copy_id in {"manual", row.source_folder_id} or copy_id.startswith("virtual:"):
            continue
        seen.add(copy_id)
        result.append(row)
    return result


@router.get("/{project_id}/safe-copies")
def safe_copy_summary(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "owner")
    return {"count": len(_safe_copy_sessions(db, project_id)), "recoverable": True, "originals_affected": False}


@router.post("/{project_id}/safe-copies/trash")
def trash_safe_copies(project_id: int, payload: SafeCopyCleanup,
                      db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "owner")
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    if payload.confirmation != project.name:
        raise HTTPException(422, "Введите точное название проекта для подтверждения")
    rows = _safe_copy_sessions(db, project_id)
    drive = DriveClient(get_drive_service(project_id=project_id, db=db))
    trashed = 0
    for row in rows:
        drive.trash_safe_copy(row.copy_folder_id)
        row.copy_folder_id = None
        row.copy_folder_name = None
        trashed += 1
    db.add(AuditLog(action="project_safe_copies_trashed", entity_type="project", entity_id=project_id,
                    details=f"count={trashed}; originals_affected=false"))
    db.commit()
    return {"trashed": trashed, "recoverable": True, "originals_affected": False}
