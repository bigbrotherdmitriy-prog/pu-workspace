from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app import equipment as equipment_service

router = APIRouter(prefix="/projects", tags=["equipment"])


class EquipmentUnitBody(BaseModel):
    serial_number: str | None = None
    location: str | None = None
    status: str | None = None
    received_date: str | None = None
    installed_date: str | None = None
    warranty_until: str | None = None
    service_interval_months: int | None = None
    last_service_date: str | None = None
    note: str | None = None


@router.get("/{project_id}/equipment")
def get_equipment(
    project_id: int,
    status: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    return {
        "units": equipment_service.list_units(db, project_id, status=status, q=q),
        "summary": equipment_service.summary(db, project_id),
    }


@router.post("/{project_id}/equipment/generate")
def generate_equipment(
    project_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    result = equipment_service.generate(db, project_id)
    db.commit()
    return result


@router.get("/{project_id}/equipment/export.xlsx")
def export_equipment_xlsx(
    project_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    content = equipment_service.to_xlsx(db, project_id)
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": 'attachment; filename="Oborudovanie.xlsx"'},
    )


@router.patch("/{project_id}/equipment/{unit_id}")
def patch_equipment_unit(
    project_id: int,
    unit_id: int,
    body: EquipmentUnitBody,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    data = body.model_dump(exclude_unset=True)
    try:
        result = equipment_service.update_unit(db, project_id, unit_id, data)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return result
