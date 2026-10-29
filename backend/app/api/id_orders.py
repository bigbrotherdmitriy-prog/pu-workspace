from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import id_orders as svc
from app.core.auth import require_project_role, require_user
from app.database import get_db

router = APIRouter(prefix="/projects", tags=["id-orders"])


class OrderBody(BaseModel):
    kind: str | None = None
    number: str | None = None
    order_date: str | None = None
    person_name: str | None = None
    position: str | None = None
    org_name: str | None = None
    object_name: str | None = None
    city: str | None = None
    signer_name: str | None = None
    signer_position: str | None = None
    extra: str | None = None


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(404 if isinstance(exc, LookupError) else 422, str(exc))


@router.get("/{project_id}/id-orders")
def list_id_orders(project_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    return {"catalog": svc.catalog(), "orders": svc.list_orders(db, project_id)}


@router.get("/{project_id}/id-orders/package.zip")
def id_orders_package(project_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    try:
        data = svc.package_zip(db, project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename=\"orders.zip\"; filename*=UTF-8''%D0%9F%D1%80%D0%B8%D0%BA%D0%B0%D0%B7%D1%8B.zip"})


@router.post("/{project_id}/id-orders")
def create_id_order(project_id: int, body: OrderBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    try:
        order = svc.create_order(db, project_id, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return order


@router.patch("/{project_id}/id-orders/{order_id}")
def update_id_order(project_id: int, order_id: int, body: OrderBody, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    try:
        order = svc.update_order(db, project_id, order_id, body.model_dump(exclude_unset=True))
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return order


@router.delete("/{project_id}/id-orders/{order_id}")
def delete_id_order(project_id: int, order_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    try:
        svc.delete_order(db, project_id, order_id)
    except LookupError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return {"deleted": order_id}


@router.get("/{project_id}/id-orders/{order_id}/order.docx")
def id_order_docx(project_id: int, order_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    try:
        o = svc._get(db, project_id, order_id)
    except LookupError as exc:
        raise _fail(exc) from exc
    return Response(content=svc.render_docx(o),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": "attachment; filename=\"order.docx\"; filename*=UTF-8''%D0%9F%D1%80%D0%B8%D0%BA%D0%B0%D0%B7.docx"})


@router.post("/{project_id}/id-orders/{order_id}/certificate")
async def upload_certificate(
    project_id: int, order_id: int, request: Request,
    name: str = Query(..., min_length=1, max_length=300),
    db: Session = Depends(get_db), user=Depends(require_user),
):
    require_project_role(db, user, project_id, "editor")
    data = await request.body()
    try:
        order = svc.add_certificate(db, project_id, order_id, name, request.headers.get("content-type"), data)
    except (ValueError, LookupError) as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return order


@router.get("/{project_id}/id-orders/{order_id}/certificate/{file_id}")
def download_certificate(project_id: int, order_id: int, file_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    try:
        f = svc.get_certificate(db, project_id, order_id, file_id)
    except LookupError as exc:
        raise _fail(exc) from exc
    return Response(content=f.data, media_type=f.mime_type or "application/octet-stream",
                    headers={"Content-Disposition": "inline; filename=\"certificate\""})


@router.delete("/{project_id}/id-orders/{order_id}/certificate/{file_id}")
def delete_certificate(project_id: int, order_id: int, file_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    try:
        order = svc.delete_certificate(db, project_id, order_id, file_id)
    except LookupError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.commit()
    return order
