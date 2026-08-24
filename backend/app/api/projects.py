from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.project import Project


router = APIRouter(
    prefix="/projects",
    tags=["projects"],
)


class ProjectCreate(BaseModel):
    name: str


class ProjectUpdate(BaseModel):
    name: str


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


@router.get("/")
def list_projects(
    db: Session = Depends(get_db),
):
    projects = db.scalars(
        select(Project).order_by(Project.id)
    ).all()

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
):
    item = Project(name=project.name)

    db.add(item)
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
):
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
):
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
):
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
