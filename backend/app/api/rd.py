from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.jobs.queue import enqueue
from app.models.rd import RdSpecItem, RdVolume
from app.rd_service import (
    import_folder,
    item_counts,
    item_dict,
    parse_volume,
    set_item_status,
    update_item,
    volume_dict,
)

router = APIRouter(prefix="/projects", tags=["rd"])


class ImportBody(BaseModel):
    folder_id: str


class ItemPatch(BaseModel):
    status: str | None = None
    name: str | None = None
    pos: str | None = None
    type_mark: str | None = None
    unit: str | None = None
    qty: str | None = None
    note: str | None = None


def _get_volume(db: Session, project_id: int, volume_id: int) -> RdVolume:
    vol = db.execute(
        select(RdVolume).where(
            RdVolume.id == volume_id,
            RdVolume.project_id == project_id,
        )
    ).scalar_one_or_none()
    if vol is None:
        raise HTTPException(status_code=404, detail="volume not found")
    return vol


@router.get("/{project_id}/rd/volumes")
def list_volumes(
    project_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    vols = db.execute(
        select(RdVolume)
        .where(RdVolume.project_id == project_id)
        .order_by(RdVolume.id)
    ).scalars().all()
    return [volume_dict(v, item_counts(db, v.id)) for v in vols]


@router.post("/{project_id}/rd/import")
def import_rd_folder(
    project_id: int,
    body: ImportBody,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    result = import_folder(db, project_id, body.folder_id)
    for volume_id in result["volumes"]:
        enqueue(
            db,
            "rd.parse_volume",
            {"volume_id": volume_id},
            idempotency_key=f"rd.parse:{volume_id}",
        )
    db.commit()
    return result


@router.post("/{project_id}/rd/volumes/{volume_id}/parse")
def reparse_volume(
    project_id: int,
    volume_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    vol = _get_volume(db, project_id, volume_id)
    vol.status = "new"
    db.commit()
    enqueue(
        db,
        "rd.parse_volume",
        {"volume_id": vol.id},
        idempotency_key=f"rd.parse:{vol.id}:{int(time.time() * 1000)}",
    )
    db.commit()
    return volume_dict(vol, item_counts(db, vol.id))


@router.get("/{project_id}/rd/volumes/{volume_id}/items")
def list_items(
    project_id: int,
    volume_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    _get_volume(db, project_id, volume_id)
    items = db.execute(
        select(RdSpecItem)
        .where(RdSpecItem.volume_id == volume_id)
        .order_by(RdSpecItem.sort_order, RdSpecItem.id)
    ).scalars().all()
    return [item_dict(i) for i in items]


@router.patch("/{project_id}/rd/items/{item_id}")
def patch_item(
    project_id: int,
    item_id: int,
    body: ItemPatch,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    data = body.model_dump(exclude_unset=True)
    status = data.pop("status", None)
    if data:
        updated = update_item(db, project_id, item_id, data)
        if status is not None:
            updated = set_item_status(db, project_id, item_id, status)
        return item_dict(updated)
    if status is not None:
        updated = set_item_status(db, project_id, item_id, status)
        return item_dict(updated)
    raise HTTPException(status_code=422, detail="no fields")


@router.post("/{project_id}/rd/volumes/{volume_id}/confirm-all")
def confirm_all(
    project_id: int,
    volume_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    _get_volume(db, project_id, volume_id)
    items = db.execute(
        select(RdSpecItem).where(
            RdSpecItem.volume_id == volume_id,
            RdSpecItem.status == "proposed",
        )
    ).scalars().all()
    for item in items:
        item.status = "confirmed"
    db.commit()
    return {"confirmed": len(items)}
