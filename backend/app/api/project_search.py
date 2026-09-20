from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.database import get_db
from app.models.user import User
from app.mvp3.project_search import (
    SearchDenied,
    SearchFilters,
    SearchUnavailable,
    SearchValidationError,
    project_search,
)


router = APIRouter(prefix="/project-search", tags=["project-search"])


@router.get("")
def search_project(
    project_id: int = Query(ge=1),
    q: str | None = Query(default=None, max_length=200),
    types: str | None = Query(default=None, max_length=200),
    date_from: date | None = None,
    date_to: date | None = None,
    contract_id: int | None = Query(default=None, ge=1),
    counterparty: str | None = Query(default=None, max_length=200),
    cursor: str | None = Query(default=None, max_length=768),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    try:
        filters = SearchFilters(
            query=q,
            types=tuple(part.strip() for part in types.split(",") if part.strip()) if types else (),
            date_from=date_from,
            date_to=date_to,
            contract_id=contract_id,
            counterparty=counterparty,
        )
        return project_search(
            db,
            project_id=project_id,
            actor_user_id=user.id,
            filters=filters,
            limit=limit,
            cursor=cursor,
        )
    except SearchDenied as exc:
        # Absence and lack of explicit membership are deliberately
        # indistinguishable to avoid project enumeration across tenants.
        raise HTTPException(404, "Search scope not found") from exc
    except SearchUnavailable as exc:
        raise HTTPException(503, "Project search is not configured") from exc
    except SearchValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
