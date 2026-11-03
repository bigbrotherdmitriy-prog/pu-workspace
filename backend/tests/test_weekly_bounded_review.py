# Codex authorized fallback regressions; DeepSeek PR423 remains unchanged.
import pytest
from test_id_forms import weekly_world, _seed_work, _seed_defect, _seed_photo, _seed_ks, _capture_statements
from app import work_log_form
from app.models.project import Project

@pytest.mark.parametrize("budget,overflow", [(4,False),(3,True)])
def test_bounded_read_preserves_caller_state_and_limits_all_collections(weekly_world,budget,overflow):
    db=weekly_world['db'];p=weekly_world['p1'];pid=p.id
    _seed_work(db,pid,'2026-10-20','Факт')
    defect=_seed_defect(db,pid,1,'Замечание')
    _seed_photo(db,pid,defect.id,'test.jpg',b'abc')
    _seed_ks(db,pid,'1');db.commit()
    org_id=p.organization_id
    p.name='Незаписанное изменение'
    pending=Project(name='Незаписанный проект',organization_id=org_id);db.add(pending)
    statements,detach=_capture_statements(db)
    try:
        if overflow:
            with pytest.raises(ValueError):
                work_log_form.collect_weekly_report_bounded(db,pid,'2026-10-20','2026-10-26',row_budget=budget)
        else:
            report=work_log_form.collect_weekly_report_bounded(db,pid,'2026-10-20','2026-10-26',row_budget=budget)
            assert report['project_name']=='Проект А'
            assert all(len(report[k])==1 for k in ('work_facts','defects','defect_photos','ks_acts'))
    finally:
        detach()
    assert pending.id is None and pending in db.new
    assert p in db.dirty and p.name=='Незаписанное изменение'
    assert not any(s.lstrip().lower().startswith(('insert','update','delete')) for s in statements)
    for table in ('work_log_entries','defects','defect_photos','ks_acts'):
        sql=[s.lower() for s in statements if s.lstrip().lower().startswith('select') and ('from '+table) in s.lower()]
        assert sql,table
        assert all('limit' in s for s in sql),sql
    db.rollback()
    assert pending not in db.new
    assert db.get(Project,pid).name=='Проект А'

@pytest.mark.parametrize('start,end',[('2026-10-26','2026-10-20'),('bad','2026-10-26')])
def test_bounded_invalid_period_rejected_without_sql(weekly_world,start,end):
    db=weekly_world['db'];pid=weekly_world['p1'].id
    statements,detach=_capture_statements(db)
    try:
        with pytest.raises(ValueError):
            work_log_form.collect_weekly_report_bounded(db,pid,start,end,row_budget=4)
    finally:detach()
    assert statements==[]
