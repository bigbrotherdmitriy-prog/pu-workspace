from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.project import Project
from app.models.search import SavedSearchView
from app.models.user import User
from app.mvp3.search import (
    SearchDenied,
    SearchFilters,
    SearchValidationError,
    create_saved_view,
    delete_saved_view,
    get_saved_view_history,
    list_saved_views,
    project_search,
    update_saved_view,
)


router = APIRouter(prefix="/api/search", tags=["project-search"])


class FilterPayload(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    types: list[str] = Field(default_factory=list, max_length=8)
    date_from: date | None = None
    date_to: date | None = None
    contract_id: int | None = Field(default=None, ge=1)
    counterparty: str | None = Field(default=None, max_length=200)


class SavedViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    filters: FilterPayload


class SavedViewUpdate(SavedViewCreate):
    expected_version: int = Field(ge=1)


def _filters(payload: FilterPayload) -> SearchFilters:
    try:
        return SearchFilters.from_mapping(payload.model_dump(mode="json"))
    except SearchValidationError as exc:
        raise HTTPException(422, str(exc)) from exc


def _scope(db: Session, user: User, project_id: int, role: str = "viewer") -> Project:
    # Membership is intentionally required even for a global admin: search is a
    # project data capability, not an implicit cross-tenant administrator grant.
    require_project_role(db, user, project_id, role)
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


def _view_payload(row: SavedSearchView) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "name": row.name,
        "filters": row.filters,
        "record_version": row.record_version,
        "state": row.state,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _service_error(exc: Exception):
    if isinstance(exc, SearchDenied):
        raise HTTPException(404, "Search resource not found") from exc
    status = 409 if str(exc) == "version_conflict" else 422
    raise HTTPException(status, str(exc)) from exc


@router.get("/projects/{project_id}")
def search_project(
    project_id: int,
    query: str | None = Query(default=None, max_length=200),
    types: str | None = Query(default=None, max_length=200),
    date_from: date | None = None,
    date_to: date | None = None,
    contract_id: int | None = Query(default=None, ge=1),
    counterparty: str | None = Query(default=None, max_length=200),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    project = _scope(db, user, project_id)
    try:
        filters = SearchFilters(
            query=query,
            types=tuple(part.strip() for part in types.split(",") if part.strip()) if types else (),
            date_from=date_from,
            date_to=date_to,
            contract_id=contract_id,
            counterparty=counterparty,
        )
        return project_search(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            actor_user_id=user.id,
            filters=filters,
            limit=limit,
            cursor=cursor,
        )
    except (SearchDenied, SearchValidationError) as exc:
        _service_error(exc)


@router.get("/projects/{project_id}/views")
def get_views(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = _scope(db, user, project_id)
    return {"views": [_view_payload(row) for row in list_saved_views(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        actor_user_id=user.id,
    )]}


@router.post("/projects/{project_id}/views")
def post_view(project_id: int, payload: SavedViewCreate, db: Session = Depends(get_db),
              user: User = Depends(require_user)):
    project = _scope(db, user, project_id)
    try:
        row = create_saved_view(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            actor_user_id=user.id,
            name=payload.name,
            filters=_filters(payload.filters),
        )
        db.commit()
        db.refresh(row)
        return _view_payload(row)
    except (SearchDenied, SearchValidationError) as exc:
        db.rollback()
        _service_error(exc)


@router.patch("/projects/{project_id}/views/{view_id}")
def patch_view(project_id: int, view_id: int, payload: SavedViewUpdate,
               db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = _scope(db, user, project_id)
    try:
        row = update_saved_view(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            actor_user_id=user.id,
            view_id=view_id,
            expected_version=payload.expected_version,
            name=payload.name,
            filters=_filters(payload.filters),
        )
        db.commit()
        db.refresh(row)
        return _view_payload(row)
    except (SearchDenied, SearchValidationError) as exc:
        db.rollback()
        _service_error(exc)


@router.delete("/projects/{project_id}/views/{view_id}")
def remove_view(project_id: int, view_id: int, expected_version: int = Query(ge=1),
                db: Session = Depends(get_db), user: User = Depends(require_user)):
    project = _scope(db, user, project_id)
    try:
        row = delete_saved_view(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            actor_user_id=user.id,
            view_id=view_id,
            expected_version=expected_version,
        )
        db.commit()
        return _view_payload(row)
    except (SearchDenied, SearchValidationError) as exc:
        db.rollback()
        _service_error(exc)


@router.get("/projects/{project_id}/views/{view_id}/history")
def view_history(project_id: int, view_id: int, db: Session = Depends(get_db),
                 user: User = Depends(require_user)):
    project = _scope(db, user, project_id)
    try:
        rows = get_saved_view_history(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            actor_user_id=user.id,
            view_id=view_id,
        )
        return {"history": [{
            "sequence": row.sequence,
            "event": row.event,
            "record_version": row.resulting_version,
            "snapshot": row.snapshot,
            "occurred_at": row.occurred_at,
        } for row in rows]}
    except (SearchDenied, SearchValidationError) as exc:
        _service_error(exc)
