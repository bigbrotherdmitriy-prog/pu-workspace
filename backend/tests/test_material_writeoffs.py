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


def test_overdraw_default_forbidden_and_explicit_opt_in(stock):
    arrive(stock)
    with pytest.raises(ValueError,match='Insufficient'):consume(stock,qty='4')
    consume(stock,qty='4',policy=ProvisionalPolicy(negative_stock='allow_explicit'))
    assert physical_balance(stock[0],stock[1],stock[2])==Decimal('-1')


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
