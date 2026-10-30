import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import executive_docs as api
from app.core.auth import require_user
from app.database import Base, get_db
from app.models.estimates import Estimate, EstimateLine
from app.models.executive_docs import IdRequirement
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.rd import RdSpecItem, RdVolume


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Org")
        db.add(org)
        db.flush()
        p = Project(name="P", organization_id=org.id)
        db.add(p)
        db.flush()
        v = RdVolume(project_id=p.id, object_name="ДУБНА", section="ЭМ", title="ЭМ", file_name="em.pdf", provider="pu_storage", external_id="1")
        db.add(v)
        db.flush()
        db.add(RdSpecItem(volume_id=v.id, project_id=p.id, name="Кабель ВВГнг-LS 3х2,5", status="confirmed"))
        db.add(RdSpecItem(volume_id=v.id, project_id=p.id, name="Щит ЩР-1 с АВР", status="proposed"))
        db.add(RdSpecItem(volume_id=v.id, project_id=p.id, name="Воздуховод круглый", status="rejected"))
        e = Estimate(project_id=p.id, name="Смета")
        db.add(e)
        db.flush()
        db.add(EstimateLine(project_id=p.id, estimate_id=e.id, name="Устройство заземления"))
        db.commit()
        yield {"db": db, "p": p}
    engine.dispose()


def _client(world):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: world["db"]
    app.dependency_overrides[require_user] = lambda: type("U", (), {"id": 1, "is_active": True, "is_admin": True})()
    return TestClient(app)


def test_proposal_from_rd_and_estimates(world):
    c = _client(world)
    r = c.get(f"/projects/{world['p'].id}/executive-docs/kit/proposal", params={"object_name": "ДУБНА"})
    assert r.status_code == 200
    data = r.json()
    titles = [i["title"] for i in data["items"]]
    assert any("кабельных линий" in t for t in titles)
    assert any("заземляющее устройство" in t for t in titles)
    assert "Протокол проверки АВР" in titles
    assert not any("воздуховод" in t.lower() for t in titles)  # отклонённая позиция РД не учитывается
    assert any(i["section"] == "Общее" for i in data["items"])
    assert data["sections"] == ["ЭМ"]
    assert all(i["checked"] for i in data["items"])
    cable = next(i for i in data["items"] if "кабельных линий" in i["title"] and i["doc_kind"] == "aosr")
    assert "в РД" in cable["reason"]


def test_save_replaces_and_respects_selection(world):
    c = _client(world)
    pid = world["p"].id
    world["db"].add(IdRequirement(project_id=pid, object_name="ДУБНА", title="Старое", doc_kind="act", source="norm"))
    world["db"].commit()
    body = {"object_name": "ДУБНА", "items": [
        {"title": "Журнал прокладки кабелей", "doc_kind": "journal", "section": "ЭМ"},
        {"title": "журнал прокладки кабелей", "doc_kind": "journal"},
        {"title": "Своё: акт на ограждение", "doc_kind": "act"},
    ]}
    assert c.post(f"/projects/{pid}/executive-docs/kit", json=body).json() == {"created": 2}
    rows = world["db"].query(IdRequirement).filter_by(project_id=pid).all()
    assert sorted(r.title for r in rows) == ["Журнал прокладки кабелей", "Своё: акт на ограждение"]
    assert {r.source for r in rows} == {"kit"}
    # повторное предложение показывает уже собранный комплект
    again = c.get(f"/projects/{pid}/executive-docs/kit/proposal", params={"object_name": "ДУБНА"}).json()
    assert next(i for i in again["items"] if i["title"].startswith("Своё"))["reason"] == "уже в комплекте"
    assert c.post(f"/projects/{pid}/executive-docs/kit", json={"items": []}).status_code == 400
