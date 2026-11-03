"""A decision must obey current database state rather than a cached sent act."""
import pytest
from sqlalchemy.orm import Session
from app import customer_portal as cp, estimates as est
from app.models.estimates import KsAct
from test_customer_portal import world


@pytest.mark.parametrize('other_action',['approved','signed','draft'])
def test_stale_customer_cannot_override_newer_state(world, other_action):
    db, pid, aid, uid = world['db'], world['p'].id, world['act']['id'], world['customer'].id
    est.set_act_status(db, pid, aid, 'sent')
    db.commit()
    cached = db.get(KsAct, aid)
    assert cached.status == 'sent' and cached.customer_status == 'pending'
    with Session(db.get_bind()) as other:
        if other_action == 'approved':
            cp.decide(other, pid, aid, uid, 'approved', None)
        else:
            est.set_act_status(other, pid, aid, other_action)
        other.commit()
    assert cached.status == 'sent' and cached.customer_status == 'pending'
    with pytest.raises(LookupError if other_action == 'draft' else ValueError):
        cp.decide(db, pid, aid, uid, 'rejected', 'Must not overwrite')
    db.rollback()
    actual = db.get(KsAct, aid)
    assert actual.customer_status == ('approved' if other_action == 'approved' else 'pending')
    assert actual.status == ('sent' if other_action == 'approved' else other_action)


def test_customer_decision_remains_in_callers_rollback(world):
    db, pid, aid, uid = world['db'], world['p'].id, world['act']['id'], world['customer'].id
    est.set_act_status(db, pid, aid, 'sent')
    db.commit()
    assert cp.decide(db, pid, aid, uid, 'approved', None)['customer_status'] == 'approved'
    db.rollback()
    actual = db.get(KsAct, aid)
    assert actual.customer_status == 'pending'
    assert actual.customer_decided_at is None and actual.customer_decided_by is None
