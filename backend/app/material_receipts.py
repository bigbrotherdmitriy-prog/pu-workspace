"""Explicit physical arrivals; no payment inference, writeoff or API wiring."""
from decimal import Decimal, InvalidOperation, localcontext

from sqlalchemy import Numeric, cast, literal, select
from sqlalchemy.orm import Session

from app.models.purchase import MaterialReceipt, PurchaseItem


_QUANTUM = Decimal("0.001")
_MAX_QTY = Decimal("999999999999999.999")


def _text(value, limit, label, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Invalid {label}")
    value = value.strip()
    if optional and not value:
        return None
    if not value or len(value) > limit:
        raise ValueError(f"Invalid {label}")
    return value


def _quantity(value):
    if isinstance(value, bool):
        raise ValueError("Invalid physical receipt quantity")
    try:
        quantity = value if isinstance(value, Decimal) else Decimal(str(value))
        if not quantity.is_finite() or quantity <= 0 or quantity > _MAX_QTY:
            raise ValueError("Invalid physical receipt quantity")
        with localcontext() as context:
            context.prec = 28
            exact = quantity.quantize(_QUANTUM)
        if exact != quantity:
            raise ValueError("Physical receipt quantity requires at most three decimal places")
        return exact
    except (InvalidOperation, TypeError, OverflowError) as exc:
        raise ValueError("Invalid physical receipt quantity") from exc


def record_receipt(db: Session, project_id: int, purchase_item_id: int, *,
                   operation_key: str, qty, unit: str, source_document_ref: str | None = None) -> dict:
    """Record/replay one arrival in the caller transaction without flushing caller state."""
    if type(project_id) is not int or project_id <= 0 or type(purchase_item_id) is not int or purchase_item_id <= 0:
        raise ValueError("Invalid receipt project or material")
    key = _text(operation_key, 160, "receipt operation key")
    unit = _text(unit, 40, "receipt unit")
    document = _text(source_document_ref, 500, "receipt source document", optional=True)
    quantity = _quantity(qty)
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise RuntimeError("Physical receipt insertion requires SQLite or PostgreSQL")
    with db.no_autoflush:
        item = db.execute(select(PurchaseItem.id, PurchaseItem.unit).where(
            PurchaseItem.id == purchase_item_id, PurchaseItem.project_id == project_id,
        )).first()
        if item is None:
            raise LookupError("Material not found in project")
        receipt_query = select(MaterialReceipt.__table__).where(
            MaterialReceipt.project_id == project_id, MaterialReceipt.operation_key == key,
        )
        row = db.execute(receipt_query).mappings().one_or_none()
        if row is None:
            if not isinstance(item.unit, str) or item.unit.strip() != unit:
                raise ValueError("Receipt unit must match the material unit; conversion is not supported")
            # SQLite Numeric uses floating-point storage. Reject lossy values before writing.
            represented = db.scalar(select(cast(literal(quantity, type_=Numeric(18, 3)), Numeric(18, 3))))
            if represented != quantity:
                raise ValueError("Database cannot represent receipt quantity exactly")
            command = insert(MaterialReceipt).values(
                project_id=project_id, purchase_item_id=purchase_item_id, operation_key=key,
                qty=quantity, unit=unit, source_document_ref=document,
            ).on_conflict_do_nothing(index_elements=["project_id", "operation_key"])
            db.execute(command)
            row = db.execute(receipt_query).mappings().one()
        if (row['purchase_item_id'], row['qty'], row['unit'], row['source_document_ref']) != (
            purchase_item_id, quantity, unit, document,
        ):
            raise ValueError("Receipt operation key conflicts with the existing arrival")
        return {
            'id': row['id'], 'project_id': row['project_id'], 'purchase_item_id': row['purchase_item_id'],
            'operation_key': row['operation_key'], 'qty': format(row['qty'], '.3f'), 'unit': row['unit'],
            'source_document_ref': row['source_document_ref'], 'created_at': row['created_at'].isoformat(),
        }


def prepare_writeoff(db: Session, project_id: int, purchase_item_id: int, work_log_entry_id: int, *,
                     operation_key: str, qty, unit: str) -> dict:
    """Validate an explicit consumption command without applying any stock movement.

    Applying a movement must revalidate/lock its inputs and resolve stock/deletion
    policy separately. This preparation grants neither API permission nor stock admission.
    """
    from sqlalchemy import func as sqlfunc
    from app.models.work_log import WorkLogEntry
    if any(type(value) is not int or value <= 0 for value in (project_id, purchase_item_id, work_log_entry_id)):
        raise ValueError("Invalid writeoff project, material or work entry")
    key = _text(operation_key, 160, "writeoff operation key")
    unit = _text(unit, 40, "writeoff unit")
    quantity = _quantity(qty)
    with db.no_autoflush:
        material = db.execute(select(PurchaseItem.id, sqlfunc.substr(PurchaseItem.unit, 1, 41).label('unit')).where(
            PurchaseItem.id == purchase_item_id, PurchaseItem.project_id == project_id,
        )).first()
        if material is None:
            raise LookupError("Material not found in project")
        if not isinstance(material.unit, str) or len(material.unit) > 40 or material.unit.strip() != unit:
            raise ValueError("Writeoff unit must match the material unit; conversion is not supported")
        entry = db.scalar(select(WorkLogEntry.id).where(
            WorkLogEntry.id == work_log_entry_id, WorkLogEntry.project_id == project_id,
        ))
        if entry is None:
            raise LookupError("Work log entry not found in project")
        represented = db.scalar(select(cast(literal(quantity, type_=Numeric(18, 3)), Numeric(18, 3))))
        if represented != quantity:
            raise ValueError("Database cannot represent writeoff quantity exactly")
    return {'project_id': project_id, 'purchase_item_id': purchase_item_id,
            'work_log_entry_id': work_log_entry_id, 'operation_key': key,
            'qty': format(quantity, '.3f'), 'unit': unit, 'applied': False}


def receipt_history(db: Session, project_id: int, *, limit: int, before_id: int | None = None) -> dict:
    """Read bounded arrival pages by descending identity; no immutable-audit claim."""
    from sqlalchemy import func as sqlfunc
    from app.models.project import Project
    if type(project_id) is not int or project_id <= 0:
        raise ValueError("Invalid receipt project")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Receipt history page limit must be between 1 and 100")
    if before_id is not None and (type(before_id) is not int or before_id <= 0):
        raise ValueError("Invalid receipt history cursor")
    with db.no_autoflush:
        if db.scalar(select(Project.id).where(Project.id == project_id)) is None:
            raise LookupError("Project not found")
        statement = select(
            MaterialReceipt.id, MaterialReceipt.purchase_item_id, MaterialReceipt.qty,
            sqlfunc.substr(MaterialReceipt.unit, 1, 41).label('unit'),
            sqlfunc.substr(MaterialReceipt.operation_key, 1, 161).label('operation_key'),
            sqlfunc.substr(MaterialReceipt.source_document_ref, 1, 501).label('source_document_ref'),
            MaterialReceipt.created_at, PurchaseItem.project_id.label('material_project'),
        ).outerjoin(PurchaseItem, PurchaseItem.id == MaterialReceipt.purchase_item_id).where(
            MaterialReceipt.project_id == project_id,
        )
        if before_id is not None:
            statement = statement.where(MaterialReceipt.id < before_id)
        rows = db.execute(statement.order_by(MaterialReceipt.id.desc()).limit(limit + 1)).mappings()
        try:
            page = rows.all()
        finally:
            rows.close()
    items = []
    for row in page[:limit]:
        if row['material_project'] != project_id:
            raise ValueError("Receipt material ownership is invalid")
        for field, maximum in (('unit', 40), ('operation_key', 160)):
            value = row[field]
            if not isinstance(value, str) or not value or len(value) > maximum or value.strip() != value:
                raise ValueError("Stored receipt history text is invalid")
        document = row['source_document_ref']
        if document is not None and (not isinstance(document, str) or len(document) > 500):
            raise ValueError("Stored receipt source document is invalid")
        quantity = _quantity(row['qty'])
        items.append({'id': row['id'], 'project_id': project_id,
                      'purchase_item_id': row['purchase_item_id'], 'operation_key': row['operation_key'],
                      'qty': format(quantity, '.3f'), 'unit': row['unit'],
                      'source_document_ref': document, 'created_at': row['created_at'].isoformat()})
    more = len(page) > limit
    return {'project_id': project_id, 'items': items,
            'next_before_id': items[-1]['id'] if more else None}
