"""Integration-seam router.

The router is deliberately not included from ``app.main`` until the schema
owner lands the documented sequential migration.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.user import User
from app.mvp4.supply.contracts import (
    CreateSupplyRequest,
    PrepareOrder,
    ProposeAcceptanceAct,
    RecordDelivery,
    RecordOrder,
    ResolveDiscrepancy,
    ReviewSupplyRequest,
    VersionedCommand,
)
from app.mvp4.supply.service import SupplyConflict, SupplyDenied, SupplyService


router = APIRouter(prefix="/api/mvp4/supply", tags=["mvp4-supply"])
service = SupplyService()


def _fail(exc: Exception):
    if isinstance(exc, SupplyConflict):
        raise HTTPException(409, str(exc)) from exc
    if str(exc) == "manual_review_required":
        raise HTTPException(409, "manual_review_required") from exc
    raise HTTPException(404, "resource_unavailable") from exc


def _run(db: Session, operation):
    try:
        result = operation()
        db.commit()
        return result.model_dump(mode="json")
    except (SupplyConflict, SupplyDenied) as exc:
        db.rollback()
        _fail(exc)


@router.post("/requests")
def create_request(payload: CreateSupplyRequest, db: Session = Depends(get_db),
                   user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    return _run(db, lambda: service.create_request(db, actor_user_id=user.id, command=payload))


@router.post("/{supply_case_id}/review")
def review_request(supply_case_id: int, organization_id: int, project_id: int,
                   payload: ReviewSupplyRequest, db: Session = Depends(get_db),
                   user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    return _run(db, lambda: service.review_request(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/approve-request")
def approve_request(supply_case_id: int, organization_id: int, project_id: int,
                    payload: VersionedCommand, db: Session = Depends(get_db),
                    user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    return _run(db, lambda: service.approve_request(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/order")
def prepare_order(supply_case_id: int, organization_id: int, project_id: int,
                  payload: PrepareOrder, db: Session = Depends(get_db),
                  user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    return _run(db, lambda: service.prepare_order(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/approve-order")
def approve_order(supply_case_id: int, organization_id: int, project_id: int,
                  payload: VersionedCommand, db: Session = Depends(get_db),
                  user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    return _run(db, lambda: service.approve_order(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/record-order")
def record_order(supply_case_id: int, organization_id: int, project_id: int,
                 payload: RecordOrder, db: Session = Depends(get_db),
                 user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    return _run(db, lambda: service.record_order(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/deliveries")
def record_delivery(supply_case_id: int, organization_id: int, project_id: int,
                    payload: RecordDelivery, db: Session = Depends(get_db),
                    user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    return _run(db, lambda: service.record_delivery(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/resolve-discrepancy")
def resolve_discrepancy(supply_case_id: int, organization_id: int, project_id: int,
                        payload: ResolveDiscrepancy, db: Session = Depends(get_db),
                        user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    return _run(db, lambda: service.resolve_discrepancy(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/acceptance-acts")
def propose_acceptance_act(supply_case_id: int, organization_id: int, project_id: int,
                           payload: ProposeAcceptanceAct, db: Session = Depends(get_db),
                           user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    return _run(db, lambda: service.propose_acceptance_act(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/approve-acceptance-act")
def approve_acceptance_act(supply_case_id: int, organization_id: int, project_id: int,
                           payload: VersionedCommand, db: Session = Depends(get_db),
                           user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    return _run(db, lambda: service.approve_acceptance_act(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.get("/{supply_case_id}/history")
def history(supply_case_id: int, organization_id: int, project_id: int,
            db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    try:
        rows = service.history(
            db, organization_id=organization_id, project_id=project_id, supply_case_id=supply_case_id
        )
    except SupplyDenied as exc:
        _fail(exc)
    return {
        "history": [
            {
                "sequence": row.sequence,
                "event": row.event,
                "record_version": row.resulting_record_version,
                "snapshot": row.snapshot,
                "evidence_pin": row.evidence_pin,
                "occurred_at": row.occurred_at,
            }
            for row in rows
        ]
    }
