from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import Session

import app.models
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.purchase import MaterialReceipt, PurchaseItem
from app.material_receipts import record_receipt


@pytest.fixture
def world():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, record):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name='Synthetic receipt org')
        db.add(org)
        db.flush()
        first = Project(name='Own', organization_id=org.id)
        other = Project(name='Other', organization_id=org.id)
        db.add_all([first, other])
        db.flush()
        item = PurchaseItem(project_id=first.id, group_key='own', name='Material', unit='м',
                            qty_needed=Decimal('10'), qty_ordered=Decimal('10'), status='paid')
        foreign = PurchaseItem(project_id=other.id, group_key='other', name='Foreign', unit='м')
        db.add_all([item, foreign])
        db.commit()
        yield db, first.id, other.id, item.id, foreign.id
    engine.dispose()


def _record(world, **changes):
    db, pid, other, item, foreign = world
    args = dict(operation_key='delivery-1-line-1', qty='1.250', unit='м', source_document_ref='Delivery 1')
    args.update(changes)
    return record_receipt(db, pid, item, **args)


def test_explicit_receipt_replay_and_finance_independence(world):
    db, pid, other, item_id, foreign = world
    assert db.scalar(select(func.count()).select_from(MaterialReceipt)) == 0
    item = db.get(PurchaseItem, item_id)
    before = item.status, item.qty_needed, item.qty_ordered
    first = _record(world)
    second = _record(world, qty=Decimal('1.2500'))
    assert second == first and first['qty'] == '1.250'
    assert db.scalar(select(func.count()).select_from(MaterialReceipt)) == 1
    db.refresh(item)
    assert (item.status, item.qty_needed, item.qty_ordered) == before


@pytest.mark.parametrize('qty', ['0', '-1', 'NaN', 'sNaN', 'Infinity', '-Infinity', '1e100', '0.0001', '1e-1000', True, None])
def test_invalid_quantity_never_writes(world, qty):
    db = world[0]
    with pytest.raises(ValueError):
        _record(world, qty=qty)
    assert db.scalar(select(func.count()).select_from(MaterialReceipt)) == 0


def test_sqlite_lossy_extreme_rejected_before_write(world):
    with pytest.raises(ValueError, match='cannot represent'):
        _record(world, qty='999999999999999.999')
    assert world[0].scalar(select(func.count()).select_from(MaterialReceipt)) == 0


def test_foreign_material_is_not_receipted(world):
    db, pid, other, item, foreign = world
    with pytest.raises(LookupError):
        record_receipt(db, pid, foreign, operation_key='key', qty='1', unit='м')
    assert db.scalar(select(func.count()).select_from(MaterialReceipt)) == 0


@pytest.mark.parametrize('change', [{'qty': '2'}, {'unit': 'кг'}, {'source_document_ref': 'Different'}])
def test_conflicting_repeat_preserves_original(world, change):
    first = _record(world)
    with pytest.raises(ValueError):
        _record(world, **change)
    assert _record(world) == first
    assert world[0].scalar(select(func.count()).select_from(MaterialReceipt)) == 1


def test_service_does_not_flush_or_discard_caller_state(world):
    db, pid, other, item_id, foreign = world
    item = db.get(PurchaseItem, item_id)
    item.note = 'Pending caller note'
    pending = PurchaseItem(project_id=pid, group_key='pending', name='Pending', unit='м')
    db.add(pending)
    with pytest.raises(ValueError):
        _record(world, unit='wrong')
    assert item in db.dirty and pending in db.new and pending.id is None
    with db.no_autoflush:
        assert db.scalar(select(PurchaseItem.note).where(PurchaseItem.id == item_id)) is None
    _record(world)
    assert item in db.dirty and pending in db.new and pending.id is None
    with db.no_autoflush:
        assert db.scalar(select(PurchaseItem.note).where(PurchaseItem.id == item_id)) is None


def test_receipt_is_not_committed_outside_caller_transaction(world):
    db = world[0]
    _record(world)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(MaterialReceipt)) == 0


def _concurrent_receipt_worker(path, pid, item_id, qty, ready, start, queue):
    engine = create_engine('sqlite+pysqlite:///' + str(path), connect_args={'timeout': 5})
    try:
        with Session(engine) as db:
            ready.set()
            if not start.wait(5):
                raise RuntimeError('start timeout')
            try:
                result = record_receipt(db, pid, item_id, operation_key='concurrent-delivery', qty=qty, unit='м')
                db.commit()
                queue.put(('ok', result['id'], result['qty']))
            except ValueError:
                db.rollback()
                queue.put(('conflict', None, None))
    except BaseException as exc:
        queue.put(('error', type(exc).__name__, None))
    finally:
        engine.dispose()


@pytest.mark.parametrize('quantities', [('1.250', '1.250'), ('1.250', '2.500')])
def test_concurrent_material_receipt_is_single_operation(tmp_path, quantities):
    import multiprocessing
    path = tmp_path / 'receipt-concurrency.sqlite'
    engine = create_engine('sqlite+pysqlite:///' + str(path))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name='Concurrent synthetic org')
        db.add(org)
        db.flush()
        project = Project(name='Concurrent synthetic project', organization_id=org.id)
        db.add(project)
        db.flush()
        item = PurchaseItem(project_id=project.id, group_key='material', name='Material', unit='м', status='paid', qty_ordered=Decimal('10'))
        db.add(item)
        db.flush()
        pid, item_id = project.id, item.id
        db.commit()
    engine.dispose()
    ctx = multiprocessing.get_context('fork')
    queue, start = ctx.Queue(), ctx.Event()
    ready = [ctx.Event(), ctx.Event()]
    workers = [ctx.Process(target=_concurrent_receipt_worker, args=(path, pid, item_id, qty, ready[index], start, queue)) for index, qty in enumerate(quantities)]
    try:
        for worker in workers:
            worker.start()
        assert all(event.wait(5) for event in ready)
        start.set()
        for worker in workers:
            worker.join(8)
        assert all(not worker.is_alive() and worker.exitcode == 0 for worker in workers)
        outcomes = [queue.get(timeout=1) for _ in workers]
        if quantities[0] == quantities[1]:
            assert outcomes[0] == outcomes[1] and outcomes[0][0] == 'ok', outcomes
        else:
            assert sorted(row[0] for row in outcomes) == ['conflict', 'ok'], outcomes
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(MaterialReceipt)) == 1
            receipt = db.scalar(select(MaterialReceipt))
            winner = next(row for row in outcomes if row[0] == 'ok')
            assert receipt.id == winner[1] and format(receipt.qty, '.3f') == winner[2]
            item = db.get(PurchaseItem, item_id)
            assert item.status == 'paid' and item.qty_ordered == Decimal('10')
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            if worker.pid is not None:
                worker.join(2)
        queue.close()
        queue.join_thread()
        engine.dispose()


def _writeoff_world(world):
    from datetime import date
    from app.models.work_log import WorkLogEntry
    db, pid, other, material, foreign_material = world
    own = WorkLogEntry(project_id=pid, work_date=date(2026, 11, 1), work_name='Synthetic work', unit='м²', qty=Decimal('9'))
    foreign = WorkLogEntry(project_id=other, work_date=date(2026, 11, 1), work_name='Foreign work', unit='м²', qty=Decimal('9'))
    db.add_all([own, foreign])
    db.commit()
    return own.id, foreign.id


def test_prepare_writeoff_uses_explicit_material_quantity_without_applying(world):
    from app.material_receipts import prepare_writeoff
    entry, _ = _writeoff_world(world)
    db, pid, other, material, foreign = world
    result = prepare_writeoff(db, pid, material, entry, operation_key='consume-1', qty='1.250', unit='м')
    assert result == {'project_id': pid, 'purchase_item_id': material, 'work_log_entry_id': entry,
                      'operation_key': 'consume-1', 'qty': '1.250', 'unit': 'м', 'applied': False}
    assert db.scalar(select(func.count()).select_from(MaterialReceipt)) == 0
    assert db.get(PurchaseItem, material).status == 'paid'


@pytest.mark.parametrize('foreign_side', ['material', 'work'])
def test_prepare_writeoff_rejects_cross_project_inputs(world, foreign_side):
    from app.material_receipts import prepare_writeoff
    entry, foreign_entry = _writeoff_world(world)
    db, pid, other, material, foreign_material = world
    with pytest.raises(LookupError):
        prepare_writeoff(db, pid, foreign_material if foreign_side == 'material' else material,
                         foreign_entry if foreign_side == 'work' else entry,
                         operation_key='consume-1', qty='1', unit='м')


@pytest.mark.parametrize('changes', [
    {'qty': 'NaN'}, {'qty': 'Infinity'}, {'qty': '-1'}, {'qty': '0'},
    {'qty': '0.0001'}, {'qty': True}, {'unit': 'кг'}, {'operation_key': ''},
])
def test_prepare_writeoff_rejects_invalid_command(world, changes):
    from app.material_receipts import prepare_writeoff
    entry, _ = _writeoff_world(world)
    db, pid, other, material, foreign = world
    command = {'operation_key': 'consume-1', 'qty': '1', 'unit': 'м'}
    command.update(changes)
    with pytest.raises(ValueError):
        prepare_writeoff(db, pid, material, entry, **command)
    assert db.scalar(select(func.count()).select_from(MaterialReceipt)) == 0


def test_prepare_writeoff_is_readonly_and_preserves_pending_caller(world):
    from app.material_receipts import prepare_writeoff
    from app.models.work_log import WorkLogEntry
    entry, _ = _writeoff_world(world)
    db, pid, other, material_id, foreign = world
    material = db.get(PurchaseItem, material_id)
    work = db.get(WorkLogEntry, entry)
    material.note = 'Pending material edit'
    work.note = 'Pending work edit'
    pending = PurchaseItem(project_id=pid, group_key='pending-command', name='Pending', unit='м')
    db.add(pending)
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    engine = db.get_bind()
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        result = prepare_writeoff(db, pid, material_id, entry, operation_key='consume-1', qty='1', unit='м')
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert result['applied'] is False
    assert material in db.dirty and work in db.dirty and pending in db.new and pending.id is None
    assert statements and all(not sql.lstrip().lower().startswith(('insert', 'update', 'delete')) for sql in statements)
    with db.no_autoflush:
        assert db.scalar(select(PurchaseItem.note).where(PurchaseItem.id == material_id)) is None
        assert db.scalar(select(WorkLogEntry.note).where(WorkLogEntry.id == entry)) is None


def test_receipt_history_pages_do_not_duplicate_when_new_receipt_arrives(world):
    from app.material_receipts import receipt_history
    db, pid, other, material, foreign = world
    original = [_record(world, operation_key=f'row-{i}', source_document_ref=f'Note {i}') for i in range(5)]
    record_receipt(db, other, foreign, operation_key='foreign', qty='100', unit='м')
    db.commit()
    first = receipt_history(db, pid, limit=2)
    assert [row['id'] for row in first['items']] == [original[4]['id'], original[3]['id']]
    latest = _record(world, operation_key='new-after-page')
    db.commit()
    second = receipt_history(db, pid, limit=2, before_id=first['next_before_id'])
    third = receipt_history(db, pid, limit=2, before_id=second['next_before_id'])
    ids = [row['id'] for row in first['items'] + second['items'] + third['items']]
    assert ids == [row['id'] for row in reversed(original)]
    assert len(ids) == len(set(ids)) == 5 and latest['id'] not in ids
    assert third['next_before_id'] is None
    assert first['items'][0] == original[4]


@pytest.mark.parametrize('kwargs', [{'limit': 0}, {'limit': 101}, {'limit': True},
                                   {'limit': '2'}, {'limit': 1, 'before_id': 0},
                                   {'limit': 1, 'before_id': True}, {'limit': 1, 'before_id': '2'}])
def test_receipt_history_rejects_invalid_pagination(world, kwargs):
    from app.material_receipts import receipt_history
    with pytest.raises(ValueError):
        receipt_history(world[0], world[1], **kwargs)


def test_receipt_history_empty_and_unknown_project(world):
    from app.material_receipts import receipt_history
    assert receipt_history(world[0], world[1], limit=1) == {'project_id': world[1], 'items': [], 'next_before_id': None}
    with pytest.raises(LookupError):
        receipt_history(world[0], 999999, limit=1)


def test_receipt_history_readonly_bounded_and_no_pending_flush(world):
    from app.material_receipts import receipt_history
    db, pid, other, item_id, foreign = world
    row = _record(world)
    db.commit()
    item = db.get(PurchaseItem, item_id)
    item.note = 'Pending caller edit'
    pending = MaterialReceipt(project_id=pid, purchase_item_id=item_id, operation_key='pending-history', qty=Decimal('9'), unit='м')
    db.add(pending)
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    engine = db.get_bind()
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        page = receipt_history(db, pid, limit=1)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert page['items'] == [row]
    assert item in db.dirty and pending in db.new and pending.id is None
    assert all(not sql.lstrip().lower().startswith(('insert', 'update', 'delete')) for sql in statements)
    query = next(sql.lower() for sql in statements if 'from material_receipts' in sql.lower())
    assert 'limit' in query and query.count('substr(') == 3
    with db.no_autoflush:
        assert db.scalar(select(PurchaseItem.note).where(PurchaseItem.id == item_id)) is None


@pytest.mark.parametrize('corruption', ['foreign-material', 'large-document'])
def test_receipt_history_rejects_corrupt_record(world, corruption):
    from app.material_receipts import receipt_history
    db, pid, other, item_id, foreign = world
    receipt = MaterialReceipt(project_id=pid, purchase_item_id=foreign if corruption == 'foreign-material' else item_id,
                              operation_key='corrupt', qty=Decimal('1'), unit='м',
                              source_document_ref='X' * 100000 if corruption == 'large-document' else None)
    db.add(receipt)
    db.commit()
    with pytest.raises(ValueError):
        receipt_history(db, pid, limit=1)


def test_historical_replay_survives_current_unit_change(world):
    db, pid, other, item_id, foreign = world
    original = _record(world)
    db.commit()
    db.get(PurchaseItem, item_id).unit = 'шт'
    db.commit()
    assert _record(world) == original
    with pytest.raises(ValueError):
        _record(world, qty='2')
    with pytest.raises(ValueError):
        _record(world, unit='шт')
    with pytest.raises(ValueError, match='unit must match'):
        _record(world, operation_key='new-arrival-old-unit')
    # Provisional full stock ledger rejects adding incomparable units. A genuine
    # historical replay still returns the exact original receipt above.
    with pytest.raises(ValueError, match='истории материала'):
        _record(world, operation_key='new-arrival-current-unit', unit='шт')
    assert db.scalar(select(func.count()).select_from(MaterialReceipt)) == 1
    with pytest.raises(LookupError):
        record_receipt(db, other, item_id, operation_key='delivery-1-line-1', qty='1.250', unit='м')
