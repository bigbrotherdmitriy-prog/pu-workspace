"""Atomic offline journal service; trusted authenticated actor, no HTTP wiring.

Codex fallback. Caller owns commit/rollback; dedicate a request unit of work.
"""
from copy import deepcopy
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app import work_log
from app.core.auth import require_project_role
from app.mobile_work_log_contract import OfflineWorkLogCreateRequest
from app.models.mobile_sync import MobileSyncCommand
from app.models.project import Project
from app.models.user import User


def _receipt(row: MobileSyncCommand, payload: dict) -> dict:
    if row.payload != payload:
        raise ValueError('client_mutation_id_reused')
    if row.operation != 'work_log.create' or row.status != 'applied' or not isinstance(row.result, dict):
        raise ValueError('offline_receipt_not_applied')
    return {'receipt_id':row.id,'client_mutation_id':row.client_mutation_id,
            'operation':row.operation,'entity_type':row.entity_type,
            'entity_id':row.entity_id,'status':row.status,'result':deepcopy(row.result)}


def submit_work_log_create(db: Session, actor: User, request: OfflineWorkLogCreateRequest) -> dict:
    if not isinstance(request, OfflineWorkLogCreateRequest):
        raise TypeError('Validated offline envelope required')
    if type(getattr(actor,'id',None)) is not int or actor.id <= 0:
        raise HTTPException(401,'Authenticated actor required')
    with db.no_autoflush:
        project=db.get(Project,request.project_id)
        if project is None:
            raise LookupError('Project not found')
        # Rechecked before cached replay as well as first delivery.
        require_project_role(db,actor,project.id,'editor')
        payload=request.model_dump(mode='json')
        query=select(MobileSyncCommand).where(
            MobileSyncCommand.organization_id==project.organization_id,
            MobileSyncCommand.user_id==actor.id,
            MobileSyncCommand.client_mutation_id==request.client_mutation_id,
        ).with_for_update()
        existing=db.scalar(query)
        if existing is not None:
            return _receipt(existing,payload)
    # sqlite3 legacy transaction mode may RELEASE an outermost SAVEPOINT as COMMIT.
    # Force a real caller-owned transaction before entering the atomic savepoint.
    if db.get_bind().dialect.name=='sqlite':
        connection=db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN')
    try:
        with db.begin_nested():
            entry=work_log.add_entry(db,project.id,actor.id,request.data.model_dump(exclude_unset=True))
            row=MobileSyncCommand(
                organization_id=project.organization_id,project_id=project.id,user_id=actor.id,
                client_mutation_id=request.client_mutation_id,device_id=request.device_id,
                operation='work_log.create',entity_type='work_log',entity_id=entry['id'],
                client_created_at=request.client_created_at,payload=payload,
                status='applied',result=entry,
            )
            db.add(row);db.flush()
        return _receipt(row,payload)
    except IntegrityError:
        # A competing identical command may win after our initial lookup.
        # Savepoint rollback removes our provisional journal/labor rows first.
        with db.no_autoflush:
            existing=db.scalar(query)
        if existing is None:
            raise
        return _receipt(existing,payload)
