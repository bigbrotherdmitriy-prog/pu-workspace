"""Finite-value validation must reject before persistence, using the API's ValueError contract."""
import pytest
from sqlalchemy import select,func
from app import work_log as wl
from app.models.work_log import WorkLogEntry,WorkLogLabor
from test_mobile_work_log_service import world  # Existing FK-enabled synthetic fixture.


def parse(field,value):
    if field=='qty':return wl._parse_qty(value)
    if field=='hours':return wl._clean({'hours':value},partial=True)
    row={'person_name':'Synthetic worker','hours':'1'}
    row['hours' if field=='labor_hours' else 'hourly_rate']=value
    return wl._parse_labor([row])


@pytest.mark.parametrize('field',['qty','hours','labor_hours','hourly_rate'])
@pytest.mark.parametrize('value',['NaN','sNaN','Infinity','-Infinity',float('nan'),float('inf')])
def test_nonfinite_values_raise_validation_error(field,value):
    with pytest.raises(ValueError):parse(field,value)


@pytest.mark.parametrize('field',['qty','hours','labor_hours','hourly_rate'])
@pytest.mark.parametrize('value',['NaN','Infinity'])
def test_nonfinite_values_do_not_create_partial_journal(world,field,value):
    db,actor,project,_=world
    data={'work_date':'2026-10-30','work_name':'Synthetic finite check','qty':'1'}
    if field in ['qty','hours']:data[field]=value
    else:data['labor']=[{'person_name':'Synthetic worker','hours':value if field=='labor_hours' else '1','hourly_rate':value if field=='hourly_rate' else None}]
    with pytest.raises(ValueError):wl.add_entry(db,project.id,actor.id,data)
    assert db.scalar(select(func.count()).select_from(WorkLogEntry))==0
    assert db.scalar(select(func.count()).select_from(WorkLogLabor))==0
    valid=wl.add_entry(db,project.id,actor.id,{'work_date':'2026-10-30','work_name':'Valid after rejection','qty':'1'})
    assert valid['qty']=='1.000' and db.is_active


def test_zero_optional_and_comma_behavior_preserved():
    assert wl._parse_qty(None) is None and wl._parse_qty('') is None
    assert wl._parse_qty('0')==0 and str(wl._parse_qty('1,250'))=='1.250'
    assert wl._clean({'hours':'0'},partial=True)['hours']==0
    assert wl._parse_labor([{'person_name':'Synthetic','hours':'0','hourly_rate':'0'}])[0]['hourly_rate']==0
