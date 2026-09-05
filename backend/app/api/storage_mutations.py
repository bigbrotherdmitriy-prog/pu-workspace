"""Project-scoped, synthetic-cohort API for exact storage mutations."""
from __future__ import annotations

import os
from hashlib import sha256

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.jobs.queue import enqueue
from app.models.job import BackgroundJob
from app.models.user import User
from app.organizer_engine.storage_mutation_repository import StorageMutationResolver
from app.organizer_engine.storage_mutation_runtime import DurableMutationLedger
from app.organizer_engine.storage_mutations import MutationConflict


router = APIRouter(prefix="/projects/{project_id}/storage-mutations", tags=["storage-mutations"])


class MutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: int = Field(gt=0)
    action_id: int = Field(gt=0)
    record_version: int = Field(gt=0)


def _enabled() -> bool:
    return os.getenv("PU_STORAGE_MUTATION_SYNTHETIC_API_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def _payload(project_id: int, body: MutationRequest, command_key: str, operation: str) -> dict:
    return {"project_id": project_id, "proposal_id": body.proposal_id, "action_id": body.action_id,
            "command_key": command_key, "expected_record_version": body.record_version,
            "operation": operation}


def _command_key(value: str | None) -> str:
    if value is None or not 8 <= len(value) <= 120 or any(char.isspace() for char in value):
        raise HTTPException(422, "A bounded Idempotency-Key is required")
    return value


def _resolve(db: Session, payload: dict):
    try:
        return StorageMutationResolver(db).resolve(payload)
    except (MutationConflict, ValueError, TypeError):
        raise HTTPException(409, "Storage mutation is stale or unavailable") from None


def _preview(db: Session, payload: dict) -> dict:
    command = _resolve(db, payload)
    ledger = DurableMutationLedger(db, payload, command)
    operation = command.operations[0]
    synthetic = command.pin.connection_id.startswith("synthetic:")
    return {
        "project_id": command.pin.project_id,
        "proposal_id": int(payload["proposal_id"]),
        "action_id": int(payload["action_id"]),
        "record_version": ledger.current_record_version(command.pin.project_id),
        "kind": operation.kind,
        "before_name": operation.old_name,
        "after_name": operation.new_name,
        "provider": command.pin.provider,
        "synthetic_only": True,
        "execution_allowed": bool(_enabled() and synthetic),
        "can_rollback": ledger.latest_applied() is not None,
    }


@router.get("/{proposal_id}/actions/{action_id}/prepare")
def prepare(project_id: int, proposal_id: int, action_id: int,
            db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    payload = _payload(project_id, MutationRequest(proposal_id=proposal_id, action_id=action_id,
                                                    record_version=1),
                       f"preview:{proposal_id}:{action_id}", "apply")
    return _preview(db, payload)


def _queue(project_id: int, body: MutationRequest, operation: str, idempotency_key: str | None,
           db: Session, user: User) -> dict:
    require_project_role(db, user, project_id, "manager")
    key = _command_key(idempotency_key)
    payload = _payload(project_id, body, key, operation)
    preview = _preview(db, payload)
    if not preview["execution_allowed"]:
        raise HTTPException(403, "Live storage mutation is disabled")
    if operation == "rollback" and not preview["can_rollback"]:
        raise HTTPException(409, "No exact applied receipt is available for rollback")
    if body.record_version != preview["record_version"]:
        raise HTTPException(409, "Storage mutation version changed; refresh preview")
    queue_key = "storage-mutation:" + sha256(f"{project_id}:{operation}:{key}".encode()).hexdigest()
    existing = db.scalar(select(BackgroundJob.id).where(BackgroundJob.idempotency_key == queue_key))
    job = enqueue(db, "workspace.storage_mutation", payload, idempotency_key=queue_key)
    return {"job_id": job.id, "status": job.status, "already_queued": existing is not None,
            "project_id": project_id, "record_version": body.record_version}


@router.post("/confirm")
def confirm(project_id: int, body: MutationRequest,
            idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
            db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _queue(project_id, body, "apply", idempotency_key, db, user)


@router.post("/rollback")
def explicit_rollback(project_id: int, body: MutationRequest,
                      idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                      db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _queue(project_id, body, "rollback", idempotency_key, db, user)


@router.get("/jobs/{job_id}")
def status(project_id: int, job_id: int, db: Session = Depends(get_db),
           user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    job = db.get(BackgroundJob, job_id)
    if job is None or job.kind != "workspace.storage_mutation" or int((job.payload or {}).get("project_id", 0)) != project_id:
        raise HTTPException(404, "Storage mutation job not found")
    result = job.result or {}
    return {"job_id": job.id, "project_id": project_id, "status": job.status,
            "progress": int(job.progress or 0),
            "outcome": result.get("outcome") if job.status == "completed" else None,
            "record_version": result.get("resulting_record_version") if job.status == "completed" else None}
