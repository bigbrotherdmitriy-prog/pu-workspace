from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import ROLE_LEVEL, require_user
from app.database import get_db
from app.models.management import ManagementHistory
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.saved_search_view import SavedSearchView
from app.models.user import User

router = APIRouter(prefix="/saved-search-views", tags=["saved-search-views"])

SEARCH_TYPES = {"project", "document", "contract", "task", "obligation", "risk", "decision", "message"}
FILTER_KEYS = {"q", "types", "date_from", "date_to", "contract_id", "counterparty"}


class SavedSearchFilters(BaseModel):
    q: str = Field(default="", max_length=300)
    types: list[str] = Field(default_factory=list, max_length=8)
    date_from: date | None = None
    date_to: date | None = None
    contract_id: int | None = Field(default=None, gt=0)
    counterparty: str | None = Field(default=None, max_length=300)

    model_config = {"extra": "forbid"}

    @field_validator("q")
    @classmethod
    def clean_query(cls, value: str) -> str:
        return value.strip()

    @field_validator("counterparty")
    @classmethod
    def clean_counterparty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("types")
    @classmethod
    def allowed_types(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(item not in SEARCH_TYPES for item in value):
            raise ValueError("types must be unique supported search types")
        return value

    @model_validator(mode="after")
    def valid_date_range(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class SavedSearchViewCreate(BaseModel):
    project_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)
    filters: SavedSearchFilters

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class SavedSearchViewUpdate(BaseModel):
    expected_record_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    filters: SavedSearchFilters | None = None

    @model_validator(mode="after")
    def has_change(self):
        if not ({"name", "filters"} & self.model_fields_set):
            raise ValueError("at least one change is required")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        if "filters" in self.model_fields_set and self.filters is None:
            raise ValueError("filters cannot be null")
        return self

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _project_for_member(db: Session, user: User, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    role = db.scalar(select(ProjectMember.role).where(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user.id,
    ))
    # Saved views are personal even for administrators: an explicit project
    # membership is required, avoiding a privileged cross-tenant side channel.
    if not role or ROLE_LEVEL.get(role, 0) < ROLE_LEVEL["viewer"]:
        raise HTTPException(403, "Insufficient project access")
    return project


def _filters_payload(filters: SavedSearchFilters) -> dict:
    return filters.model_dump(mode="json", exclude_none=True)


def _filter_metadata(filters: dict | None) -> dict:
    normalized = filters or {}
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "filter_keys": sorted(key for key in normalized if key in FILTER_KEYS),
        "filter_fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def _serialize(row: SavedSearchView) -> dict:
    return {
        "id": row.id,
        "record_version": row.record_version,
        "project_id": row.project_id,
        "owner_user_id": row.owner_user_id,
        "name": row.name,
        "filters": row.filters,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _owned_active(db: Session, user: User, view_id: int) -> SavedSearchView:
    row = db.scalar(select(SavedSearchView).where(
        SavedSearchView.id == view_id,
        SavedSearchView.owner_user_id == user.id,
        SavedSearchView.deleted_at.is_(None),
    ))
    if row is None:
        raise HTTPException(404, "Saved search view not found")
    _project_for_member(db, user, row.project_id)
    return row


def _history(db: Session, row: SavedSearchView, *, action: str, actor_user_id: int,
             old_filters: dict | None, new_filters: dict | None) -> None:
    # Saved search terms are private.  The shared project history stores only
    # allowlisted key names and a one-way fingerprint, never values or names.
    db.add(ManagementHistory(
        organization_id=row.organization_id,
        project_id=row.project_id,
        entity_type="saved_search_view",
        entity_id=row.id,
        record_version=row.record_version,
        action=action,
        actor_user_id=actor_user_id,
        old_values=_filter_metadata(old_filters),
        new_values=_filter_metadata(new_filters),
        evidence={"owner_user_id": row.owner_user_id},
        reason="Saved search view lifecycle",
    ))


def _conflict(db: Session, view_id: int, user_id: int, expected: int) -> None:
    actual = db.scalar(select(SavedSearchView.record_version).where(
        SavedSearchView.id == view_id,
        SavedSearchView.owner_user_id == user_id,
        SavedSearchView.deleted_at.is_(None),
    ))
    if actual is None:
        raise HTTPException(404, "Saved search view not found")
    raise HTTPException(409, {"code": "record_version_conflict", "expected": expected, "actual": actual})


@router.get("")
def list_saved_search_views(project_id: int, db: Session = Depends(get_db),
                            user: User = Depends(require_user)):
    _project_for_member(db, user, project_id)
    rows = list(db.scalars(select(SavedSearchView).where(
        SavedSearchView.project_id == project_id,
        SavedSearchView.owner_user_id == user.id,
        SavedSearchView.deleted_at.is_(None),
    ).order_by(SavedSearchView.name, SavedSearchView.id)))
    return {"views": [_serialize(row) for row in rows]}


@router.post("", status_code=201)
def create_saved_search_view(payload: SavedSearchViewCreate, db: Session = Depends(get_db),
                             user: User = Depends(require_user)):
    project = _project_for_member(db, user, payload.project_id)
    row = SavedSearchView(
        organization_id=project.organization_id,
        project_id=project.id,
        owner_user_id=user.id,
        name=payload.name,
        filters=_filters_payload(payload.filters),
    )
    try:
        db.add(row)
        db.flush()
        _history(db, row, action="created", actor_user_id=user.id, old_filters=None, new_filters=row.filters)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, {"code": "saved_view_name_conflict"}) from exc
    db.refresh(row)
    return _serialize(row)


@router.patch("/{view_id}")
def update_saved_search_view(view_id: int, payload: SavedSearchViewUpdate,
                             db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = _owned_active(db, user, view_id)
    old_filters = dict(row.filters)
    values: dict = {"record_version": payload.expected_record_version + 1, "updated_at": _utcnow()}
    if "name" in payload.model_fields_set:
        values["name"] = payload.name
    if "filters" in payload.model_fields_set:
        values["filters"] = _filters_payload(payload.filters)
    try:
        result = db.execute(update(SavedSearchView).where(
            SavedSearchView.id == view_id,
            SavedSearchView.owner_user_id == user.id,
            SavedSearchView.deleted_at.is_(None),
            SavedSearchView.record_version == payload.expected_record_version,
        ).values(**values))
        if result.rowcount != 1:
            db.rollback()
            _conflict(db, view_id, user.id, payload.expected_record_version)
        row.record_version = values["record_version"]
        if "name" in values:
            row.name = values["name"]
        if "filters" in values:
            row.filters = values["filters"]
        _history(db, row, action="updated", actor_user_id=user.id,
                 old_filters=old_filters, new_filters=row.filters)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, {"code": "saved_view_name_conflict"}) from exc
    db.refresh(row)
    return _serialize(row)


@router.delete("/{view_id}")
def delete_saved_search_view(view_id: int, expected_record_version: int = Query(ge=1),
                             db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = _owned_active(db, user, view_id)
    old_filters = dict(row.filters)
    deleted_at = _utcnow()
    result = db.execute(update(SavedSearchView).where(
        SavedSearchView.id == view_id,
        SavedSearchView.owner_user_id == user.id,
        SavedSearchView.deleted_at.is_(None),
        SavedSearchView.record_version == expected_record_version,
    ).values(record_version=expected_record_version + 1, deleted_at=deleted_at, updated_at=deleted_at))
    if result.rowcount != 1:
        db.rollback()
        _conflict(db, view_id, user.id, expected_record_version)
    row.record_version = expected_record_version + 1
    row.deleted_at = deleted_at
    _history(db, row, action="deleted", actor_user_id=user.id,
             old_filters=old_filters, new_filters=None)
    db.commit()
    return {"id": view_id, "record_version": row.record_version, "deleted": True}
