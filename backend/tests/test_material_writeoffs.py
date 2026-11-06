from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import select, func
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.purchase import PurchaseItem, MaterialWriteoff
from app.models.work_log import WorkLogEntry
from app.material_receipts import record_receipt
from app.material_writeoffs import record_writeoff, reverse_writeoff, physical_balance
from app.provisional_policy import ProvisionalPolicy
from app.work_log import delete_entry


@pytest.fixture
def stock(db_session):
    db=db_session
    org=Organization(name='SYNTHETIC stock');db.add(org);db.flush()
    p=Project(name='SYNTHETIC own',organization_id=org.id);other=Project(name='SYNTHETIC other',organization_id=org.id);db.add_all([p,other]);db.flush()
    item=PurchaseItem(project_id=p.id,group_key='synthetic',name='SYNTHETIC',unit='m',status='paid',qty_ordered=100)
    entry=WorkLogEntry(project_id=p.id,work_date=date(2026,10,5),work_name='SYNTHETIC',qty=1)
    foreign=WorkLogEntry(project_id=other.id,work_date=date(2026,10,5),work_name='SYNTHETIC foreign',qty=1)
    db.add_all([item,entry,foreign]);db.commit()
    return db,p.id,item.id,entry.id,foreign.id


def consume(stock, key='synthetic-consume', qty='2.000', **kwargs):
    db,p,item,entry,_=stock
    return record_writeoff(db,p,item,entry,operation_key=key,qty=qty,unit='m',**kwargs)


def arrive(stock):
    db,p,item,entry,_=stock
    record_receipt(db,p,item,operation_key='synthetic-arrive',qty='3',unit='m')


def _ledger_snapshot(stock):
    db,p,item,entry,_=stock
    rows=db.execute(select(MaterialWriteoff.id,MaterialWriteoff.operation_key,
        MaterialWriteoff.qty,MaterialWriteoff.unit,MaterialWriteoff.reverses_id)
        .where(MaterialWriteoff.project_id==p,MaterialWriteoff.purchase_item_id==item)
        .order_by(MaterialWriteoff.id)).all()
    return [(r.id,r.operation_key,Decimal(r.qty),r.unit,r.reverses_id) for r in rows]


def _count(stock):
    return stock[0].scalar(select(func.count()).select_from(MaterialWriteoff))


def test_paid_purchase_is_not_physical_stock(stock):
    with pytest.raises(ValueError,match='Insufficient'):consume(stock)
    assert stock[0].scalar(select(func.count()).select_from(MaterialWriteoff))==0


def test_idempotent_explicit_consumption_conflict_and_storno(stock):
    arrive(stock);first=consume(stock);assert consume(stock)==first
    db,p,item,entry,_=stock
    assert physical_balance(db,p,item)==Decimal('1')
    with pytest.raises(ValueError,match='conflict'):consume(stock,qty='1')
    reverse=reverse_writeoff(db,p,first['id'],operation_key='synthetic-reverse')
    assert reverse_writeoff(db,p,first['id'],operation_key='synthetic-reverse')==reverse
    assert physical_balance(db,p,item)==Decimal('3')
    assert db.scalar(select(func.count()).select_from(MaterialWriteoff))==2
    with pytest.raises(ValueError,match='already reversed'):reverse_writeoff(db,p,first['id'],operation_key='second-reverse')
    with pytest.raises(ValueError,match='reverse a reversal'):reverse_writeoff(db,p,reverse['id'],operation_key='bad')


@pytest.mark.parametrize('policy',[
    None,
    ProvisionalPolicy(negative_stock='forbid'),
    ProvisionalPolicy(negative_stock='allow_explicit'),
])
def test_overdraw_rejected_for_all_policies(stock,policy):
    arrive(stock)
    db,p,item,entry,_=stock
    before=_ledger_snapshot(stock)
    assert before==[] and _count(stock)==0
    assert physical_balance(db,p,item)==Decimal('3')
    with pytest.raises(ValueError,match='Insufficient'):
        consume(stock,qty='4',policy=policy)
    assert _ledger_snapshot(stock)==before
    assert _count(stock)==0
    assert physical_balance(db,p,item)==Decimal('3')


def test_overdraw_rejected_with_monkeypatched_env_allow_explicit(stock,monkeypatch):
    monkeypatch.setenv('PU_NEGATIVE_STOCK_POLICY','allow_explicit')
    arrive(stock)
    db,p,item,entry,_=stock
    before=_ledger_snapshot(stock)
    assert before==[] and _count(stock)==0
    with pytest.raises(ValueError,match='Insufficient'):
        consume(stock,qty='4')
    assert _ledger_snapshot(stock)==before
    assert _count(stock)==0
    assert physical_balance(db,p,item)==Decimal('3')


def test_exact_available_quantity_succeeds_then_further_rejected(stock):
    arrive(stock)
    db,p,item,entry,_=stock
    result=consume(stock,qty='3')
    assert result['qty']=='3.000'
    assert physical_balance(db,p,item)==Decimal('0')
    assert _count(stock)==1
    before=_ledger_snapshot(stock)
    with pytest.raises(ValueError,match='Insufficient'):
        consume(stock,key='synthetic-consume-2',qty='0.001')
    assert _ledger_snapshot(stock)==before
    assert _count(stock)==1
    assert physical_balance(db,p,item)==Decimal('0')


def test_genuine_replay_succeeds_after_later_drain_to_zero(stock):
    arrive(stock)
    db,p,item,entry,_=stock
    first=consume(stock,qty='2')
    assert physical_balance(db,p,item)==Decimal('1')
    consume(stock,key='synthetic-consume-drain',qty='1')
    assert physical_balance(db,p,item)==Decimal('0')
    before=_ledger_snapshot(stock)
    assert _count(stock)==2
    replay=consume(stock,qty='2')
    assert replay==first
    assert _ledger_snapshot(stock)==before
    assert _count(stock)==2
    assert physical_balance(db,p,item)==Decimal('0')


def test_replay_payload_conflict_rejected_without_mutation(stock):
    arrive(stock)
    db,p,item,entry,_=stock
    consume(stock,qty='2')
    before=_ledger_snapshot(stock)
    assert _count(stock)==1
    with pytest.raises(ValueError,match='conflict'):
        consume(stock,qty='1')
    assert _ledger_snapshot(stock)==before
    assert _count(stock)==1
    assert physical_balance(db,p,item)==Decimal('1')


def test_foreign_fact_rejected_without_movement(stock):
    db,p,item,entry,foreign=stock;arrive(stock)
    with pytest.raises(LookupError):record_writeoff(db,p,item,foreign,operation_key='bad',qty=1,unit='m')
    assert db.scalar(select(func.count()).select_from(MaterialWriteoff))==0


@pytest.mark.parametrize('qty',['NaN','Infinity','0','-1','0.0001',True])
def test_invalid_quantity_preserves_ledger(stock,qty):
    arrive(stock)
    with pytest.raises(ValueError):consume(stock,qty=qty)
    assert stock[0].scalar(select(func.count()).select_from(MaterialWriteoff))==0


def test_fact_with_historical_consumption_cannot_be_deleted_even_after_storno(stock):
    arrive(stock);first=consume(stock);db,p,item,entry,_=stock
    with pytest.raises(ValueError,match='складской историей'):delete_entry(db,p,entry)
    reverse_writeoff(db,p,first['id'],operation_key='reverse')
    with pytest.raises(ValueError,match='складской историей'):delete_entry(db,p,entry)
    assert db.get(WorkLogEntry,entry) is not None


def test_caller_rollback_removes_admitted_movement(stock):
    arrive(stock);stock[0].commit();consume(stock);stock[0].rollback()
    assert stock[0].scalar(select(func.count()).select_from(MaterialWriteoff))==0


def test_replay_preserves_historical_unit_after_material_unit_changes(stock):
    arrive(stock);first=consume(stock);db,p,item,entry,_=stock
    db.get(PurchaseItem,item).unit='kg';db.flush()
    assert consume(stock)==first
    with pytest.raises(ValueError,match='unit must match'):
        consume(stock,key='new-consumption')
    reverse_writeoff(db,p,first['id'],operation_key='historical-unit-storno')
    assert physical_balance(db,p,item)==Decimal('3')


def test_recent_history_binds_scope_reversals_and_physical_balance(stock):
    from app.material_writeoffs import stock_history
    arrive(stock);first=consume(stock);db,p,item,entry,_=stock
    reverse_writeoff(db,p,first['id'],operation_key='history-storno')
    history=stock_history(db,p,item,limit=25)
    assert history['balance']=='3.000' and history['unit']=='m'
    assert len(history['movements'])==3
    original=next(row for row in history['movements'] if row['kind']=='writeoff')
    assert original['reversed'] is True
    assert next(row for row in history['movements'] if row['kind']=='storno')['reverses_id']==first['id']
    page=stock_history(db,p,item,limit=1)
    assert len(page['movements'])==1 and page['has_more'] is True
    with pytest.raises(LookupError):stock_history(db,p+999,item,limit=25)
    for limit in [0,101,True]:
        with pytest.raises(ValueError):stock_history(db,p,item,limit=limit)


def test_new_arrivals_cannot_mix_historical_units_after_material_unit_change(stock):
    from app.material_writeoffs import stock_history
    arrive(stock);first=consume(stock);db,p,item,entry,_=stock
    db.get(PurchaseItem,item).unit='kg';db.flush()
    with pytest.raises(ValueError,match='истории материала'):
        record_receipt(db,p,item,operation_key='new-different-unit',qty='1',unit='kg')
    assert record_receipt(db,p,item,operation_key='synthetic-arrive',qty='3',unit='m')['qty']=='3.000'
    assert consume(stock)==first
    reverse_writeoff(db,p,first['id'],operation_key='unit-storno')
    assert stock_history(db,p,item,limit=25)['unit']=='m'


def test_legacy_mixed_units_remain_auditable_without_a_fake_sum(stock):
    from app.models.purchase import MaterialReceipt
    from app.material_writeoffs import stock_history
    from app.material_receipts import MixedPhysicalUnitsError
    arrive(stock);db,p,item,entry,_=stock
    # Historical fixture bypasses new safe admission to exercise legacy recovery.
    db.add(MaterialReceipt(project_id=p,purchase_item_id=item,operation_key='SYNTHETIC-legacy-mixed',qty=2,unit='kg'));db.flush()
    history=stock_history(db,p,item,limit=25)
    assert len(history['movements'])==2 and history['balance'] is None and history['unit'] is None
    assert history['warning'] and {row['unit'] for row in history['movements']}=={'m','kg'}
    with pytest.raises(MixedPhysicalUnitsError):physical_balance(db,p,item)
