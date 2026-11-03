"""Bind a queued create to its expected account, never choose its author."""
from fastapi import HTTPException
from app.models.user import User


def require_create_owner(actor: User, expected_user_id: int) -> None:
    actual = getattr(actor, "id", None)
    if type(actual) is not int or actual <= 0:
        raise HTTPException(401, "Authenticated actor required")
    if type(expected_user_id) is not int or expected_user_id <= 0:
        raise HTTPException(422, "Expected queue owner required")
    if expected_user_id != actual:
        raise HTTPException(403, "Сеанс пользователя изменился: войдите исходной учётной записью")
