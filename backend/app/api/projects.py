from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.organization_contract import Organization
from app.models.audit_log import AuditLog
from app.core.auth import require_project_role, require_user


router = APIRouter(
    prefix="/projects",
    tags=["projects"],
)


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


@router.get("/")
def list_projects(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    query = select(Project).order_by(Project.id)
    if not user.is_admin:
        query = query.join(ProjectMember).where(ProjectMember.user_id == user.id)
    projects = db.scalars(query).all()

    return {
        "projects": [
            {
                "id": project.id,
                "name": project.name,
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

    db.delete(item)
    db.commit()

    return {
        "deleted": project_id,
    }
