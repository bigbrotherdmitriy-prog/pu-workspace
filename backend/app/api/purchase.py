from __future__ import annotations

from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Response, Header, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app import purchase as purchase_service

router = APIRouter(prefix="/projects", tags=["purchase"])

_STATUS_LABELS = {
    "need": "Нужно заказать",
    "ordered": "Заказано",
    "paid": "Оплачено",
    "received": "Получено",
}

_HEADERS = [
    "№", "Наименование", "Тип/марка", "Производитель", "Ед.",
    "Нужно", "Заказано", "Осталось", "Статус", "Примечание",
]
_COL_WIDTHS = [6, 45, 25, 25, 8, 12, 12, 12, 18, 40]


def to_xlsx(items: list[dict]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Закупка"
    ws.append(_HEADERS)
    bold = Font(bold=True)
    for cell in ws[1]:
        cell.font = bold
    for idx, item in enumerate(items, start=1):
        ws.append([
            idx,
            item.get("name") or "",
            item.get("type_mark") or "",
            item.get("manufacturer") or "",
            item.get("unit") or "",
            item.get("qty_needed"),
            item.get("qty_ordered"),
            item.get("qty_left"),
            _STATUS_LABELS.get(item.get("status"), item.get("status") or ""),
            item.get("note") or "",
        ])
    for col_idx, width in enumerate(_COL_WIDTHS, start=1):
        letter = ws.cell(row=1, column=col_idx).column_letter
        ws.column_dimensions[letter].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


class PurchaseStatusBody(BaseModel):
    status: str | None = None
    qty_ordered: str | None = None
    note: str | None = None


@router.get("/{project_id}/purchase")
def get_purchase(
    project_id: int,
    status: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    return {
        "items": purchase_service.list_items(db, project_id, status=status),
        "summary": purchase_service.summary(db, project_id),
    }


@router.get("/{project_id}/purchase/report")
def get_purchase_report(
    project_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    return purchase_service.report(db, project_id)


@router.get("/{project_id}/purchase/invoices")
def get_purchase_invoices(
    project_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    return {"invoices": purchase_service.invoices_for_matching(db, project_id)}


@router.post("/{project_id}/purchase/rebuild")
def rebuild_purchase(
    project_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    return purchase_service.rebuild(db, project_id)


@router.patch("/{project_id}/purchase/{item_id}")
def patch_purchase_item(
    project_id: int,
    item_id: int,
    body: PurchaseStatusBody,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    data = body.model_dump(exclude_unset=True)
    status = data.get("status")
    if status is None:
        raise HTTPException(status_code=422, detail="status required")
    return purchase_service.set_status(
        db,
        project_id,
        item_id,
        status,
        qty_ordered=data.get("qty_ordered"),
        note=data.get("note"),
    )


@router.get("/{project_id}/purchase/export.xlsx")
def export_purchase_xlsx(
    project_id: int,
    status: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    items = purchase_service.list_items(db, project_id, status=status)
    content = to_xlsx(items)
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": 'attachment; filename="Zakupka.xlsx"'},
    )


class PhysicalReceiptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_key: str = Field(min_length=1,max_length=160)
    qty: str = Field(min_length=1,max_length=64)
    unit: str = Field(min_length=1,max_length=40)
    source_document_ref: str | None = Field(default=None,max_length=500)


class PhysicalWriteoffBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_key: str = Field(min_length=1,max_length=160)
    qty: str = Field(min_length=1,max_length=64)
    unit: str = Field(min_length=1,max_length=40)
    work_log_entry_id: StrictInt = Field(gt=0,le=2147483647)


class StornoBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_key: str = Field(min_length=1,max_length=160)


def _stock_failure(db, exc):
    db.rollback()
    return HTTPException(404 if isinstance(exc, LookupError) else 422,str(exc))


@router.get('/{project_id}/purchase/{item_id}/physical-balance')
def get_physical_balance(project_id: int,item_id: int,db: Session=Depends(get_db),user=Depends(require_user)):
    require_project_role(db,user,project_id,'viewer')
    from sqlalchemy import select
    from app.models.purchase import PurchaseItem
    from app.material_writeoffs import physical_balance
    if db.scalar(select(PurchaseItem.id).where(PurchaseItem.id==item_id,PurchaseItem.project_id==project_id)) is None:
        raise HTTPException(404,'Material not found in project')
    return {'qty':format(physical_balance(db,project_id,item_id),'.3f')}


@router.post('/{project_id}/purchase/{item_id}/physical-receipts')
def create_physical_receipt(project_id: int,item_id: int,body: PhysicalReceiptBody,db: Session=Depends(get_db),user=Depends(require_user),expected_user_id: int | None=Header(default=None,alias="X-PU-Expected-User",gt=0)):
    require_project_role(db,user,project_id,'manager')
    if expected_user_id is not None:
        from app.mobile_create_owner import require_create_owner
        require_create_owner(user,expected_user_id)
    from app.material_receipts import record_receipt
    try:result=record_receipt(db,project_id,item_id,**body.model_dump())
    except (ValueError,LookupError) as exc:raise _stock_failure(db,exc) from exc
    db.commit();return result


@router.post('/{project_id}/purchase/{item_id}/writeoffs')
def create_physical_writeoff(project_id: int,item_id: int,body: PhysicalWriteoffBody,db: Session=Depends(get_db),user=Depends(require_user),expected_user_id: int | None=Header(default=None,alias="X-PU-Expected-User",gt=0)):
    require_project_role(db,user,project_id,'manager')
    if expected_user_id is not None:
        from app.mobile_create_owner import require_create_owner
        require_create_owner(user,expected_user_id)
    from app.material_writeoffs import record_writeoff
    try:result=record_writeoff(db,project_id,item_id,body.work_log_entry_id,operation_key=body.operation_key,qty=body.qty,unit=body.unit)
    except (ValueError,LookupError) as exc:raise _stock_failure(db,exc) from exc
    db.commit();return result


@router.post('/{project_id}/purchase/writeoffs/{writeoff_id}/storno')
def create_physical_storno(project_id: int,writeoff_id: int,body: StornoBody,db: Session=Depends(get_db),user=Depends(require_user),expected_user_id: int | None=Header(default=None,alias="X-PU-Expected-User",gt=0)):
    require_project_role(db,user,project_id,'manager')
    if expected_user_id is not None:
        from app.mobile_create_owner import require_create_owner
        require_create_owner(user,expected_user_id)
    from app.material_writeoffs import reverse_writeoff
    try:result=reverse_writeoff(db,project_id,writeoff_id,operation_key=body.operation_key)
    except (ValueError,LookupError) as exc:raise _stock_failure(db,exc) from exc
    db.commit();return result


@router.get('/{project_id}/purchase/{item_id}/stock-history')
def read_physical_stock_history(project_id: int,item_id: int,limit: int=Query(default=25,ge=1,le=100),db: Session=Depends(get_db),user=Depends(require_user)):
    require_project_role(db,user,project_id,'viewer')
    from app.material_writeoffs import stock_history
    try:return stock_history(db,project_id,item_id,limit=limit)
    except (ValueError,LookupError) as exc:raise HTTPException(404 if isinstance(exc,LookupError) else 422,str(exc)) from exc
