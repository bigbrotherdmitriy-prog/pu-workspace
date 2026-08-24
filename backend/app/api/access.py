from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


router = APIRouter(
    prefix="/projects",
    tags=["project-access"],
)


class MemberCreate(BaseModel):
    user_id: int
    role: str = "member"


@router.post("/{project_id}/members")
def add_project_member(
    project_id: int,
    payload: MemberCreate,
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    user = db.get(User, payload.user_id)

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    member = ProjectMember(
        project_id=project_id,
        user_id=payload.user_id,
        role=payload.role,
    )

    db.add(member)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()

        raise HTTPException(
            status_code=409,
            detail="User already belongs to project",
        )

    db.refresh(member)

    return {
        "id": member.id,
        "project_id": member.project_id,
        "user_id": member.user_id,
        "role": member.role,
    }


@router.get("/{project_id}/members")
def list_project_members(
    project_id: int,
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    rows = db.execute(
        select(
            ProjectMember.id,
            ProjectMember.role,
            User.id.label("user_id"),
            User.name,
            User.email,
        )
        .join(
            User,
            User.id == ProjectMember.user_id,
        )
        .where(
            ProjectMember.project_id == project_id
        )
        .order_by(ProjectMember.id)
    ).all()

    return {
        "members": [
            {
                "membership_id": row.id,
                "user_id": row.user_id,
                "name": row.name,
                "email": row.email,
                "role": row.role,
            }
            for row in rows
        ]
    }
