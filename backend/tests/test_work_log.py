from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import work_log as wl
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.rd import RdSpecItem, RdVolume


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="WL org")
        db.add(org)
        db.flush()
        p = Project(name="P", organization_id=org.id)
        o = Project(name="O", organization_id=org.id)
        db.add_all([p, o])
        db.flush()
        db.commit()
        yield {"db": db, "p": p, "o": o}
    engine.dispose()


def _add(db, pid, **kw):
    data = {"work_date": "2026-10-20", "work_name": "Прокладка кабеля", "qty": "10", "unit": "м"}
    data.update(kw)
    return wl.add_entry(db, pid, 1, data)


def test_add_list_update_delete(world):
    db, pid = world["db"], world["p"].id
    e = _add(db, pid)
    db.commit()
    assert wl.list_entries(db, pid)[0]["work_name"] == "Прокладка кабеля"
    wl.update_entry(db, pid, e["id"], {"qty": "12,5", "note": "ок"})
    assert wl.list_entries(db, pid)[0]["qty"] == "12.500"
    wl.delete_entry(db, pid, e["id"])
    assert wl.list_entries(db, pid) == []


def test_validation(world):
    db, pid = world["db"], world["p"].id
    with pytest.raises(ValueError):
        _add(db, pid, work_name="  ")
    with pytest.raises(ValueError):
        _add(db, pid, work_date=(date.today() + timedelta(days=5)).isoformat())
    with pytest.raises(ValueError):
        _add(db, pid, qty="-3")
    with pytest.raises(ValueError):
        _add(db, pid, workers=-1)
    assert _add(db, pid, qty="12,5")["qty"] == "12.500"
    vol = RdVolume(project_id=world["o"].id, title="V", file_name="v.pdf", provider="google_drive")
    db.add(vol)
    db.flush()
    foreign = RdSpecItem(volume_id=vol.id, project_id=world["o"].id, name="X", status="confirmed")
    db.add(foreign)
    db.flush()
    with pytest.raises(LookupError):
        _add(db, pid, rd_spec_item_id=foreign.id)


def test_foreign_project_and_filters(world):
    db, pid = world["db"], world["p"].id
    _add(db, pid, work_date="2026-10-01", work_name="Монтаж щита", location="Этаж 1")
    _add(db, pid, work_date="2026-10-20", work_name="Прокладка кабеля")
    e = _add(db, world["o"].id)
    db.commit()
    assert len(wl.list_entries(db, pid)) == 2
    assert [x["work_name"] for x in wl.list_entries(db, pid, date_from="2026-10-10")] == ["Прокладка кабеля"]
    assert [x["work_name"] for x in wl.list_entries(db, pid, date_to="2026-10-10")] == ["Монтаж щита"]
    assert [x["work_name"] for x in wl.list_entries(db, pid, q="ЭТАЖ")] == ["Монтаж щита"]
    with pytest.raises(LookupError):
        wl.update_entry(db, pid, e["id"], {"note": "x"})
    with pytest.raises(LookupError):
        wl.delete_entry(db, pid, e["id"])


def test_summary_suggestions_xlsx(world):
    db, pid = world["db"], world["p"].id
    _add(db, pid, qty="10", object_name="ДУБНА")
    _add(db, pid, qty="5", work_date="2026-10-21")
    vol = RdVolume(project_id=pid, title="V", file_name="v.pdf", provider="google_drive")
    db.add(vol)
    db.flush()
    db.add(RdSpecItem(volume_id=vol.id, project_id=pid, name="Насос", unit="шт", status="confirmed"))
    db.commit()
    s = wl.summary(db, pid)
    assert s["entries"] == 2 and s["days"] == 2
    assert s["by_work"][0]["qty_total"] == "15.000"
    sg = wl.suggestions(db, pid)
    assert sg["works"][0]["work_name"] == "Прокладка кабеля" and sg["objects"] == ["ДУБНА"]
    assert sg["rd_items"][0]["name"] == "Насос"
    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(wl.to_xlsx(db, pid)))
    assert wb["Журнал работ"]["A1"].value == "Дата"
    assert wb["Журнал работ"].max_row == 3


def test_api(world, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from app.api import work_log as api
    from app.core.auth import require_user
    from app.database import get_db

    db, pid, other = world["db"], world["p"].id, world["o"].id
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_user] = lambda: type("U", (), {"id": 1, "is_active": True, "is_admin": True})()
    client = TestClient(app)

    r = client.post(f"/projects/{pid}/work-log", json={"work_date": "2026-10-20", "work_name": "Монтаж щита", "qty": "2", "unit": "шт"})
    assert r.status_code == 200
    eid = r.json()["id"]
    assert client.post(f"/projects/{pid}/work-log", json={"work_date": "2026-10-20", "work_name": " "}).status_code == 422
    r = client.get(f"/projects/{pid}/work-log")
    assert r.status_code == 200 and len(r.json()["entries"]) == 1 and r.json()["summary"]["entries"] == 1
    assert client.get(f"/projects/{pid}/work-log/suggestions").status_code == 200
    r = client.get(f"/projects/{pid}/work-log/export.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert client.patch(f"/projects/{pid}/work-log/{eid}", json={"note": "ок"}).json()["note"] == "ок"

    def scoped(_db, _user, p, minimum="viewer"):
        if p != pid:
            raise HTTPException(status_code=404, detail="nf")
        return "viewer"

    monkeypatch.setattr(api, "require_project_role", scoped)
    assert client.get(f"/projects/{other}/work-log").status_code == 404
    assert client.delete(f"/projects/{pid}/work-log/{eid}").json() == {"deleted": eid}
    assert client.delete(f"/projects/{pid}/work-log/{eid}").status_code == 404
