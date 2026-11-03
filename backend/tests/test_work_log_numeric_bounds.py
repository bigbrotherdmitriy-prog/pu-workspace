"""Schema-derived limits, independent of estimate pricing/rounding policy."""
from decimal import Decimal
import pytest
from sqlalchemy import select, func
from app import work_log as wl
from app.models.work_log import WorkLogEntry, WorkLogLabor
from test_mobile_work_log_service import world


@pytest.mark.parametrize('field,value', [('qty','1000000000000000'),('qty','1e100'),('hourly_rate','10000000000'),('hourly_rate','1e100')])
def test_storage_overflow_rejected_before_persistence(world, field, value):
    db, actor, project, _ = world
    data = {'work_date':'2026-10-30','work_name':'Synthetic overflow','qty':'1'}
    if field == 'qty':
        data[field] = value
    else:
        data['labor'] = [{'person_name':'Synthetic','hours':'1','hourly_rate':value}]
    with pytest.raises(ValueError):
        wl.add_entry(db, project.id, actor.id, data)
    assert db.scalar(select(func.count()).select_from(WorkLogEntry)) == 0
    assert db.scalar(select(func.count()).select_from(WorkLogLabor)) == 0
    assert db.is_active


def test_exact_schema_maxima_parse_without_float_conversion():
    assert wl._parse_qty('999999999999999.999') == Decimal('999999999999999.999')
    assert wl._parse_labor([{'person_name':'Synthetic','hours':'1','hourly_rate':'9999999999.99'}])[0]['hourly_rate'] == Decimal('9999999999.99')
    assert WorkLogEntry.__table__.c.qty.type.precision == 18
    assert WorkLogEntry.__table__.c.qty.type.scale == 3
    assert WorkLogLabor.__table__.c.hourly_rate.type.precision == 12
    assert WorkLogLabor.__table__.c.hourly_rate.type.scale == 2


@pytest.mark.parametrize('field,value', [('qty','1000000000000000'),('qty','1e100'),('hourly_rate','10000000000'),('hourly_rate','1e100')])
def test_rejected_update_preserves_fact_and_labor(world, field, value):
    db, actor, project, _ = world
    before = wl.add_entry(db, project.id, actor.id, {'work_date':'2026-10-30','work_name':'Original synthetic fact','qty':'2','labor':[{'person_name':'Original synthetic worker','hours':'1','hourly_rate':'3'}]})
    data = {'work_name':'Must not persist'}
    if field == 'qty':
        data['qty'] = value
    else:
        data['labor'] = [{'person_name':'Must not replace','hours':'1','hourly_rate':value}]
    with pytest.raises(ValueError):
        wl.update_entry(db, project.id, before['id'], data)
    db.flush()
    db.expire_all()
    actual = wl._to_dict(db.get(WorkLogEntry, before['id']), wl._labor_of(db, before['id']))
    assert actual == before
    assert db.is_active
