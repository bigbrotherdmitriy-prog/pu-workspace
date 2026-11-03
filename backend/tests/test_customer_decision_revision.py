"""Synthetic revision checks; no HTTP requests or production database."""
import pytest
from sqlalchemy.orm import Session
from app import customer_portal as cp, estimates as est
from app.models.estimates import KsAct
from test_customer_portal import world


def sent(world):
    db, pid, aid = world["db"], world["p"].id, world["act"]["id"]
    act = est.set_act_status(db, pid, aid, "sent")
    db.commit()
    return db, pid, aid, world["customer"].id, act["decision_revision"]


def test_old_decision_revision_cannot_apply_after_draft_resend(world):
    db, pid, aid, uid, old = sent(world)
    est.set_act_status(db, pid, aid, "draft")
    current = est.set_act_status(db, pid, aid, "sent")["decision_revision"]
    db.commit()
    assert current > old
    with pytest.raises(cp.StaleDecisionError):
        cp.decide(db, pid, aid, uid, "approved", None, expected_revision=old)
    assert db.get(KsAct, aid).customer_status == "pending"
    assert cp.decide(db, pid, aid, uid, "approved", None, expected_revision=current)["customer_status"] == "approved"


def test_cached_act_cannot_bypass_newer_revision(world):
    db, pid, aid, uid, old = sent(world)
    cached = db.get(KsAct, aid)
    with Session(db.get_bind()) as other:
        est.set_act_status(other, pid, aid, "draft")
        fresh = est.set_act_status(other, pid, aid, "sent")["decision_revision"]
        other.commit()
    assert cached.decision_revision == old
    with pytest.raises(cp.StaleDecisionError):
        cp.decide(db, pid, aid, uid, "approved", None, expected_revision=old)
    db.rollback()
    assert db.get(KsAct, aid).decision_revision == fresh
    assert db.get(KsAct, aid).customer_status == "pending"


def test_revision_and_status_roll_back_together(world):
    db, pid, aid, uid, old = sent(world)
    assert est.set_act_status(db, pid, aid, "draft")["decision_revision"] > old
    db.rollback()
    actual = db.get(KsAct, aid)
    assert actual.decision_revision == old and actual.status == "sent"


def test_header_change_invalidates_an_open_decision(world):
    db, pid, aid, uid, old = sent(world)
    new = est.set_act_status(db, pid, aid, "sent", {"customer": "Synthetic changed customer"})["decision_revision"]
    db.commit()
    assert new > old
    with pytest.raises(cp.StaleDecisionError):
        cp.decide(db, pid, aid, uid, "rejected", "Synthetic reason", expected_revision=old)


@pytest.mark.parametrize("bad", [True, -1, "1", 1.5, 2147483648])
def test_revision_is_a_strict_nonnegative_database_integer(world, bad):
    db, pid, aid, uid, old = sent(world)
    with pytest.raises(ValueError):
        cp.decide(db, pid, aid, uid, "approved", None, expected_revision=bad)
    assert db.get(KsAct, aid).customer_status == "pending"
