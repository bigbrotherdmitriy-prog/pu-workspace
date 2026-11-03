"""Successful customer decisions retain append-only audit events in caller transaction."""
import json
import pytest
from sqlalchemy import select
from app import customer_portal as cp, estimates as est
from app.models.audit_log import AuditLog
from app.models.estimates import KsAct
from test_customer_portal import world


def setup_sent(world):
    db, pid, aid, uid = world['db'], world['p'].id, world['act']['id'], world['customer'].id
    est.set_act_status(db,pid,aid,'sent')
    db.commit()
    return db,pid,aid,uid


def events(db,aid):
    return db.scalars(select(AuditLog).where(AuditLog.action=='customer_act_decision',AuditLog.entity_type=='ks_act',AuditLog.entity_id==aid).order_by(AuditLog.id)).all()


def test_success_has_one_event_and_rejected_repeat_adds_nothing(world):
    db,pid,aid,uid = setup_sent(world)
    cp.decide(db,pid,aid,uid,'approved','Synthetic comment')
    saved = events(db,aid)
    assert len(saved)==1
    assert json.loads(saved[0].details)=={'project_id':pid,'user_id':uid,'decision':'approved','comment':'Synthetic comment'}
    with pytest.raises(ValueError):cp.decide(db,pid,aid,uid,'rejected','Must not append')
    assert len(events(db,aid))==1


def test_history_survives_return_to_draft_and_another_decision(world):
    db,pid,aid,uid = setup_sent(world)
    cp.decide(db,pid,aid,uid,'approved',None)
    db.commit()
    assert len(events(db,aid))==1
    original = events(db,aid)[0].details
    est.set_act_status(db,pid,aid,'draft')
    est.set_act_status(db,pid,aid,'sent')
    cp.decide(db,pid,aid,uid,'rejected','Synthetic correction')
    assert len(events(db,aid))==2
    assert events(db,aid)[0].details==original
    assert json.loads(events(db,aid)[1].details)['decision']=='rejected'


def test_audit_event_and_decision_roll_back_together(world):
    db,pid,aid,uid = setup_sent(world)
    cp.decide(db,pid,aid,uid,'approved',None)
    assert len(events(db,aid))==1
    db.rollback()
    assert events(db,aid)==[]
    assert db.get(KsAct,aid).customer_status=='pending'
