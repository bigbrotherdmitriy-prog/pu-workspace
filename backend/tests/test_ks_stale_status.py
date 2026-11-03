"""Signed status cannot be downgraded through an older cached draft."""
from datetime import date
import pytest
from sqlalchemy.orm import Session
from app import estimates as svc
from app.models.estimates import KsAct
from test_estimates import world, _log


def test_stale_draft_cannot_downgrade_act_signed_by_another_session(world):
    db, pid, eid = world['db'], world['p'].id, world['est']['id']
    _log(db, pid, world['lines'][0]['id'], '2026-10-05', '10')
    act = svc.create_act(db, pid, eid, date(2026,10,1), date(2026,10,31))
    db.commit()
    cached = db.get(KsAct, act['id'])
    assert cached.status == 'draft'
    with Session(db.get_bind()) as other:
        svc.set_act_status(other, pid, act['id'], 'signed')
        other.commit()
    assert cached.status == 'draft'
    with pytest.raises(ValueError, match='Подписанный'):
        svc.set_act_status(db, pid, act['id'], 'draft')
    db.rollback()
    assert db.get(KsAct, act['id']).status == 'signed'


def test_status_transition_remains_in_callers_rollback(world):
    db, pid, eid = world['db'], world['p'].id, world['est']['id']
    _log(db, pid, world['lines'][0]['id'], '2026-10-05', '10')
    act = svc.create_act(db, pid, eid, date(2026,10,1), date(2026,10,31))
    db.commit()
    assert svc.set_act_status(db, pid, act['id'], 'sent')['status'] == 'sent'
    db.rollback()
    assert db.get(KsAct, act['id']).status == 'draft'
