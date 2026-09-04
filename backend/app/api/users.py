from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.core.auth import hash_password, require_admin


router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(require_admin)],
)


class UserCreate(BaseModel):
    name: str
    email: str
    password: str = Field(min_length=12, max_length=256)


@router.get("/")
def list_users(
    db: Session = Depends(get_db),
):
    users = db.scalars(
        select(User).order_by(User.id)
    ).all()

    return {
        "users": [
            {
                "id": user.id,
                "name": user.name,
                "email": user.email,
            }
            for user in users
        ]
    }


@router.post("/")
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
):
    user = User(
        name=payload.name,
        email=payload.email.strip().lower(),
        password_hash=hash_password(payload.password),
    )

    db.add(user)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()

        raise HTTPException(
            status_code=409,
            detail="Email already exists",
        )

    db.refresh(user)

    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
    }
