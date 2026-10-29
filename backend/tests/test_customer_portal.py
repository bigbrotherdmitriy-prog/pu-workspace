from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import customer_portal as cp
from app import estimates as est
from app import work_log as wl
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.user import User

PARSED = {"lines": [{"sort_order": 1, "section": "Р1", "position": "1", "code": "", "name": "Кабель", "unit": "м", "qty": "100",
                     "unit_price": "50", "total": "5000", "note": ""}], "total": "5000"}


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Cust org")
        db.add(org)
        db.flush()
        p = Project(name="Проект", organization_id=org.id)
        o = Project(name="Чужой", organization_id=org.id)
        db.add_all([p, o])
        db.flush()
        customer = User(email="zak@example.ru", name="Заказчик")
        stranger = User(email="x@example.ru", name="Чужой")
        db.add_all([customer, stranger])
        db.flush()
        e = est.create_estimate(db, p.id, "Смета", PARSED)
        line = est.get_estimate(db, p.id, e["id"])["lines"][0]["id"]
        wl.add_entry(db, p.id, None, {"work_date": "2026-10-05", "work_name": "Кабель", "qty": "40", "unit": "м", "hours": "8",
                                      "estimate_line_id": line, "labor": [{"person_name": "Иванов", "hours": "8", "hourly_rate": "500"}]})
        act = est.create_act(db, p.id, e["id"], date(2026, 10, 1), date(2026, 10, 28))
        db.commit()
        yield {"db": db, "p": p, "o": o, "customer": customer, "stranger": stranger, "est": e, "act": act}
    engine.dispose()


def test_invite_and_scope(world):
    db = world["db"]
    with pytest.raises(LookupError):
        cp.invite(db, world["p"].id, "nobody@example.ru", None)
    with pytest.raises(ValueError):
        cp.invite(db, world["p"].id, " ", None)
    a = cp.invite(db, world["p"].id, "ZAK@example.ru", 1)
    assert a["email"] == "zak@example.ru"
    assert cp.invite(db, world["p"].id, "zak@example.ru", 1)["id"] == a["id"]  # повторное приглашение идемпотентно
    assert [x["email"] for x in cp.list_access(db, world["p"].id)] == ["zak@example.ru"]
    assert cp.has_access(db, world["customer"].id, world["p"].id) is True
    assert cp.has_access(db, world["customer"].id, world["o"].id) is False
    assert cp.has_access(db, world["stranger"].id, world["p"].id) is False
    assert [p["name"] for p in cp.my_projects(db, world["customer"].id)] == ["Проект"]
    cp.revoke(db, world["p"].id, a["id"])
    assert cp.my_projects(db, world["customer"].id) == []
    with pytest.raises(LookupError):
        cp.revoke(db, world["p"].id, a["id"])


def test_customer_sees_only_sent_acts_and_no_internal_data(world):
    db, pid = world["db"], world["p"].id
    assert cp.summary(db, pid)["acts"] == []  # черновик заказчику не виден
    est.set_act_status(db, pid, world["act"]["id"], "sent", {"customer": "ФНС", "object_name": "ЦОД"})
    s = cp.summary(db, pid)
    assert [a["status"] for a in s["acts"]] == ["sent"] and s["estimates"][0]["percent"] == 40.0
    log = cp.progress_log(db, pid)
    assert log == [{"work_date": "2026-10-05", "work_name": "Кабель", "unit": "м", "qty": "40.000", "location": None, "object_name": None}]
    for hidden in ("hours", "workers", "labor", "crew_id", "hourly_rate", "note"):
        assert hidden not in log[0]


def test_decision_rules(world):
    db, pid, aid = world["db"], world["p"].id, world["act"]["id"]
    with pytest.raises(LookupError):
        cp.decide(db, pid, aid, world["customer"].id, "approved", None)  # ещё не направлен заказчику
    est.set_act_status(db, pid, aid, "sent")
    with pytest.raises(ValueError):
        cp.decide(db, pid, aid, world["customer"].id, "rejected", " ")
    with pytest.raises(ValueError):
        cp.decide(db, pid, aid, world["customer"].id, "maybe", "x")
    r = cp.decide(db, pid, aid, world["customer"].id, "rejected", "Не сходится объём")
    assert r["customer_status"] == "rejected" and r["customer_comment"] == "Не сходится объём"
    est.set_act_status(db, pid, aid, "draft")  # возврат в черновик сбрасывает решение
    assert est.list_acts(db, pid, world["est"]["id"])[0]["customer_status"] == "pending"
    est.set_act_status(db, pid, aid, "sent")
    assert cp.decide(db, pid, aid, world["customer"].id, "approved", None)["customer_status"] == "approved"
    est.set_act_status(db, pid, aid, "signed")
    with pytest.raises(ValueError):
        cp.decide(db, pid, aid, world["customer"].id, "approved", None)
    with pytest.raises(ValueError):
        est.set_act_status(db, pid, aid, "draft")


def test_api_isolation_and_downloads(world):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import customer as api
    from app.core.auth import require_user
    from app.database import get_db

    db, pid, aid = world["db"], world["p"].id, world["act"]["id"]
    cp.invite(db, pid, "zak@example.ru", None)
    est.set_act_status(db, pid, aid, "sent", {"customer": "ФНС"})
    db.commit()
    current = {"user": world["customer"]}
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_user] = lambda: type("U", (), {"id": current["user"].id, "is_active": True, "is_admin": False})()
    c = TestClient(app)

    assert c.get("/customer/projects").json()["projects"][0]["id"] == pid
    assert c.get(f"/customer/projects/{pid}/summary").status_code == 200
    assert c.get(f"/customer/projects/{pid}/progress").json()["entries"][0]["work_name"] == "Кабель"
    assert c.get(f"/customer/projects/{pid}/ks-acts/{aid}/ks2.docx").content[:2] == b"PK"
    assert c.get(f"/customer/projects/{pid}/ks-acts/{aid}/ks3.docx").content[:2] == b"PK"
    assert c.get(f"/customer/projects/{world['o'].id}/summary").status_code == 403
    r = c.post(f"/customer/projects/{pid}/ks-acts/{aid}/decision", json={"decision": "approved"})
    assert r.status_code == 200 and r.json()["customer_status"] == "approved"
    # заказчик не управляет доступом и не видит внутренние маршруты
    assert c.post(f"/projects/{pid}/customer-access", json={"email": "zak@example.ru"}).status_code == 403
    current["user"] = world["stranger"]
    assert c.get(f"/customer/projects/{pid}/summary").status_code == 403
    assert c.get("/customer/projects").json()["projects"] == []
