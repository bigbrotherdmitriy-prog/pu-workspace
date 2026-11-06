"""Provisional explicit stock ledger. Caller owns commit/rollback; no external IO."""
from decimal import Decimal, localcontext
from sqlalchemy import select, func, text
from app.models.purchase import MaterialReceipt, MaterialWriteoff, PurchaseItem
from app.material_receipts import prepare_writeoff, _quantity, _text, existing_physical_unit, MixedPhysicalUnitsError
from app.provisional_policy import get_policy


def _locked_item(db, project_id, item_id):
    dialect = db.get_bind().dialect.name
    if dialect == 'sqlite':
        # Reserve the single SQLite writer without changing finance/updated_at.
        db.execute(text('UPDATE purchase_items SET id=id WHERE id=:id AND project_id=:p'), {'id': item_id, 'p': project_id})
    elif dialect != 'postgresql':
        raise RuntimeError('Stock ledger requires SQLite or PostgreSQL')
    item = db.scalar(select(PurchaseItem).where(PurchaseItem.id == item_id, PurchaseItem.project_id == project_id).with_for_update())
    if item is None: raise LookupError('Material not found in project')
    return item


def physical_balance(db, project_id, item_id):
    with db.no_autoflush, localcontext() as context:
        context.prec = 50
        existing_physical_unit(db, project_id, item_id)
        receipts = db.scalar(select(func.coalesce(func.sum(MaterialReceipt.qty), 0)).where(MaterialReceipt.project_id == project_id, MaterialReceipt.purchase_item_id == item_id))
        rows = db.execute(select(MaterialWriteoff.qty, MaterialWriteoff.reverses_id).where(MaterialWriteoff.project_id == project_id, MaterialWriteoff.purchase_item_id == item_id)).all()
        return Decimal(receipts) + sum((qty if reverse is not None else -qty for qty, reverse in rows), Decimal(0))


def _result(row):
    return {'id': row.id, 'project_id': row.project_id, 'purchase_item_id': row.purchase_item_id,
            'work_log_entry_id': row.work_log_entry_id, 'operation_key': row.operation_key,
            'qty': format(row.qty, '.3f'), 'unit': row.unit, 'reverses_id': row.reverses_id, 'applied': True}


def record_writeoff(db, project_id, item_id, entry_id, *, operation_key, qty, unit, policy=None):
    # Negative physical stock is never allowed (user decision 06Nov). The
    # supplied/legacy policy is accepted for signature compatibility only and
    # cannot bypass the sufficient-balance check for new consumption.
    policy = policy or get_policy()
    if any(type(value) is not int or value <= 0 for value in [project_id,item_id,entry_id]):
        raise ValueError('Invalid writeoff identity')
    quantity = _quantity(qty)
    key = _text(operation_key,160,'writeoff operation key')
    normalized_unit = _text(unit,40,'writeoff unit')
    with db.no_autoflush:
        _locked_item(db, project_id, item_id)
        from app.models.work_log import WorkLogEntry
        entry = db.scalar(select(WorkLogEntry.id).where(WorkLogEntry.id == entry_id, WorkLogEntry.project_id == project_id).with_for_update())
        if entry is None: raise LookupError('Work log entry not found in project')
        old = db.scalar(select(MaterialWriteoff).where(MaterialWriteoff.project_id == project_id, MaterialWriteoff.operation_key == key))
        if old:
            if (old.purchase_item_id, old.work_log_entry_id, old.qty, old.unit, old.reverses_id) != (item_id, entry_id, quantity, normalized_unit, None):
                raise ValueError('Writeoff operation key conflict')
            # Genuine replay of a stored movement: read-only success, no
            # balance check and no mutation of historical rows.
            return _result(old)
        # A genuine replay is bound to its persisted unit; only NEW consumption
        # is checked against the current material definition (no conversion).
        intent = prepare_writeoff(db, project_id, item_id, entry_id, operation_key=key, qty=quantity, unit=normalized_unit)
        historical_unit = existing_physical_unit(db,project_id,item_id)
        if historical_unit is not None and historical_unit != normalized_unit:
            raise ValueError("Единица движения отличается от истории материала; автоматического пересчёта нет")
        # Physical stock may never go negative for any new writeoff, regardless
        # of supplied policy or legacy explicit opt-in.
        if physical_balance(db, project_id, item_id) < quantity:
            raise ValueError('Insufficient physical stock')
        # Core INSERT does not flush unrelated pending caller objects.
        pk = db.execute(MaterialWriteoff.__table__.insert().values(project_id=project_id, purchase_item_id=item_id,
             work_log_entry_id=entry_id, operation_key=intent['operation_key'], qty=quantity, unit=intent['unit'])).inserted_primary_key[0]
        return _result(db.get(MaterialWriteoff, pk))


def reverse_writeoff(db, project_id, writeoff_id, *, operation_key):
    if type(project_id) is not int or project_id <= 0 or type(writeoff_id) is not int or writeoff_id <= 0:
        raise ValueError('Invalid writeoff identity')
    key = _text(operation_key, 160, 'writeoff reversal key')
    with db.no_autoflush:
        original = db.scalar(select(MaterialWriteoff).where(MaterialWriteoff.id == writeoff_id, MaterialWriteoff.project_id == project_id))
        if original is None: raise LookupError('Writeoff not found in project')
        if original.reverses_id is not None: raise ValueError('Cannot reverse a reversal')
        _quantity(original.qty)
        _text(original.unit,40,'stored writeoff unit')
        _locked_item(db, project_id, original.purchase_item_id)
        old = db.scalar(select(MaterialWriteoff).where(MaterialWriteoff.project_id == project_id, MaterialWriteoff.operation_key == key))
        if old:
            if old.reverses_id != original.id: raise ValueError('Reversal operation key conflict')
            return _result(old)
        if db.scalar(select(MaterialWriteoff.id).where(MaterialWriteoff.reverses_id == original.id)) is not None:
            raise ValueError('Writeoff already reversed')
        pk = db.execute(MaterialWriteoff.__table__.insert().values(project_id=project_id, purchase_item_id=original.purchase_item_id,
             work_log_entry_id=original.work_log_entry_id, operation_key=key, qty=original.qty, unit=original.unit, reverses_id=original.id)).inserted_primary_key[0]
        return _result(db.get(MaterialWriteoff, pk))


def require_deletable_fact(db, project_id, entry_id, *, policy=None):
    policy = policy or get_policy()
    with db.no_autoflush:
        if db.scalar(select(MaterialWriteoff.id).where(MaterialWriteoff.project_id == project_id, MaterialWriteoff.work_log_entry_id == entry_id).limit(1)) is not None:
            raise ValueError('Запись связана со складской историей: удаление запрещено')



def stock_history(db,project_id,item_id,*,limit=25):
    """Bounded recent physical movements; does not infer stock from finance."""
    from sqlalchemy.orm import aliased
    if any(type(v) is not int or v<=0 for v in [project_id,item_id,limit]) or limit>100:
        raise ValueError('Invalid stock history scope/budget')
    with db.no_autoflush:
        item=db.scalar(select(PurchaseItem.id).where(PurchaseItem.id==item_id,PurchaseItem.project_id==project_id))
        if item is None:raise LookupError('Material not found in project')
        arrivals=db.execute(select(MaterialReceipt.id,MaterialReceipt.qty,MaterialReceipt.unit,
            MaterialReceipt.operation_key,MaterialReceipt.source_document_ref,MaterialReceipt.created_at).where(
            MaterialReceipt.project_id==project_id,MaterialReceipt.purchase_item_id==item_id).order_by(MaterialReceipt.created_at.desc(),MaterialReceipt.id.desc()).limit(limit+1)).all()
        other=aliased(MaterialWriteoff)
        reversed_query=select(other.id).where(other.reverses_id==MaterialWriteoff.id,other.project_id==project_id).exists().correlate(MaterialWriteoff)
        consumption=db.execute(select(MaterialWriteoff.id,MaterialWriteoff.qty,MaterialWriteoff.unit,
            MaterialWriteoff.operation_key,MaterialWriteoff.work_log_entry_id,MaterialWriteoff.reverses_id,
            MaterialWriteoff.created_at,reversed_query.label('reversed')).where(
            MaterialWriteoff.project_id==project_id,MaterialWriteoff.purchase_item_id==item_id).order_by(MaterialWriteoff.created_at.desc(),MaterialWriteoff.id.desc()).limit(limit+1)).all()
        rows=[{'id':a.id,'kind':'receipt','qty':format(_quantity(a.qty),'.3f'),'unit':a.unit,
               'operation_key':a.operation_key,'source_document_ref':a.source_document_ref,
               'created_at':a.created_at.isoformat(),'work_log_entry_id':None,'reverses_id':None,'reversed':False} for a in arrivals]
        rows += [{'id':a.id,'kind':'storno' if a.reverses_id is not None else 'writeoff','qty':format(_quantity(a.qty),'.3f'),
                 'unit':a.unit,'operation_key':a.operation_key,'source_document_ref':None,
                 'created_at':a.created_at.isoformat(),'work_log_entry_id':a.work_log_entry_id,
                 'reverses_id':a.reverses_id,'reversed':bool(a.reversed)} for a in consumption]
        rows.sort(key=lambda row:(row['created_at'],row['kind'],row['id']),reverse=True)
        warning=None
        try:
            historical_unit=existing_physical_unit(db,project_id,item_id)
            balance=format(physical_balance(db,project_id,item_id),'.3f')
        except MixedPhysicalUnitsError as exc:
            historical_unit=None;balance=None;warning=str(exc)
        current_unit=db.scalar(select(PurchaseItem.unit).where(PurchaseItem.id==item_id,PurchaseItem.project_id==project_id))
        return {'movements':rows[:limit],'limit':limit,'has_more':len(rows)>limit,
                'unit':historical_unit or current_unit if warning is None else None,
                'balance':balance,'warning':warning}
