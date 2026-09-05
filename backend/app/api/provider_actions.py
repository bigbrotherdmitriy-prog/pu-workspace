from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.job import BackgroundJob
from app.models.project import Project
from app.models.user import User
from app.models.v54_provider_action import (
    ProviderAction,
    ProviderActionApproval,
    ProviderDispatchOutbox,
    ProviderOutcomeObservation,
)
from app.provider_actions.contracts import ProviderActionError
from app.provider_actions.product import RECONCILE_KIND, queue_reconciliation
from app.provider_actions.runtime import PRODUCT_KIND


router = APIRouter(prefix="/provider-actions", tags=["provider-actions"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_SAFE_OBSERVATION_CODES = frozenset({
    "adapter_failure",
    "precondition_failed",
    "provider_receipt_mismatch",
    "receipt_not_found",
    "timeout_after_effect",
    "timeout_before_effect",
})
_ACTIVE_JOB_STATES = frozenset({"queued", "running", "retrying"})
_FAILED_JOB_STATES = frozenset({"failed", "dead_letter"})

BusinessStatus = Literal[
    "awaiting_approval",
    "queued",
    "running",
    "completed",
    "not_applied",
    "requires_reconciliation",
    "blocked",
    "cancelled",
]
ReconciliationStatus = Literal[
    "not_required",
    "required",
    "queued",
    "running",
    "retrying",
    "failed",
    "dead_letter",
    "cancelled",
    "resolved",
]


class ProviderJobStatusView(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    job_id: int
    status: Literal[
        "queued", "running", "retrying", "failed", "dead_letter", "completed", "cancelled",
    ]
    progress: int = Field(ge=0, le=100)
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    duration_ms: int | None = Field(default=None, ge=0)


class ProviderActionStatusView(BaseModel):
    """Allowlisted operational projection; never contains provider material."""

    model_config = ConfigDict(extra="forbid", strict=True)

    action_id: str
    revision: int = Field(ge=1)
    project_id: int = Field(ge=1)
    provider: Literal["synthetic", "google_workspace"]
    action_kind: Literal[
        "synthetic.effect.apply",
        "synthetic.effect.send",
        "synthetic.effect.rollback",
        "synthetic.effect.compensate",
        "synthetic.effect.corrective",
        "gmail.message.send",
        "google.tasks.upsert",
        "google.calendar.upsert",
    ]
    mode: Literal["CONFIRM"]
    reversibility: Literal["REVERSIBLE", "COMPENSATABLE", "IRREVERSIBLE"]
    business_status: BusinessStatus
    approval_status: Literal["missing", "granted", "revoked", "expired"]
    is_current_revision: bool
    dispatch: ProviderJobStatusView | None
    reconciliation_status: ReconciliationStatus
    reconciliation: ProviderJobStatusView | None
    receipt_id: int | None
    receipt_outcome: Literal["APPLIED", "NOT_APPLIED", "UNKNOWN"] | None
    receipt_late: bool
    retry_state: Literal["none", "retrying", "failed", "dead_letter"]
    safe_reason: str | None
    created_at: datetime


class ProviderActionListView(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[ProviderActionStatusView]
    count: int = Field(ge=0)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _authorized_project(db: Session, user: User, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Provider action is unavailable")
    require_project_role(db, user, project_id, "viewer")
    return project


def _job_view(job: BackgroundJob | None) -> ProviderJobStatusView | None:
    if job is None:
        return None
    # Database constraints own the status vocabulary. Refuse an unexpected
    # value rather than accidentally serializing a future internal state.
    if job.status not in {
        "queued", "running", "retrying", "failed", "dead_letter", "completed", "cancelled",
    }:
        return None
    return ProviderJobStatusView(
        job_id=job.id,
        status=job.status,
        progress=max(0, min(100, int(job.progress or 0))),
        attempts=max(0, int(job.attempts or 0)),
        max_attempts=max(1, int(job.max_attempts or 1)),
        duration_ms=max(0, int(job.duration_ms)) if job.duration_ms is not None else None,
    )


def _bound_job(
    job: BackgroundJob | None,
    row: ProviderAction,
    *,
    expected_kind: str,
) -> BackgroundJob | None:
    expected_payload = {
        "organization_id": row.organization_id,
        "action_id": row.action_id,
        "revision": row.revision,
    }
    if job is None or job.kind != expected_kind or job.payload != expected_payload:
        return None
    return job


def _latest_observation(db: Session, row: ProviderAction) -> ProviderOutcomeObservation | None:
    return db.scalar(
        select(ProviderOutcomeObservation)
        .where(
            ProviderOutcomeObservation.organization_id == row.organization_id,
            ProviderOutcomeObservation.action_id == row.action_id,
            ProviderOutcomeObservation.revision == row.revision,
        )
        .order_by(ProviderOutcomeObservation.sequence.desc())
        .limit(1)
    )


def _has_unknown_observation(db: Session, row: ProviderAction) -> bool:
    return db.scalar(
        select(ProviderOutcomeObservation.id)
        .where(
            ProviderOutcomeObservation.organization_id == row.organization_id,
            ProviderOutcomeObservation.action_id == row.action_id,
            ProviderOutcomeObservation.revision == row.revision,
            ProviderOutcomeObservation.outcome == "UNKNOWN",
        )
        .limit(1)
    ) is not None


def _reconciliation_job(
    db: Session,
    row: ProviderAction,
    latest: ProviderOutcomeObservation | None,
) -> BackgroundJob | None:
    if latest is not None and latest.source == "RECONCILE" and latest.job_id is not None:
        job = db.get(BackgroundJob, latest.job_id)
        return _bound_job(job, row, expected_kind=RECONCILE_KIND)
    if latest is None or latest.outcome != "UNKNOWN":
        return None
    expected_key = (
        f"provider-reconcile:{row.organization_id}:{row.action_id}:"
        f"{row.revision}:{latest.sequence}"
    )
    job = db.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.kind == RECONCILE_KIND,
            BackgroundJob.idempotency_key == expected_key,
        )
        .order_by(BackgroundJob.id.desc())
        .limit(1)
    )
    return _bound_job(job, row, expected_kind=RECONCILE_KIND)


def _approval_status(approval: ProviderActionApproval | None) -> str:
    if approval is None:
        return "missing"
    if approval.state == "REVOKED":
        return "revoked"
    if approval.state == "EXPIRED" or (_aware(approval.expires_at) or datetime.min.replace(
        tzinfo=timezone.utc
    )) <= datetime.now(timezone.utc):
        return "expired"
    return "granted"


def _business_status(row: ProviderAction, dispatch: BackgroundJob | None) -> BusinessStatus:
    if row.state == "APPLIED":
        return "completed"
    if row.state == "NOT_APPLIED":
        return "not_applied"
    if row.state == "UNKNOWN":
        return "requires_reconciliation"
    if row.state == "BLOCKED" or (dispatch is not None and dispatch.status in _FAILED_JOB_STATES):
        return "blocked"
    if dispatch is not None and dispatch.status == "cancelled":
        return "cancelled"
    if row.state == "EXECUTING" or (dispatch is not None and dispatch.status == "running"):
        return "running"
    if row.state == "READY" or (dispatch is not None and dispatch.status in _ACTIVE_JOB_STATES):
        return "queued"
    return "awaiting_approval"


def _reconciliation_status(
    latest: ProviderOutcomeObservation | None,
    job: BackgroundJob | None,
    *,
    had_unknown: bool,
) -> ReconciliationStatus:
    if latest is not None and latest.outcome != "UNKNOWN" and had_unknown:
        return "resolved"
    if latest is None or latest.outcome != "UNKNOWN":
        return "not_required"
    if job is None:
        return "required"
    return job.status if job.status in {
        "queued", "running", "retrying", "failed", "dead_letter", "cancelled",
    } else "required"


def _safe_reason(
    row: ProviderAction,
    approval_status: str,
    latest: ProviderOutcomeObservation | None,
    dispatch: BackgroundJob | None,
    reconciliation: BackgroundJob | None,
) -> str | None:
    if approval_status in {"revoked", "expired"}:
        return f"approval_{approval_status}"
    if latest is not None and latest.safe_code in _SAFE_OBSERVATION_CODES:
        return latest.safe_code
    if row.state == "UNKNOWN":
        return "outcome_unknown"
    job = reconciliation or dispatch
    if job is not None and job.status in _FAILED_JOB_STATES:
        return "job_failed"
    if row.state == "BLOCKED":
        return "action_blocked"
    return None


def _projection(
    db: Session,
    row: ProviderAction,
    *,
    latest_revision: int,
) -> ProviderActionStatusView:
    approval = db.scalar(select(ProviderActionApproval).where(
        ProviderActionApproval.organization_id == row.organization_id,
        ProviderActionApproval.action_id == row.action_id,
        ProviderActionApproval.revision == row.revision,
    ))
    outbox = db.get(ProviderDispatchOutbox, (row.action_id, row.revision))
    dispatch_candidate = db.get(BackgroundJob, outbox.job_id) if outbox and outbox.job_id else None
    dispatch_job = _bound_job(dispatch_candidate, row, expected_kind=PRODUCT_KIND)
    latest = _latest_observation(db, row)
    reconciliation_job = _reconciliation_job(db, row, latest)
    approval_state = _approval_status(approval)
    retry_job = reconciliation_job or dispatch_job
    retry_state = retry_job.status if retry_job and retry_job.status in {
        "retrying", "failed", "dead_letter",
    } else "none"
    return ProviderActionStatusView(
        action_id=row.action_id,
        revision=row.revision,
        project_id=row.project_id,
        provider=row.provider,
        action_kind=row.action_kind,
        mode=row.mode,
        reversibility=row.reversibility,
        business_status=_business_status(row, dispatch_job),
        approval_status=approval_state,
        is_current_revision=row.revision == latest_revision,
        dispatch=_job_view(dispatch_job),
        reconciliation_status=_reconciliation_status(
            latest,
            reconciliation_job,
            had_unknown=_has_unknown_observation(db, row),
        ),
        reconciliation=_job_view(reconciliation_job),
        receipt_id=latest.id if latest is not None else None,
        receipt_outcome=latest.outcome if latest is not None else None,
        receipt_late=bool(latest and latest.late),
        retry_state=retry_state,
        safe_reason=_safe_reason(row, approval_state, latest, dispatch_job, reconciliation_job),
        created_at=row.created_at,
    )


def _latest_revision(db: Session, row: ProviderAction) -> int:
    return int(db.scalar(select(func.max(ProviderAction.revision)).where(
        ProviderAction.organization_id == row.organization_id,
        ProviderAction.project_id == row.project_id,
        ProviderAction.action_id == row.action_id,
    )) or row.revision)


def _scoped_action(
    db: Session,
    user: User,
    *,
    project_id: int,
    action_id: str,
    revision: int,
) -> tuple[ProviderAction, int]:
    project = _authorized_project(db, user, project_id)
    row = db.scalar(select(ProviderAction).where(
        ProviderAction.organization_id == project.organization_id,
        ProviderAction.project_id == project_id,
        ProviderAction.action_id == action_id,
        ProviderAction.revision == revision,
    ))
    if row is None:
        raise HTTPException(404, "Provider action is unavailable")
    return row, _latest_revision(db, row)


@router.get("", response_model=ProviderActionListView)
def list_provider_actions(
    response: Response,
    project_id: int = Query(ge=1),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    project = _authorized_project(db, user, project_id)
    rows = list(db.scalars(
        select(ProviderAction)
        .where(
            ProviderAction.organization_id == project.organization_id,
            ProviderAction.project_id == project_id,
        )
        .order_by(ProviderAction.created_at.desc(), ProviderAction.action_id, ProviderAction.revision.desc())
        .limit(limit)
    ))
    response.headers.update(_NO_STORE)
    items = [
        _projection(db, row, latest_revision=_latest_revision(db, row))
        for row in rows
    ]
    return ProviderActionListView(items=items, count=len(items))


@router.get("/{action_id}/revisions/{revision}", response_model=ProviderActionStatusView)
def get_provider_action(
    action_id: str,
    revision: int,
    project_id: int,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    row, latest_revision = _scoped_action(
        db, user, project_id=project_id, action_id=action_id, revision=revision,
    )
    response.headers.update(_NO_STORE)
    return _projection(db, row, latest_revision=latest_revision)


@router.get("/{action_id}/revisions/{revision}/status", response_model=ProviderActionStatusView)
def get_provider_action_status(
    action_id: str,
    revision: int,
    project_id: int,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    return get_provider_action(action_id, revision, project_id, response, db, user)


@router.post("/{action_id}/revisions/{revision}/reconcile")
def reconcile_provider_action(
    action_id: str,
    revision: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Queue authoritative lookup; never call a provider in the API process."""
    try:
        return queue_reconciliation(db, action_id=action_id, revision=revision, actor=user)
    except ProviderActionError as exc:
        db.rollback()
        raise HTTPException(409, f"Provider reconciliation is unavailable ({exc.code})") from exc
