"""Bounded decision history reads committed project-scoped state without flushing callers."""
import json
import pytest
from app import customer_portal as cp, estimates as est
from app.models.audit_log import AuditLog
from app.models.project import Project
from test_customer_portal import world
from test_customer_decision_audit import setup_sent
from test_id_forms import _capture_statements


def test_history_whitelists_fields_and_ignores_malformed_or_foreign_payload(world):
    db,pid,aid,uid = setup_sent(world)
    cp.decide(db,pid,aid,uid,'approved','Synthetic history')
    for payload in ['not json',json.dumps({'project_id':world['o'].id,'user_id':uid,'decision':'approved','comment':'Foreign synthetic'}),json.dumps(['wrong shape'])]:
        db.add(AuditLog(action='customer_act_decision',entity_type='ks_act',entity_id=aid,details=payload))
    db.commit()
    result = cp.decision_history(db,pid,aid)
    assert len(result['events'])==1 and result['has_more'] is False
    event = result['events'][0]
    assert set(event)=={'id','created_at','user_id','decision','comment'}
    assert event['decision']=='approved' and event['user_id']==uid and event['comment']=='Synthetic history'


@pytest.mark.parametrize('scope',['foreign','missing','draft'])
def test_history_rejects_nonvisible_or_foreign_act(world,scope):
    db,pid,aid,uid = setup_sent(world)
    if scope=='foreign':pid=world['o'].id
    elif scope=='missing':aid=-1
    else:est.set_act_status(db,pid,aid,'draft');db.commit()
    with pytest.raises(LookupError):cp.decision_history(db,pid,aid)


def test_history_limits_sql_and_does_not_flush_caller_state(world):
    db,pid,aid,uid = setup_sent(world)
    cp.decide(db,pid,aid,uid,'approved',None)
    db.commit()
    project = world['p']
    org_id = project.organization_id
    project.name='Pending synthetic rename'
    pending = Project(name='Pending synthetic project',organization_id=org_id)
    db.add(pending)
    statements,detach = _capture_statements(db)
    try:result=cp.decision_history(db,pid,aid,limit=1)
    finally:detach()
    assert len(result['events'])==1
    assert pending.id is None and pending in db.new and project in db.dirty
    assert not any(sql.lstrip().lower().startswith(('insert','update','delete')) for sql in statements)
    selects=[sql.lower() for sql in statements if 'from audit_logs' in sql.lower()]
    assert selects and all('limit' in sql for sql in selects)


@pytest.mark.parametrize('limit',[0,-1,True,101,'1'])
def test_history_rejects_invalid_limit_before_query(world,limit):
    db,pid,aid,uid = setup_sent(world)
    with pytest.raises(ValueError):cp.decision_history(db,pid,aid,limit=limit)


@pytest.mark.parametrize('malformed',[json.dumps({'project_id':1,'user_id':1,'decision':[]}), '['*1100])
def test_malformed_nested_payload_cannot_break_history(world,malformed):
    db,pid,aid,uid = setup_sent(world)
    db.add(AuditLog(action='customer_act_decision',entity_type='ks_act',entity_id=aid,details=malformed))
    db.commit()
    assert cp.decision_history(db,pid,aid)['events']==[]


def test_history_reports_bounded_window_and_newest_event_first(world):
    db,pid,aid,uid = setup_sent(world)
    cp.decide(db,pid,aid,uid,'approved',None)
    est.set_act_status(db,pid,aid,'draft')
    est.set_act_status(db,pid,aid,'sent')
    cp.decide(db,pid,aid,uid,'rejected','Synthetic correction')
    db.commit()
    result=cp.decision_history(db,pid,aid,limit=1)
    assert result['has_more'] is True
    assert len(result['events'])==1 and result['events'][0]['decision']=='rejected'
