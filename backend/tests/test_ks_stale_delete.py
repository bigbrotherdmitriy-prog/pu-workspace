"""A cached draft must not authorize deletion after another transaction signs it."""
from datetime import date
import pytest
from sqlalchemy.orm import Session
from app import estimates as svc
from app.models.estimates import KsAct
from test_estimates import world, _log


def test_stale_draft_cannot_delete_act_signed_by_another_session(world):
    db, pid, eid = world['db'], world['p'].id, world['est']['id']
    _log(db, pid, world['lines'][0]['id'], '2026-10-05', '10')
    act = svc.create_act(db, pid, eid, date(2026,10,1), date(2026,10,31))
    db.commit()
    cached = db.get(KsAct, act['id'])
    assert cached.status == 'draft'
    with Session(db.get_bind()) as other:
        svc.set_act_status(other, pid, act['id'], 'signed')
        other.commit()
    assert cached.status == 'draft'  # Deliberately stale ORM identity map.
    with pytest.raises(ValueError, match='Подписанный'):
        svc.delete_act(db, pid, act['id'])
    db.rollback()
    assert db.get(KsAct, act['id']).status == 'signed'


def test_draft_delete_remains_in_callers_rollback(world):
    db, pid, eid = world['db'], world['p'].id, world['est']['id']
    _log(db, pid, world['lines'][0]['id'], '2026-10-05', '10')
    act = svc.create_act(db, pid, eid, date(2026,10,1), date(2026,10,31))
    db.commit()
    svc.delete_act(db, pid, act['id'])
    assert db.get(KsAct, act['id']) is None
    db.rollback()
    assert db.get(KsAct, act['id']).status == 'draft'
