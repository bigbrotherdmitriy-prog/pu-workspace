"""Integration-seam router.

The router is deliberately not included from ``app.main`` until the schema
owner lands the documented sequential migration.
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.user import User
from app.mvp4.supply.contracts import (
    CreateSupplyRequest,
    CreateDdsProposal,
    PrepareOrder,
    ProposeAcceptanceAct,
    RecordDelivery,
    RecordOrder,
    ResolveDiscrepancy,
    ReviewSupplyRequest,
    VersionedCommand,
)
from app.mvp4.supply.service import SupplyConflict, SupplyDenied, SupplyService
from app.mvp4.supply.models import SupplyCase


router = APIRouter(prefix="/api/mvp4/supply", tags=["mvp4-supply"])
service = SupplyService()


def _require_idempotency(command_key: str, idempotency_key: str) -> None:
    if idempotency_key != command_key:
        raise HTTPException(409, "idempotency_key_conflict")


def _view(row: SupplyCase) -> dict:
    return {
        "id": row.id,
        "recordVersion": row.record_version,
        "title": row.title,
        "supplier": row.supplier,
        "status": row.status,
        "reviewState": row.review_state,
        "requestedQuantity": str(row.requested_quantity),
        "orderedQuantity": str(row.ordered_quantity),
        "deliveredQuantity": str(row.delivered_quantity),
        "acceptedQuantity": str(row.accepted_quantity),
        "unit": row.unit,
        "unitPrice": str(row.unit_price),
        "currency": row.currency,
        "projectId": row.project_id,
        "contractId": row.contract_id,
        "scheduleBaselineId": row.schedule_baseline_id,
        "scheduleBaselineVersion": row.schedule_baseline_version,
        "scheduleItemId": row.schedule_item_id,
        "taskId": row.task_id,
        "documentVersionId": row.document_version_id,
        "evidenceId": row.evidence_id,
        "evidenceRevision": row.evidence_revision,
        "sourceVersionId": row.source_version_id,
        "discrepancyCode": row.discrepancy_code,
        "externalActionStatus": row.external_action_status,
    }


@router.get("")
def list_supply_cases(project_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    rows = db.scalars(
        select(SupplyCase).where(SupplyCase.project_id == project_id).order_by(SupplyCase.id.desc())
    ).all()
    return {"items": [_view(row) for row in rows], "total": len(rows)}


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
                   user: User = Depends(require_user),
                   idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120)):
    require_project_role(db, user, payload.project_id, "editor")
    _require_idempotency(payload.command_key, idempotency_key)
    return _run(db, lambda: service.create_request(db, actor_user_id=user.id, command=payload))


@router.post("/{supply_case_id}/review")
def review_request(supply_case_id: int, organization_id: int, project_id: int,
                   payload: ReviewSupplyRequest, db: Session = Depends(get_db),
                   user: User = Depends(require_user),
                   idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120)):
    require_project_role(db, user, project_id, "manager")
    _require_idempotency(payload.command_key, idempotency_key)
    return _run(db, lambda: service.review_request(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/dds-proposals")
def create_dds_proposal(supply_case_id: int, organization_id: int, project_id: int,
                        payload: CreateDdsProposal, db: Session = Depends(get_db),
                        user: User = Depends(require_user),
                        idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120)):
    require_project_role(db, user, project_id, "editor")
    _require_idempotency(payload.command_key, idempotency_key)
    return _run(db, lambda: service.create_dds_proposal(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/approve-request")
def approve_request(supply_case_id: int, organization_id: int, project_id: int,
                    payload: VersionedCommand, db: Session = Depends(get_db),
                    user: User = Depends(require_user),
                    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120)):
    require_project_role(db, user, project_id, "manager")
    _require_idempotency(payload.command_key, idempotency_key)
    return _run(db, lambda: service.approve_request(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/order")
def prepare_order(supply_case_id: int, organization_id: int, project_id: int,
                  payload: PrepareOrder, db: Session = Depends(get_db),
                  user: User = Depends(require_user),
                  idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120)):
    require_project_role(db, user, project_id, "editor")
    _require_idempotency(payload.command_key, idempotency_key)
    return _run(db, lambda: service.prepare_order(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/approve-order")
def approve_order(supply_case_id: int, organization_id: int, project_id: int,
                  payload: VersionedCommand, db: Session = Depends(get_db),
                  user: User = Depends(require_user),
                  idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120)):
    require_project_role(db, user, project_id, "manager")
    _require_idempotency(payload.command_key, idempotency_key)
    return _run(db, lambda: service.approve_order(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/record-order")
def record_order(supply_case_id: int, organization_id: int, project_id: int,
                 payload: RecordOrder, db: Session = Depends(get_db),
                 user: User = Depends(require_user),
                 idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120)):
    require_project_role(db, user, project_id, "editor")
    _require_idempotency(payload.command_key, idempotency_key)
    return _run(db, lambda: service.record_order(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/deliveries")
def record_delivery(supply_case_id: int, organization_id: int, project_id: int,
                    payload: RecordDelivery, db: Session = Depends(get_db),
                    user: User = Depends(require_user),
                    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120)):
    require_project_role(db, user, project_id, "editor")
    _require_idempotency(payload.command_key, idempotency_key)
    return _run(db, lambda: service.record_delivery(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/resolve-discrepancy")
def resolve_discrepancy(supply_case_id: int, organization_id: int, project_id: int,
                        payload: ResolveDiscrepancy, db: Session = Depends(get_db),
                        user: User = Depends(require_user),
                        idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120)):
    require_project_role(db, user, project_id, "manager")
    _require_idempotency(payload.command_key, idempotency_key)
    return _run(db, lambda: service.resolve_discrepancy(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/acceptance-acts")
def propose_acceptance_act(supply_case_id: int, organization_id: int, project_id: int,
                           payload: ProposeAcceptanceAct, db: Session = Depends(get_db),
                           user: User = Depends(require_user),
                           idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120)):
    require_project_role(db, user, project_id, "editor")
    _require_idempotency(payload.command_key, idempotency_key)
    return _run(db, lambda: service.propose_acceptance_act(
        db, organization_id=organization_id, project_id=project_id,
        supply_case_id=supply_case_id, actor_user_id=user.id, command=payload,
    ))


@router.post("/{supply_case_id}/approve-acceptance-act")
def approve_acceptance_act(supply_case_id: int, organization_id: int, project_id: int,
                           payload: VersionedCommand, db: Session = Depends(get_db),
                           user: User = Depends(require_user),
                           idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120)):
    require_project_role(db, user, project_id, "manager")
    _require_idempotency(payload.command_key, idempotency_key)
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
