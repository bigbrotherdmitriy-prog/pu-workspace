"""Atomic defect+receipt creation. Codex fallback; caller owns commit/rollback.

Use a dedicated request unit of work: begin_nested may flush caller state.
No photos, external storage, bots or invitations are performed here.
"""
from copy import deepcopy
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app import defects
from app.core.auth import require_project_role
from app.mobile_defect_contract import OfflineDefectCreateRequest
from app.models.mobile_sync import MobileSyncCommand
from app.models.project import Project
from app.models.user import User


def _receipt(row: MobileSyncCommand, payload: dict) -> dict:
    if row.payload != payload:
        raise ValueError("client_mutation_id_reused")
    result = row.result
    if (row.operation != "defect.create" or row.status != "applied"
            or row.entity_type != "defect" or not isinstance(result, dict)
            or row.project_id != payload["project_id"]
            or type(row.entity_id) is not int or row.entity_id <= 0
            or type(result.get("id")) is not int or result["id"] != row.entity_id
            or type(result.get("project_id")) is not int or result["project_id"] != row.project_id
            or type(result.get("author_user_id")) is not int or result["author_user_id"] != row.user_id):
        raise ValueError("offline_receipt_not_applied")
    return {"receipt_id": row.id, "client_mutation_id": row.client_mutation_id,
            "operation": row.operation, "entity_type": row.entity_type,
            "entity_id": row.entity_id, "status": row.status, "result": deepcopy(result)}


def submit_defect_create(db: Session, actor: User, request: OfflineDefectCreateRequest) -> dict:
    if not isinstance(request, OfflineDefectCreateRequest):
        raise TypeError("Validated offline envelope required")
    if type(getattr(actor, "id", None)) is not int or actor.id <= 0:
        raise HTTPException(401, "Authenticated actor required")
    with db.no_autoflush:
        project = db.get(Project, request.project_id)
        if project is None:
            raise LookupError("Project not found")
        require_project_role(db, actor, project.id, "editor")
        payload = request.model_dump(mode="json")
        query = select(MobileSyncCommand).where(
            MobileSyncCommand.organization_id == project.organization_id,
            MobileSyncCommand.user_id == actor.id,
            MobileSyncCommand.client_mutation_id == request.client_mutation_id,
        ).with_for_update()
        existing = db.scalar(query)
        if existing is not None:
            return _receipt(existing, payload)
    if db.get_bind().dialect.name == "sqlite":
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
    try:
        with db.begin_nested():
            created = defects.create_defect(db, project.id, actor.id,
                                            request.data.model_dump(exclude_unset=True))
            result = {**created, "project_id": project.id, "author_user_id": actor.id}
            row = MobileSyncCommand(
                organization_id=project.organization_id, project_id=project.id, user_id=actor.id,
                client_mutation_id=request.client_mutation_id, device_id=request.device_id,
                operation="defect.create", entity_type="defect", entity_id=created["id"],
                client_created_at=request.client_created_at, payload=payload,
                status="applied", result=result,
            )
            db.add(row)
            db.flush()
        return _receipt(row, payload)
    except IntegrityError:
        with db.no_autoflush:
            existing = db.scalar(query)
        if existing is None:
            raise
        return _receipt(existing, payload)
